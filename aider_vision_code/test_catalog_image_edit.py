import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from catalog_image_edit import apply_edits, edit_from_image, respect_instruction
from test_wpf_catalog import example
from wpf_catalog import render, save_owned


class ImageEditTests(unittest.TestCase):
    def weighted_panels(self):
        plan=example();plan['width']=1000;plan['height']=560
        plan['nodes']=[
            dict(id='layout',parent='root',kind='column',text='',weight=1),
            dict(id='body',parent='layout',kind='row',text='',weight=1),
            dict(id='menu',parent='body',kind='column',text='',weight=1),
            dict(id='menu_text',parent='menu',kind='text',text='Menú',weight=1),
            dict(id='center',parent='body',kind='column',text='',weight=4),
            dict(id='content',parent='center',kind='text',text='Contenido',weight=1),
        ]
        return plan

    def test_independent_scroll_operation_is_structural_and_validated(self):
        plan=example();plan['nodes']=[
            dict(id='layout',parent='root',kind='column',text='',weight=1),
            dict(id='content',parent='layout',kind='text',text='Contenido',weight=1),
            dict(id='actions',parent='layout',kind='row',text='',weight=1),
            dict(id='save',parent='actions',kind='button',text='Guardar',weight=1,action='none'),
        ]
        after=apply_edits(plan,dict(supported=True,reason='',operations=[
            dict(kind='independent_scroll',container='layout',fixed_children=['actions'])]))
        self.assertEqual(after['independent_scroll'],
                         {'container':'layout','fixed_children':['actions']})
        self.assertNotIn('independent_scroll',plan)
        with self.assertRaisesRegex(ValueError,'hijos directos'):
            apply_edits(plan,dict(supported=True,reason='',operations=[
                dict(kind='independent_scroll',container='layout',fixed_children=['save'])]))

    def test_background_edit_preserves_entire_compact_plan(self):
        before=example();before.update(window_fit='content_height',outer_margin=24)
        after=apply_edits(before,dict(supported=True,operations=[dict(kind='color',target='window',property='background',value='#ADD8E6')]))
        expected=copy.deepcopy(before);expected['background']=['#ADD8E6']*2
        self.assertEqual(after,expected)
        self.assertEqual(render(before)['MainWindow.xaml.cs'],render(after)['MainWindow.xaml.cs'])

    def test_node_color_does_not_change_window(self):
        before=example()
        after=apply_edits(before,dict(supported=True,operations=[dict(kind='color',target='submit',property='background',value='#123456')]))
        expected=copy.deepcopy(before);expected['nodes'][-1]['background']='#123456'
        self.assertEqual(after,expected)

    def test_invalid_color_operations_are_atomic(self):
        for extra in [dict(value='red'),dict(target='missing'),dict(property='width'),dict(target='window',property='foreground'),dict(window_fit='fixed')]:
            op=dict(kind='color',target='window',property='background',value='#ADD8E6');op.update(extra)
            with self.subTest(extra=extra),self.assertRaises(ValueError):apply_edits(example(),dict(supported=True,operations=[op]))

    def test_text_edits_send_operations_schema_not_replacement_plan(self):
        from unittest.mock import Mock
        from catalog_image_edit import edit_existing
        api=Mock();api.post_ollama.return_value={'message':{'content':'{"supported":true,"operations":[{"kind":"color","target":"window","property":"background","value":"#ADD8E6"}]}'}}
        before=example();before['window_fit']='content_height'
        with tempfile.TemporaryDirectory() as d:
            after=edit_existing(api,before,'Fondo celeste',None,'coder',Path(d))
        payload=api.post_ollama.call_args.args[1]
        self.assertNotIn('images',payload['messages'][0])
        self.assertIn('operations',payload['format']['properties'])
        self.assertNotIn('nodes',payload['format']['properties'])
        self.assertEqual(payload['options']['num_ctx'],16384)
        self.assertEqual(after['window_fit'],'content_height')

    def test_fit_window_preserves_nodes_and_validation(self):
        before=example()
        after=apply_edits(before,dict(supported=True,operations=[dict(kind='fit_window',margin=24)]))
        self.assertEqual(after['nodes'],before['nodes'])
        self.assertEqual(after['width'],before['width'])
        self.assertEqual(after['background'],before['background'])
        files=render(after)
        self.assertEqual(files['MainWindow.xaml.cs'],render(before)['MainWindow.xaml.cs'])
        self.assertIn('SizeToContent="Height" MinHeight="0"',files['MainWindow.xaml'])
        self.assertNotIn('MinHeight="420"',files['MainWindow.xaml'])

    def test_density_is_a_global_plan_property(self):
        before=example()
        after=apply_edits(before,dict(supported=True,operations=[
            dict(kind='set_density',value='tight')]))
        self.assertEqual(after['density'],'tight')
        self.assertEqual(after['nodes'],before['nodes'])
        self.assertIn('Padding="6"',render(after)['MainWindow.xaml'])
        with self.assertRaises(ValueError):
            apply_edits(before,dict(supported=True,operations=[
                dict(kind='set_density',value='tiny')]))

    def test_weights_resize_only_sibling_regions_and_render(self):
        before=self.weighted_panels()
        after=apply_edits(before,dict(supported=True,reason='',operations=[
            dict(kind='set_weight',id='center',value=5)]))
        expected=copy.deepcopy(before);expected['nodes'][4]['weight']=5
        self.assertEqual(after,expected)
        self.assertEqual(before['nodes'][4]['weight'],4)
        xaml=render(after)['MainWindow.xaml']
        self.assertIn('<ColumnDefinition Width="1*"',xaml)
        self.assertIn('<ColumnDefinition Width="5*"',xaml)

    def test_clear_independent_scroll_requires_existing_region(self):
        before=self.weighted_panels()
        before['independent_scroll']={'container':'center','fixed_children':['content']}
        after=apply_edits(before,dict(supported=True,reason='',operations=[
            dict(kind='clear_independent_scroll'),
            dict(kind='set_viewport_fit',value='fit')]))
        self.assertNotIn('independent_scroll',after)
        self.assertEqual(after['viewport_fit'],'fit')
        with self.assertRaisesRegex(ValueError,'No existe una región'):
            apply_edits(self.weighted_panels(),dict(supported=True,reason='',operations=[
                dict(kind='clear_independent_scroll')]))

    def test_invalid_weight_changes_are_atomic(self):
        before=self.weighted_panels()
        invalid=[
            dict(kind='set_weight',id='missing',value=5),
            dict(kind='set_weight',id='layout',value=2),
            dict(kind='set_weight',id='center',value=0),
            dict(kind='set_weight',id='center',value=True),
            dict(kind='set_weight',id='center',value=5,extra='x'),
        ]
        for op in invalid:
            with self.subTest(op=op),self.assertRaises(ValueError):
                apply_edits(before,dict(supported=True,reason='',operations=[op]))
            self.assertEqual(before,self.weighted_panels())
        with self.assertRaisesRegex(ValueError,'no produjo cambios'):
            apply_edits(before,dict(supported=True,reason='',operations=[
                dict(kind='set_weight',id='center',value=4)]))

    def test_localized_request_rejects_global_density_substitute(self):
        plan=self.weighted_panels()
        proposal=dict(supported=True,reason='',operations=[
            dict(kind='set_density',value='comfortable'),
            dict(kind='set_weight',id='center',value=5)])
        constrained=respect_instruction(
            plan,proposal,'Ampliá únicamente el área central y conservá todo lo demás.')
        self.assertEqual(constrained['operations'],[
            dict(kind='set_weight',id='center',value=5)])
        global_request=respect_instruction(
            plan,proposal,'Aumentá el espaciado global de toda la pantalla.')
        self.assertEqual(global_request['operations'],proposal['operations'])
        only_density=respect_instruction(plan,dict(supported=True,reason='',operations=[
            dict(kind='set_density',value='comfortable')]),
            'Agrandá solamente la zona central.')
        self.assertEqual(only_density['operations'],[])
        with self.assertRaisesRegex(ValueError,'cambios concretos'):
            apply_edits(plan,only_density)

    def test_central_request_requires_actual_central_weight_increase(self):
        plan=self.weighted_panels()
        plan['nodes'].extend([
            dict(id='actions',parent='center',kind='row',text='',weight=1),
            dict(id='save',parent='actions',kind='button',text='Guardar',weight=1,action='none'),
        ])
        plan['independent_scroll']={'container':'center','fixed_children':['actions']}
        wrong=dict(supported=True,reason='',operations=[
            dict(kind='set_weight',id='content',value=5)])
        self.assertEqual(respect_instruction(
            plan,wrong,'Ampliá solamente la zona central.')['operations'],[])
        repeated=dict(supported=True,reason='',operations=[
            dict(kind='set_weight',id='center',value=4)])
        self.assertEqual(respect_instruction(
            plan,repeated,'Ampliá solamente la zona central.')['operations'],[])
        valid=dict(supported=True,reason='',operations=[
            dict(kind='set_weight',id='center',value=5)])
        self.assertEqual(respect_instruction(
            plan,valid,'Ampliá solamente la zona central.')['operations'],valid['operations'])

    def test_no_scale_constraint_wins_and_duplicate_operations_collapse(self):
        plan=example()
        proposal=dict(supported=True,reason='',operations=[
            dict(kind='set_viewport_fit',value='fit'),
            dict(kind='set_viewport_fit',value='fit')])
        instruction=('No escales ni reduzcas globalmente el formulario. Conservá el tamaño de textos '
                     'y controles. Aumentá únicamente la altura hasta el contenido.')
        constrained=respect_instruction(plan,proposal,instruction)
        self.assertEqual(constrained['operations'],[dict(kind='fit_window',margin=24)])
        result=apply_edits(plan,constrained)
        self.assertEqual(result['window_fit'],'content_height')
        self.assertNotEqual(result.get('viewport_fit'),'fit')
        exact=respect_instruction(plan,proposal,
            'No reduzcas el tamaño de los textos ni de los controles.')
        self.assertEqual(exact['operations'],[])

    def test_literal_no_scroll_without_reduction_uses_window_fit(self):
        plan=self.weighted_panels()
        plan['independent_scroll']={'container':'center','fixed_children':['content']}
        proposal=dict(supported=True,reason='',operations=[
            dict(kind='clear_independent_scroll'),
            dict(kind='set_viewport_fit',value='fit')])
        instruction=('Ampliá el área central para que todo el contenido sea visible sin '
                     'desplazamiento vertical. Conservá los textos, controles, colores, '
                     'proporciones generales y funciones existentes. No reduzcas el tamaño '
                     'de los elementos.')
        constrained=respect_instruction(plan,proposal,instruction)
        self.assertEqual(constrained['operations'],[
            dict(kind='clear_independent_scroll'),dict(kind='fit_window',margin=24)])
        result=apply_edits(plan,constrained)
        self.assertNotIn('independent_scroll',result)
        self.assertEqual(result['window_fit'],'content_height')
        self.assertNotEqual(result.get('viewport_fit'),'fit')

    def test_negative_height_does_not_become_height_growth(self):
        plan=example()
        proposal=dict(supported=True,reason='',operations=[
            dict(kind='set_viewport_fit',value='fit')])
        constrained=respect_instruction(
            plan,proposal,'No aumentes la altura y no reduzcas el tamaño de los elementos.')
        self.assertEqual(constrained['operations'],[])

    def test_fit_margin_preserves_available_width_and_does_not_accumulate(self):
        before=example()
        after=apply_edits(before,dict(supported=True,operations=[dict(kind='fit_window',margin=40)]))
        self.assertEqual(after['width']-80,before['width']-48)
        again=apply_edits(after,dict(supported=True,operations=[dict(kind='fit_window',margin=24)]))
        self.assertEqual(again['width'],before['width'])

    def test_invalid_fit_is_rejected_without_mutation(self):
        before=example()
        for margin in (-1,97,True,'24',None):
            with self.subTest(margin=margin),self.assertRaises(ValueError):
                apply_edits(before,dict(supported=True,operations=[dict(kind='fit_window',margin=margin)]))
        self.assertEqual(before,example())

    def test_group_preserves_controls_and_behavior(self):
        before=example()
        after=apply_edits(before,dict(supported=True,operations=[dict(kind='row',ids=['user','secret'],value='Stretch')]))
        self.assertEqual(render(before)['MainWindow.xaml.cs'],render(after)['MainWindow.xaml.cs'])
        for old in before['nodes']:
            new=next(n for n in after['nodes'] if n['id']==old['id'])
            self.assertEqual({k:v for k,v in old.items() if k!='parent'}, {k:v for k,v in new.items() if k!='parent'})
        self.assertEqual(before,example())
        self.assertEqual(after['nodes'][2]['kind'],'row')

    def test_align_text_only_and_render(self):
        p=example();p['nodes'].insert(2,dict(id='heading',parent='card',kind='title',text='Datos'))
        after=apply_edits(p,dict(supported=True,operations=[dict(kind='align',ids=['heading'],value='Left')]))
        self.assertIn('HorizontalAlignment="Left"',render(after)['MainWindow.xaml'])
        self.assertEqual(render(p)['MainWindow.xaml.cs'],render(after)['MainWindow.xaml.cs'])

    def test_unsupported_ambiguous_or_invalid_edits_rejected(self):
        for op in [dict(kind='delete',ids=['user']),dict(kind='row',ids=['user','user']),
                   dict(kind='row',ids=['user','submit']),dict(kind='row',ids=['secret','user']),
                   dict(kind='row',ids=['user','help']),dict(kind='align',ids=['user'],value='Left'),
                   dict(kind='align',ids=['missing'],value='Left')]:
            with self.subTest(op=op),self.assertRaises(ValueError):apply_edits(example(),dict(supported=True,operations=[op]))
        with self.assertRaises(ValueError):apply_edits(example(),dict(supported=False,reason='ambiguo'))

    def test_image_model_receives_screenshot_and_plan(self):
        from unittest.mock import Mock
        api=Mock();api.post_ollama.return_value={'message':{'content':'{"supported":true,"operations":[{"kind":"row","ids":["user","secret"],"value":"Stretch"}]}'}}
        with tempfile.TemporaryDirectory() as d:
            edit_from_image(api,example(),'Juntá los campos','data:image/png;base64,YWJj','vision',Path(d))
        payload=api.post_ollama.call_args.args[1]
        self.assertEqual(payload['model'],'vision');self.assertEqual(payload['messages'][0]['images'],['YWJj'])
        self.assertIn('Usuario',payload['messages'][0]['content'])

    def test_truncation_stops(self):
        from unittest.mock import Mock
        api=Mock();api.post_ollama.return_value={'done_reason':'length'}
        with tempfile.TemporaryDirectory() as d,self.assertRaises(ValueError):edit_from_image(api,example(),'x','abc','vision',Path(d))

    def test_owned_image_dispatch_precedes_reference(self):
        import server
        with tempfile.TemporaryDirectory() as d,patch('catalog_pipeline.run_catalog_job') as catalog,patch('visual_pipeline.run_reference_job') as reference:
            root=Path(d);save_owned(root,example())
            server.run_job(root,'Poné los campos en una fila','',1,'code',image='abc',vision_model='vision',build_mode='wpf')
            catalog.assert_called_once();reference.assert_not_called()
            self.assertEqual(catalog.call_args.args[-2:],('abc','vision'))
            catalog.reset_mock()
            server.run_job(root,'Solo analizá la captura','',1,'code',image='abc',build_mode='wpf')
            catalog.assert_not_called()

    def test_foreign_image_keeps_reference_route(self):
        import server
        with tempfile.TemporaryDirectory() as d,patch('catalog_pipeline.run_catalog_job') as catalog,patch('visual_pipeline.run_reference_job') as reference:
            server.run_job(Path(d),'Copiá esta imagen','',1,'code',image='abc',build_mode='wpf')
            catalog.assert_not_called();reference.assert_called_once()

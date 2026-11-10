import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from catalog_plan_edit import (_density_fallback, _require_visible_catalog,
                               _independent_scroll_fallback,
                               _relayout_fallback,
                               _balance_new_rows,
                               _wrap_leaf_rows,
                               _fit_row_weights,
                               _compressed_leaf_ids,
                               _require_no_new_horizontal_compression,
                               _instruction_requires_general_editor,
                               _requires_general_editor, apply_catalog_candidate,
                               edit_plan, StructuredEditUnsupported)
from catalog_instruction_intents import requests_relayout
from code_preview import project_fingerprint
from test_wpf_catalog import example
from wpf_catalog import render, save_owned


class CatalogPlanEditTests(unittest.TestCase):
    def test_row_weight_solver_preserves_each_intrinsic_width(self):
        demands=[104,156,132,88,144];width=690
        weights=_fit_row_weights(demands,width)
        self.assertIsNotNone(weights)
        usable=width-8*len(demands);total=sum(weights)
        self.assertTrue(all(usable*weight/total>=demand
                            for weight,demand in zip(weights,demands)))

    def test_overflowing_field_rows_wrap_in_order_only_when_authorized(self):
        before=example();before['width']=900;before['outer_margin']=24
        before['nodes']=[
            dict(id='rootcol',parent='root',kind='column',text='',weight=1),
            dict(id='card',parent='rootcol',kind='card',text='',weight=1),
            dict(id='fields',parent='card',kind='row',text='',weight=1),
            *[dict(id=f'f{i}',parent='fields',kind='field',text='Campo extenso',
                   weight=1,icon='none',required=False,value='') for i in range(5)],
        ]
        after=copy.deepcopy(before);after['width']=420
        unchanged=_wrap_leaf_rows(
            before,after,700,'Conservá el diseño y aumentá el ancho si hace falta.')
        self.assertEqual(unchanged,after)
        instruction=('Podés aumentar el ancho de la ventana y reducir espacios y márgenes. '
                     'Distribuí los campos en más columnas.')
        wrapped=_wrap_leaf_rows(before,after,700,instruction)
        rows=[node for node in wrapped['nodes'] if node['kind']=='row']
        self.assertGreater(len(rows),1)
        order=[node['id'] for row in rows for node in wrapped['nodes']
               if node.get('parent')==row['id']]
        self.assertEqual(order,[f'f{i}' for i in range(5)])
        self.assertEqual(wrapped['outer_margin'],0)
        self.assertEqual(wrapped['width'],700)
        self.assertEqual(after['width'],420)

    def test_overflowing_button_rows_are_not_rewritten_as_field_rows(self):
        before=example();before['width']=900;before['outer_margin']=24
        before['nodes']=[
            dict(id='rootcol',parent='root',kind='column',text='',weight=1),
            dict(id='buttons',parent='rootcol',kind='row',text='',weight=1),
            *[dict(id=f'b{i}',parent='buttons',kind='button',text='Acción extensa',
                   weight=1,action='none',tone='primary') for i in range(5)],
        ]
        after=copy.deepcopy(before);after['width']=420
        wrapped=_wrap_leaf_rows(
            before,after,700,
            'Podés aumentar el ancho de la ventana y reducir espacios y márgenes.')
        rows=[node for node in wrapped['nodes'] if node['kind']=='row']
        self.assertEqual([node['id'] for node in rows],['buttons'])
        self.assertEqual([node['parent'] for node in wrapped['nodes'] if node['kind']=='button'],
                         ['buttons']*5)

    def test_new_row_weights_follow_generic_content_demand(self):
        before=example();before['nodes']=[
            dict(id='rootcol',parent='root',kind='column',text='',weight=1),
            dict(id='large',parent='rootcol',kind='card',text='',weight=1),
            dict(id='many',parent='large',kind='row',text='',weight=1),
            *[dict(id=f'field{i}',parent='many',kind='field',text='Campo extenso',
                   weight=1,icon='none',required=False,value='') for i in range(4)],
            dict(id='small',parent='rootcol',kind='card',text='',weight=1),
            dict(id='short',parent='small',kind='text',text='Estado',weight=1),
        ]
        after=copy.deepcopy(before)
        after['nodes'].append(dict(id='pair',parent='rootcol',kind='row',text='',weight=1))
        for node in after['nodes']:
            if node['id'] in ('large','small'):node['parent']='pair'
        balanced=_balance_new_rows(before,after)
        weights={node['id']:node['weight'] for node in balanced['nodes']
                 if node['id'] in ('large','small')}
        self.assertEqual(weights['large'],12)
        self.assertLess(weights['small'],weights['large'])
        self.assertEqual(next(n for n in after['nodes'] if n['id']=='large')['weight'],1)

    def test_relayout_rejects_only_new_single_line_compression(self):
        before=example();before['width']=900;before['outer_margin']=24
        before['nodes']=[
            dict(id='rootcol',parent='root',kind='column',text='',weight=1),
            dict(id='card_a',parent='rootcol',kind='card',text='',weight=1),
            dict(id='fields',parent='card_a',kind='row',text='',weight=1),
            *[dict(id=f'f{i}',parent='fields',kind='field',text=label,weight=1,
                   icon='none',required=False,value='') for i,label in enumerate(
                       ('Puerto COM','Velocidad (baud)','Bits de datos','Paridad','Bits de parada'))],
            dict(id='card_b',parent='rootcol',kind='card',text='',weight=1),
            dict(id='status',parent='card_b',kind='text',text='Estado',weight=1),
        ]
        after=copy.deepcopy(before)
        row=dict(id='paired',parent='rootcol',kind='row',text='',weight=1)
        after['nodes'].insert(1,row)
        for node in after['nodes']:
            if node['id'] in ('card_a','card_b'):node['parent']='paired'
        self.assertFalse(_compressed_leaf_ids(before))
        self.assertTrue(_compressed_leaf_ids(after))
        with self.assertRaisesRegex(ValueError,'no cabe en el ancho conservado'):
            _require_no_new_horizontal_compression(before,after)

        widened=_require_no_new_horizontal_compression(
            before,after,'Podés aumentar el ancho de la ventana dentro del área útil.')
        self.assertGreater(widened['width'],after['width'])
        self.assertFalse(_compressed_leaf_ids(widened)-_compressed_leaf_ids(before))
        self.assertEqual(after['width'],900)

        # Existing narrow controls are not blamed on an unrelated edit.
        already_narrow=copy.deepcopy(before);already_narrow['width']=360
        recolored=copy.deepcopy(already_narrow);recolored['background']=['#000000','#000000']
        _require_no_new_horizontal_compression(already_narrow,recolored)

    def test_unambiguous_sibling_cards_reflow_without_model_guessing(self):
        plan=example();plan['nodes']=[
            dict(id='rootcol',parent='root',kind='column',text='',weight=1),
            dict(id='center',parent='rootcol',kind='column',text='',weight=1),
            dict(id='heading',parent='center',kind='title',text='Panel',weight=1),
            dict(id='card_a',parent='center',kind='card',text='',weight=1),
            dict(id='a',parent='card_a',kind='text',text='A',weight=1),
            dict(id='card_b',parent='center',kind='card',text='',weight=1),
            dict(id='b',parent='card_b',kind='text',text='B',weight=1),
            dict(id='actions',parent='center',kind='row',text='',weight=1),
            dict(id='save',parent='actions',kind='button',text='Guardar',weight=1,action='none'),
        ];plan['independent_scroll']={'container':'center','fixed_children':['actions']}
        instruction=('Reorganizá el contenido central y distribuí las secciones en más columnas. '
                     'Evitá el desplazamiento y no reduzcas el tamaño de los elementos. '
                     'Podés reducir espacios y márgenes.')
        result=_relayout_fallback(plan,instruction)
        new_group=next(node for node in result['nodes'] if node['id'] not in {
            item['id'] for item in plan['nodes']})
        self.assertEqual(new_group['kind'],'row')
        self.assertEqual([node['id'] for node in result['nodes'] if node['parent']==new_group['id']],
                         ['card_a','card_b'])
        self.assertNotIn('independent_scroll',result)
        self.assertEqual(result['window_fit'],'content_height')
        self.assertEqual(result['density'],'tight')
        ambiguous=copy.deepcopy(plan)
        ambiguous['nodes'].extend([
            dict(id='other',parent='rootcol',kind='column',text='',weight=1),
            dict(id='card_c',parent='other',kind='card',text='',weight=1),
            dict(id='card_d',parent='other',kind='card',text='',weight=1),
        ])
        self.assertIsNone(_relayout_fallback(
            ambiguous,'Redistribuí las secciones de la interfaz en columnas.'))

    def test_explicit_section_redistribution_requires_general_editor(self):
        instruction=('Reorganizá el contenido central para aprovechar mejor el ancho disponible. '
                     'Distribuí las secciones en más columnas.')
        self.assertTrue(requests_relayout(instruction))
        self.assertTrue(requests_relayout(
            'Redistribuí los campos en dos filas horizontales.'))
        self.assertFalse(requests_relayout(
            'Ampliá el área central y conservá sus controles.'))
        self.assertFalse(requests_relayout(
            'Cambiá el color de los controles de la columna derecha.'))

    def test_relayout_tries_bounded_hierarchy_before_general_fallback(self):
        with tempfile.TemporaryDirectory() as d:
            project=Path(d)/'project';project.mkdir();folder=Path(d)/'evidence';folder.mkdir()
            files=render(example())
            for name,text in files.items():(project/name).write_text(text,encoding='utf8')
            save_owned(project,example());api=Mock();api.STATE={'chat_id':'chat'}
            with patch('catalog_plan_edit.edit_existing',side_effect=ValueError(
                    'Edición visual no soportada: requiere reparentar controles')) as editor:
                with self.assertRaises(StructuredEditUnsupported):
                    edit_plan(api,project,
                        'Reorganizá el contenido y distribuí las secciones en más columnas.',
                        'model',folder)
            editor.assert_called_once()
            self.assertIn('editor general en copia',api.log.call_args.args[0])

    def test_independent_scroll_region_is_resolved_from_typed_hierarchy(self):
        instruction=('Si no entra, agregá desplazamiento vertical solamente en la zona central, '
                     'manteniendo visibles la cabecera y los botones inferiores.')
        self.assertTrue(_instruction_requires_general_editor(instruction))
        self.assertFalse(_instruction_requires_general_editor(
            'Compactá los espacios para que el contenido entre en la ventana.'))
        plan=example();plan['nodes']=[
            dict(id='rootcol',parent='root',kind='column',text='',weight=1),
            dict(id='header',parent='rootcol',kind='row',text='',weight=1),
            dict(id='brand',parent='header',kind='title',text='Marca',weight=1),
            dict(id='body',parent='rootcol',kind='row',text='',weight=11),
            dict(id='menu',parent='body',kind='column',text='',weight=1),
            dict(id='option',parent='menu',kind='text',text='Inicio',weight=1),
            dict(id='center',parent='body',kind='column',text='',weight=4),
            dict(id='detail',parent='center',kind='text',text='Contenido',weight=1),
            dict(id='actions',parent='center',kind='row',text='',weight=1),
            dict(id='save',parent='actions',kind='button',text='Guardar',weight=1,action='none'),
        ]
        candidate=_independent_scroll_fallback(plan,instruction)
        self.assertEqual(candidate['independent_scroll'],
                         {'container':'center','fixed_children':['actions']})
        self.assertNotIn('independent_scroll',plan)

    def test_only_known_structural_limits_request_general_editor(self):
        self.assertTrue(_requires_general_editor(ValueError(
            'No se eliminan contenedores ni sus controles en cascada')))
        self.assertTrue(_requires_general_editor(ValueError(
            'Edición visual no soportada: requiere una zona central desplazable')))
        self.assertFalse(_requires_general_editor(ValueError('Color inválido')))

    def test_container_cascade_proposal_is_routed_without_changing_project(self):
        with tempfile.TemporaryDirectory() as d:
            project=Path(d)/'project';project.mkdir();folder=Path(d)/'evidence';folder.mkdir()
            files=render(example())
            for name,text in files.items():(project/name).write_text(text,encoding='utf8')
            save_owned(project,example())
            before={name:(project/name).read_bytes() for name in files}
            api=Mock();api.STATE={'chat_id':'chat'}
            with patch('catalog_plan_edit.edit_existing',side_effect=ValueError(
                    'No se eliminan contenedores ni sus controles en cascada')):
                with self.assertRaises(StructuredEditUnsupported):
                    edit_plan(api,project,'Dejá fija la cabecera y desplazá solo el centro',
                              'model',folder)
            self.assertEqual({name:(project/name).read_bytes() for name in files},before)
            self.assertEqual(json.loads((project/'.avc_catalog.json').read_text())['plan'],example())
            self.assertIn('editor general en copia',api.log.call_args.args[0])

    def test_content_height_larger_than_work_area_is_rejected(self):
        plan=example();plan['window_fit']='content_height'
        capture={'observation':{'capture_constraints':{
            'window_size_px':[1000,1035],
            'work_area_size_px':[1366,728],
            'fits_work_area':False,
        }}}
        with self.assertRaisesRegex(ValueError,'No es posible mostrar todo'):
            _require_visible_catalog(plan,capture)

    def test_rejected_content_height_keeps_diagnostic_preview(self):
        with tempfile.TemporaryDirectory() as d:
            project=Path(d)/'project';project.mkdir()
            plan=example();files=render(plan)
            for name,text in files.items():(project/name).write_text(text,encoding='utf8')
            save_owned(project,plan)
            api=Mock();api.STATE={'chat_id':'chat'};api.STATE_LOCK=Mock()
            api.STATE_LOCK.__enter__=Mock();api.STATE_LOCK.__exit__=Mock(return_value=False)
            api.post_ollama.return_value={'message':{'content':json.dumps({
                'supported':True,'reason':'','operations':[{'kind':'fit_window','margin':24}]})}}
            base={'ok':True,'shot':b'before','exe':'base.exe'}
            rejected={'ok':True,'shot':b'after','exe':'candidate.exe','observation':{
                'capture_constraints':{'window_size_px':[1000,1035],
                'work_area_size_px':[1366,720],'fits_work_area':False}}}
            with patch('catalog_plan_edit.WpfAdapter') as adapter, patch('code_preview.save_preview') as saved, \
                    patch('catalog_plan_edit.review_candidate',return_value={'approved':True}) as reviewed:
                adapter.return_value.sources.return_value={'MainWindow.xaml':'hash'}
                adapter.return_value.build_capture.side_effect=[base,rejected]
                with self.assertRaisesRegex(ValueError,'No es posible mostrar todo'):
                    edit_plan(api,project,'Ajustá la altura al contenido','model',Path(d)/'evidence')
            reviewed.assert_called_once()
            diagnostic=saved.call_args.args[2]
            self.assertTrue(diagnostic['blocked'])
            self.assertEqual(diagnostic['before'],b'before')
            self.assertEqual(diagnostic['after'],b'after')

    def test_noop_density_can_advance_without_control_specific_rules(self):
        plan=example();plan['density']='compact'
        result=_density_fallback(plan,'Compactá la altura y los espacios verticales')
        self.assertEqual(result['density'],'tight');self.assertEqual(result['nodes'],plan['nodes'])
        fitted=_density_fallback(plan,'Compactá para que quede todo visible sin desplazamiento')
        self.assertEqual(fitted['viewport_fit'],'fit')
        self.assertIsNone(_density_fallback(plan,'Cambiá el título a azul'))

    def test_structured_preview_keeps_catalog_and_original_sources(self):
        with tempfile.TemporaryDirectory() as d:
            project=Path(d)/'project';project.mkdir()
            files=render(example())
            for name,text in files.items():(project/name).write_text(text,encoding='utf8')
            save_owned(project,example())
            before=(project/'MainWindow.xaml').read_bytes()
            api=Mock();api.STATE={'chat_id':'chat'};api.STATE_LOCK=Mock()
            api.STATE_LOCK.__enter__=Mock();api.STATE_LOCK.__exit__=Mock(return_value=False)
            api.post_ollama.return_value={'message':{'content':json.dumps({
                'supported':True,'reason':'','operations':[{'kind':'set_density','value':'tight'}]})}}
            capture={'ok':True,'shot':b'png','exe':'app.exe'}
            with patch('catalog_plan_edit.WpfAdapter') as adapter, patch('code_preview.save_preview') as saved, \
                    patch('catalog_plan_edit.review_candidate',return_value={'approved':True}) as reviewed:
                adapter.return_value.sources.return_value={'MainWindow.xaml':'hash'}
                adapter.return_value.build_capture.return_value=capture
                result=edit_plan(api,project,'Compactá los espacios','model',Path(d)/'evidence')
            reviewed.assert_called_once()
            self.assertIn('Vista previa estructurada',result)
            self.assertEqual((project/'MainWindow.xaml').read_bytes(),before)
            self.assertNotEqual(saved.call_args.args[2]['changed']['MainWindow.xaml'],before)
            self.assertEqual(saved.call_args.args[2]['plan']['density'],'tight')
            self.assertEqual(json.loads((project/'.avc_catalog.json').read_text())['plan'],example())
            sent=api.post_ollama.call_args.args[1]['messages'][0]['content']
            self.assertIn('"density": "comfortable"',sent)

    def test_rejected_partial_candidate_gets_one_reviewed_repair(self):
        with tempfile.TemporaryDirectory() as d:
            project=Path(d)/'project';project.mkdir();folder=Path(d)/'evidence'
            before=example();before['width']=1000;before['height']=560
            before['nodes']=[
                dict(id='layout',parent='root',kind='column',text='',weight=1),
                dict(id='body',parent='layout',kind='row',text='',weight=1),
                dict(id='menu',parent='body',kind='column',text='',weight=1),
                dict(id='option',parent='menu',kind='text',text='Inicio',weight=1),
                dict(id='center',parent='body',kind='column',text='',weight=5),
                dict(id='detail',parent='center',kind='text',text='Contenido',weight=1),
                dict(id='actions',parent='center',kind='row',text='',weight=1),
                dict(id='save',parent='actions',kind='button',text='Guardar',weight=1,action='none'),
            ]
            before['independent_scroll']={'container':'center','fixed_children':['actions']}
            for name,text in render(before).items():(project/name).write_text(text,encoding='utf8')
            save_owned(project,before)
            partial=json.loads(json.dumps(before));next(
                n for n in partial['nodes'] if n['id']=='center')['weight']=6
            repaired=json.loads(json.dumps(partial));repaired.pop('independent_scroll')
            repaired['window_fit']='content_height'
            first={'approved':False,'failed_checks':['no_scroll'],
                   'localai':{'reason':'El desplazamiento continúa','missing_intents':['no_scroll'],
                              'contradictions':[]}}
            second={'approved':True,'failed_checks':[],'localai':{'reason':'Cumple',
                    'missing_intents':[],'contradictions':[]}}
            api=Mock();api.STATE={'chat_id':'chat'};api.STATE_LOCK=Mock()
            api.STATE_LOCK.__enter__=Mock();api.STATE_LOCK.__exit__=Mock(return_value=False)
            capture={'ok':True,'shot':b'png','exe':'app.exe'}
            with patch('catalog_plan_edit.edit_existing',side_effect=[partial,repaired]) as editor, \
                    patch('catalog_plan_edit.review_candidate',side_effect=[first,second]) as reviewer, \
                    patch('catalog_plan_edit.WpfAdapter') as adapter, \
                    patch('code_preview.save_preview') as saved:
                adapter.return_value.sources.return_value={'MainWindow.xaml':'hash'}
                adapter.return_value.build_capture.return_value=capture
                result=edit_plan(api,project,
                    'Ampliá el centro, conservá todo lo demás y que no sea necesario el scroll.',
                    'model',folder)
            self.assertIn('Vista previa estructurada',result)
            self.assertEqual(editor.call_count,2);self.assertEqual(reviewer.call_count,2)
            self.assertEqual(editor.call_args.args[1],partial)
            self.assertIn('reviewer_feedback',editor.call_args.kwargs)
            self.assertEqual(editor.call_args.kwargs['response_name'],'image_edit_response_retry.json')
            self.assertEqual(saved.call_args.args[2]['plan'],repaired)
            self.assertTrue(saved.call_args.args[2]['dual_review']['approved'])

    def test_second_review_rejection_stops_before_build_or_preview(self):
        with tempfile.TemporaryDirectory() as d:
            project=Path(d)/'project';project.mkdir();folder=Path(d)/'evidence'
            before=example();files=render(before)
            for name,text in files.items():(project/name).write_text(text,encoding='utf8')
            save_owned(project,before);original=(project/'MainWindow.xaml').read_bytes()
            candidate=json.loads(json.dumps(before));candidate['density']='tight'
            rejected={'approved':False,'failed_checks':['no_scroll'],'checks':[],
                      'localai':{'reason':'Falta un objetivo','missing_intents':['no_scroll'],
                                 'contradictions':[]}}
            api=Mock();api.STATE={'chat_id':'chat'}
            with patch('catalog_plan_edit.edit_existing',side_effect=[candidate,candidate]) as editor, \
                    patch('catalog_plan_edit.review_candidate',side_effect=[rejected,rejected]) as reviewer, \
                    patch('catalog_plan_edit.WpfAdapter') as adapter, \
                    patch('code_preview.save_preview') as saved:
                with self.assertRaisesRegex(ValueError,'después del único reintento'):
                    edit_plan(api,project,'Mostrá todo sin scroll','model',folder)
            self.assertEqual(editor.call_count,2);self.assertEqual(reviewer.call_count,2)
            adapter.return_value.build_capture.assert_not_called();saved.assert_not_called()
            self.assertEqual((project/'MainWindow.xaml').read_bytes(),original)

    def test_apply_keeps_catalog_mode_and_persists_reviewed_plan(self):
        with tempfile.TemporaryDirectory() as d:
            project=Path(d)/'project';project.mkdir();folder=Path(d)/'evidence';folder.mkdir()
            before_plan=example();before_files=render(before_plan)
            for name,text in before_files.items():(project/name).write_text(text,encoding='utf8')
            save_owned(project,before_plan)
            marker_before=(project/'.avc_catalog.json').read_bytes()
            after_plan=dict(before_plan,density='tight',viewport_fit='fit')
            after_files=render(after_plan)
            candidate=dict(model='model',folder=folder,baseline={'MainWindow.xaml':'old'},
                marker_before=marker_before,input_hashes=project_fingerprint(project),plan=after_plan,
                before=b'before',source_bytes={name:(project/name).read_bytes() for name in after_files},
                changed={name:text.encode('utf8') for name,text in after_files.items()
                         if text.encode('utf8')!=(project/name).read_bytes()})
            api=Mock()
            with patch('catalog_plan_edit.WpfAdapter') as adapter:
                adapter.return_value.sources.return_value=candidate['baseline']
                adapter.return_value.build_capture.return_value={'ok':True,'shot':b'after','exe':'app.exe'}
                result=apply_catalog_candidate(api,project,candidate)
            marker=json.loads((project/'.avc_catalog.json').read_text())
            self.assertIn('aplicada y publicada',result)
            self.assertEqual(marker['plan']['density'],'tight')
            self.assertEqual(marker['plan']['viewport_fit'],'fit')
            self.assertNotIn('mode',marker)
            self.assertIn('<Viewbox', (project/'MainWindow.xaml').read_text())


if __name__=='__main__':unittest.main()

import base64
import copy
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import server
from visual_blueprint import (parse_blueprint, parse_comparison, describe_blueprint,
                              BLUEPRINT_PROMPT, WPF_CONTRACT, wpf_layout_requirements,
                              inspect_wpf_blueprint_layout)
from test_edit_inspection import png


def login_plan():
    return dict(canvas=dict(bounds=[0,0,1,.94],background='degradado cian/magenta/azul'),nodes=[
        dict(id='card',kind='container',parent=None,bounds=[.2,.14,.8,.72],fill='blanco translúcido'),
        dict(id='title',kind='text',parent='card',bounds=[.4,.22,.6,.29],label='Sign In',foreground='gris'),
        dict(id='username',kind='input',parent='card',bounds=[.28,.30,.72,.39],label='Username'),
        dict(id='password',kind='password',parent='card',bounds=[.28,.43,.72,.52],label='Password'),
        dict(id='login',kind='button',parent='card',bounds=[.28,.55,.72,.63],label='LOGIN')],
        rows=[],uncertainties=['La opacidad es aproximada'])


class BlueprintTests(unittest.TestCase):
    def parse(self,doc):
        return parse_blueprint(json.dumps(doc),b'image',(1500,1600))

    def test_login_preserves_hierarchy_appearance_and_uncertainty(self):
        plan=self.parse(login_plan())
        self.assertEqual(plan['nodes'][0]['fill'],'blanco translúcido')
        self.assertEqual(plan['nodes'][2]['parent'],'card')
        self.assertEqual(plan['status'],'orientativo')
        self.assertTrue(plan['uncertainties'])
        self.assertIn('no mediciones UIA',describe_blueprint(plan))
        self.assertEqual(plan['image_size'],[1500,1600])

    def test_invalid_coordinates_are_not_forwarded(self):
        for bounds in ([.9,0,.5,.5],[0,0,float('nan'),.5],[True,0,.4,.5],[0,-.1,.5,.5]):
            with self.subTest(bounds=bounds):
                plan=login_plan();plan['nodes'][1]['bounds']=bounds
                result=self.parse(plan)
                self.assertIsNone(result['nodes'][1]['bounds'])
                self.assertTrue(any('descartadas' in s for s in result['warnings']))

    def test_missing_position_remains_unknown(self):
        plan=login_plan();plan['nodes'][1]['bounds']=None
        result=self.parse(plan)
        self.assertIsNone(result['nodes'][1]['bounds'])
        self.assertTrue(result['warnings'])

    def test_parent_and_cycle_must_be_valid(self):
        for parent in ('missing','title','card'):
            plan=login_plan();plan['nodes'][0]['parent']=parent
            with self.subTest(parent=parent),self.assertRaises(ValueError):self.parse(plan)

    def test_duplicate_ids_rejected(self):
        plan=login_plan();plan['nodes'][1]['id']='card'
        with self.assertRaises(ValueError):self.parse(plan)

    def test_transposed_grid_rejected_before_editing(self):
        plan=login_plan();plan['rows']=[['username','password','login']]
        with self.assertRaisesRegex(ValueError,'columna transpuesta'):self.parse(plan)

    def test_reversed_row_and_repeated_element_rejected(self):
        plan=login_plan()
        plan['nodes'][3]['bounds']=[.55,.30,.70,.39]
        for rows in ([['password','username']],[['username'],['username']],[['missing']]):
            plan['rows']=rows
            with self.subTest(rows=rows),self.assertRaises(ValueError):self.parse(plan)

    def test_child_outside_parent_has_specific_warning(self):
        plan=login_plan();plan['nodes'][2]['bounds']=[.81,.3,.99,.39]
        result=self.parse(plan)
        self.assertTrue(any('username' in s and 'fuera' in s for s in result['warnings']))
        self.assertIsNone(result['nodes'][2]['parent'])

    def test_embedded_field_icon_must_be_an_independent_visual_node(self):
        plan=login_plan()
        plan['nodes'][2]['detail']='campo con icono de usuario a la izquierda'
        with self.assertRaisesRegex(ValueError,'no existe un nodo icon'):
            self.parse(plan)
        plan['nodes'].append(dict(id='username_icon',kind='icon',parent='card',
            bounds=[.29,.32,.33,.37],label='usuario'))
        result=self.parse(plan)
        self.assertIn('username_icon',{node['id'] for node in result['nodes']})

    def test_wpf_contract_covers_actual_login_failures(self):
        for item in ('PlaceholderText','PasswordBox.Password','PNG inexistentes','Background','Ellipse',
                     'leyendas','sin recorte','icono dentro de un campo',
                     'Border con Grid de dos columnas'):
            self.assertIn(item,WPF_CONTRACT)
        self.assertIn('Cada icono visible es un nodo independiente',BLUEPRINT_PROMPT)
        self.assertIn('No escondas iconos en detail',BLUEPRINT_PROMPT)
        self.assertIn('Stretch="Uniform"',WPF_CONTRACT)
        self.assertIn('cuerpo cerrado',WPF_CONTRACT)

    def test_wpf_guard_detects_clipped_circle_and_crowded_footer(self):
        folder=Path(self.enterContext(tempfile.TemporaryDirectory()))
        xaml=folder/'MainWindow.xaml'
        xaml.write_text('''<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation" Width="560" Height="600">
          <Border Height="400"><Grid><Grid.RowDefinitions>
            <RowDefinition Height="*"/><RowDefinition Height="*"/><RowDefinition Height="*"/>
            <RowDefinition Height="*"/><RowDefinition Height="*"/>
          </Grid.RowDefinitions>
          <Grid Grid.Row="0"><Ellipse Width="100" Height="100"/></Grid>
          <TextBlock Grid.Row="1"/><StackPanel Grid.Row="2"><TextBox/><PasswordBox/></StackPanel>
          <Button Grid.Row="3"/><StackPanel Grid.Row="4"><CheckBox/><TextBlock/><TextBlock/></StackPanel>
          </Grid></Border></Window>''')
        issues=inspect_wpf_blueprint_layout(xaml,self.parse(login_plan()))
        self.assertTrue(any('contornos cerrados' in issue for issue in issues),issues)
        self.assertTrue(any('leyendas' in issue for issue in issues),issues)
        xaml.write_text(xaml.read_text().replace('Height="400"','Height="600"').replace(
            '<StackPanel Grid.Row="4"><CheckBox/><TextBlock/><TextBlock/></StackPanel>',
            '<TextBlock Grid.Row="4"/>'))
        self.assertEqual(inspect_wpf_blueprint_layout(xaml,self.parse(login_plan())),[])

    def test_wpf_guard_detects_content_assigned_to_undefined_row(self):
        folder=Path(self.enterContext(tempfile.TemporaryDirectory()))
        xaml=folder/'MainWindow.xaml'
        xaml.write_text('''<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation" Width="560" Height="600">
        <Grid><Grid.RowDefinitions><RowDefinition Height="Auto"/><RowDefinition Height="Auto"/></Grid.RowDefinitions>
        <TextBlock Grid.Row="2" Text="Pie invisible"/></Grid></Window>''',encoding='utf8')
        issues=inspect_wpf_blueprint_layout(xaml,self.parse(login_plan()))
        self.assertTrue(any('Grid.Row=2' in issue and 'solo define 2 filas' in issue for issue in issues))

    def test_wpf_guard_requires_icon_and_input_inside_same_field_border(self):
        plan=login_plan()
        plan['nodes'].extend([
            dict(id='user_group',kind='container',parent='card',bounds=[.27,.30,.73,.39]),
            dict(id='user_icon',kind='icon',parent='user_group',bounds=[.29,.32,.34,.37]),
        ])
        plan['nodes'][2]['parent']='user_group'
        parsed=self.parse(plan)
        folder=Path(self.enterContext(tempfile.TemporaryDirectory()))
        xaml=folder/'MainWindow.xaml'
        xaml.write_text('''<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation" Width="750" Height="800">
        <StackPanel Orientation="Horizontal"><Path/><TextBox/></StackPanel></Window>''',encoding='utf8')
        issues=inspect_wpf_blueprint_layout(xaml,parsed)
        self.assertTrue(any('campos compuestos' in issue and 'solo representa 0' in issue for issue in issues))
        xaml.write_text('''<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation" Width="750" Height="800">
        <Border><Grid><Path/><TextBox/></Grid></Border></Window>''',encoding='utf8')
        self.assertEqual(inspect_wpf_blueprint_layout(xaml,parsed),[])

    def test_wpf_guard_rejects_unscaled_path_and_empty_composite_circle(self):
        plan=login_plan()
        plan['nodes'].append(dict(id='avatar',kind='icon',parent='card',bounds=[.44,.08,.56,.18],
            shape='círculo',detail='silueta de perfil dentro del círculo'))
        parsed=self.parse(plan)
        folder=Path(self.enterContext(tempfile.TemporaryDirectory()))
        xaml=folder/'MainWindow.xaml'
        xaml.write_text('''<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation" Width="750" Height="800">
        <Grid><Grid><Ellipse Width="80" Height="80"/></Grid><Path Width="16" Height="16" Data="M10,10 L20,20"/></Grid></Window>''',encoding='utf8')
        issues=inspect_wpf_blueprint_layout(xaml,parsed)
        self.assertTrue(any('sin Stretch' in issue for issue in issues))
        self.assertTrue(any('Ellipse vacía' in issue for issue in issues))
        xaml.write_text('''<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation" Width="750" Height="800">
        <Grid><Ellipse Width="80" Height="80"/><Viewbox><Path Stretch="Uniform" Data="M10,10 L20,20"/></Viewbox></Grid></Window>''',encoding='utf8')
        self.assertEqual(inspect_wpf_blueprint_layout(xaml,parsed),[])

    def test_wpf_requirements_make_global_aspect_and_container_occupancy_explicit(self):
        plan=self.parse(login_plan())
        text=wpf_layout_requirements(plan)
        self.assertIn('reproducción global',text)
        self.assertIn('Width/Height',text)
        self.assertIn('card: ancho ~60% y alto ~58%',text)
        self.assertIn('imagen completa',text)

    def test_wpf_guard_detects_horizontal_window_for_vertical_reference(self):
        folder=Path(self.enterContext(tempfile.TemporaryDirectory()))
        xaml=folder/'MainWindow.xaml'
        xaml.write_text('<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation" Width="800" Height="450"><Grid/></Window>')
        issues=inspect_wpf_blueprint_layout(xaml,self.parse(login_plan()))
        self.assertEqual(len(issues),1)
        self.assertIn('1.778',issues[0])
        xaml.write_text('<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation" Width="750" Height="800"><Grid/></Window>')
        self.assertEqual(inspect_wpf_blueprint_layout(xaml,self.parse(login_plan())),[])

    def test_wpf_guard_abstains_without_numeric_window_dimensions(self):
        folder=Path(self.enterContext(tempfile.TemporaryDirectory()))
        xaml=folder/'MainWindow.xaml'
        xaml.write_text('<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation" SizeToContent="WidthAndHeight"><Grid/></Window>')
        self.assertEqual(inspect_wpf_blueprint_layout(xaml,self.parse(login_plan())),[])
        xaml.write_text('<!DOCTYPE Window [<!ENTITY xxe SYSTEM "file:///secret">]><Window Width="800" Height="450">&xxe;</Window>')
        self.assertEqual(inspect_wpf_blueprint_layout(xaml,self.parse(login_plan())),[])

    def test_wpf_guard_detects_all_sections_compressed_in_one_grid_column(self):
        folder=Path(self.enterContext(tempfile.TemporaryDirectory()))
        xaml=folder/'MainWindow.xaml'
        xaml.write_text('''<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation" Width="750" Height="800">
          <Grid><Grid.ColumnDefinitions><ColumnDefinition/><ColumnDefinition/></Grid.ColumnDefinitions>
          <TextBlock Grid.Column="1"/><TextBox Grid.Column="1"/><PasswordBox Grid.Column="1"/>
          <Button Grid.Column="1"/><CheckBox Grid.Column="1"/></Grid></Window>''')
        issues=inspect_wpf_blueprint_layout(xaml,self.parse(login_plan()))
        self.assertEqual(len(issues),1)
        self.assertIn('solo en la columna 1',issues[0])
        xaml.write_text(xaml.read_text().replace(
            '<PasswordBox Grid.Column="1"/>','<PasswordBox Grid.Column="0"/>'))
        self.assertEqual(inspect_wpf_blueprint_layout(xaml,self.parse(login_plan())),[])


class ExtractionTests(unittest.TestCase):
    def setUp(self):
        self.folder=Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(server,'audit'))
        self.enterContext(patch.object(server,'log'))
        self.image=base64.b64encode(png()).decode()

    def test_extraction_and_review_persist_same_image_identity(self):
        first=login_plan();revised=copy.deepcopy(first);revised['nodes'][1]['foreground']='gris oscuro'
        with patch.object(server,'ask_vision',side_effect=[json.dumps(first),json.dumps(revised)]) as vision:
            result=server.visual_specification(self.image,'vision',self.folder)
        self.assertEqual(vision.call_count,2)
        saved=json.loads((self.folder/'.avc_visual_spec.json').read_text(encoding='utf8'))
        self.assertEqual(saved['schema'],3)
        self.assertEqual(saved['nodes'][1]['foreground'],'gris oscuro')
        self.assertIn(saved['image_sha256'],result)
        from visual_schemas import SCENE
        self.assertEqual(vision.call_args.kwargs['structured'],SCENE)

    def test_incoherent_extraction_does_not_overwrite_previous_reference(self):
        target=self.folder/'.avc_visual_spec.json';target.write_text('old')
        with patch.object(server,'ask_vision',return_value='{"nodes":[]}') as vision:
            with self.assertRaisesRegex(ValueError,'no se envía al editor'):
                server.visual_specification(self.image,'vision',self.folder)
        self.assertEqual(target.read_text(),'old')
        self.assertEqual(vision.call_count,2)

    def test_failed_review_is_explicit_and_keeps_valid_first_description(self):
        with patch.object(server,'ask_vision',side_effect=[json.dumps(login_plan()),'']):
            server.visual_specification(self.image,'vision',self.folder)
        saved=json.loads((self.folder/'.avc_visual_spec.json').read_text(encoding='utf8'))
        self.assertTrue(any('inconclusa' in s for s in saved['warnings']))

    def test_declared_aspect_contradiction_gets_one_internal_repair(self):
        xaml=self.folder/'MainWindow.xaml'
        xaml.write_text('<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation" Width="800" Height="450"><Grid/></Window>')

        def repair(*_args, **_kwargs):
            xaml.write_text('<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation" Width="750" Height="800"><Grid/></Window>')
            return 0,'edición aplicada'

        with patch.object(server,'aider_command',return_value=['aider']), \
             patch.object(server,'run_process',side_effect=repair) as run, \
             patch.object(server,'record_aider_report'), \
             patch.object(server,'source_snapshot',side_effect=[{'before':'1'},{'after':'2'}]):
            remaining=server.repair_wpf_blueprint_layout(
                self.folder,parse_blueprint(json.dumps(login_plan()),b'image',(1500,1600)),
                'pedido completo','code',1)
        self.assertEqual(remaining,[])
        self.assertEqual(run.call_count,1)
        self.assertEqual(run.call_args.kwargs['timeout'],server.AIDER_PROCESS_TIMEOUT)

    def test_preflight_turns_current_xaml_contradictions_into_editor_feedback(self):
        xaml=self.folder/'MainWindow.xaml'
        xaml.write_text('''<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation" Width="560" Height="600">
        <Border Height="400"><Grid><Grid.RowDefinitions>
        <RowDefinition Height="*"/><RowDefinition Height="*"/><RowDefinition Height="*"/>
        <RowDefinition Height="*"/><RowDefinition Height="*"/></Grid.RowDefinitions>
        <Grid Grid.Row="0"><Ellipse Width="100" Height="100"/></Grid>
        <StackPanel Grid.Row="4"><TextBlock/><TextBlock/><TextBlock/></StackPanel>
        </Grid></Border></Window>''',encoding='utf8')
        plan=parse_blueprint(json.dumps(login_plan()),b'image',(1500,1600))
        issues,prompt=server.wpf_blueprint_preflight(self.folder,plan)
        self.assertEqual(len(issues),2)
        self.assertIn('elemento de 100px',prompt)
        self.assertIn('3 elementos verticales',prompt)
        self.assertIn('No copies el mismo XAML',prompt)


class AdvisoryComparisonTests(unittest.TestCase):
    def test_image_aspect_warning_is_measured_independently_from_model(self):
        raw=json.dumps(dict(summary='Diseño similar',differences=[],uncertainties=[]))
        with patch.object(server,'ask_vision',return_value=raw):
            result=server.review_reference(png(size=(100,160)),png(size=(200,100)),'vision')
        self.assertEqual(result['image_geometry']['reference_px'],[100,160])
        self.assertEqual(result['image_geometry']['result_aspect'],2)
        self.assertIn('imágenes completas',result['geometry_warning'])

    def test_structured_response_is_not_cut_inside_json(self):
        text=json.dumps({'description':'x'*5000})
        with patch.object(server,'CANCEL',threading.Event()),patch.object(server,'audit'),patch.object(server,'post_ollama',return_value={'message':{'content':text}}):
            result=server.ask_vision('vision',['image'],'describe','test','json',structured=True)
        self.assertEqual(json.loads(result),json.loads(text))

    def test_oversized_text_and_json_are_rejected_without_silent_truncation(self):
        with patch.object(server,'CANCEL',threading.Event()),patch.object(server,'audit'),patch.object(server,'post_ollama',return_value={'message':{'content':'x'*80000}}):
            self.assertEqual(server.ask_vision('vision',['image'],'describe','test','text'),'')
            self.assertEqual(server.ask_vision('vision',['image'],'describe','test','json',structured=True),'')

    def test_separate_images_reference_first_and_no_false_approval(self):
        raw=dict(summary='Falta el panel',differences=[dict(aspect='contenedor',reference='Panel translúcido',
            actual='Sin panel',priority='alta',suggestion='Agregar el panel')],uncertainties=[])
        with patch.object(server,'ask_vision',return_value=json.dumps(raw)) as vision:
            result=server.review_reference(b'reference',b'actual','vision')
        images=vision.call_args.args[1]
        self.assertEqual([base64.b64decode(i) for i in images],[b'reference',b'actual'])
        self.assertEqual(result['status'],'orientativo')
        self.assertTrue(result['human_review'])
        self.assertNotIn('score',result)
        self.assertNotEqual(result['reference_sha256'],result['result_sha256'])

    def test_empty_or_bad_comparison_does_not_claim_similarity(self):
        for value in ('','{}','{"summary":"igual","differences":[],"uncertainties":""}'):
            with self.subTest(value=value),patch.object(server,'ask_vision',return_value=value):
                result=server.review_reference(b'ref',b'actual','vision')
                self.assertEqual(result['status'],'no_verificable')
                self.assertTrue(result['uncertainties'])

    def test_differences_require_evidence_and_suggestion(self):
        with self.assertRaises(ValueError):
            parse_comparison(json.dumps(dict(summary='mal',differences=[dict(priority='alta')],uncertainties=[])))


if __name__=='__main__':unittest.main()

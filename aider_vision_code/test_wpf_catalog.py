import copy
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
from wpf_catalog import MAX_NODES, creation_eligible, creation_request, normalize, validate, render, eligible, read_owned, save_owned


def example():
    return dict(supported=True,limitations=[],title='Prueba',width=500,height=650,
        background=['#214ECC','#982FBB'],nodes=[
            dict(id='card',parent='root',kind='card',text=''),
            dict(id='avatar',parent='card',kind='avatar',text='',icon='user'),
            dict(id='user',parent='card',kind='field',text='Usuario',icon='user',required=True),
            dict(id='secret',parent='card',kind='password',text='Clave',icon='lock',required=True),
            dict(id='row',parent='card',kind='row',text=''),
            dict(id='remember',parent='row',kind='checkbox',text='Recordarme'),
            dict(id='help',parent='row',kind='link',text='Ayuda'),
            dict(id='submit',parent='card',kind='button',text='Enviar',action='validate_required')])


class CatalogTests(unittest.TestCase):
    def test_full_screen_request_is_routed_without_project_specific_words(self):
        self.assertTrue(creation_request('Creá una interfaz de escritorio parecida a la descripción'))
        self.assertTrue(creation_request('Diseñar un tablero horizontal con menú lateral'))
        self.assertTrue(creation_request('Quiero una pantalla de escritorio con dos paneles'))
        self.assertTrue(creation_request('Crear una primera aproximación visual de una aplicación de escritorio horizontal'))
        self.assertTrue(creation_request('Construye y compila una aplicación de escritorio con menú'))
        self.assertFalse(creation_request('Explicame qué contiene esta pantalla'))
        self.assertFalse(creation_request('Cambiá el archivo existente'))

    def test_weighted_nested_layout_is_deterministic(self):
        plan=example();plan['width']=1000;plan['height']=560
        plan['nodes']=[
            dict(id='n1',parent='root',kind='column',text='',background='#EFF4F9',weight=1),
            dict(id='n2',parent='n1',kind='row',text='',background='#102D4D',weight=1),
            dict(id='n3',parent='n2',kind='title',text='SISBÁSCULA',foreground='#FFFFFF',weight=3),
            dict(id='n4',parent='n2',kind='text',text='Usuario: operador',foreground='#FFFFFF',weight=1),
            dict(id='n5',parent='n1',kind='row',text='',weight=1),
            dict(id='n6',parent='n5',kind='column',text='',background='#102D4D',weight=1),
            dict(id='n7',parent='n6',kind='text',text='Configuración',foreground='#FFFFFF',weight=1),
            dict(id='n8',parent='n5',kind='column',text='',background='#EFF4F9',weight=4),
            dict(id='n9',parent='n8',kind='title',text='Puerto y prueba de báscula',foreground='#102D4D',weight=1),
            dict(id='n10',parent='n8',kind='card',text='',background='#FFFFFF',weight=1),
            dict(id='n11',parent='n10',kind='row',text='',weight=1),
            dict(id='n12',parent='n11',kind='field',text='Puerto COM',foreground='#102D4D',background='#FFFFFF',weight=2),
            dict(id='n13',parent='n11',kind='field',text='Velocidad',foreground='#102D4D',background='#FFFFFF',weight=1),
        ]
        xaml=render(plan)['MainWindow.xaml'];ET.fromstring(xaml)
        self.assertIn('<ColumnDefinition Width="1*"',xaml)
        self.assertIn('<ColumnDefinition Width="4*"',xaml)
        self.assertIn('Background="#102D4D" Padding="4"',xaml)
        self.assertIn('Puerto y prueba de báscula',xaml)
        self.assertIn('VerticalAlignment="Top"',xaml)

    def test_catalog_accepts_screen_sized_component_count(self):
        plan=example()
        for i in range(len(plan['nodes'])+1,MAX_NODES+1):
            plan['nodes'].append(dict(id='x'+str(i),parent='card',kind='text',text=str(i)))
        self.assertEqual(len(validate(plan)['nodes']),MAX_NODES)
        plan['nodes'].append(dict(id='overflow',parent='card',kind='text',text='x'))
        with self.assertRaises(ValueError):validate(plan)

    def test_compound_fields_and_validation_are_emitted_without_resources(self):
        files=render(example());x=files['MainWindow.xaml'];cs=files['MainWindow.xaml.cs']
        ET.fromstring(x)
        self.assertNotIn('PlaceholderText',x);self.assertNotIn('Source=',x)
        self.assertIn('C_secret.Password',cs);self.assertIn('C_user.Text',cs)
        self.assertEqual(x.count('Grid.Column="1"'),3)
        self.assertIn('no se autenticó',cs)

    def test_untrusted_text_is_literal_not_markup_or_code(self):
        p=example();p['nodes'][2]['text']='{Binding Evil} < & "'
        x=render(p)['MainWindow.xaml'];ET.fromstring(x)
        self.assertIn('{}{Binding Evil}',x);self.assertIn('&lt;',x)
        p['nodes'][2]['id']='x;system()'
        with self.assertRaises(ValueError):render(p)

    def test_invalid_plan_rejected_before_generation(self):
        for mutate in [lambda p:p.update(supported=False),
                       lambda p:p.update(width=30),
                       lambda p:p['nodes'][0].update(parent='missing'),
                       lambda p:p['nodes'][1].update(icon='remote'),
                       lambda p:p['nodes'][2].update(background='light gray'),
                       lambda p:p['nodes'][2].update(action='validate_required')]:
            p=example();mutate(p)
            with self.assertRaises(ValueError):validate(p)

    def test_containers_cannot_hide_visible_content_or_be_empty(self):
        p=example();p['nodes'][0]['text']='Título escondido'
        with self.assertRaisesRegex(ValueError,'contenedores'):validate(p)
        p=example();p['nodes'].append(dict(id='empty',parent='root',kind='row',text=''))
        with self.assertRaisesRegex(ValueError,'vacío'):validate(p)

    def test_weight_supports_rows_and_columns_but_not_cards(self):
        p=example();p['nodes'][0]['weight']=4
        with self.assertRaisesRegex(ValueError,'raíz'):validate(p)
        p=example();p['nodes'][1]['weight']=4
        with self.assertRaisesRegex(ValueError,'row o column'):validate(p)
        p=example();p['nodes'].insert(1,dict(id='nested',parent='card',kind='column',text='',weight=1))
        p['nodes'][2]['parent']='nested';p['nodes'][2]['weight']=3
        self.assertEqual(validate(p)['nodes'][2]['weight'],3)

    def test_vertical_weights_and_visual_only_are_normalized_and_rendered(self):
        p=example();p['height']=560
        p['nodes']=[
            dict(id='rootcol',parent='root',kind='column',text='',weight=12),
            dict(id='header',parent='rootcol',kind='row',text='',weight=1),
            dict(id='brand',parent='header',kind='title',text='SISBÁSCULA',weight=1),
            dict(id='body',parent='rootcol',kind='column',text='',weight=11),
            dict(id='go',parent='body',kind='button',text='Iniciar',weight=1,action='validate_required'),
        ]
        p=normalize(p,visual_only=True)
        self.assertEqual(p['nodes'][0]['weight'],1)
        self.assertEqual(p['nodes'][-1]['action'],'none')
        xaml=render(p)['MainWindow.xaml']
        ET.fromstring(xaml)
        self.assertIn('<RowDefinition Height="1*"',xaml)
        self.assertIn('<RowDefinition Height="11*"',xaml)
        self.assertNotIn('Click="On_go"',xaml)

    def test_tight_density_changes_spacing_not_components_or_code(self):
        compact=example();compact['density']='compact'
        tight=copy.deepcopy(compact);tight['density']='tight'
        compact_files=render(compact);tight_files=render(tight)
        self.assertEqual(compact['nodes'],tight['nodes'])
        self.assertEqual(compact_files['MainWindow.xaml.cs'],tight_files['MainWindow.xaml.cs'])
        self.assertIn('Padding="6"',tight_files['MainWindow.xaml'])
        self.assertIn('Margin="0,2"',tight_files['MainWindow.xaml'])
        invalid=copy.deepcopy(compact);invalid['density']='tiny'
        with self.assertRaisesRegex(ValueError,'Densidad'):validate(invalid)

    def test_viewport_fit_uses_proportional_host_without_scroll(self):
        plan=example();plan['viewport_fit']='fit'
        xaml=render(plan)['MainWindow.xaml'];ET.fromstring(xaml)
        self.assertIn('<Viewbox Stretch="Uniform" StretchDirection="DownOnly"',xaml)
        self.assertNotIn('<ScrollViewer',xaml)
        invalid=copy.deepcopy(plan);invalid['viewport_fit']='clip'
        with self.assertRaisesRegex(ValueError,'área visible'):validate(invalid)

    def test_independent_scroll_keeps_final_actions_outside_scroll_region(self):
        plan=example();plan['width']=1000;plan['height']=560
        plan['nodes']=[
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
        plan['independent_scroll']={'container':'center','fixed_children':['actions']}
        xaml=render(plan)['MainWindow.xaml'];ET.fromstring(xaml)
        self.assertEqual(xaml.count('<ScrollViewer'),1)
        self.assertIn('<ScrollViewer Grid.Row="0"',xaml)
        scroll_end=xaml.index('</ScrollViewer>')
        self.assertLess(xaml.index('Contenido'),scroll_end)
        self.assertGreater(xaml.index('Guardar'),scroll_end)
        invalid=copy.deepcopy(plan);invalid['independent_scroll']['fixed_children']=['detail']
        with self.assertRaisesRegex(ValueError,'tramo final'):validate(invalid)

    def test_content_height_uses_natural_vertical_measurement(self):
        plan=example();plan['window_fit']='content_height'
        plan['nodes']=[
            dict(id='rootcol',parent='root',kind='column',text='',weight=1),
            dict(id='header',parent='rootcol',kind='row',text='',weight=1),
            dict(id='brand',parent='header',kind='title',text='Marca',weight=1),
            dict(id='body',parent='rootcol',kind='column',text='',weight=11),
            dict(id='detail',parent='body',kind='text',text='Contenido completo',weight=1),
        ]
        xaml=render(plan)['MainWindow.xaml'];ET.fromstring(xaml)
        self.assertIn('SizeToContent="Height"',xaml)
        self.assertNotIn('<RowDefinition Height="11*"',xaml)
        self.assertNotIn('<ScrollViewer',xaml)
        self.assertIn('Contenido completo',xaml)

    def test_only_new_or_owned_projects_and_external_edits_protected(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);self.assertTrue(eligible(root))
            (root/'MainWindow.xaml').write_text('old')
            self.assertFalse(eligible(root));save_owned(root,example())
            self.assertTrue(eligible(root));self.assertEqual(read_owned(root),example())
            (root/'MainWindow.xaml').write_text('manual edit')
            with self.assertRaisesRegex(ValueError,'fuera del catálogo'):read_owned(root)

    def test_failed_avc_blank_scaffold_can_be_reused_but_user_sources_cannot(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            (root/'Aplicacion.csproj').write_text('<Project><PropertyGroup><UseWPF>true</UseWPF></PropertyGroup></Project>')
            (root/'MainWindow.xaml').write_text('<Window Title="MainWindow" Height="450" Width="800"><Grid>\n</Grid></Window>')
            (root/'MainWindow.xaml.cs').write_text('public partial class MainWindow { MainWindow(){ InitializeComponent(); } }')
            self.assertTrue(creation_eligible(root))
            (root/'Custom.cs').write_text('class Custom {}')
            self.assertFalse(creation_eligible(root))

    def test_dispatch_and_read_only_route_remain_separate(self):
        import server
        with tempfile.TemporaryDirectory() as d, patch('catalog_pipeline.run_catalog_job') as catalog, patch.object(server,'run_legacy_job') as legacy:
            root=Path(d)
            server.run_job(root,'Creá un formulario','','1','model',build_mode='wpf')
            catalog.assert_called_once();legacy.assert_not_called()
            catalog.reset_mock()
            server.run_job(root,'Solo analizá el formulario','','1','model',build_mode='wpf')
            catalog.assert_not_called();legacy.assert_called_once()

    def test_explicit_new_screen_uses_catalog_without_advanced_build_option(self):
        import server
        with tempfile.TemporaryDirectory() as d, patch('catalog_pipeline.run_catalog_job') as catalog, patch.object(server,'run_legacy_job') as legacy:
            server.run_job(Path(d),'Quiero crear una pantalla horizontal con menú lateral','','1','model',build_mode='off')
            catalog.assert_called_once();legacy.assert_not_called()

    def test_catalog_preview_ignores_stale_quality_and_uia_options(self):
        import server
        with tempfile.TemporaryDirectory() as d, patch('catalog_pipeline.run_catalog_job') as catalog, patch.object(server,'run_legacy_job') as legacy:
            root=Path(d)
            (root/'App.csproj').write_text('<Project><PropertyGroup><UseWPF>true</UseWPF></PropertyGroup></Project>')
            (root/'.avc_catalog.json').write_text('{}')
            with patch('wpf_catalog.eligible',return_value=True):
                server.run_job(root,'Alineá el título a la derecha','old test',3,'model',
                               build_mode='wpf',quality_mode=True,
                               inspection_criteria={'square_bounds':True})
            catalog.assert_called_once();legacy.assert_not_called()

    def test_stale_saved_reference_cannot_hijack_explicit_text_creation(self):
        import server
        with tempfile.TemporaryDirectory() as d, patch('catalog_pipeline.run_catalog_job') as catalog, patch('visual_pipeline.run_reference_job') as reference:
            base=Path(d);project=base/'project';project.mkdir();store=base/'runtime'/'recovery'/'visual_projects'
            from visual_reference import ReferenceStore
            active=ReferenceStore(store,project).active;active.parent.mkdir(parents=True,exist_ok=True);active.write_text('{}')
            with patch.object(server,'ROOT',base/'runtime'):
                server.run_job(project,'Crear una aplicación de escritorio con menú','','1','model',build_mode='wpf')
            catalog.assert_called_once();reference.assert_not_called()

    def test_new_chat_does_not_activate_saved_reference_without_request(self):
        import server
        with tempfile.TemporaryDirectory() as d, patch('visual_pipeline.run_reference_job') as reference, patch.object(server,'run_legacy_job') as legacy:
            base=Path(d);project=base/'project';project.mkdir()
            from visual_reference import ReferenceStore
            active=ReferenceStore(base/'runtime'/'recovery'/'visual_projects',project).active
            active.parent.mkdir(parents=True,exist_ok=True);active.write_text('{}')
            with patch.object(server,'ROOT',base/'runtime'):
                server.run_job(project,'Corregí el color del fondo','','1','model',build_mode='wpf')
            reference.assert_not_called();legacy.assert_called_once()

    def test_truncated_plan_never_scaffolds_or_writes(self):
        import server
        from catalog_pipeline import run_catalog_job
        with tempfile.TemporaryDirectory() as d, patch.object(server,'ROOT',Path(d)), patch.object(server,'LOCK',threading.Lock()), patch.object(server,'post_ollama',return_value={'done_reason':'length'}), patch.object(server,'ensure_wpf_project') as scaffold, patch.object(server,'record_job'), patch.object(server,'audit'), patch.object(server,'log'), patch.dict(server.STATE,started=time.time(),clear=False):
            project=Path(d)/'project';project.mkdir();server.LOCK.acquire()
            run_catalog_job(server,project,'Crear formulario','model')
            scaffold.assert_not_called();self.assertEqual(list(project.iterdir()),[])
            self.assertFalse(server.LOCK.locked());self.assertEqual(server.STATE['phase'],'error')

    def test_failed_build_restores_sources_and_does_not_claim_completion(self):
        import server
        from catalog_pipeline import run_catalog_job
        with tempfile.TemporaryDirectory() as d, patch.object(server,'ROOT',Path(d)), patch.object(server,'LOCK',threading.Lock()), patch.object(server,'post_ollama',return_value={'message':{'content':json.dumps(example())}}), patch.object(server,'record_job'), patch.object(server,'audit'), patch.object(server,'log'), patch('catalog_pipeline.WpfAdapter') as adapter, patch.dict(server.STATE,started=time.time(),clear=False):
            project=Path(d)/'project';project.mkdir()
            def scaffold(p):
                (p/'MainWindow.xaml').write_bytes(b'original')
                (p/'MainWindow.xaml.cs').write_bytes(b'original cs')
                return True,''
            adapter.return_value.build_capture.return_value={'ok':False,'stage':'compilacion','detail':'test failure'}
            with patch.object(server,'ensure_wpf_project',side_effect=scaffold):
                server.LOCK.acquire();run_catalog_job(server,project,'Crear formulario','model')
            self.assertEqual((project/'MainWindow.xaml').read_bytes(),b'original')
            self.assertEqual((project/'MainWindow.xaml.cs').read_bytes(),b'original cs')
            self.assertFalse((project/'.avc_catalog.json').exists())
            self.assertEqual(server.STATE['phase'],'error');self.assertFalse(server.LOCK.locked())


if __name__=='__main__':unittest.main()

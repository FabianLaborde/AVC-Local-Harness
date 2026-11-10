import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch, Mock
from catalog_code_edit import (PatchApplicationError, apply_patches, code_mode, edit_code, style_conflicts,
                               structural_conflicts, compile_diagnostics)
from wpf_catalog import read_owned, save_owned
from test_wpf_catalog import example


class CodeEditTests(unittest.TestCase):
    def test_partial_patch_error_retains_only_unambiguous_edits(self):
        sources={'MainWindow.xaml':'ONE TWO TWO'}
        edits=[dict(file='MainWindow.xaml',search='ONE',replace='DONE'),
               dict(file='MainWindow.xaml',search='TWO',replace='BAD')]
        with self.assertRaises(PatchApplicationError) as caught:
            apply_patches(sources,edits)
        self.assertEqual('DONE TWO TWO',caught.exception.partial['MainWindow.xaml'])
        self.assertEqual('ONE TWO TWO',sources['MainWindow.xaml'])

    def test_explicit_replace_all_changes_repeated_identical_fragments(self):
        sources={'MainWindow.xaml':'WHITE gap WHITE'}
        result=apply_patches(sources,[dict(file='MainWindow.xaml',search='WHITE',
            replace='DARK',replace_all=True)])
        self.assertEqual('DARK gap DARK',result['MainWindow.xaml'])

    def test_independent_scroll_requirement_rejects_window_wrapper(self):
        request=('Agregá desplazamiento vertical solamente en la zona central, '
                 'manteniendo visibles la cabecera y los botones inferiores.')
        wrapped={'MainWindow.xaml':('<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation">'
            '<ScrollViewer><Grid><Border/><StackPanel/><Button/></Grid></ScrollViewer></Window>')}
        self.assertIn('no puede envolver todo',structural_conflicts(wrapped,request)[0])
        valid={'MainWindow.xaml':('<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation">'
            '<Grid><Border/><ScrollViewer><StackPanel/></ScrollViewer><StackPanel><Button/></StackPanel>'
            '</Grid></Window>')}
        self.assertEqual(structural_conflicts(valid,request),[])
        app={'App.xaml':('<Application xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation">'
                         '<Application.Resources/></Application>')}
        self.assertEqual(structural_conflicts(app,request),[])
        self.assertEqual(structural_conflicts(wrapped,'Cambiá el título a azul'),[])

    def test_local_properties_shadow_style_triggers(self):
        for prop in ('Foreground','Background','Opacity'):
            x=f'<Button xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation" {prop}="x"><Button.Style><Style><Style.Triggers><Trigger Property="IsMouseOver" Value="True"><Setter Property="{prop}" Value="y"/></Trigger></Style.Triggers></Style></Button.Style></Button>'
            self.assertTrue(style_conflicts({'MainWindow.xaml':x}))
            self.assertFalse(style_conflicts({'MainWindow.xaml':x.replace(f' {prop}="x"','')}))

    def test_exact_patches_preserve_unrelated_code(self):
        source={'MainWindow.xaml':'before WHITE after','MainWindow.xaml.cs':'handler'}
        result=apply_patches(source,[dict(file='MainWindow.xaml',search='WHITE',replace='STYLE')])
        self.assertEqual(result,{'MainWindow.xaml':'before STYLE after','MainWindow.xaml.cs':'handler'})
        self.assertEqual(source['MainWindow.xaml'],'before WHITE after')

    def test_invalid_patches_never_mutate_input(self):
        src={'MainWindow.xaml':'xx'}
        for edit in [dict(file='../MainWindow.xaml',search='xx',replace='y'),
                     dict(file='MainWindow.xaml',search='x',replace='y'),
                     dict(file='MainWindow.xaml',search='absent',replace='y'),
                     dict(file='MainWindow.xaml',search='',replace='y')]:
            with self.subTest(edit=edit),self.assertRaises(ValueError):apply_patches(src,[edit])
        self.assertEqual(src,{'MainWindow.xaml':'xx'})

    def test_patch_diagnostics_identify_every_ambiguous_or_noop_edit(self):
        src={'MainWindow.xaml':'<Grid/><Grid/><Border/>'}
        edits=[dict(file='MainWindow.xaml',search='<Grid/>',replace='<Grid Margin="1"/>'),
               dict(file='MainWindow.xaml',search='<Border/>',replace='<Border/>')]
        with self.assertRaises(ValueError) as caught:apply_patches(src,edits)
        detail=str(caught.exception)
        self.assertIn('Parche 1 (MainWindow.xaml): search aparece 2 veces',detail)
        self.assertIn('Parche 2 (MainWindow.xaml): no produjo cambios; replace es idéntico',detail)
        self.assertEqual(src,{'MainWindow.xaml':'<Grid/><Grid/><Border/>'})

    def test_code_marker_blocks_regeneration(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);save_owned(p,example())
            marker=p/'.avc_catalog.json';doc=json.loads(marker.read_text());doc['mode']='code';marker.write_text(json.dumps(doc))
            self.assertTrue(code_mode(p))
            with self.assertRaises(ValueError):read_owned(p)
            with self.assertRaises(ValueError):save_owned(p,example())

    def run_candidate(self, valid, repair=False, cancel=False, stage_failure="compilacion", noop=False):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);project=root/'project';project.mkdir();folder=root/'evidence';folder.mkdir()
            source=project/'MainWindow.xaml';source.write_text('<Window>WHITE</Window>')
            save_owned(project,example());old_marker=(project/'.avc_catalog.json').read_bytes()
            api=Mock();api.CANCEL.is_set.return_value=False;api.STATE_LOCK=threading.Lock();api.STATE={}
            api.post_ollama.side_effect=[{'message':{'content':json.dumps({'edits':[dict(file='MainWindow.xaml',search=a,replace=b)]})}} for a,b in [('WHITE','BLACK'),('BLACK','GRAY'),('GRAY','BLUE')]]
            if noop:
                api.post_ollama.side_effect=[{'message':{'content':json.dumps({'edits':[dict(file='MainWindow.xaml',search=a,replace=b)]})}} for a,b in [('WHITE','BLACK'),('BLACK','BLACK'),('BLACK','GRAY')]]
            good=dict(ok=True,shot=b'png',exe='app.exe')
            review=dict(approved=True,initial_satisfies=False,candidate_satisfies=True,
                        reason='cumple',missing_intents=[],contradictions=[])
            with patch('catalog_code_edit.WpfAdapter') as cls, \
                    patch('code_semantic_review.review_code_candidate',return_value=review):
                original=Mock();candidate=Mock();cls.side_effect=[original,candidate,original]
                original.checkpoint.return_value={};original.sources.return_value={};original.build_capture.return_value=good
                failure=dict(ok=False,stage=stage_failure,detail='MC3072 invalid property')
                candidate.build_capture.side_effect=[failure,good] if repair else None
                candidate.build_capture.return_value=good if valid else failure
                if cancel:
                    def cancelled_build():
                        api.CANCEL.is_set.return_value=True
                        return failure
                    candidate.build_capture.side_effect=cancelled_build
                if valid:
                    edit_code(api,project,'Change','model',folder)
                    self.assertTrue(code_mode(project));self.assertEqual(source.read_text(),'<Window>'+('GRAY' if repair else 'BLACK')+'</Window>')
                    if repair:
                        self.assertEqual(api.post_ollama.call_count,3 if noop else 2)
                        prompt=api.post_ollama.call_args.args[1]['messages'][0]['content']
                        self.assertIn('MC3072',prompt)
                        self.assertIn('<Window>BLACK</Window>',prompt)
                else:
                    with self.assertRaisesRegex(ValueError,'Cancelado' if cancel else 'Candidato rechazado'):edit_code(api,project,'Change','model',folder)
                    self.assertEqual(source.read_text(),'<Window>WHITE</Window>');self.assertEqual((project/'.avc_catalog.json').read_bytes(),old_marker)
                    self.assertEqual(api.post_ollama.call_count,1 if cancel or stage_failure!='compilacion' else 3)

    def test_candidate_failure_keeps_original_and_catalog_mode(self):self.run_candidate(False)
    def test_success_transitions_only_after_verification(self):self.run_candidate(True)

    def test_compile_feedback_repairs_current_candidate(self):self.run_candidate(True,repair=True)
    def test_cancel_after_compile_does_not_retry(self):self.run_candidate(False,cancel=True)
    def test_capture_failure_does_not_request_code_repair(self):self.run_candidate(False,stage_failure='captura')

    def test_noop_repair_uses_remaining_budget(self):self.run_candidate(True,repair=True,noop=True)

    def test_already_satisfied_request_never_creates_preview_or_writes(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);project=root/'project';project.mkdir();folder=root/'evidence';folder.mkdir()
            source=project/'MainWindow.xaml'
            source.write_text('<TextBlock Text="Titulo" HorizontalAlignment="Right"/>',encoding='utf8')
            save_owned(project,example())
            api=Mock();api.CANCEL.is_set.return_value=False
            api.STATE_LOCK=threading.Lock();api.STATE={}
            api.post_ollama.return_value={'message':{'content':json.dumps({'edits':[dict(
                file='MainWindow.xaml',search='HorizontalAlignment="Right"',
                replace='HorizontalAlignment="Left"')]})}}
            good=dict(ok=True,shot=b'png',exe='app.exe')
            semantic=dict(approved=False,initial_satisfies=True,candidate_satisfies=False,
                          reason='La base ya estaba a la derecha',missing_intents=[],
                          contradictions=['El candidato mueve el título a la izquierda'])
            with patch('catalog_code_edit.WpfAdapter') as cls, \
                    patch('code_semantic_review.review_code_candidate',return_value=semantic), \
                    patch('code_preview.save_preview') as saved:
                original=Mock();base_copy=Mock();candidate=Mock()
                cls.side_effect=[original,base_copy,candidate]
                original.checkpoint.return_value={};original.sources.return_value={}
                base_copy.build_capture.return_value=good;candidate.build_capture.return_value=good
                result=edit_code(api,project,'Alineá el título a la derecha','model',folder,
                                 preview=True)
            self.assertEqual(result,'El proyecto ya cumple el pedido; no se modificaron archivos.')
            self.assertIn('HorizontalAlignment="Right"',source.read_text())
            saved.assert_not_called()

    def test_deterministic_preflight_skips_checkpoint_build_and_models(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);project=root/'project';project.mkdir();folder=root/'evidence';folder.mkdir()
            source=project/'MainWindow.xaml'
            source.write_text('<Window Background="#121212"><Border Background="#242424">'
                              '<TextBlock Foreground="White"/></Border></Window>',encoding='utf8')
            save_owned(project,example())
            api=Mock();api.CANCEL.is_set.return_value=False
            api.STATE_LOCK=threading.Lock();api.STATE={}
            with patch('catalog_code_edit.WpfAdapter') as adapter, \
                    patch('code_preview.save_preview') as saved:
                result=edit_code(api,project,'Aplicá un tema oscuro coherente','model',folder,
                                 preview=True)
            self.assertEqual(result,'El proyecto ya cumple el pedido; no se modificaron archivos.')
            api.post_ollama.assert_not_called()
            adapter.return_value.checkpoint.assert_not_called()
            adapter.return_value.build_capture.assert_not_called()
            saved.assert_not_called()
            self.assertTrue(api.STATE['verification']['preflight'])
            self.assertFalse(api.STATE['verification']['compilado'])
            shadow=json.loads((folder/'intent_shadow.json').read_text(encoding='utf8'))
            self.assertFalse(shadow['authoritative'])
            self.assertEqual('not_verifiable',shadow['resolution']['status'])

    def test_literal_target_preflight_skips_models_build_and_unneeded_candidate(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);project=root/'project';project.mkdir();folder=root/'evidence';folder.mkdir()
            source=project/'MainWindow.xaml'
            source.write_text('<Grid><TextBlock Text="Datos de contacto" '
                              'HorizontalAlignment="Right"/><Button Content="Enviar" '
                              'HorizontalAlignment="Right"/></Grid>',encoding='utf8')
            save_owned(project,example())
            api=Mock();api.CANCEL.is_set.return_value=False
            api.STATE_LOCK=threading.Lock();api.STATE={}
            with patch('catalog_code_edit.WpfAdapter') as adapter, \
                    patch('code_preview.save_preview') as saved:
                result=edit_code(api,project,
                    'Alineá el título "Datos de contacto" a la derecha. Conservá todo lo demás.',
                    'model',folder,preview=True)
            self.assertEqual(result,'El proyecto ya cumple el pedido; no se modificaron archivos.')
            api.post_ollama.assert_not_called()
            adapter.return_value.checkpoint.assert_not_called()
            adapter.return_value.build_capture.assert_not_called()
            saved.assert_not_called()
            self.assertTrue(api.STATE['verification']['preflight'])
            events=[call.args[0] for call in api.audit.call_args_list]
            self.assertIn('intent_preflight_satisfied',events)

    def test_literal_control_size_preflight_skips_models_build_and_candidate(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);project=root/'project';project.mkdir();folder=root/'evidence';folder.mkdir()
            source=project/'MainWindow.xaml'
            source.write_text('<Grid><Button Content="Enviar" Width="200" Height="50"/>'
                              '<Button Content="Cancelar" Width="200" Height="50"/></Grid>',
                              encoding='utf8')
            save_owned(project,example())
            api=Mock();api.CANCEL.is_set.return_value=False
            api.STATE_LOCK=threading.Lock();api.STATE={}
            with patch('catalog_code_edit.WpfAdapter') as adapter, \
                    patch('code_preview.save_preview') as saved:
                result=edit_code(api,project,
                    'Redimensioná el botón "Enviar" a 200 por 50. Conservá todo lo demás.',
                    'model',folder,preview=True)
            self.assertEqual(result,'El proyecto ya cumple el pedido; no se modificaron archivos.')
            api.post_ollama.assert_not_called()
            adapter.return_value.checkpoint.assert_not_called()
            adapter.return_value.build_capture.assert_not_called()
            saved.assert_not_called()
            self.assertTrue(api.STATE['verification']['preflight'])

    def test_required_literal_alignment_uses_deterministic_preview_without_models(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);project=root/'project';project.mkdir();folder=root/'evidence';folder.mkdir()
            source=project/'MainWindow.xaml'
            source.write_text('<Grid><TextBlock Text="Datos de contacto" '
                              'HorizontalAlignment="Left"/></Grid>',encoding='utf8')
            save_owned(project,example())
            api=Mock();api.CANCEL.is_set.return_value=False
            api.STATE_LOCK=threading.Lock();api.STATE={}
            baseline={'MainWindow.xaml':'hash'}
            good=dict(ok=True,shot=b'png',exe='app.exe')
            with patch('catalog_code_edit.WpfAdapter') as adapter, \
                    patch('code_semantic_review.review_code_candidate') as semantic, \
                    patch('code_preview.save_preview') as saved:
                original=Mock();base_copy=Mock();staged=Mock()
                adapter.side_effect=[original,base_copy,staged]
                original.checkpoint.return_value=baseline
                original.sources.return_value=baseline
                base_copy.build_capture.return_value=good
                staged.build_capture.return_value=good
                result=edit_code(api,project,
                    'Alineá el título "Datos de contacto" a la derecha. Conservá todo lo demás.',
                    'model',folder,preview=True)
            self.assertEqual('Vista previa lista; el proyecto sigue intacto. '
                             'Escribí aplicalo, descartalo o pedí un ajuste.',result)
            self.assertIn('HorizontalAlignment="Left"',source.read_text(encoding='utf8'))
            api.post_ollama.assert_not_called()
            semantic.assert_not_called()
            saved.assert_called_once()
            candidate=saved.call_args.args[2]
            self.assertEqual(['MainWindow.xaml'],list(candidate['changed']))
            self.assertIn(b'HorizontalAlignment="Right"',
                          candidate['changed']['MainWindow.xaml'])
            events=[call.args[0] for call in api.audit.call_args_list]
            self.assertIn('intent_deterministic_patch_planned',events)
            self.assertIn('intent_deterministic_patch_verified',events)

    def test_failed_deterministic_alignment_stops_without_model_or_original_write(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);project=root/'project';project.mkdir();folder=root/'evidence';folder.mkdir()
            source=project/'MainWindow.xaml'
            original_text='<TextBlock Text="Datos" HorizontalAlignment="Left"/>'
            source.write_text(original_text,encoding='utf8')
            save_owned(project,example())
            api=Mock();api.CANCEL.is_set.return_value=False
            api.STATE_LOCK=threading.Lock();api.STATE={}
            baseline={'MainWindow.xaml':'hash'}
            good=dict(ok=True,shot=b'before',exe='base.exe')
            failure=dict(ok=False,stage='compilacion',detail='error MC3072: inválido')
            with patch('catalog_code_edit.WpfAdapter') as adapter, \
                    patch('code_preview.save_preview') as saved:
                current=Mock();base_copy=Mock();staged=Mock()
                adapter.side_effect=[current,base_copy,staged]
                current.checkpoint.return_value=baseline
                current.sources.return_value=baseline
                base_copy.build_capture.return_value=good
                staged.build_capture.return_value=failure
                with self.assertRaisesRegex(ValueError,'Candidato determinista rechazado'):
                    edit_code(api,project,
                        'Alineá el título "Datos" a la derecha.',
                        'model',folder,preview=True)
            self.assertEqual(original_text,source.read_text(encoding='utf8'))
            api.post_ollama.assert_not_called()
            saved.assert_not_called()

    def test_compiler_diagnostics_keep_distinct_errors(self):
        line='C:/project/MainWindow.xaml(3,20): error MC3072: invalid property [C:/project/App.csproj]'
        self.assertEqual(compile_diagnostics(line+'\n'+line), 'error MC3072: invalid property')
        self.assertEqual(compile_diagnostics('compiler unavailable'), 'compiler unavailable')

    def test_followup_uses_code_instead_of_renderer(self):
        import server
        from catalog_pipeline import run_catalog_job
        with tempfile.TemporaryDirectory() as d,patch.object(server,'ROOT',Path(d)),patch.object(server,'LOCK',threading.Lock()),patch.object(server,'audit'),patch.object(server,'log'),patch.object(server,'record_job'),patch.dict(server.STATE,started=time.time()),patch('catalog_code_edit.edit_code',return_value='ok') as edit,patch('catalog_pipeline.render') as render:
            p=Path(d)/'project';p.mkdir();save_owned(p,example())
            marker=p/'.avc_catalog.json';doc=json.loads(marker.read_text());doc['mode']='code';marker.write_text(json.dumps(doc))
            server.LOCK.acquire();run_catalog_job(server,p,'Change title','model')
            edit.assert_called_once();render.assert_not_called();self.assertFalse(server.LOCK.locked())

    def test_structural_catalog_limit_falls_back_to_general_editor(self):
        import server
        from catalog_pipeline import run_catalog_job
        from catalog_plan_edit import StructuredEditUnsupported
        with tempfile.TemporaryDirectory() as d, \
                patch.object(server,'ROOT',Path(d)), \
                patch.object(server,'LOCK',threading.Lock()), \
                patch.object(server,'audit') as audit, \
                patch.object(server,'log'),patch.object(server,'record_job'), \
                patch.dict(server.STATE,started=time.time()), \
                patch('catalog_plan_edit.edit_plan',side_effect=StructuredEditUnsupported(
                    'No se eliminan contenedores ni sus controles en cascada')), \
                patch('catalog_code_edit.edit_code',return_value='Vista previa general lista') as edit:
            project=Path(d)/'project';project.mkdir();save_owned(project,example())
            server.LOCK.acquire()
            run_catalog_job(server,project,'Cabecera fija y centro desplazable','model')
            edit.assert_called_once()
            self.assertEqual(server.STATE['phase'],'review')
            self.assertEqual(server.STATE['result'],'Vista previa general lista')
            audit.assert_any_call('catalog_general_editor_fallback',
                                  reason='No se eliminan contenedores ni sus controles en cascada',
                                  evidence=unittest.mock.ANY)
            self.assertFalse(server.LOCK.locked())

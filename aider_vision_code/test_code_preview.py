import contextvars
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

import code_preview as preview
from catalog_code_edit import edit_code


class PreviewTests(unittest.TestCase):
    def setUp(self):
        preview.clear_previews()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root/'project'
        self.project.mkdir()
        (self.project/'MainWindow.xaml').write_text('<Window Background="White"/>')
        (self.project/'.avc_catalog.json').write_text('{"mode":"code"}')
        (self.project/'logo.png').write_bytes(b'original asset')
        self.api = Mock()
        self.api.CANCEL.is_set.return_value = False
        self.api.STATE = dict(chat_id='chat', job_id=1, started=time.time())
        self.api.STATE_LOCK = threading.RLock()
        self.api.LOCK = threading.Lock()
        self.api.USAGE_CONTEXT = contextvars.ContextVar('preview_test', default=None)
        self.builds = []
        self.fail_final = False
        def adapter(api, project, *args):
            obj = Mock()
            def sources():
                return {p.name:hashlib.sha256(p.read_bytes()).hexdigest()
                        for p in project.glob('*.xaml')}
            obj.sources.side_effect = sources
            obj.checkpoint.side_effect = lambda _:sources()
            def build():
                self.builds.append(project)
                if self.fail_final and project == self.project:
                    return dict(ok=False,stage='captura',detail='failure')
                return dict(ok=True,shot=b'png',exe=str(project/'app.exe'))
            obj.build_capture.side_effect = build
            return obj
        self.addCleanup(patch.stopall)
        patch('catalog_code_edit.WpfAdapter', side_effect=adapter).start()
        patch('code_semantic_review.review_code_candidate',return_value=dict(
            approved=True,initial_satisfies=False,candidate_satisfies=True,
            reason='cumple',missing_intents=[],contradictions=[])).start()

    def propose(self, search='White', replace='Blue', previous=None):
        folder=self.root/('attempt'+str(len(list(self.root.iterdir()))))
        folder.mkdir()
        self.api.post_ollama.return_value={'message':{'content':json.dumps({'edits':[
            dict(file='MainWindow.xaml',search=search,replace=replace)]})}}
        edit_code(self.api,self.project,'Change '+replace,'local',folder,preview=True,previous=previous)
        return preview.get_preview(self.api,self.project)

    def apply(self, candidate, operation='apply', token=None):
        self.api.LOCK.acquire()
        preview.run_preview_action(self.api,self.project,token or candidate['id'],operation)
        self.assertFalse(self.api.LOCK.locked())

    def test_prepare_and_adjust_leave_original_bytes_and_publication_untouched(self):
        baseline=preview.project_fingerprint(self.project)
        first=self.propose()
        second=self.propose('Blue','Green',first)
        self.assertNotEqual(first['id'],second['id'])
        self.assertIn('Green',second['proposed']['MainWindow.xaml'])
        self.assertEqual(preview.project_fingerprint(self.project),baseline)
        self.assertNotIn(self.project,self.builds)
        self.assertIn('Blue',self.api.post_ollama.call_args.args[1]['messages'][0]['content'])

    def test_explicit_apply_uses_reviewed_bytes_without_more_model_calls(self):
        candidate=self.propose()
        calls=self.api.post_ollama.call_count
        self.apply(candidate)
        self.assertIn('Blue',(self.project/'MainWindow.xaml').read_text())
        self.assertEqual(self.api.post_ollama.call_count,calls)
        self.assertIsNone(preview.get_preview(self.api,self.project))
        self.assertEqual(self.api.STATE['phase'],'done')
        self.assertIn('Aplicación completada correctamente',self.api.STATE['result'])
        self.assertEqual(self.api.STATE['verification']['estado'],'no_verificable')
        self.assertIn('no verifica hover ni Click',self.api.STATE['verification']['nota'])

    def test_discard_keeps_project_and_does_not_build(self):
        candidate=self.propose()
        original=preview.project_fingerprint(self.project)
        count=len(self.builds)
        self.apply(candidate,'discard')
        self.assertEqual(original,preview.project_fingerprint(self.project))
        self.assertEqual(count,len(self.builds))
        self.assertIn('próximos pedidos partirán de esa versión', self.api.STATE['result'])
        self.assertIn('sin descartarlo', self.api.STATE['result'])

    def test_external_visual_preview_without_complete_pair_is_rejected(self):
        validation = dict(visual_evidence=dict(requested=True, available=False))
        with self.assertRaisesRegex(ValueError, 'pareja antes/después completa'):
            preview.save_preview(self.api, self.project, dict(
                kind='external', changed={'MainWindow.xaml': b'changed'},
                validation=validation, before=b'before', after=None))
        self.assertIsNone(preview.get_preview(self.api, self.project))

    def test_blocked_diagnostic_is_visible_but_cannot_be_applied(self):
        candidate=self.propose()
        candidate['blocked']=True;candidate['block_reason']='No cabe en el monitor'
        public=preview.public_preview(self.api,self.project)
        self.assertTrue(public['blocked'])
        self.assertEqual(public['block_reason'],'No cabe en el monitor')
        self.apply(candidate)
        self.assertIn('White',(self.project/'MainWindow.xaml').read_text())
        self.assertEqual(self.api.STATE['phase'],'error')
        self.assertIsNotNone(preview.get_preview(self.api,self.project))

    def test_compiled_visual_rejection_can_be_applied_by_explicit_human_choice(self):
        validation = dict(
            passed=True, summary='Compilación correcta', limitation='No verifica eventos.',
            artifacts=[], fulfillment=dict(status='uncertain'),
            visual_evidence=dict(available=True, review=dict(
                status='uncertain', reason='El observador agotó su salida.')))
        preview.save_preview(self.api, self.project, dict(
            kind='external', changed={'MainWindow.xaml': b'<Window Background="Blue"/>'},
            source_bytes={'MainWindow.xaml': b'<Window Background="White"/>'},
            instruction='Cambiá el fondo',
            folder=self.root/'evidence', trial=self.root/'trial', validation=validation,
            before=b'before-png', after=b'after-png', blocked=True,
            block_reason='Cumplimiento visual no verificado.'))
        candidate = preview.get_preview(self.api, self.project)
        public = preview.public_preview(self.api, self.project)
        self.assertFalse(public['blocked'])
        self.assertTrue(public['manual_review'])
        with patch('project_candidate.apply_candidate', return_value='Fuentes aplicados.') as apply:
            self.apply(candidate)
        apply.assert_called_once()
        self.assertEqual(validation['fulfillment']['status'], 'uncertain')
        self.assertIn('bajo revisión visual', self.api.STATE['result'])
        self.assertTrue(any(call.args and call.args[0] == 'code_preview_manual_override'
                            for call in self.api.audit.call_args_list))

    def test_manual_override_is_not_available_without_both_captures(self):
        validation = dict(
            passed=True, summary='Compilación correcta', limitation='Sin captura.',
            artifacts=[], fulfillment=dict(status='uncertain'),
            visual_evidence=dict(available=False, review=dict(
                status='uncertain', reason='Falta captura.')))
        preview.save_preview(self.api, self.project, dict(
            kind='external', changed={'MainWindow.xaml': b'<Window Background="Blue"/>'},
            source_bytes={'MainWindow.xaml': b'<Window Background="White"/>'},
            instruction='Cambiá el fondo',
            folder=self.root/'evidence', trial=self.root/'trial', validation=validation,
            before=b'before-png', after=None, blocked=True,
            block_reason='Falta la captura candidata.'))
        candidate = preview.get_preview(self.api, self.project)
        self.assertTrue(preview.public_preview(self.api, self.project)['blocked'])
        self.apply(candidate)
        self.assertEqual(self.api.STATE['phase'], 'error')
        self.assertIsNotNone(preview.get_preview(self.api, self.project))

    def test_stale_token_does_not_apply_newer_candidate(self):
        first=self.propose()
        second=self.propose('Blue','Green',first)
        self.apply(second,token=first['id'])
        self.assertIn('White',(self.project/'MainWindow.xaml').read_text())
        self.assertEqual(preview.get_preview(self.api,self.project)['id'],second['id'])

    def test_resource_change_blocks_apply_even_without_source_change(self):
        candidate=self.propose()
        (self.project/'logo.png').write_bytes(b'external change')
        self.apply(candidate)
        self.assertEqual(self.api.STATE['phase'],'error')
        self.assertIn('White',(self.project/'MainWindow.xaml').read_text())
        self.assertNotIn(self.project,self.builds)

    def test_source_change_blocks_adjustment(self):
        candidate=self.propose()
        (self.project/'MainWindow.xaml').write_text('<Window Background="Red"/>')
        with self.assertRaisesRegex(ValueError,'proyecto cambió'):
            self.propose('Blue','Green',candidate)
        self.assertIn('Red',(self.project/'MainWindow.xaml').read_text())

    def test_failed_final_build_restores_source_and_keeps_candidate(self):
        candidate=self.propose()
        self.fail_final=True
        self.apply(candidate)
        self.assertIn('White',(self.project/'MainWindow.xaml').read_text())
        self.assertIsNotNone(preview.get_preview(self.api,self.project))

    def test_cancelled_apply_and_new_chat_cannot_write(self):
        candidate=self.propose()
        self.api.CANCEL.is_set.return_value=True
        self.apply(candidate)
        self.assertIn('White',(self.project/'MainWindow.xaml').read_text())
        self.api.STATE['chat_id']='different'
        self.assertIsNone(preview.get_preview(self.api,self.project))

    def test_acceptance_parser_requires_unambiguous_whole_instruction(self):
        self.assertEqual(preview.action('Sí, aplicalo.'),'apply')
        for text in ('no lo apliques','aplicalo pero cambiá el color','¿puedo aplicarlo?'):
            self.assertEqual(preview.action(text),'adjust')


class PreviewRoutingTests(unittest.TestCase):
    def test_web_requires_a_complete_visual_pair_and_explains_blocked_preview(self):
        page = (Path(__file__).parent/'web/index.html').read_text(encoding='utf8')
        self.assertIn('const hasImages=!!(pendingPreview.before&&pendingPreview.after)', page)
        self.assertIn("blocked?'Comparación diagnóstica: este candidato no puede aplicarse. Podés escribir un ajuste", page)
        self.assertIn("pendingPreview.kind==='external_visual'", page)

    def test_read_only_stale_tokens_project_and_restart(self):
        import server
        preview.clear_previews()
        self.addCleanup(preview.clear_previews)
        with tempfile.TemporaryDirectory() as d, patch.dict(server.STATE,server.STATE.copy(),clear=True), patch.object(server,'LOCK',threading.Lock()), patch.object(server,'audit'), patch('server.threading.Thread') as thread:
            project=Path(d)
            preview.save_preview(server,project,dict(changed={},folder=project))
            candidate=preview.get_preview(server,project)
            payload=dict(project=d,instruction='aplicalo',attempts=1,build_mode='off',preview_id=candidate['id'])
            client=server.app.test_client()
            response=client.post('/api/job',json=dict(payload,
                instruction='Solo analizá el proyecto; no apliques cambios',analysis_only=False))
            self.assertEqual(response.status_code,202)
            self.assertIs(thread.call_args.kwargs['target'],server.run_analysis_job)
            server.LOCK.release()
            response=client.post('/api/job',json=dict(payload,preview_id='stale'))
            self.assertEqual(response.status_code,409)
            self.assertFalse(server.LOCK.locked())
            response=client.post('/api/job',json=payload)
            self.assertEqual(response.status_code,202)
            self.assertIs(thread.call_args.kwargs['target'],preview.run_preview_action)
            server.LOCK.release()
            preview.clear_previews()
            self.assertEqual(client.post('/api/job',json=payload).status_code,409)

    def test_external_preview_accepts_natural_language_adjustment(self):
        import external_edit
        import server
        preview.clear_previews()
        self.addCleanup(preview.clear_previews)
        with tempfile.TemporaryDirectory() as d, \
                patch.dict(server.STATE, server.STATE.copy(), clear=True), \
                patch.object(server, 'LOCK', threading.Lock()), \
                patch.object(server, 'audit'), \
                patch('server.threading.Thread') as thread:
            project = Path(d)
            preview.save_preview(server, project, dict(
                kind='external', changed={'Form.cs': b'class Form {}'},
                source_bytes={'Form.cs': b'class Form {}'}, folder=project))
            candidate = preview.get_preview(server, project)
            response = server.app.test_client().post('/api/job', json=dict(
                project=d, instruction='Redondealo un poco más', attempts=1,
                build_mode='off', preview_id=candidate['id']))
            self.assertEqual(response.status_code, 202)
            self.assertIs(thread.call_args.kwargs['target'],
                          external_edit.run_external_adjustment_job)
            self.assertIs(thread.call_args.kwargs['args'][4], candidate)

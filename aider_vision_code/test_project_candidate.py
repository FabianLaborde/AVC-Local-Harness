import json
import contextvars
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import code_preview
from project_candidate import (prepare_candidate, apply_candidate, inventory,
                               visual_preview_requested)
from project_validation import select_adapter


def run(command, cwd, timeout):
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    return result.returncode, result.stdout + result.stderr


class CandidateTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.project = self.root/'project'
        self.project.mkdir()
        self.source = b"def label():\n    return 'Enviar'\n"
        (self.project/'app.py').write_bytes(self.source)
        self.api = SimpleNamespace(CANCEL=threading.Event(), log=Mock(), audit=Mock(),
                                   run_process=run, STATE={'chat_id':'test'})
        self.report = self.root/'report'
        self.report.mkdir()
        self.addCleanup(code_preview.clear_previews)

    def prepare(self, raw=b"def label():\n    return 'Guardar'\n"):
        return prepare_candidate(self.api, self.project, self.report, 'Cambiar texto',
                                 {'app.py':raw}, {'app.py':self.source})

    def test_visual_edits_require_evidence_without_preview_words(self):
        requests = (
            'Redondeá visiblemente las esquinas de los botones INGRESAR y Salir.',
            'Cambiá el color del título a azul.',
            'Alineá el título a la derecha.',
            'Aumentá el ancho de la ventana.',
            'Usá una tipografía en negrita.',
            'Ocultá el panel lateral.',
            'Hacé visible el panel lateral.',
        )
        for request in requests:
            with self.subTest(request=request):
                self.assertTrue(visual_preview_requested(request))

    def test_nonvisual_or_preservation_only_requests_do_not_require_evidence(self):
        self.assertFalse(visual_preview_requested(
            'Cambiá la validación para rechazar campos vacíos.'))
        self.assertFalse(visual_preview_requested(
            'Conservá los colores, las dimensiones y la distribución.'))

    def test_explicit_preview_request_remains_supported(self):
        self.assertTrue(visual_preview_requested(
            'Cambiá la validación y mostrámelo antes y después.'))

    def test_python_real_validation_apply_backup_and_public_preview(self):
        candidate = self.prepare()
        self.assertEqual((self.project/'app.py').read_bytes(), self.source)
        self.assertEqual(candidate['validation']['level'], 'syntax')
        code_preview.save_preview(self.api, self.project, candidate)
        public = code_preview.public_preview(self.api, self.project)
        self.assertIsNone(public['after'])
        self.assertIn('Guardar', public['diff'])
        self.assertNotIn('output', public)
        apply_candidate(self.api, self.project, candidate)
        self.assertEqual((self.project/'app.py').read_bytes(), candidate['changed']['app.py'])
        self.assertEqual((self.report/'before_apply/app.py').read_bytes(), self.source)

    def test_python_invalid_syntax_rejected_with_diagnostic(self):
        with self.assertRaisesRegex(ValueError, 'validación falló'):
            self.prepare(b'def broken(:\n')
        result = json.loads((self.report/'validation.json').read_text())
        self.assertFalse(result['passed'])
        self.assertIn('SyntaxError', result['output'])
        self.assertEqual((self.project/'app.py').read_bytes(), self.source)

    def test_no_user_code_executed(self):
        candidate = self.prepare(b"raise RuntimeError('must not execute')\n")
        self.assertTrue(candidate['validation']['passed'])

    def test_changed_dependency_blocks_acceptance(self):
        (self.project/'theme.txt').write_text('one')
        candidate = self.prepare()
        (self.project/'theme.txt').write_text('two')
        with self.assertRaisesRegex(ValueError, 'proyecto cambió'):
            apply_candidate(self.api, self.project, candidate)
        self.assertFalse((self.report/'before_apply').exists())

    def test_tampered_trial_blocks_acceptance(self):
        candidate = self.prepare()
        (candidate['trial']/'app.py').write_text('unexpected = 1')
        with self.assertRaisesRegex(ValueError, 'copia de prueba cambió'):
            apply_candidate(self.api, self.project, candidate)

    def test_tampered_payload_blocks_acceptance(self):
        candidate = self.prepare()
        candidate['changed']['app.py'] = b'unreviewed = True'
        with self.assertRaisesRegex(ValueError, 'bytes a aplicar'):
            apply_candidate(self.api, self.project, candidate)

    def test_changed_capability_contract_blocks_acceptance(self):
        candidate = self.prepare()
        candidate['capabilities']['validation']['level'] = 'execution'
        with self.assertRaisesRegex(ValueError, 'selección de dependencias'):
            apply_candidate(self.api, self.project, candidate)

    def test_missing_capability_contract_blocks_acceptance(self):
        candidate = self.prepare()
        del candidate['capabilities']
        with self.assertRaisesRegex(ValueError, 'selección de dependencias'):
            apply_candidate(self.api, self.project, candidate)

    def test_capability_contract_is_persisted_with_candidate(self):
        candidate = self.prepare()
        persisted = json.loads((self.report / 'capabilities.json').read_text(encoding='utf8'))
        self.assertEqual(candidate['capabilities'], persisted)
        self.assertEqual(candidate['scope'], persisted['copy_scope'])

    def test_cancelled_validation_never_reviewable(self):
        self.api.CANCEL.set()
        with self.assertRaisesRegex(ValueError, 'cancelada'):
            self.prepare()

    def test_unsupported_is_not_success(self):
        with self.assertRaisesRegex(ValueError, 'validador inequívoco'):
            select_adapter(self.project, 'app.unknown')

    def test_dotnet_selects_nearest_project_and_copies_shared_resources(self):
        (self.project/'src').mkdir()
        (self.project/'src/app.csproj').write_text('<Project />')
        (self.project/'src/app.cs').write_text('class App {}')
        (self.project/'Directory.Build.props').write_text('<Project />')
        def build(command, cwd, timeout):
            self.assertEqual(command[:3], ['dotnet','build','src/app.csproj'])
            self.assertTrue((cwd/'Directory.Build.props').exists())
            return 0, 'Build succeeded'
        self.api.run_process = build
        candidate = prepare_candidate(self.api, self.project, self.report, 'Change',
                                      {'src/app.cs': b'class App { int x; }'},
                                      {'src/app.cs': b'class App {}'})
        self.assertEqual(candidate['validation']['level'], 'compilation')

    def test_explicit_visual_preview_captures_baseline_and_candidate(self):
        (self.project/'src').mkdir()
        (self.project/'src/app.csproj').write_text('<Project />')
        original = b'class App {}'
        (self.project/'src/app.cs').write_bytes(original)
        def build(command, cwd, timeout):
            executable = cwd/'src/bin/Release/net10.0-windows/app.exe'
            executable.parent.mkdir(parents=True, exist_ok=True)
            executable.write_bytes(b'exe')
            return 0, 'Build succeeded'
        self.api.run_process = build
        self.api.capture_published_window = Mock(side_effect=[(b'before-png', ''),
                                                               (b'after-png', '')])
        with patch('preview_visual_review.review_visual_candidate', return_value=dict(
                status='fulfilled', reason='Cambio visible')):
            candidate = prepare_candidate(
                self.api, self.project, self.report,
                'Cambia el formulario y muestra una vista previa antes de aplicar.',
                {'src/app.cs': b'class App { int x; }'}, {'src/app.cs': original},
                runtime_contract=dict(status='ready', runtime=[], target=dict(
                    title_hints=[], required_controls=[], forbidden_titles=[], forbidden_text=[])),
                vision_model='vision')
        self.assertEqual(candidate['before'], b'before-png')
        self.assertEqual(candidate['after'], b'after-png')
        self.assertFalse(candidate['blocked'])
        self.assertEqual(self.api.capture_published_window.call_count, 2)
        code_preview.save_preview(self.api, self.project, candidate)
        public = code_preview.public_preview(self.api, self.project)
        self.assertEqual(public['kind'], 'external_visual')
        self.assertTrue(public['before'].startswith('data:image/png;base64,'))
        self.assertTrue(public['after'].startswith('data:image/png;base64,'))

    def test_iterative_preview_compares_current_candidate_with_next_revision(self):
        (self.project/'src').mkdir()
        (self.project/'src/app.csproj').write_text('<Project />')
        original = b'class App { int radius = 0; }'
        current = b'class App { int radius = 10; }'
        revised = b'class App { int radius = 20; }'
        (self.project/'src/app.cs').write_bytes(original)
        validated_sources = []
        def build(command, cwd, timeout):
            validated_sources.append((cwd/'src/app.cs').read_bytes())
            executable = cwd/'src/bin/Release/net10.0-windows/app.exe'
            executable.parent.mkdir(parents=True, exist_ok=True)
            executable.write_bytes(b'exe')
            return 0, 'Build succeeded'
        self.api.run_process = build
        self.api.capture_published_window = Mock(side_effect=[
            (b'current-png', ''), (b'revised-png', '')])
        with patch('preview_visual_review.review_visual_candidate', return_value=dict(
                status='fulfilled', reason='Progreso visible')):
            candidate = prepare_candidate(
                self.api, self.project, self.report,
                'Redondealo un poco más.',
                {'src/app.cs': revised}, {'src/app.cs': original},
                comparison_changed={'src/app.cs': current},
                runtime_contract=dict(status='ready', runtime=[], target=dict(
                    title_hints=[], required_controls=[], forbidden_titles=[],
                    forbidden_text=[])), vision_model='vision')
        self.assertEqual(validated_sources[:2], [current, revised])
        self.assertEqual(candidate['source_bytes']['src/app.cs'], original)
        self.assertEqual(candidate['changed']['src/app.cs'], revised)
        self.assertEqual((self.project/'src/app.cs').read_bytes(), original)

    def test_visual_rejection_keeps_images_but_blocks_apply(self):
        (self.project/'src').mkdir()
        (self.project/'src/app.csproj').write_text('<Project />')
        original = b'class App {}'
        (self.project/'src/app.cs').write_bytes(original)
        def build(command, cwd, timeout):
            executable = cwd/'src/bin/Release/net10.0-windows/app.exe'
            executable.parent.mkdir(parents=True, exist_ok=True)
            executable.write_bytes(b'exe')
            return 0, 'Build succeeded'
        self.api.run_process = build
        self.api.capture_published_window = Mock(side_effect=[(b'before-png', ''),
                                                               (b'after-png', '')])
        review = dict(status='not_fulfilled', reason='Cambio visual insignificante')
        with patch('preview_visual_review.review_visual_candidate', return_value=review):
            candidate = prepare_candidate(
                self.api, self.project, self.report,
                'Cambia el formulario y muestra una vista previa antes de aplicar.',
                {'src/app.cs': b'class App { int x; }'}, {'src/app.cs': original},
                runtime_contract=dict(status='ready', runtime=[], target=dict(
                    title_hints=[], required_controls=[], forbidden_titles=[], forbidden_text=[])),
                vision_model='vision')
        self.assertTrue(candidate['blocked'])
        self.assertEqual(candidate['before'], b'before-png')
        self.assertEqual(candidate['after'], b'after-png')
        self.assertIn('insignificante', candidate['block_reason'])

    def test_missing_requested_visual_evidence_blocks_apply(self):
        candidate = prepare_candidate(
            self.api, self.project, self.report,
            'Cambia el texto y muestra una vista previa antes de aplicar.',
            {'app.py': b"def label():\n    return 'Guardar'\n"},
            {'app.py': self.source})
        self.assertTrue(candidate['blocked'])
        self.assertIn('ejecutable capturable', candidate['block_reason'])
        with self.assertRaisesRegex(ValueError, 'pareja antes/después completa'):
            code_preview.save_preview(self.api, self.project, candidate)
        self.assertIsNone(code_preview.public_preview(self.api, self.project))

    def test_ambiguous_project_rejected(self):
        for name in ('a', 'b'):
            (self.project/(name+'.csproj')).write_text('<Project />')
        with self.assertRaisesRegex(ValueError, 'Más de un proyecto'):
            select_adapter(self.project, 'app.cs')

    def test_external_project_reference_rejected_before_build(self):
        (self.project/'app.csproj').write_text('<Project><ItemGroup><ProjectReference Include="../other.csproj" /></ItemGroup></Project>')
        adapter, entry = select_adapter(self.project, 'app.cs')
        build = Mock()
        with self.assertRaisesRegex(ValueError, 'Referencia de proyecto'):
            adapter.validate(self.project, entry, build)
        build.assert_not_called()

    def test_build_failure_does_not_offer_apply(self):
        (self.project/'app.csproj').write_text('<Project />')
        (self.project/'app.cs').write_bytes(b'class App {}')
        self.api.run_process = lambda *a, **kw: (1, 'compile error')
        with self.assertRaisesRegex(ValueError, 'validación falló'):
            prepare_candidate(self.api, self.project, self.report, 'Change',
                              {'app.cs': b'class App { int x; }'}, {'app.cs': b'class App {}'})

    def test_build_source_mutation_rejected(self):
        def build(command, cwd, timeout):
            (cwd/'app.py').write_text('mutated = True')
            return 0, 'ok'
        self.api.run_process = build
        with self.assertRaisesRegex(ValueError, 'modificó fuentes'):
            self.prepare()

    def test_external_worker_registers_review_and_accepts_without_editor_rerun(self):
        import external_edit
        self.api.STATE.update(started=None, job_id=1)
        self.api.STATE_LOCK = threading.RLock()
        self.api.LOCK = Mock()
        self.api.USAGE_CONTEXT = contextvars.ContextVar('test_candidate')
        self.api.record_job = Mock()
        self.api.USAGE_FILE = self.root/'usage.json'
        self.api.VISUAL_CAPTURES = {}
        self.api.empty_evidence = lambda job: {}
        for part, raw in [('base', self.source), ('candidate', self.source.replace(b'Enviar', b'Guardar'))]:
            (self.report/part).mkdir()
            (self.report/part/'app.py').write_bytes(raw)
        (self.report/'report.json').write_text(json.dumps(dict(
            baseline={'app.py':'unused'}, changed=['app.py'], editable=['app.py'],
            editor='local_staged')))
        with patch.object(external_edit, 'prepare', return_value=self.report) as editor:
            external_edit.run_external_job(self.api, self.project, 'Change', 'model')
            candidate = code_preview.get_preview(self.api, self.project)
            self.assertIsNotNone(candidate)
            self.assertEqual((self.project/'app.py').read_bytes(), self.source)
            code_preview.run_preview_action(self.api, self.project, candidate['id'], 'apply')
            editor.assert_called_once()
        self.assertIsNone(code_preview.get_preview(self.api, self.project))
        self.assertIn(b'Guardar', (self.project/'app.py').read_bytes())

    def test_dotnet_scope_skips_unrelated_large_tree_and_follows_references(self):
        for name in ('app', 'core', 'unrelated'):
            (self.project/name).mkdir()
        (self.project/'app/app.csproj').write_text('<Project><ItemGroup><ProjectReference Include="../core/core.csproj" /></ItemGroup></Project>')
        (self.project/'core/core.csproj').write_text('<Project/>')
        (self.project/'core/Shared.cs').write_text('class Shared {}')
        (self.project/'app/App.cs').write_text('class App {}')
        (self.project/'Directory.Build.props').write_text('<Project/>')
        with (self.project/'unrelated/large.bin').open('wb') as f:
            f.truncate(251000000)
        adapter, entry = select_adapter(self.project, 'app/App.cs')
        scope = adapter.copy_scope(self.project, entry)
        inputs = inventory(self.project, scope)
        self.assertIn('core/Shared.cs', inputs)
        self.assertIn('Directory.Build.props', inputs)
        self.assertNotIn('unrelated/large.bin', inputs)
        self.assertEqual(len(scope['projects']), 2)

    def test_dotnet_scope_imports_linked_files_and_cycles(self):
        (self.project/'app').mkdir()
        (self.project/'shared').mkdir()
        (self.project/'shared/shared.props').write_text('<Project/>')
        (self.project/'shared/Linked.cs').write_text('class Linked {}')
        (self.project/'app/app.csproj').write_text('<Project><Import Project="../shared/shared.props"/><ItemGroup><Compile Include="../shared/Linked.cs"/><ProjectReference Include="app.csproj"/></ItemGroup></Project>')
        adapter, entry = select_adapter(self.project, 'app/App.cs')
        scope = adapter.copy_scope(self.project, entry)
        self.assertIn('shared/Linked.cs', scope['files'])
        self.assertIn('shared/shared.props', scope['files'])
        self.assertEqual(len(scope['projects']), 1)

    def test_dynamic_reference_blocks_copy(self):
        (self.project/'app.csproj').write_text('<Project><ItemGroup><ProjectReference Include="$(Unknown)/app.csproj"/></ItemGroup></Project>')
        adapter, entry = select_adapter(self.project, 'app.cs')
        with self.assertRaisesRegex(ValueError, 'no resuelta'):
            adapter.copy_scope(self.project, entry)

    def test_new_parent_config_blocks_apply(self):
        (self.project/'app').mkdir()
        (self.project/'app/app.csproj').write_text('<Project/>')
        (self.project/'app/App.cs').write_bytes(b'class App {}')
        self.api.run_process = lambda *args, **kw: (0, 'compiled')
        candidate = prepare_candidate(self.api, self.project, self.report, 'Change',
            {'app/App.cs': b'class App { int x; }'}, {'app/App.cs': b'class App {}'})
        (self.project/'Directory.Build.props').write_text('<Project/>')
        with self.assertRaisesRegex(ValueError, 'selección de dependencias'):
            apply_candidate(self.api, self.project, candidate)

    def test_compilation_never_certifies_request_fulfillment(self):
        candidate = self.prepare()
        fulfillment = candidate['validation']['fulfillment']
        self.assertEqual(fulfillment['status'], 'not_verified')
        self.assertEqual(fulfillment['visual'], 'not_verified')
        code_preview.save_preview(self.api, self.project, candidate)
        self.assertEqual(code_preview.public_preview(self.api, self.project)['fulfillment'], fulfillment)

    def test_excludes_outputs_database_and_secrets(self):
        (self.project/'bin').mkdir()
        (self.project/'bin/old.exe').write_bytes(b'old')
        (self.project/'database.db').write_bytes(b'private')
        (self.project/'.env').write_text('secret')
        self.assertEqual(set(inventory(self.project)), {'app.py'})


if __name__ == '__main__':
    unittest.main()

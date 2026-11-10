import json
import tempfile
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import external_edit
import server
from candidate_lineage import CandidateLineage


class ExternalEditTests(unittest.TestCase):
    def setUp(self):
        self.base = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.project = self.base/'project'
        self.project.mkdir()
        self.work = self.base/'candidate'
        self.work.mkdir()
        self.source = ('public class Screen {\n'
                       '    public void Build() {\n        var b = Theme.Create();\n    }\n' +
                       ''.join(f'    private void Existing{i}() {{ Call(); }}\n' for i in range(50)) + '}\n')
        (self.project/'Screen.cs').write_text(self.source)
        (self.project/'Theme.cs').write_text('class Theme { public static object Create() => null; }')
        self.enterContext(patch.object(external_edit.tempfile, 'mkdtemp', return_value=str(self.work)))
        self.enterContext(patch.object(server, 'ROOT', self.base/'avc'))
        for name in ('audit', 'log', 'record_aider_report', 'record_job', 'LOCK'):
            self.enterContext(patch.object(server, name, Mock()))
        self.enterContext(patch.dict(server.STATE, started=None, running=True))
        server.CANCEL.clear()
        self.addCleanup(server.CANCEL.clear)

    def prepare(self, mutate, code=0):
        def run(cmd, cwd, **kw):
            self.command = cmd
            self.assertEqual(cwd, self.work)
            self.assertEqual(kw['timeout'], server.AIDER_PROCESS_TIMEOUT)
            self.assertEqual(kw['stop_output'], external_edit.EDIT_FAILURES)
            self.assertEqual((cwd/'Screen.cs').read_text(), self.source)
            mutate(cwd)
            return code, 'Aider reports success (not evidence)'
        with patch.object(server, 'run_process', side_effect=run):
            return external_edit.prepare(server, self.project, 'Cambiar Screen.cs. Revisá Theme.cs.', 'coder')

    def test_small_edit_staged_only_with_explicit_context(self):
        result = self.prepare(lambda cwd: (cwd/'Screen.cs').write_text(self.source.replace('var b =', 'var button =')))
        self.assertEqual((self.project/'Screen.cs').read_text(), self.source)
        self.assertIn('--no-git', self.command)
        self.assertEqual(self.command[self.command.index('--edit-format')+1], 'diff')
        self.assertEqual(self.command[self.command.index('--file')+1], str(self.work/'Screen.cs'))
        self.assertEqual(self.command[self.command.index('--read')+1], str(self.work/'Theme.cs'))
        report = json.loads((result/'report.json').read_text())
        self.assertFalse(report['applied'])
        self.assertFalse(report['compiled'])
        self.assertIn('var button', (result/'changes.diff').read_text())
        prompt = self.command[self.command.index('--message')+1]
        self.assertIn('SOLO LECTURA: Theme.cs', prompt)
        self.assertIn('EDITABLE: Screen.cs', prompt)

    def test_explicit_reference_avoids_unrelated_context(self):
        (self.project/'Other.cs').write_text('class Other {}')
        (self.project/'Theme.cs').write_text('class Theme { Other unused; }')
        self.prepare(lambda cwd: (cwd/'Screen.cs').write_text(self.source.replace('var b =', 'var button =')))
        self.assertNotIn(str(self.work/'Other.cs'), self.command)

    def test_zero_exit_with_partial_edit_and_format_failure_rejected(self):
        def run(cmd, cwd, **kw):
            (cwd/'Screen.cs').write_text(self.source.replace('var b =', 'var button ='))
            return 0, 'SearchReplaceNoExactMatch: remaining block failed'
        with patch.object(server, 'run_process', side_effect=run) as process:
            with self.assertRaisesRegex(ValueError, 'detuvieron los reintentos'):
                external_edit.prepare(server, self.project, 'Cambiar Screen.cs. Revisá Theme.cs.', 'coder')
            self.assertEqual(process.call_count, 1)
        self.assertEqual((self.project/'Screen.cs').read_text(), self.source)

    def test_live_process_stopped_before_internal_retry(self):
        script = 'import time; print("SearchReplaceNoExactMatch", flush=True); time.sleep(20); print("UNWANTED_RETRY")'
        start = time.monotonic()
        code, output = server.run_process([sys.executable, '-u', '-c', script], self.work,
                                         timeout=10, stop_output=external_edit.EDIT_FAILURES)
        self.assertEqual(code, -3)
        self.assertLess(time.monotonic()-start, 8)
        self.assertNotIn('UNWANTED_RETRY', output)

    def test_generic_replacement_rejected(self):
        with self.assertRaisesRegex(ValueError, 'masivo'):
            self.prepare(lambda cwd: (cwd/'Screen.cs').write_text('class Screen { void Build() {} }'))
        self.assertEqual((self.project/'Screen.cs').read_text(), self.source)

    def test_small_method_removal_rejected(self):
        with self.assertRaisesRegex(ValueError, 'declaraciones'):
            self.prepare(lambda cwd: (cwd/'Screen.cs').write_text(self.source.replace('    private void Existing1() { Call(); }\n', '')))

    def test_duplicate_event_subscription_rejected(self):
        self.source = self.source.replace('var b = Theme.Create();', 'var b = Theme.Create();\n        b.Click += Submit;')
        (self.project/'Screen.cs').write_text(self.source)
        with self.assertRaisesRegex(ValueError, 'duplicada'):
            self.prepare(lambda cwd: (cwd/'Screen.cs').write_text(self.source.replace('b.Click += Submit;', 'b.Click += Submit;\n        b.Click += Submit;')))

    def test_readonly_dependency_change_rejected(self):
        with self.assertRaisesRegex(ValueError, 'solo lectura'):
            self.prepare(lambda cwd: (cwd/'Theme.cs').write_text('changed'))

    def test_added_file_rejected(self):
        with self.assertRaisesRegex(ValueError, 'agregaron'):
            self.prepare(lambda cwd: (cwd/'Extra.cs').write_text('extra'))

    def test_deleted_file_rejected(self):
        with self.assertRaisesRegex(ValueError, 'eliminaron'):
            self.prepare(lambda cwd: (cwd/'Screen.cs').unlink())

    def test_timeout_never_applied(self):
        with self.assertRaisesRegex(ValueError, 'agotó'):
            self.prepare(lambda cwd: (cwd/'Screen.cs').write_text(self.source.replace('Call()', 'Run()')), code=-2)
        self.assertEqual((self.project/'Screen.cs').read_text(), self.source)

    def test_concurrent_source_change_never_overwritten(self):
        def mutate(cwd):
            (cwd/'Screen.cs').write_text(self.source.replace('Call()', 'Run()'))
            (self.project/'Screen.cs').write_text('external change')
        with self.assertRaisesRegex(ValueError, 'originales cambiaron'):
            self.prepare(mutate)
        self.assertEqual((self.project/'Screen.cs').read_text(), 'external change')

    def test_cancel_never_applied(self):
        with self.assertRaisesRegex(ValueError, 'cancelada'):
            self.prepare(lambda cwd: server.CANCEL.set())
        self.assertEqual((self.project/'Screen.cs').read_text(), self.source)

    def test_missing_target_does_not_run_editor(self):
        with patch.object(server, 'run_process') as run:
            with self.assertRaisesRegex(ValueError, 'no contiene evidencia'):
                external_edit.prepare(server, self.project, 'Cambiá un botón', 'coder')
            run.assert_not_called()

    def test_external_route_does_not_use_inplace_legacy(self):
        with patch.object(external_edit, 'run_external_job') as staged, patch.object(server, 'run_legacy_job') as legacy:
            server.run_job(self.project, 'Cambiar Screen.cs', '', 1, 'coder', build_mode='off')
            staged.assert_called_once()
            legacy.assert_not_called()

    def test_existing_project_uses_staged_editor_even_when_wpf_mode_is_automatic(self):
        with patch.object(external_edit, 'run_external_job') as staged, \
                patch.object(server, 'run_legacy_job') as legacy:
            server.run_job(
                self.project, 'Cambiá el fondo del botón de acceso', '', 1, 'coder',
                build_mode='wpf')
            staged.assert_called_once()
            legacy.assert_not_called()

    def test_generic_snapshot_excludes_derived_artifacts(self):
        artifacts = self.project/'artifacts'/'old-build'
        artifacts.mkdir(parents=True)
        (artifacts/'Screen.cs').write_text('class Duplicated {}')
        snapshot = server.source_snapshot(self.project)
        self.assertIn('Screen.cs', snapshot)
        self.assertFalse(any(name.startswith('artifacts') for name in snapshot))

    def test_worker_releases_lock_on_failure(self):
        observed = []
        def reject(*_args, **_kwargs):
            observed.append(server.STATE['phase'])
            raise ValueError('Rejected')
        with patch.object(external_edit, 'prepare', side_effect=reject):
            external_edit.run_external_job(server, self.project, 'Change', 'coder')
        self.assertEqual(observed, ['planning'])
        self.assertFalse(server.STATE['running'])
        self.assertEqual(server.STATE['phase'], 'error')
        server.LOCK.release.assert_called_once()

    def test_image_interpretation_reaches_external_editor(self):
        with patch.object(server, 'ask_vision', return_value=json.dumps({'observations':['Botón a la derecha']})) as vision, patch.object(external_edit, 'prepare', side_effect=ValueError('stop')) as prepare:
            external_edit.run_external_job(server, self.project, 'Cambiar Screen.cs según imagen', 'coder', 'pixels', 'vision')
        self.assertEqual(vision.call_args.args[1], ['pixels'])
        self.assertIn('Botón a la derecha', prepare.call_args.args[2])

    def test_visual_edit_automatically_prepares_runtime_and_evidence(self):
        report = self.base/'automatic_visual'
        (report/'base').mkdir(parents=True)
        (report/'candidate').mkdir()
        original = self.source.encode()
        changed = self.source.replace('var b =', 'var rounded =').encode()
        (report/'base'/'Screen.cs').write_bytes(original)
        (report/'candidate'/'Screen.cs').write_bytes(changed)
        (report/'report.json').write_text(json.dumps(dict(
            baseline=['Screen.cs'], changed=['Screen.cs'], editable=['Screen.cs'],
            editor='local_staged')))
        contract = dict(status='ready', reason='entorno aislado', runtime=[], target={})
        candidate = dict(blocked=False, validation=dict(
            level='compilation', summary='Compilación correcta.',
            fulfillment=dict(status='visually_verified')))
        instruction = ('Redondeá visiblemente las esquinas de los botones INGRESAR y Salir. '
                       'Conservá todo lo demás.')
        with patch.object(external_edit, 'prepare', return_value=report), \
                patch('preview_reasoner.plan_preview', return_value=contract) as plan, \
                patch('project_candidate.prepare_candidate', return_value=candidate) as validate, \
                patch.object(external_edit, 'attempt_visual_retry',
                             return_value=(candidate, {'Screen.cs': changed}, False)), \
                patch('code_preview.save_preview') as preview:
            external_edit.run_external_job(server, self.project, instruction, 'coder',
                                           vision_model='vision')
        plan.assert_called_once()
        self.assertEqual(validate.call_args.kwargs['runtime_contract'], contract)
        self.assertEqual(validate.call_args.kwargs['vision_model'], 'vision')
        preview.assert_called_once()
        self.assertEqual(server.STATE['phase'], 'review')

    def test_blocked_runtime_contract_never_builds_or_offers_preview(self):
        report = self.base/'blocked_runtime'
        (report/'base').mkdir(parents=True)
        (report/'candidate').mkdir()
        original = self.source.encode()
        changed = self.source.replace('var b =', 'var rounded =').encode()
        (report/'base'/'Screen.cs').write_bytes(original)
        (report/'candidate'/'Screen.cs').write_bytes(changed)
        (report/'report.json').write_text(json.dumps(dict(
            baseline=['Screen.cs'], changed=['Screen.cs'], editable=['Screen.cs'],
            editor='local_staged')))
        contract = dict(status='blocked', reason='pantalla objetivo no identificada',
                        runtime=[], target={})
        with patch.object(external_edit, 'prepare', return_value=report), \
                patch('preview_reasoner.plan_preview', return_value=contract), \
                patch('project_candidate.prepare_candidate') as validate, \
                patch('code_preview.save_preview') as preview:
            external_edit.run_external_job(
                server, self.project, 'Redondeá visiblemente los botones', 'coder')
        validate.assert_not_called()
        preview.assert_not_called()
        self.assertEqual(server.STATE['phase'], 'error')
        self.assertIn('sin ambas capturas', server.STATE['result'])

    def test_pending_external_adjustment_edits_current_candidate_not_baseline(self):
        baseline = {
            'Screen.cs': self.source.encode(),
            'Theme.cs': (self.project/'Theme.cs').read_bytes(),
        }
        current = {'Screen.cs': self.source.replace(
            'var b =', 'var candidate =').encode()}
        improved = {'Screen.cs': current['Screen.cs'].replace(
            b'var candidate =', b'var improved =')}
        lineage = CandidateLineage(baseline, ['Screen.cs'], max_candidates=6)
        lineage.record(current, 'initial')
        pending = dict(
            kind='external', source_bytes=baseline, changed=current,
            validation=dict(visual_evidence={}), _lineage=lineage)
        candidate = dict(
            kind='external', changed=improved, source_bytes=baseline,
            validation=dict(summary='Compilación correcta', passed=True),
            folder=self.base/'prepared')
        adjustment = self.base/'adjustment'
        adjustment.mkdir()
        with patch.object(external_edit.tempfile, 'mkdtemp', return_value=str(adjustment)), \
                patch('source_editor.staged_propose', return_value=(
                    improved, {'responses': []})) as propose, \
                patch('project_candidate.prepare_candidate', return_value=candidate) as prepare, \
                patch('code_preview.save_preview') as preview:
            external_edit.run_external_adjustment_job(
                server, self.project, 'Mejorá el candidato', 'coder', pending, 'vision')
        self.assertIn(b'var candidate =', propose.call_args.args[2]['Screen.cs'])
        self.assertNotIn(b'var b =', propose.call_args.args[2]['Screen.cs'])
        self.assertEqual(prepare.call_args.args[4], improved)
        self.assertEqual(len(lineage.revisions), 2)
        self.assertEqual(lineage.current_changes, improved)
        preview.assert_called_once()
        self.assertEqual(server.STATE['phase'], 'review')

    def test_run_job_uses_second_bounded_retry_after_measured_shortfall(self):
        report = self.base/'two_visual_retries'
        (report/'base').mkdir(parents=True)
        (report/'candidate').mkdir()
        original = self.source.encode()
        changed = self.source.replace('var b =', 'var rounded =').encode()
        (report/'base'/'Screen.cs').write_bytes(original)
        (report/'candidate'/'Screen.cs').write_bytes(changed)
        (report/'report.json').write_text(json.dumps(dict(
            baseline=['Screen.cs'], changed=['Screen.cs'], editable=['Screen.cs'],
            editor='local_staged')))
        contract = dict(status='ready', reason='aislado', runtime=[], target={})
        initial = self.visual_candidate()
        first_retry = self.visual_candidate()
        final = dict(blocked=False, validation=dict(
            level='compilation', summary='Verificado',
            fulfillment=dict(status='visually_verified'), visual_evidence={}))
        manifest_changes = {'Screen.cs': changed}
        with patch.object(external_edit, 'prepare', return_value=report), \
                patch('preview_reasoner.plan_preview', return_value=contract), \
                patch('project_candidate.prepare_candidate', return_value=initial), \
                patch.object(external_edit, 'attempt_visual_retry', side_effect=[
                    (first_retry, manifest_changes, True),
                    (final, manifest_changes, True)]) as retry, \
                patch('code_preview.save_preview') as preview:
            external_edit.run_external_job(
                server, self.project, 'Redondeá visiblemente los botones', 'coder',
                vision_model='vision')
        self.assertEqual(retry.call_count, 2)
        self.assertEqual(retry.call_args_list[1].kwargs['attempt_no'], 2)
        self.assertIs(retry.call_args_list[1].args[8], first_retry)
        preview.assert_called_once()
        self.assertEqual(server.STATE['phase'], 'review')

    def test_structured_generation_never_invokes_aider(self):
        changed = {'Screen.cs': self.source.replace('var b =', 'var button =').encode()}
        with patch('source_editor.staged_propose', return_value=(changed, {'responses': [{}, {}]})), patch.object(server, 'run_process') as process:
            report = external_edit.prepare(server, self.project, 'Cambiar Screen.cs. Revisá Theme.cs.', 'coder', structured=True)
            process.assert_not_called()
        self.assertEqual((self.project/'Screen.cs').read_text(), self.source)
        self.assertEqual(json.loads((report/'report.json').read_text())['editor'], 'local_staged')

    def test_structured_scope_cleanup_is_audited_and_visible(self):
        changed = {'Screen.cs': self.source.replace('var b =', 'var rounded =').encode()}
        removed = [dict(file='Screen.cs', category='color', property='backcolor')]
        evidence = {'responses': [{}, {}],
                    'scope_cleanups': [dict(removed=removed, reason='fuera de alcance')]}
        with patch('source_editor.staged_propose', return_value=(changed, evidence)):
            external_edit.prepare(server, self.project,
                                  'En Screen.cs redondea las esquinas. Conserva todo lo demas.',
                                  'coder', structured=True)
        server.audit.assert_any_call('external_scope_cleanup', removed=removed, cleanup_count=1)
        self.assertTrue(any('Alcance conservado automáticamente' in call.args[0]
                            for call in server.log.call_args_list))

    def repair_case(self, second_pass):
        changed = {'Screen.cs': self.source.replace('var b =', 'var button =').encode()}
        revised = {'Screen.cs': self.source.replace('var b =', 'var revised =').encode()}
        validations = []
        def validate(api, project, folder, instruction, proposed, original):
            validations.append(folder)
            if len(validations) == 1 or not second_pass:
                (folder/'validation.json').write_text(json.dumps({'passed': False, 'output':'error CS0246: Missing type'}))
                raise ValueError('Failed compilation')
            return {'validation': {'passed': True, 'level':'compilation', 'summary':'Compiled'}, 'changed':proposed}
        sdk = {'provider': 'test_sdk', 'status': 'available'}
        with patch('source_editor.staged_propose', return_value=(changed, {'responses':[{}, {}], 'sdk_evidence': sdk})), patch('source_editor.repair_proposal', return_value=(revised, {})) as repair, patch('project_candidate.prepare_candidate', side_effect=validate), patch('code_preview.save_preview') as preview:
            external_edit.run_external_job(server, self.project, 'Cambiar Screen.cs. Revisá Theme.cs.', 'coder')
            self.assertEqual(repair.call_count, 1)
            self.assertIn('CS0246', repair.call_args.args[-1])
            self.assertEqual(repair.call_args.args[2]['Screen.cs'], (self.project/'Screen.cs').read_bytes())
            self.assertNotIn(b'var button', repair.call_args.args[2]['Screen.cs'])
            self.assertEqual(repair.call_args.kwargs['sdk_evidence'], sdk)
            self.assertEqual(len(validations), 2)
            self.assertEqual(preview.call_count, int(second_pass))
            self.assertEqual((self.project/'Screen.cs').read_text(), self.source)
            self.assertEqual(server.STATE['phase'], 'review' if second_pass else 'error')

    def test_compile_error_repaired_once_then_revalidated(self):
        self.repair_case(True)

    def test_second_compile_failure_never_offers_approval(self):
        self.repair_case(False)

    def test_infrastructure_failure_never_triggers_code_repair(self):
        changed = {'Screen.cs': self.source.replace('var b =', 'var button =').encode()}
        def validate(_api, _project, folder, _instruction, _proposed, _original):
            (folder/'validation.json').write_text(json.dumps({
                'passed': False,
                'output': "NuGet.targets: error : Access to NuGet.Config is denied"}))
            raise ValueError('Failed environment')
        with patch('source_editor.staged_propose', return_value=(
                changed, {'responses': [{}, {}], 'sdk_evidence': {}})), \
                patch('source_editor.repair_proposal') as repair, \
                patch('project_candidate.prepare_candidate', side_effect=validate), \
                patch('code_preview.save_preview') as preview:
            external_edit.run_external_job(server, self.project,
                                           'Cambiar Screen.cs. Revisá Theme.cs.', 'coder')
        repair.assert_not_called()
        preview.assert_not_called()
        self.assertEqual(server.STATE['phase'], 'error')
        self.assertIn('infraestructura', server.STATE['result'])

    def visual_candidate(self, status='not_fulfilled', available=True):
        return dict(blocked=True, block_reason='Cambio insignificante', validation=dict(
            visual_evidence=dict(available=available, review=dict(
                status=status, reason='Cambio insignificante', metrics={}, vision={}))))

    def test_visual_rejection_revises_current_candidate_and_records_lineage(self):
        report = self.base/'visual_report'
        report.mkdir()
        (report/'generation.json').write_text(json.dumps({'sdk_evidence': {'status':'available'}}))
        original = {'Screen.cs': self.source.encode(), 'Theme.cs': (self.project/'Theme.cs').read_bytes()}
        rejected = {'Screen.cs': self.source.replace('var b =', 'var tiny =').encode()}
        revised = {'Screen.cs': self.source.replace('var b =', 'var visible =').encode()}
        first = self.visual_candidate()
        second = dict(blocked=False, validation=dict(
            fulfillment=dict(status='visually_verified')))
        manifest = {'editable':['Screen.cs']}
        lineage = CandidateLineage(original, ['Screen.cs'], max_candidates=3)
        lineage.record(rejected, 'validated_initial')
        with patch('source_editor.visual_repair_proposal', return_value=(revised, {'response':'ok'})) as repair, \
                patch('project_candidate.prepare_candidate', return_value=second) as prepare:
            result, changed, attempted = external_edit.attempt_visual_retry(
                server, self.project, report, 'Cambia Screen.cs con vista previa', 'coder',
                manifest, original, rejected, first, {'vision_model':'vision'},
                lineage=lineage)
        self.assertTrue(attempted)
        self.assertIs(result, second)
        self.assertEqual(changed, revised)
        self.assertIs(result['folder'], report)
        self.assertEqual(manifest['visual_retry_attempts'], 1)
        self.assertEqual(repair.call_args.args[2], original)
        self.assertEqual(repair.call_args.args[5], rejected)
        self.assertEqual(prepare.call_count, 1)
        self.assertEqual(prepare.call_args.args[4], revised)
        self.assertTrue((report/'visual_first_attempt.json').is_file())
        saved_lineage = json.loads((report/'candidate_lineage.json').read_text())
        self.assertEqual(len(saved_lineage['revisions']), 2)
        self.assertEqual(saved_lineage['revisions'][1]['parent_hash'],
                         saved_lineage['revisions'][0]['content_hash'])
        self.assertEqual((report/'candidate'/'Screen.cs').read_bytes(), revised['Screen.cs'])

    def test_visual_retry_scope_cleanup_is_audited_and_visible(self):
        report = self.base/'visual_cleanup'
        report.mkdir()
        (report/'generation.json').write_text('{}')
        original = {'Screen.cs': self.source.encode()}
        revised = {'Screen.cs': self.source.replace('var b =', 'var rounded =').encode()}
        removed = [dict(file='Screen.cs', category='color', property='backcolor')]
        response = dict(scope_cleanups=[dict(removed=removed, reason='fuera de alcance')])
        candidate = dict(blocked=False, validation=dict(
            fulfillment=dict(status='visually_verified')))
        manifest = {'editable':['Screen.cs']}
        with patch('source_editor.visual_repair_proposal',
                   return_value=(revised, response)), \
                patch('project_candidate.prepare_candidate', return_value=candidate):
            external_edit.attempt_visual_retry(
                server, self.project, report, 'Redondea las esquinas', 'coder', manifest,
                original, original, self.visual_candidate(), {})
        server.audit.assert_any_call('external_visual_retry_scope_cleanup',
                                     removed=removed, cleanup_count=1)
        self.assertTrue(any('Alcance del reintento conservado automáticamente' in call.args[0]
                            for call in server.log.call_args_list))

    def test_uncertain_or_incomplete_visual_rejection_never_retries(self):
        report = self.base/'no_retry'
        report.mkdir()
        original = {'Screen.cs': self.source.encode()}
        for candidate in (self.visual_candidate('uncertain'),
                          self.visual_candidate('not_fulfilled', available=False)):
            with patch('source_editor.visual_repair_proposal') as repair:
                result, changed, attempted = external_edit.attempt_visual_retry(
                    server, self.project, report, 'Pedido', 'coder', {'editable':['Screen.cs']},
                    original, original, candidate, {})
            self.assertFalse(attempted)
            self.assertIs(result, candidate)
            self.assertEqual(changed, original)
            repair.assert_not_called()

    def test_failed_visual_retry_preserves_first_diagnostic(self):
        report = self.base/'failed_retry'
        report.mkdir()
        (report/'generation.json').write_text('{}')
        original = {'Screen.cs': self.source.encode()}
        first = self.visual_candidate()
        with patch('source_editor.visual_repair_proposal', side_effect=ValueError('sin ancla')):
            result, changed, attempted = external_edit.attempt_visual_retry(
                server, self.project, report, 'Pedido', 'coder', {'editable':['Screen.cs']},
                original, original, first, {})
        self.assertFalse(attempted)
        self.assertIs(result, first)
        self.assertEqual(changed, original)
        failure = json.loads((report/'visual_retry_failure.json').read_text())
        self.assertIn('sin ancla', failure['reason'])

    def test_visual_retry_repairs_one_compile_failure_without_new_cycle(self):
        report = self.base/'compile_retry'
        report.mkdir()
        (report/'generation.json').write_text(json.dumps({'sdk_evidence': None}))
        original = {'Screen.cs': self.source.encode()}
        failed = {'Screen.cs': self.source.replace('var b =', 'var broken =').encode()}
        repaired = {'Screen.cs': self.source.replace('var b =', 'var fixed16 =').encode()}
        generation = {'implementation_plan': {'parameter_changes': []}}
        second = dict(blocked=False, validation=dict(
            fulfillment=dict(status='visually_verified')))
        calls = []
        def prepare(*args, **kwargs):
            calls.append(args[2])
            if len(calls) == 1:
                args[2].joinpath('validation.json').write_text(json.dumps(dict(
                    passed=False, output='Screen.cs(1): error CS1503 tipo incorrecto')))
                raise ValueError('compilacion fallida')
            return second
        manifest = {'editable':['Screen.cs']}
        with patch('source_editor.visual_repair_proposal', return_value=(failed, generation)), \
                patch('source_editor.visual_compile_repair_proposal',
                      return_value=(repaired, {'response':'compile fixed'})) as compile_repair, \
                patch('project_candidate.prepare_candidate', side_effect=prepare):
            result, changed, attempted = external_edit.attempt_visual_retry(
                server, self.project, report, 'Cambia Screen.cs con vista previa', 'coder',
                manifest, original, original, self.visual_candidate(), {})
        self.assertTrue(attempted)
        self.assertIs(result, second)
        self.assertEqual(changed, repaired)
        self.assertEqual(len(calls), 2)
        self.assertEqual(manifest['visual_retry_attempts'], 1)
        self.assertEqual(manifest['visual_retry_compile_repairs'], 1)
        self.assertIn('CS1503', compile_repair.call_args.args[6])
        self.assertEqual((report/'candidate'/'Screen.cs').read_bytes(), repaired['Screen.cs'])

    def test_parent_path_rejected(self):
        with self.assertRaises(ValueError):
            external_edit.read_source(self.project, '../file.cs')

    def test_named_sibling_not_historical_duplicate(self):
        old = self.project/'artifacts'
        old.mkdir()
        (old/'Theme.cs').write_text('historical version')
        result = self.prepare(lambda cwd: (cwd/'Screen.cs').write_text(self.source.replace('var b =', 'var button =')))
        self.assertEqual((self.work/'Theme.cs').read_text(), (self.project/'Theme.cs').read_text())
        self.assertNotIn('artifacts', (result/'report.json').read_text())

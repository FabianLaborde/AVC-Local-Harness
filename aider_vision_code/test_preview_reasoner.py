import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from preview_reasoner import (collect_evidence, plan_preview, validate_contract,
                              validate_evidence_requirements,
                              reconcile_evidence_requirements)


class PreviewReasonerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name)/'project'
        (self.project/'src').mkdir(parents=True)
        (self.project/'database').mkdir()
        (self.project/'src/LoginForm.cs').write_text('class LoginForm { // INGRESAR Salir\n }')
        (self.project/'src/DatabaseLocator.cs').write_text(
            'var p = Environment.GetEnvironmentVariable("APP_DB_PATH");\n'
            'var f = Path.Combine("database", "App.db");')
        (self.project/'src/Program.cs').write_text(
            'static void Main() { new LoginForm().ShowDialog(); }')
        (self.project/'database/App.db').write_bytes(b'SQLite placeholder')
        self.report = Path(self.temp.name)/'report'
        self.report.mkdir()

    def valid(self):
        return dict(status='ready', target=dict(title_hints=[],
            required_controls=['INGRESAR', 'Salir'], forbidden_titles=['Error'],
            forbidden_text=['No se pudo iniciar']), runtime=[dict(
                environment_variable='APP_DB_PATH', source_relative='database/App.db',
                resource_type='sqlite', provider='sqlite_snapshot')],
                uncertainties=[], reason='Evidencia suficiente')

    def test_collects_bounded_startup_and_runtime_evidence(self):
        evidence = collect_evidence(self.project, 'src/LoginForm.cs', 'Cambia INGRESAR y Salir')
        paths = [item['path'] for item in evidence['sources']]
        self.assertIn('src/LoginForm.cs', paths)
        self.assertIn('src/DatabaseLocator.cs', paths)
        self.assertIn({'path':'database/App.db','size':18}, evidence['runtime_candidates'])
        self.assertEqual(evidence['runtime_requirements'], [dict(
            environment_variable='APP_DB_PATH', declared_in='src/DatabaseLocator.cs',
            path_hints=['database/App.db'], status='resolved', binding=dict(
                environment_variable='APP_DB_PATH', source_relative='database/App.db',
                provider='sqlite_snapshot', resource_type='sqlite'))])

    def test_model_omission_is_completed_from_unambiguous_source_evidence(self):
        evidence = collect_evidence(self.project, 'src/LoginForm.cs', 'Cambia INGRESAR y Salir')
        contract = self.valid()
        contract['runtime'] = []
        reconciled = reconcile_evidence_requirements(
            self.project, evidence, validate_contract(self.project, contract))
        self.assertEqual(reconciled['runtime'], [dict(
            environment_variable='APP_DB_PATH', source_relative='database/App.db',
            provider='sqlite_snapshot', resource_type='sqlite')])

    def test_ambiguous_declared_resource_blocks_ready_contract(self):
        (self.project/'archive/database').mkdir(parents=True)
        (self.project/'archive/database/App.db').write_bytes(b'other')
        # A basename-only literal cannot choose between two runtime files.
        (self.project/'src/DatabaseLocator.cs').write_text(
            'var p = Environment.GetEnvironmentVariable("APP_DB_PATH");\n'
            'var f = "App.db";')
        evidence = collect_evidence(self.project, 'src/LoginForm.cs', 'Vista previa')
        contract = self.valid()
        contract['runtime'] = []
        reconciled = reconcile_evidence_requirements(
            self.project, evidence, validate_contract(self.project, contract))
        self.assertEqual(reconciled['status'], 'blocked')
        self.assertIn('APP_DB_PATH', reconciled['reason'])

    def test_non_path_environment_values_are_not_file_requirements(self):
        (self.project/'src/Options.cs').write_text(
            'var port = Environment.GetEnvironmentVariable("APP_COM_PORT");\n'
            'var config = "serial-port.json";')
        evidence = collect_evidence(self.project, 'src/LoginForm.cs', 'Vista previa')
        variables = [item['environment_variable']
                     for item in evidence['runtime_requirements']]
        self.assertNotIn('APP_COM_PORT', variables)

    def test_ready_contract_requires_discovered_safe_preview_switch(self):
        (self.project/'src/Network.cs').write_text(
            'Environment.GetEnvironmentVariable("APP_LOCAL_TEST") == "true";')
        evidence = collect_evidence(self.project, 'src/LoginForm.cs', 'Vista previa')
        self.assertEqual(evidence['safety_switches'], [dict(
            environment_variable='APP_LOCAL_TEST', value='true')])
        contract = validate_contract(self.project, self.valid())
        with self.assertRaisesRegex(ValueError, 'interruptores de seguridad'):
            validate_evidence_requirements(evidence, contract)
        reconciled = reconcile_evidence_requirements(self.project, evidence, contract)
        self.assertIn(dict(environment_variable='APP_LOCAL_TEST',
            resource_type='literal', provider='literal_environment', value='true'),
            reconciled['runtime'])
        validate_evidence_requirements(evidence, reconciled)

    def test_contradictory_safe_preview_switch_blocks_ready_contract(self):
        (self.project/'src/Network.cs').write_text(
            'Environment.GetEnvironmentVariable("APP_LOCAL_TEST") == "true";')
        evidence = collect_evidence(self.project, 'src/LoginForm.cs', 'Vista previa')
        contract = validate_contract(self.project, self.valid())
        contract['runtime'].append(dict(environment_variable='APP_LOCAL_TEST',
            resource_type='literal', provider='literal_environment', value='false'))
        reconciled = reconcile_evidence_requirements(self.project, evidence, contract)
        self.assertEqual(reconciled['status'], 'blocked')
        self.assertIn('contradictorio', reconciled['reason'])

    def test_contract_rejects_unsafe_or_inconsistent_runtime(self):
        contract = self.valid()
        contract['runtime'][0]['provider'] = 'bounded_file_copy'
        with self.assertRaisesRegex(ValueError, 'no admite el tipo'):
            validate_contract(self.project, contract)
        contract = self.valid()
        contract['runtime'][0]['source_relative'] = '../outside.db'
        with self.assertRaisesRegex(ValueError, 'fuera del proyecto'):
            validate_contract(self.project, contract)

    def test_unknown_database_provider_fails_closed(self):
        contract = self.valid()
        contract['runtime'][0].update(resource_type='postgresql',
                                      provider='postgresql_test_database')
        with self.assertRaisesRegex(ValueError, 'proveedor runtime instalado'):
            validate_contract(self.project, contract)

    def test_model_block_without_evidence_is_promoted_deterministically(self):
        evidence = collect_evidence(self.project, 'src/LoginForm.cs', 'Vista previa')
        contract = self.valid()
        contract.update(status='blocked', runtime=[], reason=(
            'No existe un proveedor instalado que permita modificar la UI o renderizar.'))
        normalized = validate_contract(self.project, contract)
        reconciled = reconcile_evidence_requirements(self.project, evidence, normalized)
        self.assertEqual(reconciled['status'], 'ready')
        self.assertEqual(reconciled['runtime'][0]['environment_variable'], 'APP_DB_PATH')
        self.assertEqual(reconciled['reason'],
                         'Pantalla identificada y requisitos de arranque '
                         'reconciliados con evidencia del proyecto.')

    def test_uncertainty_or_missing_target_remains_blocked(self):
        evidence = collect_evidence(self.project, 'src/LoginForm.cs', 'Vista previa')
        contract = self.valid()
        contract.update(status='blocked', uncertainties=['No se identificó la ventana'])
        reconciled = reconcile_evidence_requirements(
            self.project, evidence, validate_contract(self.project, contract))
        self.assertEqual(reconciled['status'], 'blocked')
        contract = self.valid()
        contract.update(status='blocked', target=dict(title_hints=[], required_controls=[],
                        forbidden_titles=[], forbidden_text=[]))
        reconciled = reconcile_evidence_requirements(
            self.project, evidence, validate_contract(self.project, contract))
        self.assertEqual(reconciled['status'], 'blocked')

    def test_invalid_first_contract_is_repaired_once(self):
        invalid = self.valid()
        invalid['status'] = 'planned'
        responses = [dict(message=dict(content=json.dumps(invalid))),
                     dict(message=dict(content=json.dumps(self.valid())))]
        api = SimpleNamespace(post_ollama=Mock(side_effect=responses), audit=Mock())
        result = plan_preview(api, self.project, 'Cambia INGRESAR y Salir con vista previa',
                              'src/LoginForm.cs', self.report, model='reasoner')
        self.assertEqual(result['status'], 'ready')
        self.assertEqual(api.post_ollama.call_count, 2)
        self.assertEqual(api.post_ollama.call_args.args[1]['think'], 'low')
        self.assertEqual(api.post_ollama.call_args.args[1]['options']['num_ctx'], 8192)
        evidence = json.loads((self.report/'preview_runtime_plan.json').read_text())
        self.assertEqual(len(evidence['attempts']), 2)

    def test_empty_explanatory_reason_is_normalized_without_relaxing_evidence(self):
        contract = self.valid()
        contract['reason'] = ''
        api = SimpleNamespace(post_ollama=Mock(return_value=dict(
            response=json.dumps(contract), done_reason='stop')), audit=Mock())
        result = plan_preview(api, self.project, 'Cambia INGRESAR y Salir con vista previa',
                              'src/LoginForm.cs', self.report, model='reasoner')
        self.assertEqual(result['status'], 'ready')
        self.assertIn('Explicación omitida', result['reason'])
        self.assertEqual(api.post_ollama.call_count, 1)

    def test_non_text_reason_remains_invalid_and_uses_single_recovery(self):
        invalid = self.valid()
        invalid['reason'] = None
        api = SimpleNamespace(post_ollama=Mock(return_value=dict(
            response=json.dumps(invalid), done_reason='stop')), audit=Mock())
        result = plan_preview(api, self.project, 'Vista previa', 'src/LoginForm.cs',
                              self.report, model='reasoner')
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(api.post_ollama.call_count, 2)

    def test_plan_adds_required_runtime_binding_omitted_by_model(self):
        omitted = self.valid()
        omitted['runtime'] = []
        api = SimpleNamespace(post_ollama=Mock(return_value=dict(
            message=dict(content=json.dumps(omitted)))), audit=Mock())
        result = plan_preview(api, self.project, 'Cambia INGRESAR y Salir',
                              'src/LoginForm.cs', self.report, model='reasoner')
        self.assertEqual(result['status'], 'ready')
        self.assertEqual(result['runtime'][0]['environment_variable'], 'APP_DB_PATH')

    def test_reasoner_receives_same_common_technical_evidence_contract(self):
        api = SimpleNamespace(post_ollama=Mock(return_value=dict(
            message=dict(content=json.dumps(self.valid())))), audit=Mock())
        technical = dict(schema_version=1, kind='technical_evidence',
                         provider='technical_evidence_registry', status='available',
                         ecosystems=['python'], providers=[dict(
                             provider='python_local_metadata', status='available',
                             ecosystem='python', entries=[{'symbol': 'omit-this'}])],
                         entries=[{'symbol': 'also-omit-this'}])
        plan_preview(api, self.project, 'Cambia INGRESAR y Salir',
                     'src/LoginForm.cs', self.report, model='reasoner',
                     technical_evidence=technical)
        prompt = api.post_ollama.call_args.args[1]['prompt']
        self.assertIn('technical_evidence_registry', prompt)
        self.assertNotIn('omit-this', prompt)
        saved = json.loads((self.report/'preview_runtime_plan.json').read_text())
        self.assertEqual(saved['evidence']['technical_evidence'], dict(
            schema_version=1, kind='technical_evidence',
            provider='technical_evidence_registry', status='available',
            ecosystems=['python'], providers=[dict(
                provider='python_local_metadata', status='available',
                ecosystem='python')]))

    def test_tool_request_is_rejected_and_repaired_as_json(self):
        responses = [dict(done=True, done_reason='stop', response='', tool_calls=[
            {'function': {'name': 'repo_browser.print_tree', 'arguments': {}}}]),
            dict(done=True, done_reason='stop', response=json.dumps(self.valid()))]
        api = SimpleNamespace(post_ollama=Mock(side_effect=responses), audit=Mock())
        result = plan_preview(api, self.project, 'Vista previa', 'src/LoginForm.cs',
                              self.report, model='reasoner')
        self.assertEqual(result['status'], 'ready')
        retry_prompt = api.post_ollama.call_args.args[1]['prompt']
        self.assertIn('herramientas no disponibles', retry_prompt)
        self.assertIn('repo_browser.print_tree', retry_prompt)

    def test_length_exhaustion_is_reported_and_fails_closed(self):
        api = SimpleNamespace(post_ollama=Mock(return_value=dict(
            done=True, done_reason='length', response='', thinking='unfinished')),
            audit=Mock())
        result = plan_preview(api, self.project, 'Vista previa', 'src/LoginForm.cs',
                              self.report, model='reasoner')
        self.assertEqual(result['status'], 'blocked')
        saved = json.loads((self.report/'preview_runtime_plan.json').read_text())
        self.assertIn('contexto antes del contenido final', saved['attempts'][0]['error'])

    def test_empty_responses_fail_closed(self):
        api = SimpleNamespace(post_ollama=Mock(return_value=dict(
            message=dict(content='', thinking='unfinished'))), audit=Mock())
        result = plan_preview(api, self.project, 'Vista previa', 'src/LoginForm.cs',
                              self.report, model='reasoner')
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(api.post_ollama.call_count, 2)


if __name__ == '__main__':
    unittest.main()

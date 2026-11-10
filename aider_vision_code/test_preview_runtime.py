import sqlite3
import tempfile
import unittest
from pathlib import Path

from preview_runtime import materialized_environment, target_errors, inspection_summary
from preview_runtime import provider_specs, source_binding


class PreviewRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name)/'project'
        (self.project/'database').mkdir(parents=True)
        self.database = self.project/'database/App.db'
        connection = sqlite3.connect(self.database)
        connection.execute('create table sample(value text)')
        connection.execute("insert into sample values ('original')")
        connection.commit()
        connection.close()
        self.contract = dict(status='ready', runtime=[dict(
            environment_variable='APP_DB_PATH', source_relative='database/App.db',
            resource_type='sqlite', provider='sqlite_snapshot')], target=dict(
                title_hints=[], required_controls=['INGRESAR','Salir'],
                forbidden_titles=['Error de inicio'], forbidden_text=['No se pudo iniciar']))

    def test_sqlite_snapshot_is_disposable_and_does_not_mutate_original(self):
        snapshot_path = None
        with materialized_environment(self.project, self.contract) as environment:
            snapshot_path = Path(environment['APP_DB_PATH'])
            self.assertNotEqual(snapshot_path, self.database)
            reader = sqlite3.connect(snapshot_path)
            self.assertEqual(reader.execute('select value from sample').fetchone()[0], 'original')
            reader.close()
            connection = sqlite3.connect(snapshot_path)
            connection.execute("update sample set value='changed'")
            connection.commit()
            connection.close()
        self.assertFalse(snapshot_path.exists())
        reader = sqlite3.connect(self.database)
        self.assertEqual(reader.execute('select value from sample').fetchone()[0], 'original')
        reader.close()

    def test_provider_registry_is_explicit_and_unknown_provider_is_rejected(self):
        self.assertIn(dict(provider='sqlite_snapshot', resource_type='sqlite'),
                      provider_specs())
        contract = dict(self.contract)
        contract['runtime'] = [dict(self.contract['runtime'][0],
                                    provider='postgresql_test_database',
                                    resource_type='postgresql')]
        with self.assertRaisesRegex(ValueError, 'proveedor runtime instalado'):
            with materialized_environment(self.project, contract):
                pass

    def test_source_binding_prefers_specialized_safe_provider(self):
        self.assertEqual(source_binding(self.project, 'APP_DB_PATH', 'database/App.db'), dict(
            environment_variable='APP_DB_PATH', source_relative='database/App.db',
            provider='sqlite_snapshot', resource_type='sqlite'))

    def test_boolean_environment_provider_is_generic_and_bounded(self):
        contract = dict(status='ready', target=self.contract['target'], runtime=[dict(
            environment_variable='APP_LOCAL_TEST', resource_type='literal',
            provider='literal_environment', value='TRUE')])
        with materialized_environment(self.project, contract) as environment:
            self.assertEqual(environment['APP_LOCAL_TEST'], 'true')
        contract['runtime'][0]['value'] = 'secret-or-command'
        with self.assertRaisesRegex(ValueError, 'valores booleanos seguros'):
            with materialized_environment(self.project, contract):
                pass

    def test_target_validation_rejects_startup_error_and_missing_controls(self):
        error = dict(window=dict(title='Error de inicio'), uia=dict(controls=[
            dict(type='Text', name='No se pudo iniciar la aplicación')]))
        self.assertTrue(target_errors(error, self.contract))
        login = dict(window=dict(title='Inicio de sesión'), uia=dict(controls=[
            dict(type='Button', name='INGRESAR'), dict(type='Button', name='Salir')]))
        self.assertEqual(target_errors(login, self.contract), [])
        login['uia']['controls'].pop()
        self.assertIn('Salir', ' '.join(target_errors(login, self.contract)))

    def test_inspection_summary_drops_screenshot_bytes(self):
        source = dict(status='measured', screenshot='data:image/png;base64,secret',
                      window=dict(title='Login'), uia=dict(detail='', controls=[
                          dict(type='Button', name='INGRESAR', rect=[1,2,3,4],
                               rect_client_px=[5,6,70,30])]))
        result = inspection_summary(source)
        self.assertNotIn('screenshot', result)
        self.assertEqual(result['controls'], [dict(
            type='Button', name='INGRESAR', rect_client_px=[5,6,70,30])])


if __name__ == '__main__':
    unittest.main()

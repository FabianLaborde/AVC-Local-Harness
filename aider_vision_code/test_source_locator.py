import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import external_edit
from source_locator import SourceLocationError, locate_source


class SourceLocatorTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def test_dotnet_filename_evidence_selects_login_form(self):
        (self.root / 'app.csproj').write_text('<Project />', encoding='utf8')
        (self.root / 'LoginForm.cs').write_text('class LoginForm { string action = "INGRESAR"; }', encoding='utf8')
        (self.root / 'ReportsForm.cs').write_text('class ReportsForm { }', encoding='utf8')

        selected, evidence = locate_source(self.root, 'Redondea los controles de LoginForm')

        self.assertEqual(selected, 'LoginForm.cs')
        self.assertGreaterEqual(evidence['margin'], evidence['required_margin'])

    def test_unique_content_locates_python_source(self):
        (self.root / 'contact.py').write_text('submit_label = "ENVIAR CONTACTO"', encoding='utf8')
        (self.root / 'other.py').write_text('title = "Panel"', encoding='utf8')

        selected, evidence = locate_source(self.root, 'Cambia ENVIAR CONTACTO por Guardar')

        self.assertEqual(selected, 'contact.py')
        self.assertIn('enviar', evidence['query_tokens'])

    def test_quoted_literal_locates_web_source(self):
        (self.root / 'settings.html').write_text('<button>Guardar cambios</button>', encoding='utf8')
        (self.root / 'index.html').write_text('<h1>Inicio</h1>', encoding='utf8')

        selected, evidence = locate_source(self.root, 'Cambia "Guardar cambios" a Confirmar')

        self.assertEqual(selected, 'settings.html')
        self.assertIn('literal citado: guardar cambios', evidence['candidates'][0]['reasons'])

    def test_two_unquoted_ui_literals_outweigh_generic_content(self):
        (self.root / 'app.csproj').write_text('<Project />', encoding='utf8')
        (self.root / 'LoginForm.cs').write_text(
            'CreateButton("INGRESAR"); CreateButton("Salir");', encoding='utf8')
        (self.root / 'documentation.py').write_text(
            '# completo comportamiento continuo eventos dimensiones posicion\nvalue = 1',
            encoding='utf8')
        (self.root / 'other.cs').write_text(
            'var enter = "INGRESAR"; // completo continuo eventos', encoding='utf8')

        selected, evidence = locate_source(
            self.root,
            'Redondea las esquinas de los botones INGRESAR y Salir. Conserva dimensiones, eventos y comportamiento.')

        self.assertEqual(selected, 'LoginForm.cs')
        self.assertIn('literales de interfaz: ingresar, salir',
                      evidence['candidates'][0]['reasons'])
        self.assertTrue(evidence['candidates'][0]['strong'])

    def test_one_common_unquoted_literal_stays_ambiguous(self):
        for folder in ('one', 'two'):
            path = self.root / folder
            path.mkdir()
            (path / 'screen.py').write_text('label = "INGRESAR"', encoding='utf8')

        with self.assertRaises(SourceLocationError) as caught:
            locate_source(self.root, 'Cambia INGRESAR')

        self.assertFalse(caught.exception.evidence['candidates'][0]['strong'])
        self.assertFalse(caught.exception.evidence['candidates'][1]['strong'])

    def test_tie_fails_closed_with_relative_candidates(self):
        for folder in ('one', 'two'):
            path = self.root / folder
            path.mkdir()
            (path / 'login.py').write_text('title = "login"', encoding='utf8')
        with self.assertRaises(SourceLocationError) as caught:
            locate_source(self.root, 'Cambia login')
        self.assertIn('one/login.py', str(caught.exception))
        self.assertIn('two/login.py', str(caught.exception))

    def test_no_evidence_and_excluded_sources_fail_closed(self):
        (self.root / 'node_modules').mkdir()
        (self.root / 'node_modules' / 'special.js').write_text('specialTarget()', encoding='utf8')
        (self.root / 'outputs').mkdir()
        (self.root / 'outputs' / 'special.html').write_text('specialTarget', encoding='utf8')
        (self.root / 'password.py').write_text('specialTarget = True', encoding='utf8')
        with self.assertRaises(SourceLocationError) as caught:
            locate_source(self.root, 'Ajusta specialTarget')
        self.assertEqual(caught.exception.evidence['candidates'], [])

    def test_explicit_filename_keeps_priority_over_locator(self):
        (self.root / 'app.py').write_text('value = 1', encoding='utf8')
        api = SimpleNamespace(
            REQUESTED_FILE_RE=re.compile(r'([\w.-]+\.py)'),
            requested_analysis_files=Mock(return_value=['app.py']),
            CANCEL=Mock(is_set=Mock(return_value=True)),
            ROOT=self.root, audit=Mock(), log=Mock())
        with patch.object(external_edit, 'locate_source') as locator:
            with self.assertRaisesRegex(ValueError, 'cancelada'):
                external_edit.prepare(api, self.root, 'Cambia app.py', 'model')
        locator.assert_not_called()

    def test_automatic_location_is_audited_before_editor(self):
        (self.root / 'contact.py').write_text('submit_label = "ENVIAR CONTACTO"', encoding='utf8')
        (self.root / 'other.py').write_text('title = "Panel"', encoding='utf8')
        api = SimpleNamespace(
            REQUESTED_FILE_RE=re.compile(r'([\w.-]+\.unknown)'),
            CANCEL=Mock(is_set=Mock(return_value=True)),
            ROOT=self.root, audit=Mock(), log=Mock())

        with self.assertRaisesRegex(ValueError, 'cancelada'):
            external_edit.prepare(api, self.root, 'Cambia ENVIAR CONTACTO por Guardar', 'model')

        self.assertEqual(api.audit.call_args.args[0], 'source_auto_located')
        self.assertEqual(api.audit.call_args.kwargs['selected'], 'contact.py')
        self.assertIn('contact.py', api.log.call_args.args[0])

    def test_ambiguous_automatic_location_never_starts_editor(self):
        for folder in ('one', 'two'):
            path = self.root / folder
            path.mkdir()
            (path / 'login.py').write_text('title = "login"', encoding='utf8')
        api = SimpleNamespace(
            REQUESTED_FILE_RE=re.compile(r'([\w.-]+\.unknown)'),
            run_process=Mock(), audit=Mock(), log=Mock())
        with self.assertRaises(SourceLocationError):
            external_edit.prepare(api, self.root, 'Cambia login', 'model')
        api.run_process.assert_not_called()
        api.audit.assert_called_once()


if __name__ == '__main__':
    unittest.main()

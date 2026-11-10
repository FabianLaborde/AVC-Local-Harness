import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from intent_shadow import (diagnose_sources, promotable_initial_satisfaction,
                           run_shadow_diagnostic)


class IntentShadowTests(unittest.TestCase):
    def test_diagnoses_real_sources_without_writing_them(self):
        sources = {'MainWindow.xaml':
            '<Window><Grid><TextBlock Text="Datos" HorizontalAlignment="Right"/>'
            '</Grid></Window>', 'MainWindow.xaml.cs': 'class MainWindow {}'}
        before = dict(sources)
        result = diagnose_sources(sources, 'Alineá el título "Datos" a la derecha.')
        self.assertEqual('satisfied', result['resolution']['status'])
        self.assertFalse(result['authoritative'])
        self.assertEqual(before, sources)

    def test_invalid_xaml_is_recorded_and_never_claimed_as_failure(self):
        result = diagnose_sources({'Broken.xaml': '<Window>'},
                                  'Alineá el título a la derecha.')
        self.assertEqual('not_verifiable', result['resolution']['status'])
        self.assertEqual(1, len(result['source_errors']))

    def test_persists_bounded_json_and_audits_non_authoritative_status(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            api = Mock()
            result = run_shadow_diagnostic(api,
                {'MainWindow.xaml': '<Button Content="Enviar" Background="Red"/>'},
                'Cambiá el fondo del botón "Enviar" a verde.', folder)
            artifact = json.loads((folder/'intent_shadow.json').read_text(encoding='utf-8'))
            self.assertEqual('requires_change', result['resolution']['status'])
            self.assertFalse(artifact['authoritative'])
            api.audit.assert_called_once_with('intent_shadow',
                status='requires_change', contract_status='supported', changes=1,
                source_errors=0, evidence=str(folder/'intent_shadow.json'))

    def test_any_shadow_failure_is_contained(self):
        api = Mock()
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp) / 'missing'
            result = run_shadow_diagnostic(api, {}, 'Aplicá un tema oscuro.', folder)
        self.assertIn('error', result)
        api.log.assert_called_once()
        api.audit.assert_called_once()

    def test_exact_literal_direct_match_is_promotable(self):
        payload = diagnose_sources(
            {'MainWindow.xaml':
             '<Grid><TextBlock Text="Datos" HorizontalAlignment="Right"/>'
             '<Button Content="Otro" HorizontalAlignment="Right"/></Grid>'},
            'Alineá el título "Datos" a la derecha. Conservá todo lo demás.')
        fact = promotable_initial_satisfaction(payload)
        self.assertEqual('intent_evidence_preflight', fact['decision_source'])
        self.assertEqual('Datos', fact['facts'][0]['label'])

    def test_literal_control_dimensions_are_promotable(self):
        payload = diagnose_sources(
            {'MainWindow.xaml':
             '<Grid><Button Content="Enviar" Width="200" Height="50"/></Grid>'},
            'Redimensioná el botón "Enviar" a 200 por 50.')
        fact = promotable_initial_satisfaction(payload)
        self.assertEqual('element_bounds', fact['facts'][0]['property'])
        self.assertEqual({'width': 200, 'height': 50}, fact['facts'][0]['expected'])

    def test_control_dimensions_reject_duplicate_truncated_and_postcondition(self):
        instruction = 'Redimensioná el botón "Enviar" a 200 por 50.'
        duplicate = diagnose_sources(
            {'MainWindow.xaml':
             '<Grid><Button Content="Enviar" Width="200" Height="50"/>'
             '<Button Content="Enviar" Width="200" Height="50"/></Grid>'},
            instruction)
        self.assertIsNone(promotable_initial_satisfaction(duplicate))

        truncated = diagnose_sources(
            {'MainWindow.xaml': '<Grid>' + ''.join(
                f'<Button Content="Otro {index}"/>' for index in range(2105)) +
             '<Button Content="Enviar" Width="200" Height="50"/></Grid>'},
            instruction)
        self.assertEqual('target_not_found_in_truncated_source',
                         truncated['resolution']['results'][0]['code'])
        self.assertIsNone(promotable_initial_satisfaction(truncated))

        postcondition = diagnose_sources(
            {'MainWindow.xaml':
             '<Button Content="Enviar" Width="200" Height="50"/>'},
            instruction + ' Dejalo visible sin desplazamiento.')
        self.assertTrue(postcondition['contract']['postconditions'])
        self.assertIsNone(promotable_initial_satisfaction(postcondition))

    def test_promotion_rejects_missing_label_postcondition_and_source_error(self):
        no_label = diagnose_sources(
            {'MainWindow.xaml': '<TextBlock HorizontalAlignment="Right"/>'},
            'Alineá el título a la derecha.')
        self.assertIsNone(promotable_initial_satisfaction(no_label))
        postcondition = diagnose_sources(
            {'MainWindow.xaml':
             '<TextBlock Text="Datos" HorizontalAlignment="Right"/>'},
            'Alineá el título "Datos" a la derecha y dejá todo visible sin desplazamiento.')
        self.assertIsNone(promotable_initial_satisfaction(postcondition))
        source_error = diagnose_sources(
            {'MainWindow.xaml': '<Window>',
             'Other.xaml': '<TextBlock Text="Datos" HorizontalAlignment="Right"/>'},
            'Alineá el título "Datos" a la derecha.')
        self.assertIsNone(promotable_initial_satisfaction(source_error))

    def test_promotion_rejects_non_direct_or_unverified_properties(self):
        theme = diagnose_sources({'MainWindow.xaml': '<Window Background="#121212"/>'},
                                 'Aplicá un tema oscuro.')
        self.assertIsNone(promotable_initial_satisfaction(theme))
        resource = diagnose_sources(
            {'MainWindow.xaml':
             '<Button Content="Enviar" Background="{StaticResource Primary}"/>'},
            'Cambiá el fondo del botón "Enviar" a verde.')
        self.assertIsNone(promotable_initial_satisfaction(resource))


if __name__ == '__main__':
    unittest.main()

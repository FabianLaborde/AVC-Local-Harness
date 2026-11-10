import tempfile
import unittest
from pathlib import Path

from intent_contract import derive_intent_contract
from intent_evidence import resolve_intent_contract
from xaml_structure import inspect_xaml


def inventory(xaml):
    handle = tempfile.NamedTemporaryFile(suffix='.xaml', delete=False)
    path = Path(handle.name)
    handle.close()
    try:
        path.write_text(xaml, encoding='utf-8')
        return inspect_xaml(path)
    finally:
        path.unlink(missing_ok=True)


def window(body='', **attrs):
    properties = ' '.join(f'{key}="{value}"' for key, value in attrs.items())
    return (f'<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation" '
            f'{properties}><Grid>{body}</Grid></Window>')


class IntentEvidenceTests(unittest.TestCase):
    def test_login_title_alignment_already_satisfied(self):
        data = inventory(window(
            '<TextBlock Text="Iniciar sesión" HorizontalAlignment="Right"/>'))
        contract = derive_intent_contract(
            'Alineá el título "Iniciar sesión" a la derecha.')
        result = resolve_intent_contract(contract, data)
        self.assertEqual('satisfied', result['status'])
        self.assertEqual('Right', result['results'][0]['declared'])

    def test_contact_title_alignment_requires_change(self):
        data = inventory(window(
            '<TextBlock Text="Datos de contacto" HorizontalAlignment="Left"/>'))
        contract = derive_intent_contract(
            'Alineá el título "Datos de contacto" a la derecha.')
        result = resolve_intent_contract(contract, data)
        self.assertEqual('requires_change', result['status'])
        self.assertEqual('right', result['results'][0]['expected'])

    def test_duplicate_literal_title_is_ambiguous(self):
        data = inventory(window(
            '<StackPanel><TextBlock Text="Datos" HorizontalAlignment="Left"/>'
            '<Label Content="Datos" HorizontalAlignment="Left"/></StackPanel>'))
        contract = derive_intent_contract('Alineá el título "Datos" a la derecha.')
        result = resolve_intent_contract(contract, data)
        self.assertEqual('ambiguous', result['status'])
        self.assertEqual(2, len(result['results'][0]['matches']))

    def test_truncated_inventory_cannot_prove_target_uniqueness(self):
        data = inventory(window(
            '<TextBlock Text="Datos" HorizontalAlignment="Right"/>'))
        data['truncated'] = True
        contract = derive_intent_contract('Alineá el título "Datos" a la derecha.')
        result = resolve_intent_contract(contract, data)
        self.assertEqual('not_verifiable', result['status'])
        self.assertEqual('target_uniqueness_truncated', result['results'][0]['code'])

    def test_missing_direct_property_is_unknown_not_requires_change(self):
        data = inventory(window('<TextBlock Text="Datos"/>'))
        contract = derive_intent_contract('Alineá el título "Datos" a la derecha.')
        result = resolve_intent_contract(contract, data)
        self.assertEqual('not_verifiable', result['status'])
        self.assertEqual('effective_value_unknown', result['results'][0]['code'])

    def test_contact_button_direct_color(self):
        data = inventory(window('<Button Content="Enviar" Background="#008000"/>'))
        contract = derive_intent_contract(
            'Cambiá el fondo del botón "Enviar" a verde.')
        result = resolve_intent_contract(contract, data)
        self.assertEqual('satisfied', result['status'])
        self.assertEqual('#008000', result['results'][0]['actual'])

    def test_resource_color_is_not_evaluated(self):
        data = inventory(window(
            '<Button Content="Enviar" Background="{StaticResource Primary}"/>'))
        contract = derive_intent_contract(
            'Cambiá el fondo del botón "Enviar" a verde.')
        result = resolve_intent_contract(contract, data)
        self.assertEqual('not_verifiable', result['status'])

    def test_declared_initial_color_mismatch_is_ambiguous(self):
        data = inventory(window('<Button Content="Enviar" Background="Blue"/>'))
        contract = derive_intent_contract(
            'Cambiá el fondo negro del botón "Enviar" por gris claro.')
        result = resolve_intent_contract(contract, data)
        self.assertEqual('ambiguous', result['status'])
        self.assertEqual('declared_initial_differs', result['results'][0]['code'])
        self.assertFalse(result['results'][0]['declared_initial_matches_request'])

    def test_sisbascula_window_dimensions_are_direct_evidence(self):
        data = inventory(window(Width='1000', Height='560'))
        contract = derive_intent_contract('Redimensioná la ventana a 1000 por 560.')
        result = resolve_intent_contract(contract, data)
        self.assertEqual('satisfied', result['status'])
        self.assertEqual({'width': 1000.0, 'height': 560.0},
                         result['results'][0]['actual'])

    def test_literal_button_dimensions_are_direct_evidence(self):
        data = inventory(window(
            '<Button Content="Enviar" Width="200" Height="50"/>'))
        contract = derive_intent_contract(
            'Redimensioná el botón "Enviar" a 200 por 50.')
        result = resolve_intent_contract(contract, data)
        self.assertEqual('satisfied', result['status'])
        self.assertEqual('element_bounds', result['results'][0]['change']['property'])
        self.assertEqual({'width': 200.0, 'height': 50.0},
                         result['results'][0]['actual'])

    def test_literal_button_resource_size_is_not_evaluated(self):
        data = inventory(window(
            '<Button Content="Enviar" Width="{StaticResource ButtonWidth}" Height="50"/>'))
        contract = derive_intent_contract(
            'Redimensioná el botón "Enviar" a 200 por 50.')
        result = resolve_intent_contract(contract, data)
        self.assertEqual('not_verifiable', result['status'])
        self.assertEqual('effective_value_unknown', result['results'][0]['code'])

    def test_non_numeric_window_size_is_not_evaluated(self):
        data = inventory(window(Width='Auto', Height='560'))
        contract = derive_intent_contract('Redimensioná la ventana a 1000 por 560.')
        self.assertEqual('not_verifiable',
                         resolve_intent_contract(contract, data)['status'])

    def test_theme_fit_and_relayout_require_other_evidence(self):
        data = inventory(window('<Button Content="Enviar"/>'))
        theme = derive_intent_contract('Aplicá un tema oscuro.')
        fit = derive_intent_contract('Achicá la ventana al contenido más un margen.')
        layout = derive_intent_contract(
            'Redistribuí los controles en filas y columnas.')
        for contract in (theme, fit, layout):
            self.assertEqual('not_verifiable',
                             resolve_intent_contract(contract, data)['status'])

    def test_missing_target_is_not_claimed_as_noncompliance(self):
        data = inventory(window('<Button Content="Cancelar" Background="Red"/>'))
        contract = derive_intent_contract(
            'Cambiá el fondo del botón "Enviar" a verde.')
        result = resolve_intent_contract(contract, data)
        self.assertEqual('not_verifiable', result['status'])
        self.assertEqual('target_not_found', result['results'][0]['code'])

    def test_ambiguous_contract_is_not_resolved(self):
        data = inventory(window('<Button Content="Enviar"/>'))
        contract = derive_intent_contract('Movelo a la derecha.')
        result = resolve_intent_contract(contract, data)
        self.assertEqual('ambiguous', result['status'])
        self.assertFalse(result['results'])

    def test_no_scroll_postcondition_prevents_false_satisfied_result(self):
        data = inventory(window(
            '<TextBlock Text="Datos" HorizontalAlignment="Right"/>'))
        contract = derive_intent_contract(
            'Alineá el título "Datos" a la derecha y dejá todo visible sin desplazamiento.')
        result = resolve_intent_contract(contract, data)
        self.assertEqual('not_verifiable', result['status'])
        self.assertEqual({'satisfied', 'not_verifiable'},
                         {item['status'] for item in result['results']})


if __name__ == '__main__':
    unittest.main()

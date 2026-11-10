import json
import unittest

from intent_contract import derive_intent_contract


class IntentContractTests(unittest.TestCase):
    def test_login_alignment_preserves_declared_invariants(self):
        result = derive_intent_contract(
            'Alineá el título "Iniciar sesión" a la derecha. '
            'Conservá dimensiones, colores, campos y funcionamiento.')
        self.assertEqual('supported', result['status'])
        change = result['changes'][0]
        self.assertEqual(('align', 'title', 'Iniciar sesión', 'right'),
                         (change['kind'], change['target']['role'],
                          change['target']['label'], change['expected']['after']))
        self.assertEqual(['behavior', 'colors', 'content', 'dimensions'],
                         sorted(item['property'] for item in result['invariants']))
        self.assertIn('interaction_tests', result['required_evidence'])

    def test_contact_color_records_only_explicit_before_and_after(self):
        result = derive_intent_contract(
            'Cambiá el color del título "Datos de contacto" de negro a azul; '
            'mantené todo lo demás.')
        change = result['changes'][0]
        self.assertEqual('set_color', change['kind'])
        self.assertEqual('foreground', change['property'])
        self.assertEqual('#000000', change['expected']['before'])
        self.assertEqual('#0000FF', change['expected']['after'])
        self.assertEqual('everything_else', result['invariants'][0]['property'])

    def test_historical_calculator_color_request_is_normalized(self):
        result = derive_intent_contract(
            'Cambiá únicamente el fondo negro de los botones por gris claro. '
            'Conservá el resto.')
        self.assertEqual('supported', result['status'])
        change = result['changes'][0]
        self.assertEqual(('button', 'background', '#000000', '#D3D3D3'),
                         (change['target']['role'], change['property'],
                          change['expected']['before'], change['expected']['after']))

    def test_sisbascula_relayout_and_no_scroll(self):
        result = derive_intent_contract(
            'Redistribuí los controles en filas y columnas para que todo quede '
            'visible sin desplazamiento. Conservá sus tamaños y textos.')
        self.assertEqual('supported', result['status'])
        self.assertEqual('relayout', result['changes'][0]['kind'])
        self.assertEqual('grid', result['changes'][0]['expected']['after'])
        self.assertEqual('none', result['postconditions'][0]['expected'])
        self.assertIn('accessible_control_bounds', result['required_evidence'])

    def test_dark_theme_is_global_without_inventing_controls(self):
        result = derive_intent_contract(
            'Aplicá un tema oscuro coherente y conservá la distribución y el contenido.')
        self.assertEqual('supported', result['status'])
        self.assertEqual({'role': 'interface', 'label': None},
                         result['changes'][0]['target'])
        self.assertEqual('dark', result['changes'][0]['expected']['after'])

    def test_explicit_window_dimensions_are_typed(self):
        result = derive_intent_contract(
            'Redimensioná la ventana a 1000 por 560 y mantené el contenido.')
        self.assertEqual('supported', result['status'])
        self.assertEqual({'width': 1000, 'height': 560},
                         result['changes'][0]['expected']['after'])
        json.dumps(result)

    def test_explicit_literal_control_dimensions_are_typed(self):
        result = derive_intent_contract(
            'Redimensioná el botón "Enviar" a 200 por 50 y conservá el resto.')
        self.assertEqual('supported', result['status'])
        change = result['changes'][0]
        self.assertEqual({'role': 'button', 'label': 'Enviar'}, change['target'])
        self.assertEqual('element_bounds', change['property'])
        self.assertEqual({'width': 200, 'height': 50}, change['expected']['after'])

    def test_control_dimensions_without_literal_target_are_ambiguous(self):
        result = derive_intent_contract('Redimensioná el botón a 200 por 50.')
        self.assertEqual('ambiguous', result['status'])
        self.assertEqual('size_target_label', result['ambiguities'][0]['code'])

    def test_fit_window_to_content_does_not_invent_pixels(self):
        result = derive_intent_contract(
            'Achicá la ventana al contenido más un margen hacia los cuatro costados.')
        self.assertEqual('supported', result['status'])
        self.assertEqual('content_with_margin', result['changes'][0]['expected']['after'])
        self.assertNotIn('width', result['changes'][0]['expected'])

    def test_unknown_color_is_ambiguous_not_guessed(self):
        result = derive_intent_contract(
            'Cambiá el fondo de la ventana a un tono elegante parecido al amanecer.')
        self.assertEqual('ambiguous', result['status'])
        self.assertFalse(result['changes'])
        self.assertEqual('color_value', result['ambiguities'][0]['code'])

    def test_color_or_direction_inside_quoted_label_is_not_a_requested_value(self):
        color = derive_intent_contract('Cambiá el color del botón "Azul".')
        self.assertEqual('ambiguous', color['status'])
        self.assertEqual('color_value', color['ambiguities'][0]['code'])
        direction = derive_intent_contract('Alineá el botón "Derecha".')
        self.assertEqual('ambiguous', direction['status'])
        self.assertEqual('alignment_value', direction['ambiguities'][0]['code'])

    def test_unrecognized_compound_color_is_not_reduced_to_known_prefix(self):
        result = derive_intent_contract(
            'Cambiá el fondo de la ventana a azul petróleo.')
        self.assertEqual('ambiguous', result['status'])
        self.assertEqual('color_value', result['ambiguities'][0]['code'])

    def test_alignment_without_target_is_ambiguous(self):
        result = derive_intent_contract('Movelo a la derecha.')
        self.assertEqual('ambiguous', result['status'])
        self.assertEqual('alignment_target', result['ambiguities'][0]['code'])

    def test_conflicting_alignment_is_ambiguous(self):
        result = derive_intent_contract(
            'Alineá el título a la izquierda y a la derecha.')
        self.assertEqual('ambiguous', result['status'])
        self.assertEqual('alignment_value', result['ambiguities'][0]['code'])

    def test_nonvisual_behavior_request_is_unsupported(self):
        result = derive_intent_contract('Validá la contraseña contra la base de datos.')
        self.assertEqual('unsupported', result['status'])
        self.assertFalse(result['changes'])

    def test_empty_instruction_is_ambiguous(self):
        result = derive_intent_contract('  ')
        self.assertEqual('ambiguous', result['status'])
        self.assertEqual('empty_instruction', result['ambiguities'][0]['code'])


if __name__ == '__main__':
    unittest.main()

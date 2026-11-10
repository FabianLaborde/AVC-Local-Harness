"""Regressions for geometry lost by control-only inventories and misleading completion."""
import json
import unittest
import urllib.error
import base64
from io import BytesIO
from unittest.mock import Mock, patch
from PIL import Image

from vision_semantica import analyze, _differences, _inventory, _layout_review, _request, _comparison_image, _contrast_profile, _check_shape_evidence, _check_geometry_evidence
from visual_workflow import _proposal


AXES = ('distribucion', 'proporciones', 'espaciado')
INVENTORY = {'elementos': [{'etiqueta': '7', 'tipo': 'boton', 'apariencia': 'blanco'}],
             'composicion': {'distribucion': 'Teclado en la zona inferior',
                             'proporciones': 'Espacio amplio sobre el teclado',
                             'espaciado': 'Botones separados'}}


def review(state='sin_diferencia_observable'):
    return {axis: {'estado': state, 'evidencia': 'Comparación cualitativa de ambas imágenes: ' + axis}
            for axis in AXES}


def difference(category='color', scope='control'):
    return {'elemento': 'Teclado' if scope == 'global' else '7', 'tipo': category, 'ambito': scope,
            'actual': 'Ocupa casi toda la altura', 'referencia': 'Concentrado en la parte inferior',
            'evidencia': 'En las imágenes el espacio libre superior es distinto', 'certeza': 'media'}


def replies(*values):
    # A successful geometry pass is followed by an independent control comparison.
    return Mock(side_effect=[{'message': {'content': json.dumps(value)}}
                             for value in (*values, {'diferencias': []})])


class GeometryComparisonTests(unittest.TestCase):
    def test_reported_percentages_do_not_become_upper_space_measurements(self):
        geometry = review()
        geometry['distribucion'] = {'estado': 'diferencia_observada',
                                    'evidencia': 'Franja superior vacía: 2.1% frente a 29.2%.'}
        row = difference('distribucion', 'global')
        row.update(elemento='espacio superior', actual='vacío 2.1%', referencia='vacío 29.2%')
        post = replies(INVENTORY, INVENTORY, {'revision_geometrica': geometry, 'diferencias': [row]},
                       {'diferencias': [difference('color')]})
        result = analyze(post, 'model', 'a', 'b')
        self.assertEqual(result['estado'], 'COMPARACION_PARCIAL')
        checked = result['revision_geometrica']['distribucion']
        self.assertEqual(checked['estado'], 'no_verificable')
        self.assertEqual(checked['evidencia_modelo'], geometry['distribucion']['evidencia'])
        self.assertFalse(result['diferencias'][0]['utilizable_en_propuesta'])
        proposal = _proposal(result['diferencias'], {}, True, result['revision_geometrica'])
        self.assertNotIn('vacío 2.1%', proposal)
        self.assertIn('7 — color', proposal)

    def test_matching_grid_does_not_confirm_proportions_or_mutate_original(self):
        original = review()
        original['proporciones']['evidencia'] = 'Mismo número de filas y columnas; proporción similar.'
        checked, _ = _check_geometry_evidence(original, [])
        self.assertEqual(checked['proporciones']['estado'], 'no_verificable')
        self.assertEqual(original['proporciones']['estado'], 'sin_diferencia_observable')

    def test_independent_qualitative_geometry_remains_reviewable(self):
        original = review('diferencia_observada')
        rows = _differences({'diferencias': [difference('distribucion', 'global')]})
        checked, checked_rows = _check_geometry_evidence(original, rows)
        self.assertEqual(checked, original)
        self.assertEqual(checked_rows, rows)

    def test_percent_button_symbol_is_not_a_geometry_measurement(self):
        row = difference('color')
        row.update(elemento='%', actual='negro', referencia='gris', evidencia='Botón % visible')
        _, rows = _check_geometry_evidence(review(), _differences({'diferencias': [row]}))
        self.assertNotIn('utilizable_en_propuesta', rows[0])

    def test_proposal_reports_position_of_measured_band_separately(self):
        observations = {'actual': {'estado': 'medido', 'altura_relativa': .021,
                                   'inicio_relativo': .342, 'fin_relativo': .363}}
        proposal = _proposal(_differences({'diferencias': [difference()]}), {}, True,
                             pixel_observations=observations)
        self.assertIn('2.1% de la altura. Ubicación vertical: 34.2% a 36.3%', proposal)
        self.assertIn('no identifica controles', proposal)

    def setUp(self):
        self.compose = self.enterContext(patch('vision_semantica._comparison_image', return_value='labelled-view'))
        self.observations = self.enterContext(patch('vision_semantica._image_observations', return_value={}))

    def test_comparison_receives_labelled_images_and_preserves_composition(self):
        post = replies(INVENTORY, INVENTORY, {'revision_geometrica': review(), 'diferencias': []})
        result = analyze(post, 'model', 'current-image', 'reference-image')
        self.assertTrue(result['comparacion_valida'])
        self.assertEqual(result['actual']['composicion'], INVENTORY['composicion'])
        comparison = post.call_args_list[2].args[1]
        self.assertEqual(comparison['messages'][0]['images'], ['labelled-view'])
        self.assertTrue(all(call.args == ('current-image', 'reference-image') for call in self.compose.call_args_list))
        self.assertIs(comparison['think'], False)
        self.assertEqual(comparison['options']['num_ctx'], 8192)

    def test_geometry_precedes_appearance_and_accented_types_are_recognized(self):
        rows = _differences({'diferencias': [difference(), difference('Tamaño', 'global'),
                                           difference('Distribución', 'global')]})
        self.assertEqual([row['tipo'] for row in rows], ['tamano', 'distribucion', 'color'])
        self.assertEqual(rows[0]['ambito'], 'global')

    def test_equal_descriptions_are_not_proposed_as_differences(self):
        row = difference()
        row.update(actual='  Verde con texto negro ', referencia='verde  con texto NEGRO')
        self.assertEqual(_differences({'diferencias': [row]}), [])

    def test_missing_geometry_retries_with_images_and_can_recover(self):
        post = replies(INVENTORY, INVENTORY, {'diferencias': []},
                       {'revision_geometrica': review(), 'diferencias': []})
        result = analyze(post, 'model', 'a', 'b')
        self.assertEqual(result['estado'], 'COMPARACION_REINTENTADA')
        self.assertTrue(result['comparacion_valida'])
        self.assertEqual(post.call_count, 5)
        for call in post.call_args_list[2:4]:
            self.assertEqual(call.args[1]['messages'][0]['images'], ['labelled-view'])

    def test_comparison_sheet_keeps_order_and_source_aspect_ratios(self):
        def encoded(size, color):
            buffer = BytesIO()
            Image.new('RGB', size, color).save(buffer, format='PNG')
            return base64.b64encode(buffer.getvalue()).decode()

        sheet = Image.open(BytesIO(base64.b64decode(_comparison_image(
            encoded((100, 200), 'red'), encoded((200, 200), 'blue')))))
        self.assertEqual(sheet.size, (1008, 696))
        self.assertEqual(sheet.getpixel((16, 40)), (255, 0, 0))
        self.assertEqual(sheet.getpixel((335, 679)), (255, 0, 0))
        self.assertNotEqual(sheet.getpixel((336, 679)), (255, 0, 0))
        self.assertEqual(sheet.getpixel((352, 40)), (0, 0, 255))
        self.assertEqual(sheet.getpixel((991, 679)), (0, 0, 255))

    def test_control_only_report_cannot_be_marked_complete_or_produce_proposal(self):
        post = replies(INVENTORY, INVENTORY, {'diferencias': [difference()]}, {'diferencias': [difference()]})
        result = analyze(post, 'model', 'a', 'b')
        self.assertFalse(result['comparacion_valida'])
        self.assertEqual(result['revision_geometrica'], {})
        self.assertEqual(_proposal(result['diferencias'], {}, result['comparacion_valida']), '')

    def test_claimed_geometry_difference_requires_a_geometry_row(self):
        answer = {'revision_geometrica': review('diferencia_observada'), 'diferencias': [difference()]}
        result = analyze(replies(INVENTORY, INVENTORY, answer, answer), 'model', 'a', 'b')
        self.assertFalse(result['comparacion_valida'])

    def test_uncertain_geometry_is_partial_instead_of_silently_complete(self):
        answer = {'revision_geometrica': review('no_verificable'), 'diferencias': [difference()]}
        result = analyze(replies(INVENTORY, INVENTORY, answer), 'model', 'a', 'b')
        self.assertTrue(result['comparacion_valida'])
        self.assertEqual(result['estado'], 'COMPARACION_PARCIAL')
        self.assertEqual(result['revision_geometrica']['espaciado']['estado'], 'no_verificable')

    def test_incomplete_axis_or_missing_evidence_is_rejected(self):
        for broken in ({'distribucion': review()['distribucion']},
                       {axis: {'estado': 'sin_diferencia_observable', 'evidencia': ''} for axis in AXES}):
            with self.subTest(broken=broken), self.assertRaises(ValueError):
                _layout_review({'revision_geometrica': broken})

    def test_geometry_evidence_is_in_proposal_before_specific_changes(self):
        proposal = _proposal([difference('distribucion', 'global')], {}, True, review('diferencia_observada'))
        self.assertIn('sin medición calibrada', proposal)
        self.assertLess(proposal.index('espaciado:'), proposal.index('1. Teclado'))

    def test_malformed_composition_is_not_used_as_evidence(self):
        self.assertEqual(_inventory({'elementos': [], 'composicion': ['inventado']})['composicion'], {})

    def test_pixel_band_profile_measures_relative_region_without_widget_inference(self):
        heights = []
        for top in (50, 120):
            image = Image.new('RGB', (200, 200), 'white')
            image.paste('black', (50, top, 100, 200))
            buffer = BytesIO()
            image.save(buffer, format='PNG')
            profile = _contrast_profile(base64.b64encode(buffer.getvalue()).decode())
            self.assertEqual(profile['estado'], 'medido')
            self.assertAlmostEqual(profile['altura_relativa'], top / 200, places=2)
            self.assertNotIn('visor', profile)
            heights.append(profile['altura_relativa'])
        self.assertGreater(heights[1], heights[0])

    def test_contrast_profile_abstains_for_texture_without_dominant_background(self):
        image = Image.new('RGB', (100, 100))
        image.putdata([(x, y, (x + y) % 256) for y in range(100) for x in range(100)])
        buffer = BytesIO()
        image.save(buffer, format='PNG')
        self.assertEqual(_contrast_profile(base64.b64encode(buffer.getvalue()).decode())['estado'], 'no_aplicable')

    def test_disagreement_between_pixel_bands_and_model_cannot_be_complete(self):
        self.observations.return_value = {
            'actual': {'estado': 'medido', 'altura_relativa': .02},
            'referencia': {'estado': 'medido', 'altura_relativa': .29}}
        result = analyze(replies(INVENTORY, INVENTORY, {'revision_geometrica': review(), 'diferencias': []}),
                         'model', 'a', 'b')
        self.assertEqual(result['estado'], 'COMPARACION_PARCIAL')
        self.assertEqual(result['revision_geometrica']['distribucion']['estado'], 'no_verificable')
        self.assertEqual(result['observaciones_pixeles'], self.observations.return_value)

    def test_optional_thinking_parameter_can_fall_back_without_losing_images(self):
        received = []

        def post(url, payload, timeout):
            received.append(dict(payload))
            if 'think' in payload:
                raise urllib.error.HTTPError('local', 400, 'unsupported', {}, None)
            return {'message': {'content': '{}'}}

        _request(post, 'model', [{'role': 'user', 'images': ['a', 'b']}])
        self.assertEqual(len(received), 2)
        self.assertNotIn('think', received[1])
        self.assertEqual(received[1]['messages'][0]['images'], ['a', 'b'])

    def test_independent_control_review_preserves_appearance_after_geometry(self):
        geometry = difference('distribucion', 'global')
        color = difference('color')
        post = replies(INVENTORY, INVENTORY,
                       {'revision_geometrica': review('diferencia_observada'), 'diferencias': [geometry]},
                       {'diferencias': [color]})
        result = analyze(post, 'model', 'a', 'b')
        self.assertEqual([row['tipo'] for row in result['diferencias']], ['distribucion', 'color'])
        self.assertEqual(result['revision_controles']['estado'], 'completa')
        self.assertNotIn('images', post.call_args_list[-1].args[1]['messages'][0])
        schema = post.call_args_list[-1].args[1]['format']
        self.assertIn('color', schema['properties']['diferencias']['items']['properties']['tipo']['enum'])

    def test_duplicate_difference_from_both_passes_is_only_reported_once(self):
        row = difference()
        result = analyze(replies(INVENTORY, INVENTORY,
                                 {'revision_geometrica': review(), 'diferencias': [row]},
                                 {'diferencias': [row]}), 'model', 'a', 'b')
        self.assertEqual(len(result['diferencias']), 1)

    def test_failed_control_review_leaves_geometry_partial_and_explicit(self):
        geometry = difference('distribucion', 'global')
        result = analyze(replies(INVENTORY, INVENTORY,
                                 {'revision_geometrica': review('diferencia_observada'), 'diferencias': [geometry]},
                                 {'diferencias': 'invalidas'}, {'error': 'sin lista'}), 'model', 'a', 'b')
        self.assertEqual(result['estado'], 'COMPARACION_PARCIAL')
        self.assertEqual(result['revision_controles']['estado'], 'no_verificable')
        self.assertEqual(len(result['diferencias']), 1)

    def test_only_identical_control_rows_are_not_reported_as_a_successful_review(self):
        same = difference()
        same.update(actual='verde', referencia='verde')
        result = analyze(replies(INVENTORY, INVENTORY,
                                 {'revision_geometrica': review(), 'diferencias': []},
                                 {'diferencias': [same]}, {'diferencias': [same]}), 'model', 'a', 'b')
        self.assertEqual(result['estado'], 'COMPARACION_PARCIAL')
        self.assertEqual(result['revision_controles']['estado'], 'no_verificable')

    def test_shape_cannot_be_inferred_from_an_inventory_missing_shape(self):
        row = _differences({'diferencias': [difference('forma')]})[0]
        rows, count = _check_shape_evidence([row], INVENTORY, INVENTORY)
        self.assertEqual(count, 1)
        self.assertIs(rows[0]['utilizable_en_propuesta'], False)
        self.assertEqual(rows[0]['certeza'], 'baja')
        self.assertEqual(_proposal(rows, {}, True), '')

    def test_explicit_shape_in_both_inventories_remains_a_reviewable_hypothesis(self):
        row = _differences({'diferencias': [difference('forma')]})[0]
        actual = {'elementos': [{'etiqueta': '7', 'apariencia': 'circular blanco'}]}
        reference = {'elementos': [{'etiqueta': '7', 'apariencia': 'cuadrado blanco'}]}
        rows, count = _check_shape_evidence([row], actual, reference)
        self.assertEqual(count, 0)
        self.assertEqual(rows[0]['estado'], 'HIPOTESIS_NO_VERIFICADA')

    def test_ambiguous_control_label_cannot_validate_shape(self):
        row = _differences({'diferencias': [difference('forma')]})[0]
        ambiguous = {'elementos': [{'etiqueta': '7', 'apariencia': 'circular'},
                                   {'etiqueta': '7', 'apariencia': 'cuadrado'}]}
        _, count = _check_shape_evidence([row], ambiguous, ambiguous)
        self.assertEqual(count, 1)


if __name__ == '__main__':
    unittest.main()

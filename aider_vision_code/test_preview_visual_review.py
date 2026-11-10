import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from PIL import Image, ImageDraw

from preview_visual_review import (measure_visual_delta, review_visual_candidate,
                                   _acceptance_contract, _focused_target_images)


def png(draw=None):
    image = Image.new('RGB', (120, 70), 'white')
    if draw:
        draw(ImageDraw.Draw(image))
    output = io.BytesIO()
    image.save(output, format='PNG')
    return output.getvalue()


def inspection(rect=None):
    controls = [] if rect is None else [dict(type='Button', name='Guardar', rect_client_px=rect)]
    return dict(status='measured', window=dict(client_size_px=[120, 70]), controls=controls)


TARGET = dict(required_controls=['Guardar'])


def contract(mode='surface', continuity='not_applicable', symmetry='not_applicable'):
    return dict(summary='Cambio observable', conditions=[dict(
        id='C1', description='El efecto debe estar completo',
        target_scope='all_targets', spatial_mode=mode,
        continuity=continuity, symmetry=symmetry)])


def vision_result(**overrides):
    value = dict(
        status='visible_change', observed_change='Cambio completo',
        target_before='Estado anterior', target_after='Estado nuevo',
        requested_effect_observed='yes', target_effect='Cambio claro',
        unintended_changes=[],
        condition_results=[dict(id='C1', status='fulfilled', evidence='Visible')],
        target_results=[dict(target='Guardar', completeness='complete',
                             boundary='not_applicable', symmetry='not_applicable',
                             defects=[])])
    value.update(overrides)
    return value


class VisualReviewTests(unittest.TestCase):
    def setUp(self):
        self.report = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.api = SimpleNamespace(post_ollama=Mock(), audit=Mock())

    def test_identical_images_fail_without_models(self):
        image = png()
        result = review_visual_candidate(self.api, 'Cambiá Guardar', image, image,
            inspection([10, 10, 80, 45]), inspection([10, 10, 80, 45]), TARGET,
            self.report, 'vision')
        self.assertEqual(result['status'], 'not_fulfilled')
        self.api.post_ollama.assert_not_called()

    def test_tiny_target_change_is_measured_as_insignificant(self):
        before = png()
        after = png(lambda canvas: canvas.rectangle((10, 10, 14, 14), fill='black'))
        metrics = measure_visual_delta(before, after, inspection([10, 10, 80, 45]),
                                       inspection([10, 10, 80, 45]), TARGET)
        self.assertEqual(metrics['gate'], 'not_fulfilled')
        self.assertIn('insignificante', metrics['gate_reason'])

    def test_change_outside_named_target_does_not_pass(self):
        before = png()
        after = png(lambda canvas: canvas.rectangle((90, 50, 115, 65), fill='black'))
        metrics = measure_visual_delta(before, after, inspection([10, 10, 80, 45]),
                                       inspection([10, 10, 80, 45]), TARGET)
        self.assertEqual(metrics['gate'], 'not_fulfilled')
        self.assertEqual(metrics['target_changed_pixels'], 0)

    def test_positive_metrics_vision_and_reasoner_are_all_required(self):
        before = png()
        after = png(lambda canvas: canvas.rectangle((10, 10, 79, 44), fill='blue'))
        vision = vision_result(observed_change='Botón azul',
                               target_before='Botón blanco', target_after='Botón azul')
        reasoning = dict(status='fulfilled', reason='El cambio pedido es visible.',
                         evidence=['Cambio dentro de Guardar'], retry_instruction='')
        self.api.post_ollama.side_effect = [
            dict(response=json.dumps(contract())),
            dict(message=dict(content=json.dumps(vision))),
            dict(response=json.dumps(reasoning))]
        result = review_visual_candidate(self.api, 'Cambiá el color de Guardar', before, after,
            inspection([10, 10, 80, 45]), inspection([10, 10, 80, 45]), TARGET,
            self.report, 'vision', 'reasoner')
        self.assertEqual(result['status'], 'fulfilled')
        self.assertEqual(self.api.post_ollama.call_count, 3)
        self.assertTrue((self.report/'visual_fulfillment.json').is_file())
        vision_payload = self.api.post_ollama.call_args_list[1].args[1]
        self.assertIn('recortes ampliados', vision_payload['messages'][0]['content'])
        reason_payload = self.api.post_ollama.call_args_list[2].args[1]
        self.assertIn('El contrato previo es obligatorio', reason_payload['prompt'])
        self.assertIn('HALLAZGOS OBLIGATORIOS', reason_payload['prompt'])

    def test_malformed_visual_json_is_reobserved_once_with_same_images(self):
        before = png()
        after = png(lambda canvas: canvas.rectangle((10, 10, 79, 44), fill='blue'))
        vision = vision_result(observed_change='Botón azul',
                               target_before='Botón blanco', target_after='Botón azul')
        reasoning = dict(status='fulfilled', reason='El cambio pedido es visible.',
                         evidence=['Cambio dentro de Guardar'], retry_instruction='')
        self.api.post_ollama.side_effect = [
            dict(response=json.dumps(contract())),
            dict(message=dict(content='{"status":"visible_change","observed_change":"cortado')),
            dict(message=dict(content=json.dumps(vision))),
            dict(response=json.dumps(reasoning))]
        result = review_visual_candidate(
            self.api, 'Cambiá el color de Guardar', before, after,
            inspection([10, 10, 80, 45]), inspection([10, 10, 80, 45]), TARGET,
            self.report, 'vision', 'reasoner')
        self.assertEqual(result['status'], 'fulfilled')
        self.assertEqual(self.api.post_ollama.call_count, 4)
        first_payload = self.api.post_ollama.call_args_list[1].args[1]
        retry_payload = self.api.post_ollama.call_args_list[2].args[1]
        self.assertEqual(first_payload['messages'][0]['images'],
                         retry_payload['messages'][0]['images'])
        self.assertIn('Error exacto', retry_payload['messages'][-1]['content'])
        self.assertEqual(first_payload['options']['num_ctx'], 8192)
        self.assertEqual(first_payload['options']['num_predict'], 1800)
        self.assertEqual(retry_payload['options']['num_ctx'], 8192)
        self.assertEqual(retry_payload['options']['num_predict'], 2600)
        events = [call.args[0] for call in self.api.audit.call_args_list]
        self.assertIn('visual_observation_attempt', events)

    def test_two_malformed_visual_responses_remain_blocked(self):
        before = png()
        after = png(lambda canvas: canvas.rectangle((10, 10, 79, 44), fill='blue'))
        self.api.post_ollama.side_effect = [
            dict(response=json.dumps(contract())),
            dict(message=dict(content='{"status":"visible_change"')),
            dict(message=dict(content='{"status":"visible_change","target_after":"'))]
        result = review_visual_candidate(
            self.api, 'Cambiá el color de Guardar', before, after,
            inspection([10, 10, 80, 45]), inspection([10, 10, 80, 45]), TARGET,
            self.report, 'vision', 'reasoner')
        self.assertEqual(result['status'], 'uncertain')
        self.assertIn('dos intentos', result['reason'])
        self.assertEqual(self.api.post_ollama.call_count, 3)
        rejected = [call for call in self.api.audit.call_args_list
                    if call.args and call.args[0] == 'visual_observation_attempt'
                    and call.kwargs.get('status') == 'format_rejected']
        self.assertEqual(len(rejected), 2)
        self.assertNotIn('content', rejected[0].kwargs)

    def test_target_controls_are_cropped_and_enlarged_for_vision(self):
        before = png()
        after = png(lambda canvas: canvas.rectangle((10, 10, 79, 44), fill='blue'))
        metrics = dict(target_rectangles=[[10, 10, 80, 45]])
        focused_before, focused_after, focused = _focused_target_images(
            before, after, metrics)
        self.assertTrue(focused)
        before_size = Image.open(io.BytesIO(focused_before)).size
        after_size = Image.open(io.BytesIO(focused_after)).size
        self.assertEqual(before_size, after_size)
        self.assertGreater(before_size[0], 300)
        self.assertGreater(before_size[1], 100)

    def test_contract_removes_inapplicable_continuity_and_symmetry(self):
        proposed = contract('surface', 'required', 'required')
        proposed['conditions'][0]['target_scope'] = 'any_target'
        self.api.post_ollama.return_value = dict(response=json.dumps(proposed))
        result = _acceptance_contract(
            self.api, 'Cambiá el color', TARGET, 'reasoner')
        condition = result['conditions'][0]
        self.assertEqual(condition['continuity'], 'not_applicable')
        self.assertEqual(condition['symmetry'], 'not_applicable')
        self.assertEqual(condition['target_scope'], 'all_targets')

    def test_ambiguous_changed_states_get_one_semantic_correction(self):
        before = png()
        after = png(lambda canvas: canvas.rectangle((10, 10, 79, 44), fill='blue'))
        ambiguous = vision_result(status='uncertain', observed_change='Cambió la forma',
                                  target_before='Recto', target_after='Redondeado',
                                  requested_effect_observed='no')
        corrected = dict(ambiguous, status='visible_change',
                         requested_effect_observed='yes')
        reasoning = dict(status='fulfilled', reason='El efecto pedido es visible.',
                         evidence=['Antes recto, después redondeado'], retry_instruction='')
        self.api.post_ollama.side_effect = [
            dict(response=json.dumps(contract())),
            dict(message=dict(content=json.dumps(ambiguous))),
            dict(message=dict(content=json.dumps(corrected))),
            dict(response=json.dumps(reasoning))]
        result = review_visual_candidate(
            self.api, 'Redondeá Guardar', before, after,
            inspection([10, 10, 80, 45]), inspection([10, 10, 80, 45]), TARGET,
            self.report, 'vision', 'reasoner')
        self.assertEqual(result['status'], 'fulfilled')
        self.assertEqual(self.api.post_ollama.call_count, 4)
        correction = self.api.post_ollama.call_args_list[2].args[1]['messages'][-1]['content']
        self.assertIn('semánticamente ambigua', correction)

    def test_persisting_vision_contradiction_gets_independent_adjudication(self):
        before = png()
        after = png(lambda canvas: canvas.rectangle((10, 10, 79, 44), fill='blue'))
        ambiguous = vision_result(status='uncertain', observed_change='Cambió la forma',
                                  target_before='Recto', target_after='Redondeado',
                                  requested_effect_observed='no')
        match = dict(match='yes', reason='Recto a redondeado coincide con el pedido.')
        reasoning = dict(status='fulfilled', reason='El efecto pedido es visible.',
                         evidence=['Transición confirmada'], retry_instruction='')
        self.api.post_ollama.side_effect = [
            dict(response=json.dumps(contract())),
            dict(message=dict(content=json.dumps(ambiguous))),
            dict(message=dict(content=json.dumps(ambiguous))),
            dict(response=json.dumps(match)),
            dict(response=json.dumps(reasoning))]
        result = review_visual_candidate(
            self.api, 'Redondeá Guardar', before, after,
            inspection([10, 10, 80, 45]), inspection([10, 10, 80, 45]), TARGET,
            self.report, 'vision', 'reasoner')
        self.assertEqual(result['status'], 'fulfilled')
        self.assertEqual(result['vision']['requested_effect_observed'], 'yes')
        self.assertEqual(result['vision']['semantic_reconciliation']['match'], 'yes')
        self.assertEqual(self.api.post_ollama.call_count, 5)
        self.assertIn('Cambio visual confirmado', result['reason'])
        self.assertIn('píxeles fuera', result['reason'])

    def test_reasoner_cannot_override_negative_metrics(self):
        before = png()
        after = png(lambda canvas: canvas.rectangle((10, 10, 14, 14), fill='blue'))
        vision = vision_result(observed_change='Cambio mínimo',
                               target_effect='Casi imperceptible')
        reasoning = dict(status='fulfilled', reason='Aprobado', evidence=[], retry_instruction='')
        self.api.post_ollama.side_effect = [dict(response=json.dumps(contract())),
                                            dict(message=dict(content=json.dumps(vision))),
                                            dict(response=json.dumps(reasoning))]
        result = review_visual_candidate(self.api, 'Cambiá Guardar', before, after,
            inspection([10, 10, 80, 45]), inspection([10, 10, 80, 45]), TARGET,
            self.report, 'vision', 'reasoner')
        self.assertEqual(result['status'], 'not_fulfilled')

    def test_spatial_profile_records_each_corner_and_side(self):
        before = png()
        after = png(lambda canvas: canvas.rectangle((10, 10, 21, 21), fill='black'))
        metrics = measure_visual_delta(
            before, after, inspection([10, 10, 80, 45]),
            inspection([10, 10, 80, 45]), TARGET)
        profile = metrics['target_spatial_profiles'][0]
        self.assertTrue(profile['corners']['top_left']['observed'])
        self.assertFalse(profile['corners']['top_right']['observed'])
        self.assertFalse(profile['corners']['bottom_left']['observed'])
        self.assertFalse(profile['corners']['bottom_right']['observed'])

    def test_all_corners_contract_blocks_one_corner_even_if_models_approve(self):
        before = png()
        after = png(lambda canvas: canvas.rectangle((10, 10, 21, 21), fill='black'))
        vision = vision_result()
        reasoning = dict(status='fulfilled', reason='Aprobado', evidence=['Cambio'],
                         retry_instruction='')
        self.api.post_ollama.side_effect = [
            dict(response=json.dumps(contract('corners', 'required', 'required'))),
            dict(message=dict(content=json.dumps(vision))),
            dict(response=json.dumps(reasoning))]
        result = review_visual_candidate(
            self.api, 'Cambiá las cuatro esquinas de Guardar', before, after,
            inspection([10, 10, 80, 45]), inspection([10, 10, 80, 45]), TARGET,
            self.report, 'vision', 'reasoner')
        self.assertEqual(result['status'], 'not_fulfilled')
        self.assertTrue(any('bottom_right' in item
                            for item in result['contract_findings']))
        self.assertIn('Corregí el candidato vigente', result['retry_instruction'])

    def test_incomplete_spatial_metrics_use_vision_only_for_diagnosis(self):
        before = png()
        after = png(lambda canvas: canvas.rectangle((10, 10, 21, 21), fill='black'))
        vision = vision_result(
            requested_effect_observed='no', status='visible_change',
            target_results=[dict(target='Guardar', completeness='partial',
                boundary='interrupted', symmetry='no', defects=['Contorno interrumpido'])])
        self.api.post_ollama.side_effect = [
            dict(response=json.dumps(contract('corners', 'required', 'required'))),
            dict(message=dict(content=json.dumps(vision)))]
        result = review_visual_candidate(
            self.api, 'Cambiá las cuatro esquinas de Guardar', before, after,
            inspection([10, 10, 80, 45]), inspection([10, 10, 80, 45]), TARGET,
            self.report, 'vision', 'reasoner')
        self.assertEqual(result['status'], 'not_fulfilled')
        self.assertIsNotNone(result['vision'])
        self.assertEqual(self.api.post_ollama.call_count, 2)
        self.assertIn('cobertura asimétrica', result['reason'])
        self.assertIn('top_right', result['retry_instruction'])
        self.assertIn('contorno no es continuo', result['retry_instruction'])

    def test_broken_contract_model_cannot_hide_negative_metrics(self):
        before = png()
        after = png(lambda canvas: canvas.rectangle((10, 10, 14, 14), fill='black'))
        self.api.post_ollama.return_value = dict(response='{"summary":"cortado"')
        result = review_visual_candidate(
            self.api, 'Cambiá Guardar', before, after,
            inspection([10, 10, 80, 45]), inspection([10, 10, 80, 45]), TARGET,
            self.report, 'vision', 'reasoner')
        self.assertEqual(result['status'], 'not_fulfilled')
        self.assertIn('evidencia determinista', result['reason'])
        self.assertTrue(result['retry_instruction'])

    def test_required_continuity_blocks_interrupted_boundary(self):
        before = png()
        after = png(lambda canvas: canvas.rectangle((10, 10, 79, 44), fill='blue'))
        vision = vision_result(target_results=[dict(
            target='Guardar', completeness='partial', boundary='interrupted',
            symmetry='no', defects=['Contorno interrumpido'])])
        reasoning = dict(status='fulfilled', reason='Aprobado', evidence=['Cambio'],
                         retry_instruction='')
        self.api.post_ollama.side_effect = [
            dict(response=json.dumps(contract('perimeter', 'required', 'required'))),
            dict(message=dict(content=json.dumps(vision))),
            dict(response=json.dumps(reasoning))]
        result = review_visual_candidate(
            self.api, 'Aplicá un contorno completo', before, after,
            inspection([10, 10, 80, 45]), inspection([10, 10, 80, 45]), TARGET,
            self.report, 'vision', 'reasoner')
        self.assertEqual(result['status'], 'not_fulfilled')
        self.assertIn('Guardar: el contorno no es continuo.',
                      result['contract_findings'])

    def test_contradictory_vision_cannot_be_approved(self):
        before = png()
        after = png(lambda canvas: canvas.rectangle((10, 10, 79, 44), fill='blue'))
        vision = vision_result(status='no_visible_change',
                               requested_effect_observed='yes')
        reasoning = dict(status='fulfilled', reason='Aprobado', evidence=['Cambio'],
                         retry_instruction='')
        self.api.post_ollama.side_effect = [
            dict(response=json.dumps(contract())),
            dict(message=dict(content=json.dumps(vision))),
            dict(response=json.dumps(reasoning))]
        result = review_visual_candidate(
            self.api, 'Cambiá Guardar', before, after,
            inspection([10, 10, 80, 45]), inspection([10, 10, 80, 45]), TARGET,
            self.report, 'vision', 'reasoner')
        self.assertEqual(result['status'], 'not_fulfilled')
        self.assertTrue(any('contradict' in item or 'afirma cumplimiento' in item
                            for item in result['contract_findings']))


if __name__ == '__main__':
    unittest.main()

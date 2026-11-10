"""Window analysis must recover empty model output or return an explicit failure."""
import base64
import io
import threading
import unittest
import urllib.error
from unittest.mock import patch

from PIL import Image
import server


class WindowAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.client = server.app.test_client()
        self.audit = self.enterContext(patch.object(server, 'audit'))
        self.enterContext(patch.object(server, 'log'))
        self.cancel = self.enterContext(patch.object(server, 'CANCEL', threading.Event()))
        self.enterContext(patch.object(server, 'VISUAL_TOKEN_BUDGET', 1536))
        buffer = io.BytesIO()
        Image.new('RGB', (100, 100)).save(buffer, format='PNG')
        self.enterContext(patch.object(server, '_capture_visible_window',
                                      return_value=base64.b64encode(buffer.getvalue()).decode('ascii')))
        self.ollama = self.enterContext(patch.object(server, 'post_ollama'))
        self.process = self.enterContext(patch.object(server, 'run_process'))

    def request_analysis(self):
        response = self.client.post('/api/vision', json={
            'window_id': 123, 'vision_model': 'qwen3-vl:8b', 'prompt': 'Revisá los controles.'})
        self.process.assert_not_called()
        return response

    def test_nonempty_response_succeeds_without_retry(self):
        self.ollama.return_value = {'message': {'content': 'Hay un botón Guardar.'}, 'done_reason': 'stop'}
        response = self.request_analysis()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['analysis'], 'Hay un botón Guardar.')
        self.assertTrue(response.json['screenshot'].startswith('data:image/png;base64,'))
        self.assertEqual(self.ollama.call_count, 1)
        payload = self.ollama.call_args.args[1]
        self.assertIs(payload['think'], False)
        self.assertEqual(payload['options']['num_predict'], 1536)

    def test_empty_length_response_retries_and_recovers(self):
        self.ollama.side_effect = [
            {'message': {'content': '', 'thinking': 'razonamiento privado'}, 'done_reason': 'length'},
            {'message': {'content': 'El botón está recortado.'}, 'done_reason': 'stop'}]
        response = self.request_analysis()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['analysis'], 'El botón está recortado.')
        first, second = [call.args[1] for call in self.ollama.call_args_list]
        self.assertEqual(second['options']['num_predict'], 3072)
        self.assertEqual(first['messages'], second['messages'])
        attempts = [call.kwargs for call in self.audit.call_args_list
                    if call.args[0] == 'vision_stage_attempt']
        self.assertEqual(attempts[0]['done_reason'], 'length')
        self.assertEqual(attempts[0]['content_length'], 0)
        self.assertGreater(attempts[0]['thinking_length'], 0)
        self.assertNotIn('razonamiento privado', str(self.audit.call_args_list))

    def test_two_empty_responses_fail_instead_of_reporting_success(self):
        self.ollama.return_value = {'message': {'content': '  ', 'thinking': 'texto interno'},
                                    'done_reason': 'length'}
        response = self.request_analysis()
        self.assertEqual(response.status_code, 502)
        self.assertNotIn('ok', response.json)
        self.assertNotIn('analysis', response.json)
        self.assertIn('dos intentos', response.json['error'])
        self.assertEqual(self.ollama.call_count, 2)
        events = [call.args[0] for call in self.audit.call_args_list]
        self.assertIn('visual_window_analysis_failed', events)
        self.assertNotIn('visual_window_analysis', events)

    def test_connection_failure_is_not_an_empty_success(self):
        self.ollama.side_effect = urllib.error.URLError('Ollama no disponible')
        response = self.request_analysis()
        self.assertEqual(response.status_code, 502)
        self.assertIn('conexión', response.json['error'])
        self.assertEqual(self.ollama.call_count, 2)

    def test_unsupported_optional_parameter_falls_back(self):
        payloads = []

        def reply(endpoint, payload, **kwargs):
            payloads.append(dict(payload))
            if 'think' in payload:
                raise urllib.error.HTTPError('http://localhost/api/chat', 400, 'unsupported', {}, None)
            return {'message': {'content': 'Se observa una ventana con botones.'}}

        self.ollama.side_effect = reply
        response = self.request_analysis()
        self.assertEqual(response.status_code, 200)
        self.assertIn('think', payloads[0])
        self.assertNotIn('think', payloads[1])
        self.assertEqual(len(payloads), 2)

    def test_stopped_edit_job_does_not_cancel_independent_window_analysis(self):
        self.cancel.set()
        self.ollama.return_value = {'message': {'content': 'La ventana contiene un menú.'}}
        self.assertEqual(self.request_analysis().status_code, 200)
        with self.assertRaisesRegex(ValueError, 'cancelada'):
            server.ask_vision('qwen3-vl:8b', [], 'Revisá', 'job', 'test')
        self.assertEqual(self.ollama.call_count, 1)


if __name__ == '__main__':
    unittest.main()

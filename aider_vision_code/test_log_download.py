"""Regressions for stale log downloads and visual failures before model inference."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import server


class LogDownloadTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(server, 'LOG_DIR', self.root))
        self.chat_id = 'a' * 32
        self.enterContext(patch.dict(server.STATE, chat_id=self.chat_id, job_id=0))
        self.client = server.app.test_client()
        self.old = self.root / ('chat_' + 'b' * 32 + '.jsonl')
        self.old.write_text('{"event":"old_session"}\n', encoding='utf-8')
        # A foreign session may even have a later mtime than our active session.
        os.utime(self.old, (2_000_000_000, 2_000_000_000))

    def download(self):
        response = self.client.get('/api/logs/current')
        self.assertEqual(response.status_code, 200)
        events = [json.loads(line) for line in response.data.decode('utf-8').splitlines()]
        response.close()
        return response, events

    def test_new_session_download_never_falls_back_to_old_file(self):
        response, events = self.download()
        self.assertIn('chat_' + self.chat_id + '.jsonl', response.headers['Content-Disposition'])
        self.assertEqual([e['event'] for e in events], ['log_download_requested'])
        self.assertEqual(events[0]['chat_id'], self.chat_id)
        self.assertEqual(events[0]['diagnostics_version'], 2)
        self.assertEqual(events[0]['server_file'], str(Path(server.__file__).resolve()))
        self.assertIn('no-store', response.headers['Cache-Control'])

    def test_repeat_download_contains_new_events(self):
        first, before = self.download()
        server.audit('new_attempt', result='error de prueba')
        second, after = self.download()
        self.assertGreater(len(after), len(before))
        self.assertIn('new_attempt', [e['event'] for e in after])
        self.assertEqual(first.headers['Content-Disposition'], second.headers['Content-Disposition'])

    def assert_visual_failure(self, events, endpoint, status, expected_error):
        started = next(e for e in events if e['event'] == 'visual_request_started')
        finished = next(e for e in events if e['event'] == 'visual_request_finished')
        self.assertEqual(started['request_id'], finished['request_id'])
        self.assertEqual(finished['endpoint'], endpoint)
        self.assertEqual(finished['http_status'], status)
        self.assertIn(expected_error, finished['error'])

    def test_window_capture_error_is_recorded_before_any_model_call(self):
        with patch.object(server, '_capture_visible_window', side_effect=ValueError('Elegí una ventana visible')), \
                patch.object(server, 'post_ollama') as model:
            result = self.client.post('/api/vision', json={'window_id': 123})
        self.assertEqual(result.status_code, 400)
        model.assert_not_called()
        _, events = self.download()
        self.assert_visual_failure(events, 'vision', 400, 'ventana visible')

    def test_capture_exception_is_in_downloaded_diagnostic(self):
        with patch.object(server, '_capture_visible_window',
                          side_effect=OSError('No se pudo capturar la pantalla')):
            result = self.client.post('/api/vision', json={'window_id': 123})
        self.assertEqual(result.status_code, 500)
        _, events = self.download()
        self.assert_visual_failure(events, 'vision', 500, 'No se pudo capturar')

    def test_integrated_review_validation_failure_is_recorded(self):
        result = self.client.post('/api/vision/semantica', json={})
        self.assertEqual(result.status_code, 400)
        _, events = self.download()
        self.assert_visual_failure(events, 'vision_semantica_endpoint', 400, 'referencia')

    def test_log_write_failure_reports_error_without_downloading_old_log(self):
        with patch.object(server, 'audit', return_value=None):
            response = self.client.get('/api/logs/current')
        self.assertEqual(response.status_code, 500)
        self.assertIn('sesión actual', response.json['error'])
        self.assertNotIn('Content-Disposition', response.headers)

    def test_historical_download_and_listing_disable_cache(self):
        for url in ('/api/logs', '/api/logs/' + self.old.name):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertIn('no-store', response.headers['Cache-Control'])
            response.close()


if __name__ == '__main__':
    unittest.main()

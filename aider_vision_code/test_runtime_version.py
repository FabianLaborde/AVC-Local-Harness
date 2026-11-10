import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from runtime_version import RuntimeVersion
import server


class RuntimeVersionTests(unittest.TestCase):
    def test_nested_export_does_not_claim_ancestor_repository_commit(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)/'recovery'/'export'/'aider_vision_code'
            root.mkdir(parents=True)
            with patch('runtime_version.git_value', return_value=folder) as git:
                runtime = RuntimeVersion(root)
            self.assertIsNone(runtime.started['commit'])
            self.assertIsNone(runtime.started['worktree_modified'])
            git.assert_called_once()

    def test_running_identity_survives_disk_change_and_git_absence(self):
        with tempfile.TemporaryDirectory() as folder, patch('runtime_version.git_value', return_value=None):
            root = Path(folder)
            (root / 'web').mkdir()
            (root / 'web/index.html').write_text('old', encoding='utf-8')
            (root / 'version.json').write_text('{"version":"1-dev","channel":"desarrollo"}', encoding='utf-8')
            (root / 'server.py').write_text('old', encoding='utf-8')
            runtime = RuntimeVersion(root)
            self.assertFalse(runtime.status()['restart_required'])
            self.assertIsNone(runtime.started['commit'])
            (root / 'logs').mkdir()
            (root / 'logs/chat.jsonl').write_text('private', encoding='utf-8')
            self.assertFalse(runtime.status()['restart_required'])
            (root / 'server.py').write_text('new', encoding='utf-8')
            changed = runtime.status()
            self.assertTrue(changed['restart_required'])
            self.assertNotEqual(changed['disk_sha256'], changed['running']['source_sha256'])
            self.assertEqual(changed['running']['version'], '1-dev')

    def test_page_version_endpoint_and_download_agree(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(server, 'LOG_DIR', Path(folder)):
            client = server.app.test_client()
            response = client.get('/')
            digest = hashlib.sha256((server.ROOT / 'web/index.html').read_bytes()).hexdigest()
            self.assertIn(digest, response.text)
            self.assertNotIn('__AVC_PAGE_SHA256__', response.text)
            self.assertIn('no-store', response.headers['Cache-Control'])
            version = client.get('/api/version')
            self.assertIn('no-store', version.headers['Cache-Control'])
            log = client.get('/api/logs/current')
            events = [json.loads(line) for line in log.text.splitlines()]
            self.assertEqual(events[-1]['avc_runtime'], version.json['running'])
            self.assertEqual(events[-1]['avc_version_status']['running'], version.json['running'])
            log.close()

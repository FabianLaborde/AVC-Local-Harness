"""Browser-based folder explorer never uses desktop modal dialogs."""
import tempfile
import unittest
from pathlib import Path
try:
    import server
except ModuleNotFoundError as exc:
    if exc.name != 'flask':
        raise
    server = None



@unittest.skipIf(server is None, 'Flask no disponible en este entorno')
class FolderBrowseTests(unittest.TestCase):
    def test_explore_local_directory(self):
        with tempfile.TemporaryDirectory() as root:
            (Path(root) / 'Subproject').mkdir()
            with server.app.test_client() as client:
                response = client.get('/api/folders', query_string={'path': root},
                                      environ_base={'REMOTE_ADDR': '127.0.0.1'})
            self.assertEqual(response.status_code, 200)
            data = response.get_json()
            self.assertIn('Subproject', [x['name'] for x in data['folders']])
            self.assertEqual(data['path'], str(Path(root).resolve()))

    def test_rejects_nonexistent_folder(self):
        with server.app.test_client() as client:
            response = client.get('/api/folders', query_string={'path': '/unlikely_nonexistent_avc_934184'},
                                  environ_base={'REMOTE_ADDR': '127.0.0.1'})
        self.assertEqual(response.status_code, 400)

    def test_rejects_remote_request(self):
        with server.app.test_client() as client:
            response = client.get('/api/folders', environ_base={'REMOTE_ADDR': '203.0.113.3'})
        self.assertEqual(response.status_code, 403)


if __name__ == '__main__':
    unittest.main()

import json
import tempfile
import unittest
from pathlib import Path

from project_validation import resolve_capabilities, select_adapter


class ProjectCapabilitiesTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def test_python_descriptor_is_evidence_based_and_does_not_validate(self):
        (self.root / 'app.py').write_text('value = 1', encoding='utf8')
        adapter, descriptor = resolve_capabilities(self.root, 'app.py')

        self.assertIs(adapter, select_adapter(self.root, 'app.py')[0])
        self.assertEqual(descriptor['schema_version'], 1)
        self.assertEqual(descriptor['adapter'], 'python')
        self.assertEqual(descriptor['technology'], 'Python')
        self.assertEqual(descriptor['entry'], 'app.py')
        self.assertEqual(descriptor['validation']['level'], 'syntax')
        self.assertFalse(descriptor['validation']['executes_application'])
        json.dumps(descriptor)

    def test_dotnet_descriptor_records_nearest_project_and_scope(self):
        (self.root / 'src').mkdir()
        (self.root / 'src' / 'app.csproj').write_text('<Project />', encoding='utf8')
        (self.root / 'src' / 'MainWindow.xaml').write_text('<Window />', encoding='utf8')
        (self.root / 'Directory.Build.props').write_text('<Project />', encoding='utf8')

        _, descriptor = resolve_capabilities(
            self.root, 'src/MainWindow.xaml', additional_files=['src/MainWindow.xaml'])

        self.assertEqual(descriptor['adapter'], 'dotnet')
        self.assertEqual(descriptor['technology'], '.NET')
        self.assertEqual(descriptor['entry'], 'src/app.csproj')
        self.assertEqual(descriptor['validation']['level'], 'compilation')
        self.assertIn('src/app.csproj', descriptor['copy_scope']['projects'])
        self.assertIn('Directory.Build.props', descriptor['copy_scope']['files'])
        self.assertIn('src/MainWindow.xaml', descriptor['copy_scope']['files'])

    def test_descriptor_rejects_files_outside_project(self):
        (self.root / 'app.py').write_text('value = 1', encoding='utf8')
        with self.assertRaisesRegex(ValueError, 'adicional fuera'):
            resolve_capabilities(self.root, 'app.py', additional_files=['../secret.py'])


if __name__ == '__main__':
    unittest.main()

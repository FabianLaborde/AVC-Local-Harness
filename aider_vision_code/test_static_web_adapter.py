import json
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from project_candidate import apply_candidate, prepare_candidate
from project_validation import resolve_capabilities, select_adapter


class StaticWebAdapterTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def test_descriptor_uses_nearest_package_scope(self):
        target = self.root / 'apps' / 'site' / 'src' / 'app.js'
        target.parent.mkdir(parents=True)
        target.write_text('const value = 1;', encoding='utf8')
        (self.root / 'apps' / 'site' / 'package.json').write_text('{}', encoding='utf8')

        _, descriptor = resolve_capabilities(self.root, 'apps/site/src/app.js')

        self.assertEqual(descriptor['adapter'], 'static_web')
        self.assertEqual(descriptor['technology'], 'Static Web')
        self.assertEqual(descriptor['entry'], 'apps/site/src/app.js')
        self.assertEqual(descriptor['copy_scope']['directories'], ['apps/site'])
        self.assertEqual(descriptor['copy_scope']['projects'], ['apps/site/package.json'])
        self.assertFalse(descriptor['validation']['executes_application'])

    def test_project_without_manifest_uses_target_directory(self):
        page = self.root / 'public' / 'index.html'
        page.parent.mkdir()
        page.write_text('<!doctype html><title>AVC</title>', encoding='utf8')

        _, descriptor = resolve_capabilities(self.root, 'public/index.html')

        self.assertEqual(descriptor['copy_scope']['directories'], ['public'])
        self.assertEqual(descriptor['copy_scope']['projects'], [])

    def test_real_html_validation_parses_copy_without_starting_it(self):
        (self.root / 'index.html').write_text('<!doctype html><main>AVC</main>', encoding='utf8')
        adapter, entry = select_adapter(self.root, 'index.html')

        def run(command, cwd, timeout):
            result = subprocess.run(command, cwd=cwd, capture_output=True, text=True, timeout=timeout)
            return result.returncode, result.stdout + result.stderr

        result = adapter.validate(self.root, entry, run)

        self.assertTrue(result['passed'], result['output'])
        self.assertIn('Static web files readable: 1', result['output'])
        self.assertEqual(len(result['command']), 1)

    def test_validation_uses_argument_lists_and_never_runs_package_scripts(self):
        (self.root / 'index.html').write_text('<main>AVC</main>', encoding='utf8')
        (self.root / 'app.js').write_text('const value = 1;', encoding='utf8')
        adapter, entry = select_adapter(self.root, 'app.js')
        calls = []

        def run(command, cwd, timeout):
            calls.append((command, cwd, timeout))
            return 0, 'ok'

        result = adapter.validate(self.root, entry, run)

        self.assertTrue(result['passed'])
        self.assertTrue(all(isinstance(command, list) for command, _, _ in calls))
        self.assertEqual(calls[1][0], ['node', '--check', 'app.js'])
        self.assertNotIn('npm', json.dumps(result))

    def test_javascript_syntax_failure_blocks_candidate(self):
        project = self.root / 'project'
        project.mkdir()
        source = b'const value = 1;\n'
        (project / 'app.js').write_bytes(source)
        report = self.root / 'report'
        report.mkdir()

        def run(command, cwd, timeout):
            return (1, 'SyntaxError') if command[0] == 'node' else (0, 'readable')

        api = SimpleNamespace(CANCEL=threading.Event(), log=Mock(), run_process=run)
        with self.assertRaisesRegex(ValueError, 'validaci.n fall.'):
            prepare_candidate(api, project, report, 'Change',
                              {'app.js': b'const value = ;\n'}, {'app.js': source})
        self.assertFalse(json.loads((report / 'validation.json').read_text())['passed'])
        self.assertEqual((project / 'app.js').read_bytes(), source)

    def test_html_candidate_applies_with_backup(self):
        project = self.root / 'project'
        project.mkdir()
        source = b'<h1>Old</h1>\n'
        changed = b'<h1>New</h1>\n'
        (project / 'index.html').write_bytes(source)
        report = self.root / 'report'
        report.mkdir()
        api = SimpleNamespace(CANCEL=threading.Event(), log=Mock(),
                              run_process=lambda *args, **kwargs: (0, 'readable'))

        candidate = prepare_candidate(api, project, report, 'Change',
                                      {'index.html': changed}, {'index.html': source})
        apply_candidate(api, project, candidate)

        self.assertEqual((project / 'index.html').read_bytes(), changed)
        self.assertEqual((report / 'before_apply' / 'index.html').read_bytes(), source)


if __name__ == '__main__':
    unittest.main()

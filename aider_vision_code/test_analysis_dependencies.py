import tempfile
import unittest
from pathlib import Path
from analysis_dependencies import source_evidence


class DependencyEvidenceTests(unittest.TestCase):
    def test_transitive_sources_and_redaction(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name, text in {'Screen.cs': 'var b = Controls.Build();',
                               'Controls.cs': 'var color = Palette.Normal;\nAPI_KEY = "hidden-value";',
                               'Palette.cs': 'Normal = Color.White;'}.items():
                (root/name).write_text(text)
            context, report = source_evidence(root, ['Screen.cs'])
            self.assertEqual(len(report['files']), 3)
            self.assertIn('Normal = Color.White', context)
            self.assertNotIn('hidden-value', context)
            self.assertIn('Símbolos sin archivo local', context)

    def test_ambiguous_dependency_not_guessed(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root/'Screen.cs').write_text('Controls.Build();')
            for d in ['a', 'b']:
                (root/d).mkdir()
                (root/d/'Controls.cs').write_text('UNTRUSTED_CHOICE')
            context, report = source_evidence(root, ['Screen.cs'])
            self.assertIn('ambigua', context)
            self.assertNotIn('UNTRUSTED_CHOICE', context)
            self.assertEqual(report['files'], ['Screen.cs'])

    def test_budget_and_partial_evidence(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root/'Screen.cs').write_text('line\n' * 5000)
            context, report = source_evidence(root, ['Screen.cs'], max_chars=5000)
            self.assertLessEqual(len(context), 5000)
            self.assertIn('Fragmento parcial', context)

    def test_forbidden_target_and_artifacts(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root/'Screen.cs').write_text('Controls.Build();')
            (root/'artifacts').mkdir()
            (root/'artifacts'/'Controls.cs').write_text('OLD_SOURCE')
            context, _ = source_evidence(root, ['Screen.cs'])
            self.assertNotIn('OLD_SOURCE', context)
            for name in ['../Outside.cs', 'password.txt', 'artifacts/Controls.cs']:
                with self.assertRaises(ValueError):
                    source_evidence(root, [name])

    def test_python_companion_and_cycle(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root/'main.py').write_text('import widget\nwidget.draw()')
            (root/'widget.py').write_text('import main\ndef draw(): pass')
            _, report = source_evidence(root, ['main.py'])
            self.assertEqual(report['files'], ['main.py', 'widget.py'])

    def test_readonly_request_uses_dependency_evidence(self):
        from test_analysis_quality import namespace
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root/'Screen.cs').write_text('Controls.Build();')
            (root/'Controls.cs').write_text('Background = Color.White;')
            post = Mock(return_value={'message': {'content': 'Screen.cs crea un Button mediante Controls.Build; Controls.cs asigna Color.White.'}})
            namespace.update(post_ollama=post, CANCEL=Mock(is_set=Mock(return_value=False)), audit=Mock(), log=Mock())
            answer, _ = namespace['request_project_analysis'](root, 'Revisá Screen.cs y su botón', 'test')
            self.assertIn('Background = Color.White', post.call_args.args[1]['messages'][1]['content'])
            self.assertIn('Evidencia consultada por AVC', answer)

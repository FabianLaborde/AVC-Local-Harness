import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import server
from visual_pipeline import WpfAdapter


class WpfEditorTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.project = self.root/'project'
        self.project.mkdir()
        self.file = self.project/'MainWindow.xaml'
        self.original = ('<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation">\n'
                         '  <Grid>\n  </Grid>\n</Window>\n').encode()
        self.file.write_bytes(self.original)
        (self.project/'MainWindow.xaml.cs').write_text('class MainWindow { void Existing() {} }', encoding='utf-8')
        (self.project/'App.csproj').write_text('<Project Sdk="Microsoft.NET.Sdk" />', encoding='utf-8')
        self.api = SimpleNamespace(ROOT=self.root, CANCEL=threading.Event(),
            source_snapshot=server.source_snapshot, post_ollama=Mock(),
            audit=Mock(), log=Mock(), AIDER_PROCESS_TIMEOUT=30)
        self.adapter = WpfAdapter(self.api, self.project, 'chosen-local-model', {})
        self.adapter.user_request = 'Creá la interfaz sin conectar servicios ni agregar lógica de negocio.'
        sdk_patch = patch('source_editor.request_sdk_evidence', return_value=None)
        sdk_patch.start()
        self.addCleanup(sdk_patch.stop)

    def response(self, content):
        return {'message': {'content': content}, 'done_reason': 'stop'}

    def edits(self, **overrides):
        operation = dict(file='MainWindow.xaml', operation='insert_after', anchor='  <Grid>',
                         content='    <Button Content="Prueba" />')
        operation.update(overrides)
        return json.dumps({'edits': [operation]})

    def generate(self, operations=None):
        self.api.post_ollama.side_effect = [self.response('Agregar Button dentro del Grid.'),
                                          self.response(operations or self.edits())]
        return self.adapter.edit('Crear solo el botón de prueba')

    def test_blank_wpf_uses_shared_editor_full_sources_and_selected_model(self):
        code, detail = self.generate()
        self.assertEqual(code, 0, detail)
        self.assertIn(b'<Button', self.file.read_bytes())
        calls = self.api.post_ollama.call_args_list
        self.assertEqual(len(calls), 2)
        for call in calls:
            self.assertEqual(call.args[1]['model'], 'chosen-local-model')
        integration = str(calls[1].args[1]['messages'])
        self.assertIn('void Existing()', integration)
        self.assertIn('sin conectar servicios ni agregar lógica', integration)
        self.assertIn('SOLO LECTURA: MainWindow.xaml.cs', integration)
        self.assertIn('Framework comprobado', str(calls[0]))
        self.assertIn('Pendientes compilación', detail)

    def test_malformed_xml_never_reaches_project(self):
        code, _ = self.generate(self.edits(content='<Button>'))
        self.assertEqual(code, 1)
        self.assertEqual(self.file.read_bytes(), self.original)

    def test_missing_anchor_and_readonly_file_are_rejected_without_writes(self):
        for operation in (self.edits(anchor='missing'), self.edits(file='MainWindow.xaml.cs')):
            code, _ = self.generate(operation)
            self.assertEqual(code, 1)
            self.assertEqual(self.file.read_bytes(), self.original)

    def test_truncated_generation_stops_without_integration_or_writes(self):
        self.api.post_ollama.side_effect = None
        self.api.post_ollama.return_value = dict(message={'content': 'partial'}, done_reason='length')
        code, _ = self.adapter.edit('Crear la vista')
        self.assertEqual(code, 1)
        self.api.post_ollama.assert_called_once()
        self.assertEqual(self.file.read_bytes(), self.original)

    def test_concurrent_source_change_is_preserved(self):
        responses = iter([self.response('Plan'), self.response(self.edits())])
        def ask(*args, **kwargs):
            result = next(responses)
            if len(self.api.post_ollama.call_args_list) == 2:
                self.file.write_bytes(b'<Window Title="User edit"/>')
            return result
        self.api.post_ollama.side_effect = ask
        code, detail = self.adapter.edit('Crear la vista')
        self.assertEqual(code, 1)
        self.assertIn('fuentes cambiaron', detail)
        self.assertEqual(self.file.read_bytes(), b'<Window Title="User edit"/>')

    def test_runtime_error_repair_uses_actual_diagnostic_in_one_call(self):
        self.api.post_ollama.return_value = self.response(self.edits())
        diagnostic = "System.IO.IOException: No se encuentra el recurso 'logo.png'."
        code, detail = self.adapter.edit(diagnostic, repair=True)
        self.assertEqual(code, 0, detail)
        self.api.post_ollama.assert_called_once()
        self.assertIn(diagnostic, str(self.api.post_ollama.call_args.args[1]))

    def test_cancelled_step_cannot_write(self):
        self.api.CANCEL.set()
        code, _ = self.generate()
        self.assertEqual(code, -1)
        self.api.post_ollama.assert_not_called()
        self.assertEqual(self.file.read_bytes(), self.original)

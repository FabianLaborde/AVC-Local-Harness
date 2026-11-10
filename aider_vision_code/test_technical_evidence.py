import json
from pathlib import Path
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from technical_evidence import (PythonEvidenceProvider, provider_evidence,
                                request_evidence)


class TechnicalEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(self.temp)
        self.api = SimpleNamespace(CANCEL=threading.Event(), post_ollama=Mock())

    def test_python_uses_ast_and_stdlib_without_model_call(self):
        sources = {'app.py': b'import json\n\ndef greet(name="world"):\n    return name\n'}
        evidence, response = request_evidence(
            self.api, 'Cambia el saludo', sources, 'model', project=self.root)
        self.assertIsNone(response)
        self.assertEqual(evidence['kind'], 'technical_evidence')
        self.assertEqual(evidence['ecosystems'], ['python'])
        python = provider_evidence(evidence, 'python')[0]
        self.assertEqual(python['status'], 'available')
        by_symbol = {entry['symbol']: entry for entry in python['entries']}
        self.assertEqual(by_symbol['app.greet']['signature'], '(name=?)')
        self.assertEqual(by_symbol['json']['source'], 'python_stdlib')
        self.api.post_ollama.assert_not_called()

    def test_python_never_imports_or_executes_project_module(self):
        package = self.root/'dangerpkg'
        package.mkdir()
        marker = self.root/'executed.txt'
        source = ("from pathlib import Path\n"
                  f"Path({str(marker)!r}).write_text('executed')\n").encode()
        sources = {'app.py': b'import dangerpkg\n',
                   'dangerpkg/__init__.py': source}
        evidence, _ = request_evidence(
            self.api, 'Analiza', sources, 'model', project=self.root)
        self.assertFalse(marker.exists())
        module = next(entry for entry in provider_evidence(evidence, 'python')[0]['entries']
                      if entry['symbol'] == 'dangerpkg')
        self.assertEqual(module['source'], 'project_source')

    def test_declared_but_uninstalled_dependency_is_partial_not_verified(self):
        (self.root/'requirements.txt').write_text('requests>=2\n', encoding='utf8')
        evidence, _ = request_evidence(
            self.api, 'Analiza', {'app.py': b'import requests\n'}, 'model',
            project=self.root)
        python = provider_evidence(evidence, 'python')[0]
        self.assertEqual(python['status'], 'partial')
        entry = next(item for item in python['entries'] if item['symbol'] == 'requests')
        self.assertEqual(entry['status'], 'declared')

    def test_polyglot_contract_aggregates_dotnet_and_python(self):
        dotnet = dict(schema_version=1, provider='dotnet_reference_xml',
                      status='available', entries=[{'symbol': 'T:System.String'}])
        response = {'done_reason': 'stop'}
        with patch('technical_evidence.request_dotnet_evidence',
                   return_value=(dotnet, response)):
            evidence, transport = request_evidence(
                self.api, 'Edita', {'View.cs': b'class View {}',
                                    'tool.py': b'import json\n'}, 'model',
                project=self.root)
        self.assertEqual(evidence['ecosystems'], ['dotnet', 'python'])
        self.assertEqual(len(evidence['providers']), 2)
        self.assertEqual(transport, response)
        self.assertEqual(provider_evidence(evidence, 'dotnet')[0]['ecosystem'], 'dotnet')

    def test_unknown_ecosystem_returns_no_evidence(self):
        self.assertIsNone(request_evidence(
            self.api, 'Edita', {'README.md': b'text'}, 'model', project=self.root))


if __name__ == '__main__':
    unittest.main()

"""Regression tests for read-only answer quality, independent of Flask/Ollama."""
import ast
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from xaml_structure import inspect_xaml, structural_report, conflicting_dimensions
from xaml_claims import verify_xaml_claims, claims_report

SOURCE = Path(__file__).with_name('server.py')
TREE = ast.parse(SOURCE.read_text(encoding='utf-8'))
NAMES = {'analysis_file_priority', 'analysis_context', 'is_unusable_analysis',
         'request_project_analysis', 'requested_analysis_files', 'validate_analysis_relevance'}
FUNCS = [node for node in TREE.body if isinstance(node, ast.FunctionDef) and node.name in NAMES]
namespace = {'inspect_xaml': inspect_xaml, 'structural_report': structural_report, 'conflicting_dimensions': conflicting_dimensions, 'verify_xaml_claims': verify_xaml_claims, 'claims_report': claims_report, 'Path': Path, 'os': __import__('os'), 're': re,
             'REQUESTED_FILE_RE': re.compile(r'(?i)(?<![\w.])([\w.-]+(?:[/\\][\w.-]+)*\.(?:xaml|cs|csproj|fs|fsproj|vb|vbproj|py|js|jsx|ts|tsx|html|css|json|xml|md|txt|ps1|sln|yml|yaml))\b'),
             'ANALYSIS_SUFFIXES': {'.cs', '.csproj', '.xaml', '.md', '.py', '.txt'},
             'SECRET_FILENAME_RE': re.compile(r'(?i)(^\.|secret|credential|password|passwd|token|private|\.env|id_rsa|key\.|lock$)'),
             'ANALYSIS_REFUSAL_RE': re.compile(
                 r'(?i)(?:i\s*(?:am|\x27m)\s*sorry.{0,100}(?:can\x27t|cannot|unable)|'
                 r'i\s*(?:can\x27t|cannot)\s*(?:assist|help|comply)|'
                 r'no\s+(?:puedo|podr[eé])\s+(?:ayudarte|asistirte|cumplir)|'
                 r'(?:cannot|can\x27t)\s+assist\s+with\s+that\s+request|'
                 r'no\s+puedo\s+procesar\s+(?:esa|esta)\s+solicitud)')}
exec(compile(ast.Module(body=FUNCS, type_ignores=[]), str(SOURCE), 'exec'), namespace)

class AnalysisQualityTests(unittest.TestCase):
    def test_refusals_rejected(self):
        is_bad = namespace['is_unusable_analysis']
        self.assertTrue(is_bad("I'm sorry, but I can't assist with that request."))
        self.assertTrue(is_bad('{"response": "I\'m sorry, but I can\'t assist with that request."}'))
        self.assertTrue(is_bad(''))
        self.assertFalse(is_bad('El proyecto tiene MainWindow.xaml y Program.cs; se trata de una aplicacion WPF.'))

    def test_context_prioritizes_manifest_and_does_not_read_secrets(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for n in ['aaa.cs', 'zzz.cs', 'app.csproj', 'MainWindow.xaml']:
                (root / n).write_text('ARCHIVO:'+n, encoding='utf8')
            (root / 'password.txt').write_text('CREDENCIAL_SUPERSECRETA', encoding='utf8')
            (root / 'bin').mkdir()
            (root / 'bin' / 'Main.cs').write_text('BIN_SECRETO', encoding='utf8')
            output = namespace['analysis_context'](root, max_files=2)
            self.assertIn('ARCHIVO:app.csproj', output)
            self.assertIn('ARCHIVO:MainWindow.xaml', output)
            self.assertNotIn('CREDENCIAL_SUPERSECRETA', output)
            self.assertNotIn('BIN_SECRETO', output)
            listing = namespace['analysis_context'](root, metadata_only=True)
            self.assertNotIn('ARCHIVO:', listing)

    def test_specific_file_request_prioritises_source_content(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'app.csproj').write_text('<Project />', encoding='utf8')
            (root / 'MainWindow.xaml').write_text('<Window />', encoding='utf8')
            (root / 'DetallePedido.cs').write_text('public class DetallePedido { }', encoding='utf8')
            context = namespace['analysis_context'](
                root, max_files=2, instruction='Explicá DetallePedido.cs')
            self.assertIn('public class DetallePedido', context)
            self.assertIn('<Project />', context)

    def test_inline_secrets_redacted_from_context(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'main.py').write_text(
                'def main(): pass\nAPI_KEY = "do-not-send-this"\n', encoding='utf8')
            context = namespace['analysis_context'](root)
            self.assertIn('def main(): pass', context)
            self.assertNotIn('do-not-send-this', context)
            self.assertIn('posible secreto', context)

    def test_retry_once_then_abstain(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root/'app.csproj').write_text('<Project/>',encoding='utf8')
            replies = iter([
                {'message':{'content':"I'm sorry, but I can't assist with that request."}},
                {'message':{'content':'El proyecto contiene app.csproj; por la estructura es probable que use .NET.'}}])
            namespace.update(post_ollama=Mock(side_effect=lambda *args: next(replies)),
                             CANCEL=Mock(is_set=Mock(return_value=False)),
                             audit=Mock(), log=Mock())
            text, attempts = namespace['request_project_analysis'](root,'Analiza sin modificar','demo')
            self.assertEqual(attempts, 2)
            self.assertIn('app.csproj',text)
            self.assertEqual(namespace['post_ollama'].call_count, 2)
            self.assertEqual(namespace['audit'].call_count,3)

    def test_explicit_target_with_no_source_blocks_model(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            (root/'Program.cs').write_text('class Program {}',encoding='utf8')
            namespace.update(post_ollama=Mock(),CANCEL=Mock(is_set=Mock(return_value=False)))
            with self.assertRaisesRegex(ValueError,'no existe'):
                namespace['request_project_analysis'](root,'Analizá MainWindow.xaml','demo')
            namespace['post_ollama'].assert_not_called()

    def test_explicit_source_and_response_relevance_retry(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            (root/'App.csproj').write_text('<Project />',encoding='utf8')
            (root/'MainWindow.xaml').write_text('<Window><Grid><Button Width="60" /></Grid></Window>',encoding='utf8')
            calls=[]
            def fake_post(endpoint,payload):
                calls.append(payload['messages'][-1]['content'])
                if len(calls)==1:
                    return {'message':{'content':'El proyecto C# usa varios scripts PowerShell y Git.'}}
                return {'message':{'content':('MainWindow.xaml contiene Grid y Button; el ancho Width=60 es una propiedad visual. '
                                               'La distribución sitúa el botón dentro de un Grid.')}}
            namespace.update(post_ollama=Mock(side_effect=fake_post),
                             CANCEL=Mock(is_set=Mock(return_value=False)),audit=Mock(),log=Mock())
            answer,attempts=namespace['request_project_analysis'](
                root,'Analizá MainWindow.xaml: controles, propiedades visuales y distribución','demo')
            self.assertEqual(attempts,2)
            self.assertIn('MainWindow.xaml',answer)
            self.assertIn('<Button Width="60"',calls[0])
            self.assertIn('<Button Width="60"',calls[1])

    def test_specific_target_not_replaced_by_generic_retry(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            (root/'MainWindow.xaml').write_text('<Window><Grid><Button /></Grid></Window>',encoding='utf8')
            namespace.update(post_ollama=Mock(return_value={'message':{'content':
                'La arquitectura general de esta aplicación utiliza el patrón MVC.'}}),
                CANCEL=Mock(is_set=Mock(return_value=False)),audit=Mock(),log=Mock())
            with self.assertRaisesRegex(ValueError,'relevancia'):
                namespace['request_project_analysis'](root,'Analizá MainWindow.xaml: controles y distribución','demo')
            self.assertEqual(namespace['post_ollama'].call_count,2)

    def test_ambiguous_filename_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            for d in ('Uno','Dos'):
                (root/d).mkdir()
                (root/d/'MainWindow.xaml').write_text('<Window />',encoding='utf8')
            with self.assertRaisesRegex(ValueError,'ambiguo'):
                namespace['requested_analysis_files'](root,'Analizá MainWindow.xaml')

    def test_harness_prompt_prioritizes_evidence_not_no_edit_ack(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root/'MainWindow.xaml').write_text(
                '<Window><Grid><Button Content="Aceptar" Width="90" /></Grid></Window>', encoding='utf8')
            payloads=[]
            def fake_post(endpoint, payload):
                payloads.append(payload)
                return {'message': {'content': 'MainWindow.xaml incluye Button Content Aceptar con Width=90, dentro de Grid. La distribución se realiza mediante Grid.'}}
            namespace.update(post_ollama=Mock(side_effect=fake_post),
                             CANCEL=Mock(is_set=Mock(return_value=False)), audit=Mock(), log=Mock())
            answer, tries = namespace['request_project_analysis'](
                root, 'Analizá MainWindow.xaml: controles, propiedades y distribución. No modifiques ningún archivo.', 'demo')
            self.assertEqual(tries, 1)
            user_prompt=payloads[0]['messages'][1]['content']
            self.assertIn('Content="Aceptar"', user_prompt)
            self.assertIn('FORMATO DE RESPUESTA', user_prompt)
            self.assertNotIn('No modifiques ningún archivo', user_prompt)
            self.assertEqual(user_prompt.count('--- CONTENIDO:'), 1)
            self.assertTrue(any(call.args[0]=='analysis_context_prepared' for call in namespace['audit'].call_args_list))

    def test_acknowledgement_not_treated_as_analysis(self):
        self.assertTrue(namespace['is_unusable_analysis']('Entendido. No generaré comandos ni ediciones de archivos.'))

    def test_two_refusals_are_not_a_success(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            (root/'main.py').write_text('print(1)',encoding='utf8')
            namespace.update(post_ollama=Mock(return_value={'message':{'content':'I cannot assist with that request.'}}),
                             CANCEL=Mock(is_set=Mock(return_value=False)),
                             audit=Mock(),log=Mock())
            with self.assertRaisesRegex(ValueError,'no produjo un análisis válido'):
                namespace['request_project_analysis'](root,'Analizá sin modificar','demo')
            self.assertEqual(namespace['post_ollama'].call_count,2)

if __name__=='__main__':
    unittest.main()

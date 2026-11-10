import base64
from io import BytesIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from PIL import Image
import server
from conversation_context import ConversationContext, persistent_reference_intent


class ContextTests(unittest.TestCase):
    def setUp(self):
        self.context = ConversationContext()
        self.project = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def turn(self, text, image=None, chat='chat', project=None):
        r = self.context.resolve(chat, project or self.project, text, image)
        self.context.commit(r)
        return r

    def test_original_three_turn_failure(self):
        self.turn('Estudiá la imagen y decime si podés reproducirla en C#', 'pixels')
        self.turn('La interfaz está hecha con C# y WPF')
        r = self.turn('ok, creá una interfaz parecida a la imagen, solo la parte visual sin lógica')
        self.assertEqual(r['image'], 'pixels')
        self.assertEqual(r['metadata']['technology'], 'WPF')
        self.assertTrue(r['metadata']['visual_only'])
        self.assertTrue(r['metadata']['reference_reused'])

    def test_current_technology_wins_and_unrelated_edit_does_not_receive_image(self):
        self.turn('Referencia WPF', 'pixels')
        r = self.turn('Ahora hacelo en Flutter')
        self.assertEqual(r['metadata']['technology'], 'Flutter')
        self.assertIsNone(r['image'])
        self.assertNotIn('WPF', r['instruction'])

    def test_language_and_negative_framework_choice(self):
        self.assertEqual(self.turn('Creá esto en C#')['metadata']['technology'], 'C#')
        self.assertEqual(self.turn('Usá WPF, no Flutter')['metadata']['technology'], 'WPF')

    def test_new_chat_project_and_clear_isolation(self):
        self.turn('WPF solo la parte visual', 'pixels')
        for kwargs in ({'chat': 'other'}, {'project': self.project/'other'}):
            r = self.context.resolve(kwargs.get('chat', 'chat'), kwargs.get('project', self.project), 'creala')
            self.assertIsNone(r['image'])
            self.assertIsNone(r['metadata']['technology'])
        self.context.clear()
        self.assertIsNone(self.turn('creala')['image'])

    def test_forget_and_replace_reference(self):
        self.turn('Referencia', 'one')
        self.assertIsNone(self.turn('Ignorá la referencia')['image'])
        self.assertIsNone(self.turn('creala')['image'])
        self.turn('Nueva imagen', 'two')
        self.assertEqual(self.turn('creala')['image'], 'two')

    def test_persistent_reference_requires_explicit_words(self):
        self.assertFalse(persistent_reference_intent('Corregí la composición visual'))
        self.assertFalse(persistent_reference_intent('Creá la aplicación sin referencia'))
        self.assertTrue(persistent_reference_intent('Usá la referencia guardada'))
        self.assertTrue(persistent_reference_intent('Compará con la imagen anterior'))

    def test_latest_functionality_releases_visual_only(self):
        self.turn('solo la parte visual')
        self.assertFalse(self.turn('Agregá la lógica de autenticación')['metadata']['visual_only'])

    def test_maqueta_followup_preserves_negative_business_logic_request(self):
        self.turn('C# y WPF, solo la parte visual, sin lógica de negocio', 'pixels')
        result = self.turn('Ahora creá esa interfaz usando la imagen y la tecnología que acabamos de definir. '
                           'Incluí los campos y botones visibles, sin conectar la báscula ni agregar lógica de negocio.')
        self.assertTrue(result['metadata']['visual_only'])
        self.assertTrue(result['metadata']['reference_reused'])
        self.assertEqual(result['metadata']['technology'], 'WPF')
        self.assertIn('no agregar lógica de negocio', result['instruction'])
        for text in ('No agregues lógica', 'Sin implementar funcionalidad', 'Ni incorporar comportamiento'):
            self.assertTrue(self.turn(text)['metadata']['visual_only'])
        self.assertFalse(self.turn('Solo la parte visual, pero ahora agregá lógica de negocio')['metadata']['visual_only'])

    def test_endpoint_reuses_reference_and_routes_wpf_without_execution(self):
        buf = BytesIO()
        Image.new('RGB', (2, 2)).save(buf, format='PNG')
        image = base64.b64encode(buf.getvalue()).decode()
        with patch.object(server, 'CONVERSATION_CONTEXT', self.context), patch.object(server, 'LOCK'), patch.object(server, 'audit'), patch.object(server.threading, 'Thread') as thread, patch.dict(server.STATE), patch.dict(server.VISUAL_CAPTURES):
            client = server.app.test_client()
            common = dict(project=str(self.project), automatic_settings=True)
            for data in [dict(instruction='Solo analizá esta imagen en C#; no modifiques archivos', image=image),
                         dict(instruction='Solo analizá el proyecto: la tecnología es WPF'),
                         dict(instruction='Creá la interfaz de la imagen sin lógica')]:
                self.assertEqual(client.post('/api/job', json={**common, **data}).status_code, 202)
            args = thread.call_args.kwargs['args']
            self.assertIs(thread.call_args.kwargs['target'], server.run_job)
            self.assertEqual(args[5], image)
            self.assertEqual(args[8], 'wpf')
            # A subsequent read-only request still cannot execute the editor.
            client.post('/api/job', json={**common, 'instruction':'Solo analizá la referencia; no edites archivos'})
            self.assertIs(thread.call_args.kwargs['target'], server.run_analysis_job)

    def test_explicit_technology_bypasses_selector(self):
        with patch.object(server, 'post_ollama') as model:
            advice, visual = server.model_advice('Creá la interfaz en WPF', 'local')
            self.assertIn('WPF', advice)
            model.assert_not_called()

    def test_visual_text_is_not_silently_cut_at_3500_characters(self):
        content = 'Observación ' * 400
        with patch.object(server, 'post_ollama', return_value={'message':{'content':content}, 'done_reason':'stop'}), patch.object(server, 'audit'):
            result = server.ask_vision('vision', ['pixels'], 'Describe', 'test', 'image', respect_cancel=False)
        self.assertEqual(result, content.strip())

    def test_twice_truncated_visual_response_is_rejected(self):
        with patch.object(server, 'post_ollama', return_value={'message':{'content':'partial'}, 'done_reason':'length'}) as model, patch.object(server, 'audit'), patch.object(server, 'log'):
            result = server.ask_vision('vision', ['pixels'], 'Describe', 'test', 'image', respect_cancel=False)
        self.assertEqual(result, '')
        self.assertEqual(model.call_count, 2)

    def test_reference_review_has_room_and_stops_when_context_is_exhausted(self):
        with patch.object(server, 'post_ollama', return_value={
                'message': {'content': 'partial'}, 'done_reason': 'length',
                'prompt_eval_count': 12000, 'eval_count': 4384}) as model, \
                patch.object(server, 'audit'), patch.object(server, 'log'):
            result = server.ask_vision('vision', ['pixels'], 'Review', 'reference',
                                       'blueprint_review', budget=4600, respect_cancel=False)
        self.assertEqual(result, '')
        self.assertEqual(model.call_count, 1)
        self.assertEqual(model.call_args.args[1]['options']['num_ctx'], 16384)


if __name__ == '__main__':
    unittest.main()

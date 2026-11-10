"""Route scoped editing requests separately from global read-only restrictions."""
import unittest
import server
import test_conversation_options as fixtures


class RequestRoutingTests(unittest.TestCase):
    setUp = fixtures.ConversationTests.setUp
    request = fixtures.ConversationTests.request

    def test_actual_user_request_reaches_editor(self):
        instruction = ('Ajustá únicamente la distribución entre el visor y el teclado. '
            'Reducí el espacio vacío del visor y dale más altura al teclado para que sus '
            'botones tengan ancho y alto aproximadamente iguales y dejen de verse achatados. '
            'Conservá el tamaño de la ventana, todos los botones, su orden actual, textos, '
            'colores, eventos y lógica. Mantené el visor visible y alineado a la derecha. '
            'No cambies otros aspectos.')
        response, call = self.request(instruction=instruction)
        self.assertEqual(response.status_code,202)
        self.assertIs(call['target'],server.run_job)
        self.assertEqual(call['args'][1],instruction)

    def test_scoped_preservation_does_not_prevent_requested_edit(self):
        for limitation in ('No cambies otros aspectos.', 'No modifiques los colores.',
                           'No cambies nada más.', 'Sin modificar la lógica.',
                           'No edites MainWindow.xaml.cs.'):
            with self.subTest(limitation=limitation):
                response, call = self.request(instruction='Ajustá el teclado. '+limitation)
                self.assertEqual(response.status_code,202)
                self.assertIs(call['target'],server.run_job)

    def test_global_read_only_restrictions_still_block_editing(self):
        for restriction in ('No modifiques ningún archivo.', 'No edites el código.',
                            'No cambies archivos.', 'Sin modificar archivos.',
                            'Solo analizar.', 'Sólo lectura.', 'No cambies nada.',
                            'No modifiques.', 'Analizá sin modificar'):
            with self.subTest(restriction=restriction):
                response, call = self.request(instruction='Revisá el proyecto. '+restriction)
                self.assertEqual(response.status_code,202)
                self.assertIs(call['target'],server.run_analysis_job)


if __name__=='__main__': unittest.main()

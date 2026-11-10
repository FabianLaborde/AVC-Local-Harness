import unittest

from catalog_code_edit import apply_patches
from wpf_intent_patch import deterministic_required_patch
from intent_shadow import (diagnose_sources, promotable_initial_satisfaction)


class WpfIntentPatchTests(unittest.TestCase):
    def test_multiline_single_quoted_attribute_changes_only_alignment(self):
        source = ('<Grid><TextBlock Text="Datos &gt; contacto"\n'
                  " HorizontalAlignment = 'Left' /></Grid>")
        instruction = 'Alineá el título "Datos > contacto" a la derecha.'
        payload = diagnose_sources({'MainWindow.xaml': source}, instruction)
        plan = deterministic_required_patch(payload, {'MainWindow.xaml': source})
        self.assertIsNotNone(plan)
        proposed = apply_patches({'MainWindow.xaml': source}, plan['edits'])
        self.assertEqual(source.replace("'Left'", "'Right'"),
                         proposed['MainWindow.xaml'])
        verified = diagnose_sources(proposed, instruction)
        self.assertIsNotNone(promotable_initial_satisfaction(verified))

    def test_element_text_label_is_not_rewritten_without_attribute_anchor(self):
        source = '<Button HorizontalAlignment="Left">Enviar</Button>'
        payload = diagnose_sources({'MainWindow.xaml': source},
                                   'Alineá el botón "Enviar" a la derecha.')
        self.assertEqual('requires_change', payload['resolution']['status'])
        self.assertIsNone(deterministic_required_patch(
            payload, {'MainWindow.xaml': source}))

    def test_duplicate_resource_postcondition_and_changed_source_are_rejected(self):
        instruction = 'Alineá el botón "Enviar" a la derecha.'
        duplicate = ('<Grid><Button Content="Enviar" HorizontalAlignment="Left"/>'
                     '<Button Content="Enviar" HorizontalAlignment="Left"/></Grid>')
        payload = diagnose_sources({'MainWindow.xaml': duplicate}, instruction)
        self.assertIsNone(deterministic_required_patch(
            payload, {'MainWindow.xaml': duplicate}))

        resource = '<Button Content="Enviar" HorizontalAlignment="{Binding Alignment}"/>'
        payload = diagnose_sources({'MainWindow.xaml': resource}, instruction)
        self.assertIsNone(deterministic_required_patch(
            payload, {'MainWindow.xaml': resource}))

        direct = '<Button Content="Enviar" HorizontalAlignment="Left"/>'
        payload = diagnose_sources({'MainWindow.xaml': direct},
            instruction + ' Dejalo visible sin desplazamiento.')
        self.assertIsNone(deterministic_required_patch(
            payload, {'MainWindow.xaml': direct}))

        payload = diagnose_sources({'MainWindow.xaml': direct}, instruction)
        changed = direct.replace('Left', 'Center')
        self.assertIsNone(deterministic_required_patch(
            payload, {'MainWindow.xaml': changed}))


if __name__ == '__main__':
    unittest.main()

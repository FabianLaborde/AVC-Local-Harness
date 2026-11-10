"""No Ollama, Windows GUI or Flask required for deterministic workflow tests."""
import io
import tempfile
import unittest
from pathlib import Path
from PIL import Image
from visual_workflow import build_visual_cycle, _xaml_facts

XAML = '''<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
 xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
 Width="300" Height="450" Title="Calculator">
 <Grid><Button x:Name="btnSave" Content="Guardar" Width="80" Background="Green"/>
 <TextBlock Text="Estado"/></Grid></Window>'''


def png(width, height, color):
    out = io.BytesIO()
    Image.new('RGB', (width, height), color).save(out, format='PNG')
    return out.getvalue()


def semantic(ok=True, differences=None):
    return {'comparacion_valida': ok, 'diferencias': differences or []}


class VisualWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name)
        (self.project / 'MainWindow.xaml').write_text(XAML, encoding='utf8')

    def run_cycle(self, report):
        return build_visual_cycle(str(self.project), png(300, 450, 'blue'),
                                  png(300, 450, 'red'), report, 'ejecutable_publicado')

    def test_xaml_labels_and_geometry_from_real_source(self):
        cycle = self.run_cycle(semantic(True, [
            {'elemento': 'Guardar', 'tipo': 'color', 'actual': 'verde',
             'referencia': 'azul', 'evidencia': 'botón visible', 'certeza': 'media'},
        ]))
        self.assertEqual(cycle['modo'], 'SOLO_LECTURA')
        self.assertFalse(cycle['ediciones_por_avc'])
        self.assertEqual(cycle['xaml']['estado'], 'inspeccionado')
        self.assertEqual(cycle['xaml']['ventana']['Width'], '300')
        self.assertEqual(cycle['xaml']['ventana']['Height'], '450')
        diff = cycle['diferencias_vinculadas'][0]
        self.assertEqual(diff['vinculo_xaml']['estado'], 'etiqueta_coincidente')
        self.assertEqual(diff['vinculo_xaml']['nombre'], 'btnSave')
        self.assertEqual(diff['nivel_evidencia'], 'HIPOTESIS_VISUAL')
        self.assertIn('autorización explícita', cycle['propuesta'])
        self.assertEqual(cycle['mediciones']['actual_px'], [300, 450])
        self.assertEqual(cycle['mediciones']['referencia_px'], [300, 450])
        self.assertTrue(cycle['capturas']['actual'].startswith('data:image/png;base64,'))
        # No artifacts in the project: visual workflow only reads XAML.
        self.assertEqual([p.name for p in self.project.iterdir()], ['MainWindow.xaml'])

    def test_no_suggested_changes_on_invalid_model_or_empty_differences(self):
        failed = self.run_cycle(semantic(False, [{'elemento': 'Guardar', 'tipo': 'color'}]))
        self.assertEqual(failed['propuesta'], '')
        self.assertEqual(failed['etapas'][2]['estado'], 'inconclusa')
        empty = self.run_cycle(semantic(True))
        self.assertEqual(empty['propuesta'], '')
        self.assertIn('Sin diferencias', ' '.join(empty['advertencias']))

    def test_missing_or_ambiguous_xaml_keeps_visual_analysis(self):
        (self.project / 'MainWindow.xaml').unlink()
        (self.project / 'A.xaml').write_text('<Window/>', encoding='utf8')
        (self.project / 'B.xaml').write_text('<Window/>', encoding='utf8')
        cycle = self.run_cycle(semantic(True, [
            {'elemento': 'Aceptar', 'tipo': 'forma', 'actual': 'cuadrado', 'referencia': 'redondo'}
        ]))
        self.assertEqual(cycle['xaml']['estado'], 'no_disponible')
        self.assertIn('varios XAML', cycle['xaml']['mensaje'])
        self.assertEqual(cycle['diferencias_vinculadas'][0]['vinculo_xaml']['estado'],
                         'sin_vinculo_inequivoco')
        self.assertTrue(cycle['propuesta'])

    def test_single_non_main_xaml_supported(self):
        (self.project / 'MainWindow.xaml').unlink()
        (self.project / 'Home.xaml').write_text('<Window Width="200"/>', encoding='utf8')
        facts = _xaml_facts(str(self.project))
        self.assertEqual(facts['archivo'], 'Home.xaml')

    def test_different_aspect_ratios_warning_no_fake_alignment(self):
        cycle = build_visual_cycle(str(self.project), png(300, 450, 'white'),
                                   png(900, 450, 'white'), semantic(True), 'captura_ventana')
        self.assertTrue(cycle['mediciones']['relacion_aspecto_divergente'])
        self.assertIn('relación de aspecto', ' '.join(cycle['advertencias']))

    def test_xml_security_failures_do_not_break_visual_comparison(self):
        (self.project / 'MainWindow.xaml').write_text('<!DOCTYPE x [<!ENTITY a "secret">]><Window/>', encoding='utf8')
        cycle = self.run_cycle(semantic(True))
        self.assertEqual(cycle['xaml']['estado'], 'no_disponible')
        self.assertIn('No se puede inspeccionar XAML', cycle['xaml']['mensaje'])

    def test_duplicate_matching_labels_do_not_guess_element(self):
        (self.project / 'MainWindow.xaml').write_text('''<Window><StackPanel><Button Content="Enviar"/>
        <Button Content="Enviar"/></StackPanel></Window>''', encoding='utf8')
        cycle = self.run_cycle(semantic(True, [{'elemento': 'Enviar', 'tipo': 'color'}]))
        self.assertEqual(cycle['diferencias_vinculadas'][0]['vinculo_xaml']['estado'],
                         'sin_vinculo_inequivoco')

    def test_geometry_review_survives_into_cycle_and_proposal(self):
        report = semantic(True, [{'elemento': 'Distribución global', 'tipo': 'distribucion',
                                 'actual': 'visor pequeño', 'referencia': 'visor alto'}])
        report['revision_geometrica'] = {
            'proporciones': {'estado': 'diferencia_observada', 'evidencia': 'Distinta ocupación del visor'}}
        cycle = self.run_cycle(report)
        self.assertEqual(cycle['revision_geometrica'], report['revision_geometrica'])
        self.assertIn('Distinta ocupación del visor', cycle['propuesta'])

    def test_uncertain_geometry_remains_partial_in_workflow(self):
        report = semantic(True)
        report['estado'] = 'COMPARACION_PARCIAL'
        cycle = self.run_cycle(report)
        self.assertEqual(cycle['etapas'][2]['estado'], 'parcial')
        self.assertIn('aspectos no verificables', ' '.join(cycle['advertencias']))

    def test_unsubstantiated_shape_is_visible_as_diagnostic_but_not_proposed(self):
        cycle = self.run_cycle(semantic(True, [{'elemento': 'Guardar', 'tipo': 'forma',
                                              'utilizable_en_propuesta': False}]))
        self.assertEqual(cycle['propuesta'], '')
        self.assertEqual(cycle['etapas'][3]['estado'], 'sin_propuesta')
        self.assertEqual(len(cycle['diferencias_vinculadas']), 1)
        self.assertIn('se excluyeron', ' '.join(cycle['advertencias']))


if __name__ == '__main__':
    unittest.main()

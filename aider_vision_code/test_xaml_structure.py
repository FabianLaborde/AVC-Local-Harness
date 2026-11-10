import tempfile
import unittest
from pathlib import Path
from xaml_structure import inspect_xaml, structural_report, conflicting_dimensions

XAML = '''<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
 xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
 Width="300" Height="450" Title="Calculator">
 <Grid><Grid.RowDefinitions><RowDefinition Height="Auto"/></Grid.RowDefinitions>
 <Button x:Name="Calculate" Grid.Row="0" Width="80" Background="Red">Calcular</Button>
 </Grid></Window>'''

class XamlStructureTests(unittest.TestCase):
    def test_hierarchy_names_properties(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'MainWindow.xaml'
            path.write_text(XAML, encoding='utf8')
            data = inspect_xaml(path)
            self.assertEqual(data['elements'][0]['properties']['Width'], '300')
            button = next(item for item in data['elements'] if item['type'] == 'Button')
            self.assertEqual(button['name'], 'Calculate')
            self.assertEqual(button['properties']['Grid.Row'], '0')
            self.assertEqual(data['elements'][button['parent']]['type'], 'Grid')
            report = structural_report(data)
            self.assertIn('Width=\'300\'', report)
            self.assertIn('DPI', report)

    def test_warns_only_for_explicit_wrong_dimensions(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'MainWindow.xaml'; path.write_text(XAML, encoding='utf8')
            data = inspect_xaml(path)
            self.assertEqual(len(conflicting_dimensions('El ancho es 450 y el alto es 300.',data)),2)
            self.assertEqual(conflicting_dimensions('El Width es 300 y Height=450.',data),[])
            self.assertEqual(conflicting_dimensions('Hay un Button de 80 x 20.',data),[])

    def test_rejects_doctype_and_invalid_xml(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'MainWindow.xaml'
            path.write_text('<!DOCTYPE foo [<!ENTITY x "hello">]><Window/>',encoding='utf8')
            with self.assertRaises(ValueError): inspect_xaml(path)
            path.write_text('<Window>',encoding='utf8')
            with self.assertRaises(ValueError): inspect_xaml(path)

if __name__ == '__main__': unittest.main()

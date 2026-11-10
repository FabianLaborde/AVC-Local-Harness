import tempfile
import unittest
from pathlib import Path
from xaml_structure import inspect_xaml, declared_layout_report


class LayoutReportTest(unittest.TestCase):
    def test_grid_tracks_and_positions(self):
        xaml = '''<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation" xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"><Grid><Grid.RowDefinitions><RowDefinition Height="Auto"/><RowDefinition Height="*"/></Grid.RowDefinitions><Grid.ColumnDefinitions><ColumnDefinition Width="2*"/><ColumnDefinition Width="*"/></Grid.ColumnDefinitions><Button x:Name="Action" Grid.Row="1" Grid.Column="1" Grid.ColumnSpan="2"/><TextBlock/></Grid></Window>'''
        with tempfile.TemporaryDirectory() as d:
            file = Path(d)/'MainWindow.xaml'
            file.write_text(xaml, encoding='utf-8')
            report = declared_layout_report(inspect_xaml(file))
        self.assertIn("filas=['Auto', '*']", report)
        self.assertIn("columnas=['2*', '*']", report)
        self.assertIn('Button Action: fila=1, columna=1', report)
        self.assertIn('TextBlock #', report)
        self.assertIn('fila=0 (predeterminada)', report)
        self.assertIn('no coordenadas en píxeles', report)


if __name__ == '__main__':
    unittest.main()

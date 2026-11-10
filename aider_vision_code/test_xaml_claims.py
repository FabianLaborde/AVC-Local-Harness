import tempfile
import unittest
from pathlib import Path
from xaml_claims import verify_xaml_claims, claims_report
from xaml_structure import inspect_xaml

SAMPLE = '''<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
 xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml" Width="300" Height="450" Title="Calculator">
 <Grid><Button x:Name="Aceptar" Width="80" Background="Red"/></Grid>
 </Window>'''

class ClaimsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        file = Path(self.temp.name) / 'MainWindow.xaml'
        file.write_text(SAMPLE, encoding='utf-8')
        self.data = inspect_xaml(file)

    def test_matches_and_disagreements(self):
        result = verify_xaml_claims('Window Width=300 Height=450. El Button Aceptar tiene Background=Blue. ', self.data)
        self.assertEqual(len(result['verified']), 2)
        self.assertEqual(len(result['contradictory']), 1)
        self.assertEqual(result['contradictory'][0]['declared'], 'Red')

    def test_missing_is_unknown_not_false(self):
        result = verify_xaml_claims('Window BorderBrush=Black.', self.data)
        self.assertEqual(len(result['unverifiable']), 1)
        self.assertFalse(result['contradictory'])

    def test_ambiguous_controls_not_guessed(self):
        extra = '''<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"><Grid><Button Width="10"/><Button Width="20"/></Grid></Window>'''
        file = Path(self.temp.name) / 'Other.xaml'
        file.write_text(extra, encoding='utf8')
        data = inspect_xaml(file)
        result = verify_xaml_claims('Button Width=10.', data)
        self.assertFalse(result['verified'])
        self.assertFalse(result['contradictory'])

    def test_spanish_width_and_height(self):
        result = verify_xaml_claims('El ancho de la ventana es 450 y el alto de la ventana es 300.', self.data)
        self.assertEqual(len(result['contradictory']), 2)
        self.assertIn('CONTRADICEN', claims_report(result))

    def test_no_detected_claim_is_not_success(self):
        result = verify_xaml_claims('La ventana tiene botones y un aspecto bonito.', self.data)
        self.assertEqual(sum(len(result[k]) for k in ('verified','contradictory','unverifiable')), 0)
        self.assertIn('No se encontraron', claims_report(result))

if __name__ == '__main__':
    unittest.main()

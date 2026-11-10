import unittest
from unittest.mock import patch
from vision_semantica import analyze, _differences, _inventory

class SemanticTests(unittest.TestCase):
    def test_uncertain_controls(self):
        results=_differences({'diferencias':[{'elemento':'boton 7','tipo':'forma','actual':'redondo','referencia':'rectangular','evidencia':'inventario visual','certeza':'alta'}]})
        self.assertEqual(results[0]['estado'],'HIPOTESIS_NO_VERIFICADA')
    def test_malformed_difference_rejected(self):
        with self.assertRaises(ValueError):
            _differences({'diferencias':'texto'})
    def test_bad_list_results_in_abstention(self):
        answers=[{'elementos':[{'tipo':'boton','etiqueta':'A','apariencia':'circular'}]},
                 {'elementos':[{'tipo':'boton','etiqueta':'A','apariencia':'cuadrado'}]},
                 {'diferencias':'texto'}, {'error':'sin comparacion'}]
        def post(url,payload,timeout):
            return {'message':{'content':__import__('json').dumps(answers.pop(0))}}
        with patch('vision_semantica._comparison_image', return_value='comparacion'), patch('vision_semantica._image_observations', return_value={}):
            result=analyze(post,'modelo','actual','referencia')
        self.assertFalse(result['comparacion_valida'])
        self.assertEqual(result['diferencias'],[])
        self.assertEqual(len(result['actual']['elementos']),1)
    def test_valid_comparison(self):
        answers=[{'elementos':[{'tipo':'boton','etiqueta':'A','apariencia':'circular'}]},
                 {'elementos':[{'tipo':'boton','etiqueta':'A','apariencia':'cuadrado'}]},
                 {'revision_geometrica': {axis: {'estado': 'sin_diferencia_observable', 'evidencia': 'Distribución similar en ambas imágenes'}
                                          for axis in ('distribucion', 'proporciones', 'espaciado')},
                  'diferencias':[{'elemento':'A','tipo':'forma','actual':'circular','referencia':'cuadrado','evidencia':'distintas descripciones','certeza':'media'}]},
                 {'diferencias': []}]
        def post(url,payload,timeout):
            return {'message':{'content':__import__('json').dumps(answers.pop(0))}}
        with patch('vision_semantica._comparison_image', return_value='comparacion'), patch('vision_semantica._image_observations', return_value={}):
            result=analyze(post,'modelo','actual','referencia')
        self.assertTrue(result['comparacion_valida'])
        self.assertEqual(len(result['diferencias']),1)

if __name__=='__main__': unittest.main()

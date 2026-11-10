import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from catalog_dual_review import LocalAIReviewError
from code_semantic_review import (deterministic_dark_theme_fact,
                                  deterministic_initial_satisfaction,
                                  deterministic_xaml_fact, review_code_candidate,
                                  source_diff)


def response(value):
    return {'choices':[{'message':{'content':json.dumps(value)}}]}


class CodeSemanticReviewTests(unittest.TestCase):
    def test_preflight_proves_complete_dark_theme_without_candidate(self):
        dark={'MainWindow.xaml':('<Window Background="#121212"><Border Background="#242424">'
            '<TextBlock Foreground="White"/></Border></Window>')}
        result=deterministic_initial_satisfaction(dark,'Aplicá un tema oscuro coherente')
        self.assertTrue(result['initial_satisfies'])
        self.assertEqual(result['decision_source'],'deterministic_dark_theme_preflight')
        light={'MainWindow.xaml':'<Window Background="White"><TextBlock Foreground="Black"/></Window>'}
        self.assertIsNone(deterministic_initial_satisfaction(light,'Aplicá un tema oscuro'))

    def test_preflight_requires_one_compatible_explicit_alignment(self):
        right={'MainWindow.xaml':'<TextBlock Text="Datos" HorizontalAlignment="Right"/>'}
        result=deterministic_initial_satisfaction(right,'Alineá el título a la derecha')
        self.assertTrue(result['initial_satisfies'])
        self.assertEqual(result['fact']['tag'],'TextBlock')
        ambiguous={'MainWindow.xaml':('<Grid><TextBlock HorizontalAlignment="Right"/>'
            '<Button HorizontalAlignment="Right"/></Grid>')}
        self.assertIsNone(deterministic_initial_satisfaction(
            ambiguous,'Alineá el título a la derecha'))
        wrong_kind={'MainWindow.xaml':'<Button HorizontalAlignment="Right"/>'}
        self.assertIsNone(deterministic_initial_satisfaction(
            wrong_kind,'Alineá el título a la derecha'))

    def test_diff_contains_only_changed_sources(self):
        before={'MainWindow.xaml':'<TextBlock HorizontalAlignment="Right"/>',
                'App.xaml':'same'}
        after=dict(before,**{'MainWindow.xaml':'<TextBlock HorizontalAlignment="Left"/>'})
        diff=source_diff(before,after)
        self.assertIn('Right',diff);self.assertIn('Left',diff)
        self.assertNotIn('App.xaml',diff)

    def test_initially_satisfied_opposite_candidate_is_not_approved(self):
        # Even a wrong LocalAI approval cannot reverse an exact source fact.
        verdict=dict(approved=True,initial_satisfies=False,candidate_satisfies=True,
                     reason='Aprobado por error',missing_intents=[],contradictions=[])
        with tempfile.TemporaryDirectory() as d:
            api=Mock()
            result=review_code_candidate(api,
                {'MainWindow.xaml':'<TextBlock HorizontalAlignment="Right"/>'},
                {'MainWindow.xaml':'<TextBlock HorizontalAlignment="Left"/>'},
                'Alineá el título a la derecha',Path(d),caller=lambda _:response(verdict))
            self.assertTrue(result['initial_satisfies'])
            self.assertFalse(result['approved'])
            self.assertEqual('deterministic_xaml',result['decision_source'])
            self.assertTrue(result['reviewer_disagreed'])
            self.assertTrue((Path(d)/'code_semantic_review_1.json').is_file())

    def test_exact_requested_direction_overrides_wrong_localai_rejection(self):
        verdict=dict(approved=False,initial_satisfies=True,candidate_satisfies=False,
                     reason='La base ya cumplía',missing_intents=[],
                     contradictions=['El candidato no alinea a la derecha'])
        with tempfile.TemporaryDirectory() as d:
            result=review_code_candidate(Mock(),
                {'MainWindow.xaml':'<TextBlock HorizontalAlignment="Left"/>'},
                {'MainWindow.xaml':'<TextBlock HorizontalAlignment="Right"/>'},
                'Alineá el título a la derecha, conservá todo lo demás',Path(d),
                caller=lambda _:response(verdict))
            self.assertTrue(result['approved'])
            self.assertFalse(result['initial_satisfies'])
            self.assertTrue(result['candidate_satisfies'])
            self.assertTrue(result['reviewer_disagreed'])
            evidence=json.loads((Path(d)/'code_semantic_review_1.json').read_text())
            self.assertFalse(evidence['result']['localai_verdict']['approved'])

    def test_additional_change_makes_deterministic_fact_inconclusive(self):
        before={'MainWindow.xaml':'<TextBlock Text="Título" HorizontalAlignment="Left"/>'}
        after={'MainWindow.xaml':'<TextBlock Text="Otro" HorizontalAlignment="Right"/>'}
        self.assertIsNone(deterministic_xaml_fact(
            before,after,'Alineá el título a la derecha'))

    def test_dark_theme_fact_reports_light_surfaces_and_layout_changes(self):
        before={'MainWindow.xaml':('<Window Background="LightBlue"><Border Background="White">'
            '<TextBlock Text="Título" Foreground="Black" HorizontalAlignment="Right"/>'
            '</Border></Window>')}
        after={'MainWindow.xaml':('<Window Background="#212121"><Border Background="#FFFFFF">'
            '<TextBlock Text="Título" Foreground="#000000" HorizontalAlignment="Left"/>'
            '</Border></Window>')}
        fact=deterministic_dark_theme_fact(before,after,
            'Aplicá un tema oscuro y conservá distribución, contenido y dimensiones')
        self.assertFalse(fact['candidate_satisfies'])
        self.assertTrue(any('superficie clara' in item for item in fact['missing_intents']))
        self.assertTrue(any('HorizontalAlignment' in item for item in fact['contradictions']))

    def test_dark_theme_fact_approves_palette_with_contrast_and_same_structure(self):
        before={'MainWindow.xaml':('<Window Background="White"><Border Background="#E6E6FA">'
            '<TextBlock Text="Título" Foreground="Black" HorizontalAlignment="Right"/>'
            '</Border></Window>')}
        after={'MainWindow.xaml':('<Window Background="#121212"><Border Background="#2C2C2C">'
            '<TextBlock Text="Título" Foreground="White" HorizontalAlignment="Right"/>'
            '</Border></Window>')}
        fact=deterministic_dark_theme_fact(before,after,
            'Aplicá un tema oscuro y conservá distribución, contenido y dimensiones')
        self.assertTrue(fact['approved'])
        self.assertTrue(fact['candidate_satisfies'])
        self.assertFalse(fact['initial_satisfies'])

    def test_dark_theme_fact_resolves_static_brush_resources(self):
        before={'App.xaml':'<Application xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"><Application.Resources/></Application>',
                'MainWindow.xaml':'<Window Background="White"><TextBlock Foreground="Black"/></Window>'}
        after={'App.xaml':('<Application xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml">'
                '<Application.Resources><SolidColorBrush x:Key="Surface" Color="#121212"/>'
                '<SolidColorBrush x:Key="Ink" Color="#FFFFFF"/></Application.Resources></Application>'),
               'MainWindow.xaml':('<Window Background="{StaticResource Surface}">'
                '<TextBlock Foreground="{StaticResource Ink}"/></Window>')}
        fact=deterministic_dark_theme_fact(before,after,'Aplicá un tema oscuro')
        self.assertTrue(fact['approved'])
        self.assertFalse(fact['missing_intents'])

    def test_without_explicit_alignment_request_localai_remains_authoritative(self):
        verdict=dict(approved=False,initial_satisfies=False,candidate_satisfies=False,
                     reason='No se acredita',missing_intents=['pedido'],contradictions=[])
        with tempfile.TemporaryDirectory() as d:
            result=review_code_candidate(Mock(),
                {'MainWindow.xaml':'<TextBlock HorizontalAlignment="Left"/>'},
                {'MainWindow.xaml':'<TextBlock HorizontalAlignment="Right"/>'},
                'Mejorá el título',Path(d),caller=lambda _:response(verdict))
            self.assertFalse(result['approved'])
            self.assertEqual('localai',result['decision_source'])
            self.assertFalse(result['reviewer_disagreed'])

    def test_claim_that_base_already_satisfies_request_is_rechecked(self):
        first=dict(approved=False,initial_satisfies=True,candidate_satisfies=False,
                   reason='La base ya cumple',missing_intents=[],
                   contradictions=['Cambió el color solicitado'])
        corrected=dict(approved=False,initial_satisfies=False,candidate_satisfies=False,
                       reason='La base es clara y el candidato es oscuro solo en parte',
                       missing_intents=['Completar el tema en los controles restantes'],
                       contradictions=[])
        replies=iter((response(first),response(corrected)))
        with tempfile.TemporaryDirectory() as d:
            result=review_code_candidate(Mock(),
                {'MainWindow.xaml':'<Window Background="LightBlue"><TextBox Background="White"/></Window>'},
                {'MainWindow.xaml':'<Window Background="#121212"><TextBox Background="White"/></Window>'},
                'Cambiá todo el formulario a un tema oscuro y conservá la distribución',
                Path(d),caller=lambda _:next(replies))
            self.assertFalse(result['initial_satisfies'])
            self.assertFalse(result['candidate_satisfies'])
            self.assertEqual('deterministic_dark_theme',result['decision_source'])
            self.assertTrue(result['reviewer_disagreed'])
            self.assertFalse(result['recheck_performed'])
            evidence=json.loads((Path(d)/'code_semantic_review_1.json').read_text())
            self.assertIsNone(evidence['confirmation_response'])
            self.assertEqual('dark_theme',evidence['result']['deterministic_fact']['fact']['kind'])

    def test_generic_initial_compliance_without_literal_fact_fails_closed(self):
        verdict=dict(approved=False,initial_satisfies=True,candidate_satisfies=False,
                     reason='La base ya cumple',missing_intents=[],
                     contradictions=['El candidato se aleja del pedido'])
        replies=iter((response(verdict),response(verdict)))
        with tempfile.TemporaryDirectory() as d:
            result=review_code_candidate(Mock(),
                {'MainWindow.xaml':'<Window Background="#121212"/>'},
                {'MainWindow.xaml':'<Window Background="White"/>'},
                'Conservá la apariencia vigente',Path(d),caller=lambda _:next(replies))
            self.assertFalse(result['initial_satisfies'])
            self.assertFalse(result['approved'])
            self.assertFalse(result['candidate_satisfies'])
            self.assertEqual('localai_initial_guard',result['decision_source'])
            self.assertTrue(result['reviewer_disagreed'])
            self.assertTrue(result['initial_guard_overrode'])

    def test_ollama_adjudication_replaces_generic_guard_with_actionable_feedback(self):
        local=dict(approved=False,initial_satisfies=True,candidate_satisfies=False,
                   reason='La base ya cumple',missing_intents=[],
                   contradictions=['#212121 no es oscuro'])
        adjudicated=dict(approved=False,initial_satisfies=False,candidate_satisfies=False,
            reason='El candidato es parcial y altera la distribución',
            missing_intents=['Border Background=#FFFFFF permanece claro'],
            contradictions=['TextBlock HorizontalAlignment cambia Right a Left'])
        replies=iter((response(local),response(local)))
        ollama={'message':{'content':json.dumps(adjudicated)}}
        with tempfile.TemporaryDirectory() as d:
            result=review_code_candidate(Mock(),
                {'MainWindow.xaml':'<Window Background="LightBlue"><TextBlock HorizontalAlignment="Right"/></Window>'},
                {'MainWindow.xaml':'<Window Background="#212121"><TextBlock HorizontalAlignment="Left"/></Window>'},
                'Modernizá la paleta y conservá la distribución',Path(d),
                caller=lambda _:next(replies),ollama_model='coder',
                adjudicator=lambda _:ollama)
            self.assertEqual('ollama_adjudication',result['decision_source'])
            self.assertFalse(result['initial_satisfies'])
            self.assertFalse(result['candidate_satisfies'])
            self.assertIn('Background=#FFFFFF',result['missing_intents'][0])
            self.assertTrue(result['adjudication_performed'])
            evidence=json.loads((Path(d)/'code_semantic_review_1.json').read_text())
            self.assertIsNotNone(evidence['adjudication_response'])

    def test_ollama_adjudication_can_approve_a_valid_candidate(self):
        local=dict(approved=False,initial_satisfies=True,candidate_satisfies=False,
                   reason='La base ya cumple',missing_intents=[],contradictions=[])
        approved=dict(approved=True,initial_satisfies=False,candidate_satisfies=True,
                      reason='El candidato cumple',missing_intents=[],contradictions=[])
        replies=iter((response(local),response(local)))
        with tempfile.TemporaryDirectory() as d:
            result=review_code_candidate(Mock(),
                {'MainWindow.xaml':'<Window Background="White"/>'},
                {'MainWindow.xaml':'<Window Background="#121212"/>'},
                'Cambiá el color de fondo',Path(d),caller=lambda _:next(replies),
                ollama_model='coder',adjudicator=lambda _: {
                    'message':{'content':json.dumps(approved)}})
            self.assertTrue(result['approved'])
            self.assertTrue(result['candidate_satisfies'])
            self.assertEqual('ollama_adjudication',result['decision_source'])

    def test_incomplete_or_unavailable_review_fails_closed(self):
        before={'MainWindow.xaml':'a'};after={'MainWindow.xaml':'b'}
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(LocalAIReviewError):
                review_code_candidate(Mock(),before,after,'Cambiar',Path(d),
                    caller=lambda _:response({'approved':True}))
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(LocalAIReviewError):
                review_code_candidate(Mock(),before,after,'Cambiar',Path(d),
                    caller=lambda _:(_ for _ in ()).throw(RuntimeError('offline')))


if __name__=='__main__':unittest.main()

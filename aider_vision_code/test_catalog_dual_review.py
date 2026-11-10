import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from catalog_dual_review import (LocalAIReviewError, intent_checks, plan_diff,
                                 review_candidate)


class CatalogDualReviewTests(unittest.TestCase):
    def plan(self):
        return {
            'supported':True,'limitations':[],'title':'','width':1000,'height':560,
            'background':['#EFF4F9','#EFF4F9'],'density':'comfortable',
            'nodes':[
                dict(id='layout',parent='root',kind='column',text='',weight=1),
                dict(id='body',parent='layout',kind='row',text='',weight=1),
                dict(id='menu',parent='body',kind='column',text='',weight=1),
                dict(id='option',parent='menu',kind='text',text='Inicio',weight=1),
                dict(id='center',parent='body',kind='column',text='',weight=5),
                dict(id='title',parent='center',kind='title',text='Panel',weight=1,
                     foreground='#112233'),
                dict(id='actions',parent='center',kind='row',text='',weight=1),
                dict(id='save',parent='actions',kind='button',text='Guardar',weight=1,
                     action='none',background='#2563EB'),
            ],
            'independent_scroll':{'container':'center','fixed_children':['actions']},
        }

    def response(self, approved=True, reason='Correcto', missing=None):
        content=json.dumps({'approved':approved,'reason':reason,
                            'missing_intents':missing or [],'contradictions':[]})
        return {'choices':[{'message':{'content':content}}]}

    def test_compound_request_detects_omitted_no_scroll_intent(self):
        before=self.plan();partial=copy.deepcopy(before)
        next(n for n in partial['nodes'] if n['id']=='center')['weight']=6
        checks=intent_checks(before,partial,
            'Ampliá un poco más el área central, conservá todo lo demás y que no sea necesario el scroll.')
        values={check['intent']:check['passed'] for check in checks}
        self.assertTrue(values['grow_central_region'])
        self.assertFalse(values['no_scroll'])
        self.assertTrue(values['preserve_controls'])
        self.assertTrue(values['preserve_texts'])
        self.assertTrue(values['preserve_sizes'])
        self.assertEqual(plan_diff(before,partial)['nodes'][0]['id'],'center')

    def test_review_requires_deterministic_checks_and_localai_approval(self):
        before=self.plan();after=copy.deepcopy(before)
        next(n for n in after['nodes'] if n['id']=='center')['weight']=6
        after.pop('independent_scroll');after['viewport_fit']='fit'
        instruction=('Ampliá el centro y mostrá todo sin desplazamiento; '
                     'se permite ajustar la escala.')
        with tempfile.TemporaryDirectory() as d:
            approved=review_candidate(Mock(),before,after,instruction,Path(d),
                                      caller=lambda payload:self.response())
            self.assertTrue(approved['approved'])
            self.assertTrue((Path(d)/'localai_review_1.json').is_file())
            rejected=review_candidate(Mock(),before,after,instruction,Path(d),attempt=2,
                caller=lambda payload:self.response(False,'Falta evidencia',['objetivo']))
            self.assertFalse(rejected['approved'])
            self.assertTrue((Path(d)/'localai_review_2.json').is_file())

    def test_preserve_all_rejects_global_viewport_scaling(self):
        before=self.plan();after=copy.deepcopy(before)
        next(n for n in after['nodes'] if n['id']=='center')['weight']=6
        after.pop('independent_scroll');after['viewport_fit']='fit'
        checks=intent_checks(before,after,
            'Ampliá el centro, conservá todo lo demás y que no sea necesario el scroll.')
        values={check['intent']:check['passed'] for check in checks}
        self.assertTrue(values['no_scroll']);self.assertFalse(values['preserve_sizes'])

    def test_literal_no_reduction_is_a_preserve_sizes_intent(self):
        before=self.plan();after=copy.deepcopy(before)
        next(n for n in after['nodes'] if n['id']=='center')['weight']=6
        after.pop('independent_scroll');after['window_fit']='content_height'
        instruction=('Ampliá el área central para que todo el contenido sea visible sin '
                     'desplazamiento vertical. Conservá los textos, controles, colores, '
                     'proporciones generales y funciones existentes. No reduzcas el tamaño '
                     'de los elementos.')
        values={check['intent']:check['passed']
                for check in intent_checks(before,after,instruction)}
        self.assertTrue(values['no_scroll'])
        self.assertTrue(values['grow_central_region'])
        self.assertTrue(values['preserve_sizes'])

    def test_permitted_window_width_does_not_mean_control_resize(self):
        before=self.plan();after=copy.deepcopy(before);after['width']=before['width']+240
        instruction=('Mantené el tamaño de los textos y controles. '
                     'Podés aumentar el ancho de la ventana.')
        values={check['intent']:check['passed']
                for check in intent_checks(before,after,instruction)}
        self.assertTrue(values['preserve_sizes'])

    def test_avoid_vertical_scrolling_is_measured(self):
        before=self.plan();after=copy.deepcopy(before)
        checks=intent_checks(before,after,'Evitá el desplazamiento vertical.')
        values={check['intent']:check['passed'] for check in checks}
        self.assertIn('no_scroll',values)
        self.assertFalse(values['no_scroll'])

    def test_relayout_requires_real_hierarchy_change(self):
        before=self.plan();before['nodes'].extend([
            dict(id='card_a',parent='center',kind='card',text='',weight=1),
            dict(id='card_b',parent='center',kind='card',text='',weight=1),
        ])
        instruction='Reorganizá el contenido y distribuí las secciones en más columnas.'
        superficial=copy.deepcopy(before);superficial['density']='compact'
        failed={check['intent']:check['passed']
                for check in intent_checks(before,superficial,instruction)}
        self.assertFalse(failed['reflow_sections'])
        grouped=copy.deepcopy(before)
        grouped['nodes'].append(dict(id='new_row',parent='center',kind='row',text='',weight=1))
        next(n for n in grouped['nodes'] if n['id']=='card_a')['parent']='new_row'
        next(n for n in grouped['nodes'] if n['id']=='card_b')['parent']='new_row'
        passed={check['intent']:check['passed']
                for check in intent_checks(before,grouped,instruction)}
        self.assertTrue(passed['reflow_sections'])
        preserved={check['intent']:check['passed'] for check in intent_checks(
            before,grouped,instruction+' Conservá todos los controles.')}
        self.assertTrue(preserved['preserve_controls'])

    def test_failed_check_overrides_false_positive_model_approval(self):
        before=self.plan();after=copy.deepcopy(before)
        next(n for n in after['nodes'] if n['id']=='center')['weight']=6
        with tempfile.TemporaryDirectory() as d:
            result=review_candidate(Mock(),before,after,
                'Ampliá el centro y que no sea necesario el scroll.',Path(d),
                caller=lambda payload:self.response(True))
        self.assertFalse(result['approved'])
        self.assertIn('no_scroll',result['failed_checks'])

    def test_unavailable_reviewer_blocks_and_saves_evidence(self):
        before=self.plan();after=copy.deepcopy(before);after['viewport_fit']='fit'
        after.pop('independent_scroll')
        def unavailable(payload):raise LocalAIReviewError('sin servicio')
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(LocalAIReviewError,'sin servicio'):
                review_candidate(Mock(),before,after,'Sin scroll',Path(d),caller=unavailable)
            evidence=json.loads((Path(d)/'localai_review_1.json').read_text(encoding='utf8'))
            self.assertEqual(evidence['error'],'sin servicio')


if __name__=='__main__':unittest.main()

"""Reference/attempt/final provenance and explicit removal permissions."""
import base64
import unittest
from unittest.mock import patch

import edit_inspection as evidence
import server
import test_edit_inspection as fixtures
from visual_history import empty_evidence, save_attempt, image_url


class EvidenceTests(unittest.TestCase):
    def test_successful_retry_clears_previous_capture_failure(self):
        data=empty_evidence()
        data['ultimo_fallo']={'numero':1,'etapa':'compilacion','detalle':'bad XAML'}
        save_attempt(data,2,b'success')
        self.assertIsNone(data['ultimo_fallo'])
    def test_reference_mime_matches_original_jpeg_or_webp(self):
        self.assertTrue(image_url(b'\xff\xd8jpeg').startswith('data:image/jpeg;'))
        self.assertTrue(image_url(b'RIFF1234WEBPdata').startswith('data:image/webp;'))

    def test_restored_final_does_not_replace_attempt_and_report_is_copied(self):
        data=empty_evidence(7)
        report={'estado':'no_cumple_medicion','medicion_uia':{'status':'differs'}}
        save_attempt(data,1,b'attempt',report)
        data.update(despues='restored',restaurado=True)
        report['medicion_uia']['status']='changed'
        self.assertNotEqual(data['intento'],data['despues'])
        self.assertEqual(data['intento_report']['medicion_uia']['status'],'differs')

    def test_new_capture_cannot_inherit_previous_report(self):
        data=empty_evidence()
        save_attempt(data,1,b'first',{'estado':'old'})
        save_attempt(data,2,b'second')
        self.assertIsNone(data['intento_report'])
        save_attempt(data,1,report={'estado':'stale'})
        self.assertIsNone(data['intento_report'])

    def test_new_chat_clears_reference_attempt_and_restoration(self):
        with patch.dict(server.STATE,running=False),patch.dict(server.VISUAL_CAPTURES,
                referencia='private',intento='private',intento_report={'old':'private'},restaurado=True):
            response=server.app.test_client().post('/api/new-chat')
            self.assertEqual(response.status_code,200)
            self.assertIsNone(server.VISUAL_CAPTURES['referencia'])
            self.assertIsNone(server.VISUAL_CAPTURES['intento'])
            self.assertIsNone(server.VISUAL_CAPTURES['intento_report'])
            self.assertFalse(server.VISUAL_CAPTURES['restaurado'])

    def test_old_job_reference_and_attempt_are_not_returned(self):
        with patch.dict(server.STATE,job_id=15),patch.dict(server.VISUAL_CAPTURES,
                job_id=14,referencia='old',intento='old',restaurado=True):
            response=server.app.test_client().get('/api/job/visual')
        self.assertIsNone(response.json['referencia'])
        self.assertIsNone(response.json['intento'])
        self.assertFalse(response.json['restaurado'])
        self.assertEqual(response.headers['Cache-Control'],'no-store')


class RemovalCriteriaTests(unittest.TestCase):
    def test_allowed_button_removal_does_not_disable_protection_for_others(self):
        before=fixtures.observation(labels=('A','B','±'))
        after=fixtures.observation(2,labels=('A',))
        criteria=evidence.criteria_from({'allowed_removals':'±'})
        report=evidence.compare_inspections(before,after,criteria)
        self.assertEqual(report['status'],'differs')
        self.assertEqual(report['buttons']['allowed_removed'],[('name','±')])
        self.assertEqual(report['buttons']['unexpected_missing'],[('name','B')])
        after=fixtures.observation(2,labels=('A','B'))
        self.assertEqual(evidence.compare_inspections(before,after,criteria)['status'],'measured')

    def test_duplicate_names_are_not_silently_authorized(self):
        before=fixtures.observation(labels=('A','A','B'))
        after=fixtures.observation(2,labels=('A','B'))
        report=evidence.compare_inspections(before,after,evidence.criteria_from({'allowed_removals':'A'}))
        self.assertEqual(report['status'],'differs')
        self.assertEqual(report['buttons']['allowed_removed'],[])

    def test_expected_order_and_removal_of_same_name_are_rejected_before_editor(self):
        with self.assertRaisesRegex(ValueError,'contradictorios'):
            evidence.criteria_from({'expected_order':'A | B','allowed_removals':'B'})
        with patch.object(server.threading,'Thread') as thread:
            response=server.app.test_client().post('/api/job',json={
                'inspection_criteria':{'expected_order':'A | B','allowed_removals':'B'}})
        self.assertEqual(response.status_code,400)
        thread.assert_not_called()


class RestorationEvidenceTests(unittest.TestCase):
    setUp=fixtures.EditCycleTests.setUp
    run_edit=fixtures.EditCycleTests.run_edit

    def test_build_failure_explains_missing_attempt_and_survives_restoration(self):
        (self.project/'Aplicacion.csproj').write_text('<Project/>')
        (self.project/'app.exe').touch()
        self.build.side_effect=[(0,'base'),(1,"Foreground is a duplicate attribute name"),(0,'restored')]
        self.snapshots=[fixtures.observation(1),fixtures.observation(3)]
        def save(project,destination=None):
            if destination and destination.name.startswith('best_'):destination.touch()
            return True
        def restore(*args):
            self.file.write_text(self.initial,encoding='utf8')
            return True,'restored'
        with patch.object(server,'save_working_checkpoint',side_effect=save),patch.object(server,'restore_working_checkpoint',side_effect=restore):
            self.run_edit(image=base64.b64encode(fixtures.png('red')).decode())
        result=server.app.test_client().get('/api/job/visual').json
        self.assertIsNone(result['intento'])
        self.assertEqual(result['ultimo_fallo']['etapa'],'compilacion')
        self.assertIn('Foreground',result['ultimo_fallo']['detalle'])
        self.assertTrue(result['restaurado'])
        self.assertIsNotNone(result['despues'])

    def test_reference_attempt_and_restored_final_remain_distinct(self):
        (self.project/'Aplicacion.csproj').write_text('<Project/>')
        (self.project/'app.exe').touch()
        self.snapshots=[fixtures.observation(1),fixtures.observation(2,labels=('A',)),fixtures.observation(3)]
        def save(project,destination=None):
            if destination and destination.name.startswith('best_'):destination.touch()
            return True
        def restore(*args):
            self.file.write_text(self.initial,encoding='utf8')
            return True,'restored baseline'
        with patch.object(server,'save_working_checkpoint',side_effect=save),patch.object(server,'restore_working_checkpoint',side_effect=restore):
            self.run_edit(image=base64.b64encode(fixtures.png('red')).decode())
        result=server.app.test_client().get('/api/job/visual').json
        self.assertTrue(result['restaurado'])
        self.assertEqual(result['intento_numero'],1)
        self.assertTrue(result['referencia'].startswith('data:image/png;base64,'))
        self.assertEqual(result['intento_report']['medicion_uia']['after']['window']['pid'],2)
        self.assertEqual(result['report']['medicion_uia']['after']['window']['pid'],3)
        self.assertEqual(result['intento_report']['medicion_uia']['buttons']['status'],'differs')
        self.assertEqual(result['report']['medicion_uia']['buttons']['status'],'matches')
        self.assertIn('restaur',result['report']['nota'].lower())


if __name__=='__main__':unittest.main()

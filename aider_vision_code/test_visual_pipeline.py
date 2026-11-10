import base64
import copy
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from PIL import Image, PngImagePlugin

import server
import visual_pipeline as pipeline
from visual_reference import Reference, ReferenceStore, RunEvidence, digest
from visual_review import parse_contract, parse_review, compare_task, pixel_change, corrective_feedback, prepare_contract
from visual_test_support import plan_for
import test_edit_inspection as fixtures


def contract():
    return dict(schema=1,instruction='Cambiá únicamente el fondo',scope='partial',
        goals=[dict(id='g1',targets=['surface'],basis='request',expected='Fondo gris')],
        preserve=['Conservar el contenido y la posición'])


def comparison(state='different', confidence='high'):
    return dict(summary='Comparación simulada',checks=[dict(goal_id='g1',state=state,
        confidence=confidence,evidence='El fondo permanece blanco',suggestion='Aplicar gris',
        actual_bounds=[0,0,1,1])])


class ReferenceTests(unittest.TestCase):
    def setUp(self):
        self.root=Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.project=self.root/'project';self.project.mkdir()
        self.store=ReferenceStore(self.root/'store',self.project)
        self.raw=fixtures.png('red');self.plan=plan_for(self.raw)

    def test_pixels_and_description_persist_together_and_are_project_scoped(self):
        self.store.save(self.raw,self.plan)
        restored=self.store.load()
        self.assertEqual(restored.raw,self.raw)
        self.assertEqual(restored.plan,self.plan)
        self.assertEqual(restored.origin,'guardada')
        self.assertIsNone(ReferenceStore(self.root/'store',self.root/'other').load())

    def test_wrong_image_description_cannot_replace_active_reference(self):
        self.store.save(self.raw,self.plan)
        with self.assertRaises(ValueError):self.store.save(fixtures.png('blue'),self.plan)
        self.assertEqual(self.store.load().raw,self.raw)

    def test_modified_saved_pixels_are_rejected(self):
        ref=self.store.save(self.raw,self.plan)
        (self.store.folder/'references'/ref.identity/'original.image').write_bytes(fixtures.png('blue'))
        with self.assertRaises(ValueError):self.store.load()

    def test_invalid_pointer_cannot_escape_store(self):
        self.store.save(self.raw,self.plan)
        self.store.active.write_text(json.dumps(dict(reference_sha256='../other')))
        with self.assertRaises(ValueError):self.store.load()

    def test_explicit_upload_repairs_damaged_copy(self):
        ref=self.store.save(self.raw,self.plan)
        (self.store.folder/'references'/ref.identity/'original.image').write_bytes(b'broken')
        self.store.save(self.raw,self.plan)
        self.assertEqual(self.store.load().raw,self.raw)

    def test_legacy_migration_requires_exact_hash_not_familiar_filename(self):
        (self.project/'.avc_visual_spec.json').write_text(json.dumps(self.plan))
        (self.project/'referencia.png').write_bytes(fixtures.png('blue'))
        self.assertIsNone(self.store.migrate_legacy())
        (self.project/'unrelated_name.png').write_bytes(self.raw)
        self.assertEqual(self.store.migrate_legacy().raw,self.raw)

    def test_recorded_hash_identifies_saved_capture_and_source_manifest(self):
        ref=self.store.save(self.raw,self.plan)
        receipt=RunEvidence(self.store,'pedido',ref,1)
        meta=receipt.capture('before',self.raw,{'View.xaml':'source_hash'})
        self.assertEqual(meta['image_sha256'],digest((receipt.folder/meta['file']).read_bytes()))
        self.assertEqual(meta['sources'],{'View.xaml':'source_hash'})
        self.assertEqual(meta['reference_sha256'],ref.identity)


class ReviewTests(unittest.TestCase):
    def setUp(self):
        raw=fixtures.png('red')
        self.reference=Reference(raw,plan_for(raw),'adjunta')

    def test_contract_rejects_unknown_targets_and_duplicate_goals(self):
        for change in ('unknown','duplicate'):
            doc=contract()
            if change=='unknown':doc['goals'][0]['targets']=['missing']
            else:doc['goals'].append(copy.deepcopy(doc['goals'][0]))
            with self.assertRaises(ValueError):parse_contract(json.dumps(doc),'pedido',self.reference.plan)

    def test_review_cannot_introduce_unrequested_differences_or_omit_criteria(self):
        doc=comparison();doc['checks'][0]['goal_id']='unrequested'
        with self.assertRaises(ValueError):parse_review(json.dumps(doc),contract())
        with self.assertRaises(ValueError):parse_review('{"checks":[]}',contract())

    def test_global_contract_cannot_drop_reference_nodes(self):
        doc=contract();doc['scope']='reproduce';doc['goals'][0]['targets']=[]
        with self.assertRaisesRegex(ValueError,'omite elementos'):
            parse_contract(json.dumps(doc),'Reproducí la referencia',self.reference.plan)

    def test_global_criteria_derive_from_scene_after_classifying_request_alone(self):
        scene=copy.deepcopy(self.reference.plan)
        for kind in ('button','icon','text'):
            node=copy.deepcopy(scene['nodes'][0]);node.update(id=kind,kind=kind)
            scene['nodes'].append(node)
        reference=Reference(self.reference.raw,scene,'guardada')
        calls=[]
        def ask(images,prompt,stage,budget):
            calls.append((images,prompt,stage))
            return '{"scope":"reproduce","preserve":["Mantener eventos"]}'
        result=prepare_contract(ask,'Reproducí el diseño conservando eventos',reference,None,'XAML no define el pedido')
        self.assertEqual(len(calls),1)
        self.assertEqual(calls[0][2],'intent')
        self.assertNotIn('XAML no define',calls[0][1])
        self.assertEqual(result['scope'],'reproduce')
        self.assertEqual({t for g in result['goals'] for t in g['targets']},{n['id'] for n in scene['nodes']})
        self.assertEqual(result['preserve'],['Mantener eventos'])

    def test_same_pixels_with_different_file_metadata_are_identical(self):
        stream=BytesIO();info=PngImagePlugin.PngInfo();info.add_text('ignored','metadata')
        with Image.open(BytesIO(fixtures.png())) as original:
            original.save(stream,format='PNG',pnginfo=info)
        self.assertNotEqual(fixtures.png(),stream.getvalue())
        self.assertEqual(pixel_change(fixtures.png(),stream.getvalue()),'identical')

    def test_incomplete_response_is_inconclusive_and_cannot_trigger_edit(self):
        result=compare_task(lambda *args:'{}',contract(),self.reference,fixtures.png(),fixtures.png('gray'))
        self.assertEqual(result['status'],'no_verificable')
        self.assertEqual(corrective_feedback(result),'')

    def test_only_confirmed_requested_difference_can_trigger_second_edit(self):
        for confidence,confirmation,expected_calls in [('low',True,1),('high',False,2),('high',True,2)]:
            calls=[]
            def ask(images,prompt,stage,budget):
                calls.append((stage,prompt))
                return json.dumps(comparison(confidence=confidence) if stage=='comparison' else
                                  dict(confirmed=confirmation,evidence='Fondo confirmado blanco'))
            result=compare_task(ask,contract(),self.reference,fixtures.png(),fixtures.png('gray'))
            self.assertEqual(len(calls),expected_calls)
            self.assertEqual(bool(corrective_feedback(result)),confidence=='high' and confirmation)
            self.assertIn('scope',calls[0][1])
            self.assertIn('Conservar el contenido',calls[0][1])

    def test_identical_capture_cannot_be_reported_as_visible_progress(self):
        result=compare_task(lambda *a:json.dumps(comparison('observed')),contract(),self.reference,fixtures.png(),fixtures.png())
        self.assertEqual(result['status'],'sin_cambio_visible')

    def test_summary_does_not_repeat_unverified_functional_claims(self):
        response=comparison()
        response['summary']='Autenticación y eventos verificados correctamente'
        result=parse_review(json.dumps(response),contract())
        self.assertNotIn('Autenticación',result['summary'])
        self.assertIn('1 con diferencias',result['summary'])


class StructuredTransportTests(unittest.TestCase):
    def test_schema_is_sent_to_ollama_and_truncation_retries_before_parsing(self):
        from visual_schemas import contract_schema
        schema=contract_schema(['surface'])
        with patch.object(server,'post_ollama',side_effect=[
            dict(message=dict(content='{"scope":'),done_reason='length'),
            dict(message=dict(content=json.dumps(contract())),done_reason='stop')]) as post, \
                patch.object(server,'audit'),patch.object(server,'log'):
            response=server.ask_vision('local',[],'plan','visual_pipeline','contract',budget=1500,
                                      structured=schema,respect_cancel=False)
        self.assertEqual(json.loads(response)['scope'],'partial')
        self.assertEqual(post.call_count,2)
        self.assertEqual(post.call_args_list[0].args[1]['format'],schema)
        self.assertEqual(post.call_args_list[1].args[1]['options']['num_predict'],3000)
        self.assertEqual(post.call_args_list[1].args[1]['options']['temperature'],0)


class PipelineTests(unittest.TestCase):
    setUp=fixtures.EditCycleTests.setUp

    def run_pipeline(self,attempts=2,image=True):
        server.run_job(self.project,'Cambiá únicamente el fondo','',attempts,'coder',
            image=base64.b64encode(fixtures.png('red')).decode() if image else None,
            build_mode='wpf',quality_mode=False)

    def confirmed_model(self,*args,**kwargs):
        if args[4]=='intent':return '{"scope":"partial","preserve":[]}'
        if args[4]=='contract':return json.dumps(contract())
        if args[4]=='comparison':return json.dumps(comparison())
        if args[4]=='confirm_difference':return '{"confirmed":true,"evidence":"Diferencia visible confirmada"}'
        raise AssertionError(args[4])

    def test_rendered_feedback_reaches_next_edit_and_cycle_stops_after_budget(self):
        self.vision.side_effect=self.confirmed_model
        self.snapshots.append(fixtures.observation(3))
        messages=[];original=self.edit.side_effect
        def edit(args,*a,**kw):
            messages.append(args)
            return original(args,*a,**kw)
        self.edit.side_effect=edit
        self.run_pipeline()
        self.assertEqual(self.edits,2)
        self.assertEqual(self.captured,3)
        self.assertIn('El fondo permanece blanco',messages[1])
        self.assertIn('Cambiá únicamente el fondo',messages[1])
        self.assertFalse(list((server.ROOT/'recovery').glob('visual_message*')))
        manifest=next((server.ROOT/'recovery'/'visual_projects').glob('*/runs/*/run.json'))
        data=json.loads(manifest.read_text(encoding='utf8'))
        self.assertEqual(len(data['observations']),3)
        self.assertEqual(data['contract']['scope'],'partial')

    def test_second_identical_render_stops_even_with_remaining_budget(self):
        self.vision.side_effect=self.confirmed_model
        self.snapshots.append(fixtures.observation(3))
        self.run_pipeline(attempts=3)
        self.assertEqual(self.edits,2)
        self.assertEqual(server.STATE['phase'],'review')

    def test_no_source_change_does_not_republish_or_claim_success(self):
        self.edit.side_effect=lambda *a,**kw:(0,'No changes')
        self.run_pipeline()
        self.assertEqual(self.build.call_count,1)
        self.assertEqual(server.STATE['verification']['estado'],'no_verificable')
        self.assertIn('Sin cambios',server.STATE['result'])

    def test_compile_failure_uses_focused_repair_not_the_entire_design_again(self):
        self.build.side_effect=[(0,'base'),(1,'MC9999: invalid property on line 12'),(0,'repaired')]
        original=self.edit.side_effect;messages=[]
        def edit(args,*a,**kw):
            messages.append(args)
            return original(args,*a,**kw)
        self.edit.side_effect=edit
        self.run_pipeline()
        self.assertEqual(len(messages),2)
        self.assertIn('MC9999: invalid property',messages[1])
        self.assertIn('SOLAMENTE la causa',messages[1])
        self.assertNotIn('REFERENCIA (estimaciones',messages[1])
        self.assertTrue(server.STATE['verification']['compilado'])

    def test_reference_description_without_original_image_stops_before_editor(self):
        (self.project/'.avc_visual_spec.json').write_text(json.dumps(plan_for(fixtures.png())))
        server.run_job(self.project,'Cambiá únicamente el fondo usando la referencia guardada','',2,'coder',
            image=None,build_mode='wpf',quality_mode=False)
        self.assertIn('imagen original',server.STATE['result'])
        self.assertEqual(self.edits,0)
        self.build.assert_not_called()

    def test_failed_comparison_keeps_compiled_candidate_but_reports_uncertainty(self):
        original=self.vision.side_effect
        self.vision.side_effect=lambda *a,**kw: '{}' if a[4]=='comparison' else original(*a,**kw)
        with patch.object(server,'restore_working_checkpoint') as restore:
            self.run_pipeline()
        restore.assert_not_called()
        self.assertEqual(self.edits,1)
        self.assertEqual(server.STATE['verification']['estado'],'no_verificable')
        self.assertTrue(server.STATE['verification']['compilado'])

    def test_source_manifest_ignores_published_json_and_internal_reference(self):
        before=server.source_snapshot(self.project)
        folder=self.project/'avc_publish';folder.mkdir()
        (folder/'app.deps.json').write_text('{"runtime":"changed"}')
        (self.project/'.avc_visual_spec.json').write_text('{}')
        self.assertEqual(server.source_snapshot(self.project),before)

    def test_cancellation_clears_final_image_and_releases_lock(self):
        def edit(*a,**kw):
            self.file.write_text('<Window/>');server.CANCEL.set();return -1,'cancelled'
        self.edit.side_effect=edit
        self.run_pipeline()
        self.assertIsNone(server.VISUAL_CAPTURES['despues'])
        self.assertIsNone(server.STATE['verification'])
        self.assertEqual(server.STATE['phase'],'stopped')
        server.LOCK.release.assert_called_once()

    def test_real_checkpoint_removes_only_new_candidate_sources_after_failed_build(self):
        # Use actual ZIP services, not mocks that merely claim successful recovery.
        # Save originals before the fixture patches them, captured below at module load.
        self.build.side_effect=[(0,'baseline'),(1,'compile error'),(0,'restored')]
        self.snapshots=[fixtures.observation(1),fixtures.observation(3)]
        def edit(*a,**kw):
            self.file.write_text('<broken/>')
            (self.project/'New.cs').write_text('invalid code')
            (self.project/'notes.txt').write_text('user data')
            return 0,'Applied edit'
        self.edit.side_effect=edit
        with patch.object(server,'save_working_checkpoint',side_effect=REAL_SAVE),patch.object(server,'restore_working_checkpoint',side_effect=REAL_RESTORE):
            self.run_pipeline(attempts=1)
        self.assertEqual(self.file.read_text(),self.initial)
        self.assertFalse((self.project/'New.cs').exists())
        self.assertTrue((self.project/'notes.txt').exists())
        self.assertTrue(server.VISUAL_CAPTURES['restaurado'])
        self.assertIsNotNone(server.VISUAL_CAPTURES['despues'])


REAL_SAVE=server.save_working_checkpoint
REAL_RESTORE=server.restore_working_checkpoint

if __name__=='__main__':unittest.main()

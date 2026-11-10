"""Measured edit-cycle tests. Builds/editor/model simulated; no external project edits."""
import base64
import copy
import hashlib
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from PIL import Image
import edit_inspection as evidence
import server


def png(color='white', size=(200, 200)):
    out = BytesIO()
    Image.new('RGB', size, color).save(out, format='PNG')
    return out.getvalue()


def observation(pid=1, height=50, labels=('A', 'B')):
    return {'status':'measured', 'window':{'hwnd':pid+100, 'pid':pid,'title':'Test','dpi':96,
        'window_rect_screen_px':[0,0,200,230], 'client_origin_screen_px':[0,30], 'client_size_px':[200,200]},
        'started_at':'start', 'finished_at':'end', 'client_image_sha256':str(pid),
        'capture':{'width':200,'height':230},
        'screenshot':'data:image/png;base64,'+base64.b64encode(png(size=(200,230))).decode(),
        'uia':{'status':'complete','controls':[{'type':'Button','name':name,'automation_id':'',
            'visible':True,'width':50,'height':height,'rect_client_px':[i*60,100,i*60+50,100+height]}
            for i,name in enumerate(labels)]}}


class ComparisonTests(unittest.TestCase):
    def test_pairing_allows_new_pid_without_claiming_visual_success(self):
        result = evidence.compare_inspections(observation(1),observation(2),evidence.criteria_from({}))
        self.assertEqual(result['status'],'measured')
        self.assertEqual(result['buttons']['status'],'matches')
        self.assertEqual(result['geometry']['relative_changed'],0)

    def test_measured_failure_overrides_favorable_model_and_is_not_logged_as_full_inventory(self):
        report = evidence.attach_inspections({'estado':'cambio_observado_pendiente_revision'},
            observation(),observation(2,height=25),evidence.criteria_from({'square_bounds':True}))
        self.assertTrue(evidence.measured_failure(report))
        self.assertEqual(report['estado'],'no_cumple_medicion')
        repeated=evidence.attach_inspections(report,observation(),observation(2,height=25),evidence.criteria_from({'square_bounds':True}))
        self.assertEqual(repeated['nota'],report['nota'])
        logged = evidence.audit_safe(report)
        self.assertNotIn('paired',logged['medicion_uia']['geometry'])
        self.assertEqual(len(report['medicion_uia']['geometry']['paired']),2)
        self.assertNotIn('screenshot',json.dumps(report))

    def test_missing_controls_only_block_when_preservation_required(self):
        for required,status in ((True,'differs'),(False,'measured')):
            report=evidence.compare_inspections(observation(),observation(2,labels=('A',)),
                evidence.criteria_from({'preserve_buttons':required}))
            self.assertEqual(report['status'],status)
            self.assertEqual(report['buttons']['missing'],[('name','B')])

    def test_partial_or_unlinked_images_do_not_accredit_measurements(self):
        for change in ('partial','no_hash','no_buttons'):
            after=observation(2)
            if change=='partial':after['uia']['status']='partial'
            elif change=='no_hash':after.pop('client_image_sha256')
            else:after['uia']['controls']=[]
            report=evidence.compare_inspections(observation(),after,evidence.criteria_from({}))
            self.assertEqual(report['status'],'not_verifiable')

    def test_duplicate_id_is_not_unique_control_identity(self):
        after=observation(2)
        for item in after['uia']['controls']:item['automation_id']='duplicate'
        report=evidence.compare_inspections(observation(),after,evidence.criteria_from({}))
        self.assertEqual(report['buttons']['status'],'not_verifiable')

    def test_different_dpi_or_client_size_does_not_imply_regression(self):
        after=observation(2)
        after['window'].update(dpi=144,client_size_px=[300,300])
        for item in after['uia']['controls']:
            item['rect_client_px']=[v*1.5 for v in item['rect_client_px']]
            item['width']*=1.5;item['height']*=1.5
        report=evidence.compare_inspections(observation(),after,evidence.criteria_from({}))
        self.assertEqual(report['status'],'measured')
        self.assertEqual(report['geometry']['relative_changed'],0)
        self.assertFalse(report['geometry']['same_dpi'])

    def test_exact_inspected_image_is_cropped_and_hashed(self):
        data=observation()
        raw=evidence.client_image(data)
        self.assertEqual(Image.open(BytesIO(raw)).size,(200,200))
        self.assertEqual(data['client_image_sha256'],hashlib.sha256(raw).hexdigest())
        data['window']['client_origin_screen_px']=[100,100]
        with self.assertRaises(ValueError):evidence.client_image(data)

    def test_invalid_boolean_and_duplicate_expected_names_are_rejected(self):
        for data in ({'enabled':'false'},{'expected_order':'A | A'},{'preserve_buttons':1}):
            with self.assertRaises(ValueError):evidence.criteria_from(data)


class EditCycleTests(unittest.TestCase):
    def setUp(self):
        self.project=Path(self.enterContext(tempfile.TemporaryDirectory()))
        from visual_test_support import install_reference_mocks
        install_reference_mocks(self)
        self.file=self.project/'MainWindow.xaml'
        self.initial='<Window><Grid><Grid.RowDefinitions><RowDefinition Height="Auto"/></Grid.RowDefinitions><Button Content="A"/></Grid></Window>'
        self.file.write_text(self.initial,encoding='utf8')
        server.CANCEL.clear();self.addCleanup(server.CANCEL.clear)
        self.enterContext(patch.dict(server.STATE,job_id=10,started=None,result=None,verification=None,running=True))
        self.enterContext(patch.dict(server.VISUAL_CAPTURES,job_id=10,antes=None,despues=None))
        for name in ('audit','log','record_job','record_aider_report','save_working_checkpoint'):
            self.enterContext(patch.object(server,name,return_value=True))
        self.enterContext(patch.object(server,'LOCK',Mock()))
        self.enterContext(patch.object(server,'checkpoint_path',return_value=self.project/'backup.zip'))
        self.enterContext(patch.object(server,'best_checkpoint_path',return_value=self.project/'best.zip'))
        self.enterContext(patch.object(server,'ensure_wpf_project',return_value=(True,'existing')))
        self.enterContext(patch.object(server,'aider_command',side_effect=lambda p,m,text,**kw:['aider','--message',text]))
        self.build=self.enterContext(patch.object(server,'build_wpf',return_value=(0,'build')))
        self.publish=self.enterContext(patch.object(server,'publish_wpf',return_value=(0,'',[self.project/'app.exe'])))
        self.enterContext(patch.object(server,'inspect_wpf_quality',return_value=(True,'xml')))
        self.enterContext(patch.object(server,'verify_visual_change',side_effect=lambda instruction,decl,*args:dict(
            decl,estado='cambio_observado_pendiente_revision',nota='IA favorable',revision_humana=True)))
        self.snapshots=[observation(1),observation(2)]
        self.captured=0
        def capture(exe,inspection=None,**kwargs):
            data=copy.deepcopy(self.snapshots[self.captured]);self.captured+=1
            if inspection is not None:inspection.update(data)
            return png('white' if self.captured==1 else 'gray'),''
        self.capture=self.enterContext(patch.object(server,'capture_published_window',side_effect=capture))
        self.edits=0
        def edit(*args,**kwargs):
            self.assertTrue(server.save_working_checkpoint.called)
            self.assertGreaterEqual(self.captured,1)
            self.edits+=1
            self.file.write_text(self.initial.replace('Auto',str(self.edits+1)+'*'),encoding='utf8')
            return 0,'Applied edit'
        self.edit=self.enterContext(patch.object(server,'run_process',side_effect=edit))
        # Lifecycle fixtures simulate the editor boundary independently of its transport.
        # Real anchored WPF integration is covered in test_wpf_source_edit.
        self.enterContext(patch('visual_pipeline.WpfAdapter.edit',
                                side_effect=lambda prompt, **kw: self.edit(prompt, **kw)))

    def run_edit(self,criteria=None,attempts=1,**kwargs):
        server.run_job(self.project,'Modificar la distribución de la interfaz','',attempts,'coder',
            tech_mode='manual',build_mode='wpf',quality_mode=True,inspection_criteria=criteria,**kwargs)
        return server.STATE['verification']

    def test_successful_measurement_stays_review_and_backup_precedes_edit(self):
        report=self.run_edit()
        self.assertEqual(server.STATE['phase'],'review')
        self.assertEqual(report['medicion_uia']['status'],'measured')
        self.assertEqual(self.build.call_count,2)

    def test_favorable_model_cannot_hide_squashed_controls(self):
        self.snapshots[1]=observation(2,height=25)
        report=self.run_edit({'square_bounds':True})
        self.assertEqual(report['estado'],'no_cumple_medicion')
        self.assertEqual(server.STATE['phase'],'error')
        self.assertEqual(self.edits,1)

    def test_preserving_square_bounds_does_not_require_unrequested_geometry_change(self):
        def recolor(*args,**kwargs):
            self.file.write_text(self.initial.replace('<Window>','<Window Background="Red">'),encoding='utf8')
            return 0,'Applied color edit'
        self.edit.side_effect=recolor
        server.run_job(self.project,'Cambiar el color de la interfaz conservando la distribución','',1,'coder',
            tech_mode='manual',build_mode='wpf',quality_mode=True,inspection_criteria={'square_bounds':True})
        self.assertEqual(self.build.call_count,2)
        self.assertEqual(server.STATE['verification']['medicion_uia']['status'],'measured')

    def test_missing_button_blocks_and_retries_with_measured_feedback(self):
        self.snapshots=[observation(1),observation(2,labels=('A',)),observation(3)]
        report=self.run_edit(attempts=2)
        self.assertEqual(self.edits,2)
        self.assertEqual(report['medicion_uia']['after']['window']['pid'],3)
        self.assertEqual(report['medicion_uia']['status'],'measured')

    def test_failed_retry_does_not_reuse_prior_uia(self):
        self.snapshots[1]=observation(2,height=25)
        first=self.edit.side_effect
        def edit(*args,**kwargs):
            code,out=first(*args,**kwargs)
            return (code,out) if self.edits==1 else (1,'editor failed')
        self.edit.side_effect=edit
        report=self.run_edit({'square_bounds':True},attempts=2)
        self.assertEqual(report['medicion_uia']['after']['status'],'unavailable')
        self.assertNotIn('window',report['medicion_uia']['after'])
        self.assertIsNone(server.VISUAL_CAPTURES['despues'])

    def test_disabled_measurement_does_not_request_native_observation(self):
        report=self.run_edit({'enabled':False})
        self.assertTrue(all(c.kwargs['inspection'] is None for c in self.capture.call_args_list))
        self.assertEqual(report['medicion_uia']['status'],'not_verifiable')

    def test_restoration_gets_fresh_rebuilt_measurement(self):
        (self.project/'Aplicacion.csproj').write_text('<Project/>')
        (self.project/'app.exe').touch()
        self.snapshots=[observation(1),observation(2,height=25),observation(3)]
        def save(project,destination=None):
            if destination and destination.name.startswith('best_'):destination.touch()
            return True
        def restore(*args):
            self.file.write_text(self.initial,encoding='utf8')
            return True,'restored'
        with patch.object(server,'save_working_checkpoint',side_effect=save),patch.object(server,'restore_working_checkpoint',side_effect=restore):
            report=self.run_edit({'square_bounds':True},image=base64.b64encode(png()).decode())
        self.assertEqual(self.build.call_count,3)
        self.assertEqual(self.captured,3)
        self.assertEqual(report['medicion_uia']['after']['window']['pid'],3)
        self.assertEqual(report['medicion_uia']['status'],'measured')

    def test_read_only_does_not_run_inspector_or_build(self):
        with patch.dict(server.STATE,started=1),patch.object(server,'request_project_analysis',return_value=('Analysis',1)),patch.object(server,'inspect_window') as inspect:
            server.run_analysis_job(self.project,'Analizar sin modificar','coder')
        inspect.assert_not_called();self.build.assert_not_called();self.edit.assert_not_called()


class ApiCriteriaTests(unittest.TestCase):
    def test_bad_criteria_do_not_start_job(self):
        client=server.app.test_client()
        with patch.object(server.threading,'Thread') as thread:
            response=client.post('/api/job',json={'inspection_criteria':{'expected_order':'A | A'}})
        self.assertEqual(response.status_code,400)
        thread.assert_not_called()


if __name__=='__main__':unittest.main()

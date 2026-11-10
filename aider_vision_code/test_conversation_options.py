import base64
from io import BytesIO
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from PIL import Image
import server
from conversation_options import automatic_options
import test_edit_inspection as fixtures


class ConversationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name)
        stream = BytesIO()
        Image.new('RGB', (2, 2)).save(stream, format='PNG')
        self.image = base64.b64encode(stream.getvalue()).decode()

    def request(self, **extra):
        payload = dict(project=str(self.project), instruction='Cambiá el fondo de los botones',
                       automatic_settings=True)
        payload.update(extra)
        with patch.object(server, 'LOCK') as lock, patch.object(server.threading, 'Thread') as thread, patch.object(server, 'audit'), patch.dict(server.STATE), patch.dict(server.VISUAL_CAPTURES):
            lock.acquire.return_value = True
            response = server.app.test_client().post('/api/job', json=payload)
            options = thread.call_args.kwargs if thread.called else None
        return response, options

    def test_wpf_defaults_override_hidden_stale_strict_options(self):
        (self.project/'App.csproj').write_text('<Project><PropertyGroup><UseWPF>true</UseWPF></PropertyGroup></Project>')
        response, call = self.request(build_mode='off', attempts=3, quality_mode=True,
                                      test_command='never execute', inspection_criteria={'expected_order':'A | A'})
        self.assertEqual(response.status_code, 202)
        self.assertIs(call['target'], server.run_job)
        args = call['args']
        self.assertEqual((args[2],args[3],args[7],args[8],args[9]), ('',2,'auto','wpf',False))
        self.assertEqual(args[10], {})

    def test_other_technology_does_not_invent_build_command(self):
        (self.project/'app.py').write_text('print(1)')
        options = automatic_options(self.project, 'Corregí el color')
        self.assertEqual((options['build_mode'],options['test_command']), ('off',''))

    def test_explicit_new_wpf_request(self):
        self.assertEqual(automatic_options(self.project, 'Creá una calculadora en WPF')['build_mode'],'wpf')

    def test_natural_desktop_creation_is_not_downgraded_to_off(self):
        instruction='Crear una primera aproximación visual de una aplicación de escritorio horizontal de 1000 por 560.'
        self.assertEqual(automatic_options(self.project,instruction)['build_mode'],'wpf')
        response,call=self.request(instruction=instruction,build_mode='wpf')
        self.assertEqual(response.status_code,202)
        self.assertEqual(call['args'][8],'wpf')

    def test_natural_build_and_read_only_intents_need_no_manual_switches(self):
        self.assertEqual(automatic_options(self.project,'Construye y compila una aplicación de escritorio')['build_mode'],'wpf')
        response,call=self.request(instruction='Solo analizá el proyecto',build_mode='wpf')
        self.assertEqual(response.status_code,202)
        self.assertIs(call['target'],server.run_analysis_job)

    def test_old_manual_settings_cannot_change_the_natural_route(self):
        response, call = self.request(automatic_settings=False, attempts=3,
                                      tech_mode='suggest', build_mode='off',
                                      quality_mode=True, test_command='dangerous command',
                                      inspection_criteria={'square_bounds': True})
        self.assertEqual(response.status_code,202)
        self.assertIs(call['target'],server.run_job)
        args=call['args']
        self.assertEqual((args[2],args[3],args[7],args[8],args[9],args[10]),
                         ('',1,'auto','off',False,{}))

    def test_image_question_routes_to_read_only_in_same_chat(self):
        response, call = self.request(instruction='¿Qué ves en esta imagen?',image=self.image)
        self.assertEqual(response.status_code,202)
        self.assertIs(call['target'],server.run_analysis_job)
        self.assertEqual(call['args'][3],self.image)

    def test_read_only_is_expressed_in_natural_language(self):
        response, call = self.request(instruction='Solo analizá esta imagen; no modifiques archivos',
                                      analysis_only=False,image=self.image)
        self.assertEqual(response.status_code,202)
        self.assertIs(call['target'],server.run_analysis_job)

    def test_visual_analysis_has_no_execution_or_project_write(self):
        before=list(self.project.iterdir())
        with patch.object(server,'LOCK'), patch.dict(server.STATE,started=time.time()), patch.object(server,'audit'), patch.object(server,'log'), patch.object(server,'record_job'), patch.object(server,'ask_vision',return_value='Veo una calculadora') as vision, patch.object(server,'aider_command') as aider, patch.object(server,'build_wpf') as build, patch.object(server,'capture_published_window') as capture, patch.object(server,'request_project_analysis') as source:
            server.run_analysis_job(self.project,'Describí la imagen','coder',self.image,'vision')
            vision.assert_called_once()
            aider.assert_not_called();build.assert_not_called();capture.assert_not_called();source.assert_not_called()
        self.assertEqual(list(self.project.iterdir()),before)


class ApproximationCycleTests(unittest.TestCase):
    setUp = fixtures.EditCycleTests.setUp

    def test_approximation_builds_captures_and_keeps_edit_without_strict_visual_gate(self):
        def edit(*args, **kwargs):
            self.assertTrue(server.save_working_checkpoint.called)
            self.file.write_text(self.initial.replace('<Window>','<Window Background="Gray">'))
            return 0,'Applied edit'
        self.edit.side_effect=edit
        with patch.object(server,'model_advice',return_value=('WPF','visual')), patch.object(server,'compare_visual') as compare, patch.object(server,'review_reference',return_value=dict(status='orientativo',summary='Revisar distribución',differences=[],uncertainties=[],human_review=True)) as review, patch.object(server,'restore_working_checkpoint') as restore:
            server.run_job(self.project,'Modificar la distribución de la interfaz','',1,'coder',
                image=base64.b64encode(fixtures.png()).decode(),build_mode='wpf',quality_mode=False)
            compare.assert_not_called();restore.assert_not_called()
            review.assert_not_called()
            self.assertIn('comparison',[c.args[4] for c in self.vision.call_args_list])
        self.assertEqual(self.build.call_count,2)
        self.assertEqual(self.captured,2)
        self.assertIn('Background="Gray"',self.file.read_text())
        self.assertIsNotNone(server.VISUAL_CAPTURES['antes'])
        self.assertIsNotNone(server.VISUAL_CAPTURES['despues'])
        self.assertTrue(server.STATE['result'].startswith('Aplicación compilada y publicada:'))

    def test_saved_reference_is_compared_without_reattaching_image(self):
        from visual_reference import ReferenceStore
        from visual_test_support import plan_for
        raw=fixtures.png('red')
        ReferenceStore(server.ROOT/'recovery'/'visual_projects',self.project).save(raw,plan_for(raw))
        with patch.object(server,'wpf_blueprint_preflight') as preflight:
            server.run_job(self.project,'Corregir la composición visual usando la referencia guardada','',1,'coder',
                tech_mode='manual',build_mode='wpf',quality_mode=False)
        preflight.assert_not_called()
        call=next(c for c in self.vision.call_args_list if c.args[4]=='comparison')
        self.assertEqual(base64.b64decode(call.args[1][0]),raw)
        self.assertIsNotNone(server.VISUAL_CAPTURES['antes'])
        self.assertIsNotNone(server.VISUAL_CAPTURES['despues'])


if __name__=='__main__':unittest.main()

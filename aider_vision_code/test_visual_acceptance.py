import json
import base64
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock, patch
from PIL import Image

import server
from visual_acceptance import source_evidence, source_check, visual_scope, verify_visual_change, verification_summary


XAML = '''<Window xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml">
<!-- comentario --><Grid><Grid.RowDefinitions><RowDefinition Height="Auto"/>
<RowDefinition Height="*"/></Grid.RowDefinitions><Border BorderBrush="Green">
<TextBlock x:Name="Display" Text="0"/><Button Content="±" Click="Sign_Click"/>
</Border></Grid></Window>'''
REQUEST = 'Ampliá la región superior y cambiá la distribución del teclado. Conservá Display y los eventos.'


def png(color):
    buf = BytesIO()
    Image.new('RGB', (100, 180), color).save(buf, format='PNG')
    return buf.getvalue()


def model_reply(value):
    value = dict(value)
    state = 'regresion_observada' if value['estado'] == 'no_cumple' else 'mejora_observada'
    value.setdefault('aspectos', {axis: {'estado': state if axis == 'distribucion' else 'sin_cambio_observable',
        'antes': value.get('antes', 'Antes'), 'despues': value.get('despues', 'Después')}
        for axis in ('distribucion', 'proporciones', 'orden_controles', 'espaciado')})
    return json.dumps(value)


class VisualAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.project = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.path = self.project / 'MainWindow.xaml'
        self.path.write_text(XAML, encoding='utf8')
        (self.project / 'MainWindow.xaml.cs').write_text('class MainWindow {}', encoding='utf8')
        self.before = source_evidence(self.project)
        self.scope = visual_scope(REQUEST)

    def changed(self, old='Height="Auto"', new='Height="2*"'):
        self.path.write_text(XAML.replace(old, new), encoding='utf8')
        return source_check(self.before, source_evidence(self.project), self.scope)

    def test_actual_failure_comment_and_border_color_do_not_satisfy_layout_request(self):
        self.path.write_text(XAML.replace('<!-- comentario -->', '').replace('Green', 'Black'), encoding='utf8')
        report = source_check(self.before, source_evidence(self.project), self.scope)
        reviewer = Mock()
        result = verify_visual_change(REQUEST, report, png('white'), png('black'), reviewer)
        self.assertEqual(result['estado'], 'sin_evidencia_cumplimiento')
        self.assertEqual(result['archivos_geometria'], [])
        reviewer.assert_not_called()

    def test_changed_row_is_declared_evidence_not_rendered_success(self):
        report = self.changed()
        self.assertEqual(report['estado'], 'pendiente_revision')
        self.assertEqual(report['archivos_geometria'], ['MainWindow.xaml'])

    def test_style_setter_geometry_counts(self):
        style = self.project / 'App.xaml'
        style.write_text('<Application><Setter Property="Margin" Value="5"/></Application>')
        before = source_evidence(self.project)
        style.write_text('<Application><Setter Property="Margin" Value="12"/></Application>')
        report = source_check(before, source_evidence(self.project), self.scope)
        self.assertEqual(report['archivos_geometria'], ['App.xaml'])

    def test_lost_name_and_event_are_reported(self):
        report = self.changed('Click="Sign_Click"', 'Click="Other_Click"')
        self.assertTrue(report['controles_o_eventos_alterados'])

    def test_code_behind_change_makes_absent_xaml_geometry_inconclusive(self):
        (self.project / 'MainWindow.xaml.cs').write_text('class MainWindow { int Height = 2; }')
        report = source_check(self.before, source_evidence(self.project), self.scope)
        self.assertEqual(report['estado'], 'pendiente_revision')

    def test_unsafe_xaml_is_inconclusive(self):
        for encoding in ('utf-8', 'utf-16'):
            with self.subTest(encoding=encoding):
                self.path.write_bytes('<!DOCTYPE Window><Window/>'.encode(encoding))
                report = source_check(self.before, source_evidence(self.project), self.scope)
                self.assertTrue(report['limites'])
                self.assertEqual(report['estado'], 'pendiente_revision')

    def test_generated_files_are_not_inspected(self):
        (self.project / 'obj').mkdir()
        (self.project / 'obj' / 'secret.xaml').write_text('invalid XML')
        self.assertFalse(source_evidence(self.project)['limites'])

    def test_non_visual_task_does_not_request_visual_verification(self):
        self.assertEqual(visual_scope('Corregí la suma y el manejo de errores.'),
                         {'visual': False, 'geometria': False})
        self.assertTrue(visual_scope('Cambiá el color del botón')['visual'])

    def test_preserving_layout_is_not_a_request_to_change_it(self):
        scope = visual_scope('Cambiá el color de los botones. Conservá la distribución y el espaciado.')
        self.assertTrue(scope['visual'])
        self.assertFalse(scope['geometria'])

    def test_missing_baseline_never_calls_model_or_approves(self):
        reviewer = Mock()
        result = verify_visual_change(REQUEST, self.changed(), None, png('white'), reviewer)
        self.assertEqual(result['estado'], 'no_verificable')
        reviewer.assert_not_called()

    def test_identical_capture_pixels_prevent_model_claim_of_success(self):
        reviewer = Mock()
        result = verify_visual_change(REQUEST, self.changed(), png('white'), png('white'), reviewer)
        self.assertEqual(result['estado'], 'sin_evidencia_cumplimiento')
        reviewer.assert_not_called()

    def test_model_observation_always_requires_human_review(self):
        reviewer = Mock(return_value=model_reply({'estado': 'cambio_observable',
            'antes': 'Teclado arriba', 'despues': 'Teclado más abajo', 'pendientes': []}))
        result = verify_visual_change(REQUEST, self.changed(), png('white'), png('gray'), reviewer)
        self.assertEqual(result['estado'], 'cambio_observado_pendiente_revision')
        self.assertTrue(result['revision_humana'])
        self.assertIn('ANTES', reviewer.call_args.args[1])
        self.assertIn('ninguna es el diseño objetivo', reviewer.call_args.args[1])

    def test_incomplete_model_response_stays_inconclusive(self):
        result = verify_visual_change(REQUEST, self.changed(), png('white'), png('gray'),
                                      Mock(return_value='{"estado":"cambio_observable"}'))
        self.assertEqual(result['estado'], 'no_verificable')

    def test_model_rejection_is_explicitly_attributed(self):
        reviewer = Mock(return_value=model_reply({'estado': 'no_cumple', 'antes': 'Igual posición',
            'despues': 'Cambió el margen pero el visor sigue corto', 'pendientes': ['Ampliar visor']}))
        result = verify_visual_change(REQUEST, self.changed(), png('white'), png('gray'), reviewer)
        self.assertEqual(result['estado'], 'no_cumple_segun_modelo')

    def test_unchecked_requirements_cannot_be_approved(self):
        reviewer = Mock(return_value=model_reply({'estado': 'cambio_observable', 'antes': 'Arriba',
            'despues': 'Abajo', 'pendientes': ['No se puede verificar la separación']}))
        result = verify_visual_change(REQUEST, self.changed(), png('white'), png('gray'), reviewer)
        self.assertEqual(result['estado'], 'no_verificable')

    def test_invalid_images_stay_inconclusive(self):
        result = verify_visual_change(REQUEST, self.changed(), b'bad', b'bad', Mock())
        self.assertEqual(result['estado'], 'no_verificable')

    def test_improved_distribution_and_squashed_buttons_are_partial_with_regressions(self):
        aspects = {axis: {'estado': 'sin_cambio_observable', 'antes': 'Antes', 'despues': 'Después'}
                   for axis in ('distribucion', 'proporciones', 'orden_controles', 'espaciado')}
        aspects['distribucion'].update(estado='mejora_observada', antes='Visor corto', despues='Espacio superior amplio')
        aspects['proporciones'].update(estado='regresion_observada', antes='Botones redondos', despues='Botones ovalados')
        aspects['orden_controles'].update(estado='regresion_observada', antes='C arriba', despues='C abajo')
        response = model_reply({'estado': 'no_cumple', 'antes': 'Original', 'despues': 'Cambió',
                                'pendientes': ['Recuperar proporciones y orden'], 'aspectos': aspects})
        result = verify_visual_change(REQUEST, self.changed(), png('white'), png('gray'), Mock(return_value=response))
        self.assertEqual(result['estado'], 'cambio_parcial_con_regresiones')
        self.assertEqual(result['aspectos']['distribucion']['estado'], 'mejora_observada')
        self.assertIn('Conservar los avances', verification_summary(result))

    def test_global_verdict_without_individual_aspects_cannot_pass(self):
        response = json.dumps({'estado': 'cambio_observable', 'antes': 'Pequeño',
                               'despues': 'Grande', 'pendientes': []})
        result = verify_visual_change(REQUEST, self.changed(), png('white'), png('gray'), Mock(return_value=response))
        self.assertEqual(result['estado'], 'no_verificable')
        self.assertIn('Falta evidencia separada', result['nota'])

    def test_geometry_claim_without_any_individual_improvement_stays_inconclusive(self):
        aspects = {axis: {'estado': 'sin_cambio_observable', 'antes': 'Igual', 'despues': 'Igual'}
                   for axis in ('distribucion', 'proporciones', 'orden_controles', 'espaciado')}
        response = model_reply({'estado': 'cambio_observable', 'antes': 'Antes', 'despues': 'Después',
                                'pendientes': [], 'aspectos': aspects})
        result = verify_visual_change(REQUEST, self.changed(), png('white'), png('gray'), Mock(return_value=response))
        self.assertEqual(result['estado'], 'no_verificable')

    def order_report(self, contents, instruction='Fila 1: A | B'):
        self.path.write_text('<Window><Grid>' + contents + '</Grid></Window>', encoding='utf8')
        return source_check(self.before, source_evidence(self.project), visual_scope(instruction))

    def test_explicit_order_matches_declared_grid_cells(self):
        report = self.order_report('<Button Content="A" Grid.Row="0" Grid.Column="0"/><Button Content="B" Grid.Row="0" Grid.Column="1"/>')
        self.assertEqual(report['orden_solicitado']['estado'], 'coincide_declarado')

    def test_wrong_explicit_order_blocks_before_model_review(self):
        report = self.order_report('<Button Content="B" Grid.Row="0" Grid.Column="0"/><Button Content="A" Grid.Row="0" Grid.Column="1"/>')
        model = Mock()
        result = verify_visual_change('Fila 1: A | B', report, None, None, model)
        self.assertEqual(result['estado'], 'no_cumple_orden_declarado')
        self.assertIn('A: esperado fila 1, columna 1', result['nota'])
        model.assert_not_called()

    def test_ambiguous_labels_cannot_validate_requested_order(self):
        report = self.order_report('<Button Content="A"/><Button Content="A"/><Button Content="B"/>')
        self.assertEqual(report['orden_solicitado']['estado'], 'no_verificable')

    def test_dynamic_positions_cannot_validate_requested_order(self):
        report = self.order_report('<Button Content="A" Grid.Row="{Binding Row}" Grid.Column="0"/><Button Content="B" Grid.Row="0" Grid.Column="1"/>')
        self.assertEqual(report['orden_solicitado']['estado'], 'no_verificable')

    def test_implicit_or_styled_coordinates_are_not_assumed_to_be_zero(self):
        report = self.order_report('<Button Content="A" Style="{StaticResource Positioned}"/><Button Content="B"/>')
        self.assertEqual(report['orden_solicitado']['estado'], 'no_verificable')
        self.assertIn('explícito', report['orden_solicitado']['detalle'])

    def test_duplicate_requested_rows_are_rejected_as_ambiguous(self):
        report = self.order_report('<Button Content="A"/><Button Content="B"/>', 'Fila 1: A\nFila 1: B')
        self.assertEqual(report['orden_solicitado']['estado'], 'no_verificable')


class VisualAcceptanceIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.project = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.path = self.project / 'MainWindow.xaml'
        self.path.write_text(XAML, encoding='utf8')
        server.CANCEL.clear()
        self.addCleanup(server.CANCEL.clear)
        for name in ('audit', 'log', 'record_job', 'record_aider_report', 'save_working_checkpoint'):
            self.enterContext(patch.object(server, name, return_value=True))
        self.enterContext(patch.object(server, 'LOCK', Mock()))
        self.enterContext(patch.dict(server.STATE, started=None, result=None, verification=None))
        self.enterContext(patch.dict(server.VISUAL_CAPTURES, job_id=None, antes=None, despues=None))
        self.enterContext(patch.object(server, 'checkpoint_path', return_value=self.project / 'backup.zip'))
        self.enterContext(patch.object(server, 'best_checkpoint_path', return_value=self.project / 'best.zip'))
        self.enterContext(patch.object(server, 'ensure_wpf_project', return_value=(True, 'existing')))
        self.enterContext(patch.object(server, 'aider_command', return_value=['aider', 'whole', '--message', 'edit']))
        self.build = self.enterContext(patch.object(server, 'build_wpf', return_value=(0, 'build real mock')))
        self.publish = self.enterContext(patch.object(server, 'publish_wpf', return_value=(0, '', [self.project / 'app.exe'])))
        self.capture = self.enterContext(patch.object(server, 'capture_published_window', side_effect=[(png('white'), ''), (png('gray'), '')]))
        self.enterContext(patch.object(server, 'inspect_wpf_quality', return_value=(True, 'XML')))

    def test_cosmetic_only_edit_is_not_published_as_layout_success(self):
        def edit(*args, **kwargs):
            self.path.write_text(XAML.replace('Green', 'Black').replace('<!-- comentario -->', ''), encoding='utf8')
            return 0, 'Amplié la región superior. Applied edit.'
        with patch.object(server, 'run_process', side_effect=edit):
            server.run_job(self.project, REQUEST, '', 1, 'coder', tech_mode='manual', build_mode='wpf', quality_mode=True)
        self.assertEqual(server.STATE['verification']['estado'], 'sin_evidencia_cumplimiento')
        self.assertEqual(server.STATE['phase'], 'error')
        self.assertEqual(self.publish.call_count, 1)  # Only the original baseline.
        self.assertNotIn('Aplicación compilada y publicada', server.STATE['result'])

    def test_wrong_requested_grid_order_does_not_reach_post_edit_publication(self):
        def edit(*args, **kwargs):
            self.path.write_text('<Window><Grid><Button Content="B" Grid.Row="0" Grid.Column="0"/><Button Content="A" Grid.Row="0" Grid.Column="1"/></Grid></Window>')
            return 0, 'Applied edit'
        with patch.object(server, 'run_process', side_effect=edit), patch.object(server, 'ask_vision') as reviewer:
            server.run_job(self.project, 'Corregí el orden.\nFila 1: A | B', '', 1, 'coder',
                           tech_mode='manual', build_mode='wpf', quality_mode=True)
        self.assertEqual(server.STATE['phase'], 'error')
        self.assertEqual(server.STATE['verification']['estado'], 'no_cumple_orden_declarado')
        self.assertEqual(self.publish.call_count, 1)
        reviewer.assert_not_called()

    def test_visible_candidate_finishes_in_review_not_done(self):
        def edit(*args, **kwargs):
            self.path.write_text(XAML.replace('Height="Auto"', 'Height="2*"'), encoding='utf8')
            return 0, 'Applied edit'
        verdict = model_reply({'estado': 'cambio_observable', 'antes': 'Visor pequeño',
                              'despues': 'Visor ampliado', 'pendientes': []})
        with patch.object(server, 'run_process', side_effect=edit), patch.object(server, 'ask_vision', return_value=verdict):
            server.run_job(self.project, REQUEST, '', 1, 'coder', tech_mode='manual', build_mode='wpf', quality_mode=True)
        self.assertEqual(server.STATE['phase'], 'review')
        self.assertEqual(server.STATE['verification']['estado'], 'cambio_observado_pendiente_revision')
        self.assertEqual(self.build.call_count, 2)
        self.assertEqual(self.capture.call_count, 2)

    def test_failed_baseline_cannot_be_upgraded_to_visual_success(self):
        self.build.side_effect = [(1, 'original broken'), (0, 'new build')]
        self.capture.side_effect = [(png('gray'), '')]
        def edit(*args, **kwargs):
            self.path.write_text(XAML.replace('Height="Auto"', 'Height="2*"'), encoding='utf8')
            return 0, 'Applied edit'
        with patch.object(server, 'run_process', side_effect=edit), patch.object(server, 'ask_vision') as vision:
            server.run_job(self.project, REQUEST, '', 1, 'coder', tech_mode='manual', build_mode='wpf', quality_mode=True)
        self.assertEqual(server.STATE['phase'], 'review')
        self.assertEqual(server.STATE['verification']['estado'], 'no_verificable')
        vision.assert_not_called()

    def test_failed_retry_does_not_reuse_previous_attempt_verification_or_capture(self):
        count = 0
        def edit(*args, **kwargs):
            nonlocal count
            count += 1
            self.path.write_text(XAML.replace('Height="Auto"', f'Height="{count + 1}*"'), encoding='utf8')
            return (0, 'Applied edit') if count == 1 else (1, 'Aider failed')
        verdict = model_reply({'estado': 'no_cumple', 'antes': 'Visor corto',
                              'despues': 'Cambio insuficiente', 'pendientes': ['Ampliar visor']})
        with patch.object(server, 'run_process', side_effect=edit), patch.object(server, 'ask_vision', return_value=verdict):
            server.run_job(self.project, REQUEST, '', 2, 'coder', tech_mode='manual', build_mode='wpf', quality_mode=True)
        self.assertEqual(count, 2)
        self.assertEqual(server.STATE['phase'], 'error')
        self.assertEqual(server.STATE['verification']['estado'], 'no_verificable')
        self.assertIsNone(server.VISUAL_CAPTURES['despues'])

    def test_reference_flow_rebuilds_existing_exe_and_reuses_fresh_baseline_capture(self):
        from visual_test_support import install_reference_mocks, model_reply as pipeline_reply
        install_reference_mocks(self)
        (self.project / 'Aplicacion.csproj').write_text('<Project/>')
        (self.project / 'app.exe').touch()  # Existing executable must not skip the baseline build.
        def edit(*args, **kwargs):
            self.path.write_text(XAML.replace('Height="Auto"', 'Height="2*"'), encoding='utf8')
            return 0, 'Applied edit'
        verdict = model_reply({'estado': 'cambio_observable', 'antes': 'Visor corto',
                              'despues': 'Visor ampliado', 'pendientes': []})
        with patch('visual_pipeline.WpfAdapter.edit', side_effect=edit), \
             patch.object(server, 'model_advice', return_value=('WPF', '')), \
             patch.object(server, 'compare_visual', return_value=('approved', 90, 'Hipótesis')), \
             patch.object(server, 'ask_vision', side_effect=pipeline_reply):
            server.run_job(self.project, REQUEST, '', 1, 'coder', tech_mode='manual', build_mode='wpf',
                           quality_mode=True, image=base64.b64encode(png('white')).decode('ascii'))
        self.assertEqual(self.build.call_count, 2)
        self.assertEqual(self.publish.call_count, 2)
        self.assertEqual(self.capture.call_count, 2)
        self.assertEqual(server.STATE['phase'], 'review')

    def test_evidence_endpoint_is_uncached_and_images_are_not_in_polling_payload(self):
        with patch.dict(server.STATE, job_id=55, verification={'estado': 'no_verificable'}), \
             patch.dict(server.VISUAL_CAPTURES, job_id=55, antes='data:image/png;base64,abc', despues=None):
            client = server.app.test_client()
            response = client.get('/api/job/visual')
            self.assertEqual(response.headers['Cache-Control'], 'no-store')
            self.assertTrue(response.json['antes'].startswith('data:image'))
            self.assertNotIn('data:image', client.get('/api/job').get_data(as_text=True))

    def test_old_job_captures_are_not_substituted(self):
        with patch.dict(server.STATE, job_id=56), \
             patch.dict(server.VISUAL_CAPTURES, job_id=55, antes='old image', despues='old image'):
            result = server.app.test_client().get('/api/job/visual').json
            self.assertIsNone(result['antes'])
            self.assertIsNone(result['despues'])

    def test_new_chat_clears_verification_and_images(self):
        with patch.dict(server.STATE, running=False, job_id=55, verification={'estado': 'previous'}), \
             patch.dict(server.VISUAL_CAPTURES, job_id=55, antes='previous', despues='previous'):
            response = server.app.test_client().post('/api/new-chat')
            self.assertEqual(response.status_code, 200)
            self.assertIsNone(server.STATE['verification'])
            self.assertIsNone(server.VISUAL_CAPTURES['antes'])

    def test_read_only_analysis_never_enters_visual_edit_pipeline(self):
        with patch.object(server, 'request_project_analysis', return_value=('Análisis', 1)), \
             patch.object(server, 'source_evidence') as sources, patch.dict(server.STATE, started=1):
            server.run_analysis_job(self.project, 'Analizá sin modificar', 'coder')
        sources.assert_not_called()
        self.build.assert_not_called()
        self.publish.assert_not_called()
        self.capture.assert_not_called()


if __name__ == '__main__':
    unittest.main()

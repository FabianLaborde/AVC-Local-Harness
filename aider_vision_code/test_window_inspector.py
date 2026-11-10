"""Inspector acceptance boundaries: geometry, identity, stale capture and no editor."""
import json
import subprocess
import tempfile
from pathlib import Path
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import server
import window_inspector as inspector
import window_inspector_api


def control(name, x, y, width=60, height=29, **extra):
    return dict(type='Button', name=name, automation_id='', framework='WPF', visible=True,
                width=width, height=height, rect_client_px=[x, y, x+width, y+height], **extra)


def snapshot():
    return {'status': 'measured', 'window': {'hwnd': 123, 'pid': 45, 'client_size_px': [300, 450]},
            'uia': {'status': 'complete', 'controls': [
                control('7', 0, 250), control('8', 70, 250), control('C', 0, 300)]}}


class GeometryTests(unittest.TestCase):
    def test_native_chrome_and_clipped_buttons_excluded_from_criteria(self):
        data = snapshot()
        data['uia']['controls'].extend([control('Close', 200, -30), control('clipped', 270, 300)])
        report = inspector.measured_checks(data, [], True)
        self.assertEqual(report['visible_buttons'], 3)
        self.assertEqual(len(report['square_bounds']['outside']), 3)

    def test_actual_reordered_ovals_fail_both_explicit_criteria(self):
        report = inspector.measured_checks(snapshot(), [['C'], ['7', '8']], True)
        self.assertEqual(report['order']['status'], 'differs')
        self.assertEqual(report['square_bounds']['status'], 'differs')
        self.assertEqual(report['median_width'], 60)
        self.assertEqual(report['median_height'], 29)
        self.assertAlmostEqual(report['keyboard_top_fraction'], 250/450, places=4)

    def test_matching_nonoverlapping_rows_and_square_bounds(self):
        data = snapshot()
        for c in data['uia']['controls']:
            c['width'] = 29
            c['rect_client_px'][2] = c['rect_client_px'][0] + 29
        report = inspector.measured_checks(data, [['7', '8'], ['C']], True)
        self.assertEqual(report['order']['status'], 'matches')
        self.assertEqual(report['square_bounds']['status'], 'matches')

    def test_missing_ambiguous_partial_and_unavailable_do_not_pass(self):
        for variant in ('missing', 'ambiguous', 'partial', 'unavailable'):
            with self.subTest(variant=variant):
                data = snapshot()
                if variant == 'missing':
                    data['uia']['controls'].pop()
                elif variant == 'ambiguous':
                    data['uia']['controls'].append(control('C', 0, 350))
                else:
                    data['uia']['status'] = variant
                result = inspector.measured_checks(data, [['7', '8'], ['C']], True)
                self.assertEqual(result['order']['status'], 'not_verifiable')
                self.assertEqual(result['square_bounds']['status'], 'not_verifiable')

    def test_horizontal_or_vertical_overlap_is_not_order(self):
        for coords in ((20, 250), (70, 260)):
            data = snapshot()
            data['uia']['controls'][1] = control('8', *coords)
            rows = [['7', '8'], ['C']] if coords[1] == 250 else [['7'], ['8'], ['C']]
            self.assertEqual(inspector.measured_checks(data, rows, False)['order']['status'], 'differs')

    def test_no_criteria_no_invented_verdict(self):
        result = inspector.measured_checks(snapshot(), [], False)
        self.assertEqual(result['order']['status'], 'not_requested')
        self.assertEqual(result['square_bounds']['status'], 'not_requested')
        self.assertIsNone(result['keyboard_top_fraction'])

    def test_extra_separate_button_is_excluded_from_requested_measurement(self):
        data = snapshot()
        data['uia']['controls'].append(control('±', 0, 0, 40, 40))
        result = inspector.measured_checks(data, [['7', '8'], ['C']], True)
        self.assertEqual(result['median_width'], 60)
        self.assertEqual(result['visible_buttons'], 4)

    def test_invalid_expectations_rejected_before_native_work(self):
        for text in ('a | a', 'a |', 'a'*2501, ['a'], '|', '\n'.join(str(i) for i in range(21))):
            with self.subTest(text=str(text)[:40]), self.assertRaises(ValueError):
                inspector.expectations(text)
        self.assertEqual(inspector.expectations('C | ←\n7 | 8'), [['C', '←'], ['7', '8']])


class ProcessBoundaryTests(unittest.TestCase):
    def test_deadline_no_shell_no_capture_reuse(self):
        with patch.object(inspector.subprocess, 'run', side_effect=subprocess.TimeoutExpired('inspection', 25)) as run:
            with self.assertRaisesRegex(ValueError, '25 s'):
                inspector.inspect_window(123)
        self.assertEqual(run.call_args.kwargs['timeout'], 25)
        self.assertNotIn('shell', run.call_args.kwargs)
        self.assertIn('--worker', run.call_args.args[0])

    def test_worker_error_and_invalid_json_fail_closed(self):
        for output in ('not JSON', '{"error":"La ventana cambió"}'):
            with patch.object(inspector.subprocess, 'run', return_value=SimpleNamespace(stdout=output, returncode=1)):
                with self.assertRaises(ValueError):
                    inspector.inspect_window(123)

    def test_invalid_hwnd_does_not_start_process(self):
        for value in (True, None, -1, 0, '123 & something', 2**64):
            with patch.object(inspector.subprocess, 'run') as run, self.assertRaises(ValueError):
                inspector.inspect_window(value)
            run.assert_not_called()


class CaptureMetadataTests(unittest.TestCase):
    def test_native_child_metadata_is_independent_from_pixels(self):
        gui = SimpleNamespace(
            IsWindow=Mock(return_value=True), IsWindowVisible=Mock(return_value=True),
            IsWindowEnabled=Mock(return_value=True),
            ClientToScreen=Mock(return_value=(100, 130)),
            GetClientRect=Mock(return_value=(0, 0, 300, 400)),
            GetWindowRect=Mock(side_effect=lambda hwnd: (
                (100, 100, 400, 530) if hwnd == 123 else
                (200, 400, 300, 430) if hwnd == 201 else
                (310, 400, 370, 430) if hwnd == 202 else (200, 250, 350, 280))),
            GetWindowText=Mock(side_effect=lambda hwnd: {
                123: 'Login', 201: 'INGRESAR', 202: 'Salir', 203: 'dato privado'}[hwnd]),
            GetClassName=Mock(side_effect=lambda hwnd: {
                201: 'WindowsForms10.Button.app',
                202: 'WindowsForms10.Button.app',
                203: 'WindowsForms10.Edit.app'}[hwnd]))
        gui.EnumChildWindows = lambda hwnd, callback, data: [
            callback(child, data) for child in (201, 202, 203)]
        process = SimpleNamespace(
            GetWindowThreadProcessId=Mock(return_value=(1, 45)))
        with patch.dict('sys.modules', {'win32gui': gui, 'win32process': process}):
            data = server._inspect_win32_controls(123, 45)
        self.assertEqual(data['window']['title'], 'Login')
        self.assertEqual([item['name'] for item in data['uia']['controls']],
                         ['INGRESAR', 'Salir', ''])
        self.assertEqual(data['uia']['controls'][0]['rect_client_px'],
                         [100, 270, 200, 300])
        self.assertEqual(data['uia']['controls'][2]['type'], 'Edit')
        self.assertEqual(data['uia']['controls'][2]['name'], '')

    def test_pixel_failure_preserves_fresh_uia_window_and_controls(self):
        observed = snapshot()
        inspection = {'status': 'stale', 'window': {'pid': 999}}
        with patch.object(server, 'inspect_window', return_value=observed), \
                patch.object(server, 'client_image',
                             side_effect=ValueError('imagen vacía o uniforme')):
            pixels, error = server._inspect_window_for_capture(
                123, 45, inspection, (0, 0, 1366, 720), (300, 450))
        self.assertIsNone(pixels)
        self.assertIn('uniforme', error)
        self.assertEqual(inspection['window']['pid'], 45)
        self.assertEqual([item['name'] for item in inspection['uia']['controls']],
                         ['7', '8', 'C'])
        self.assertEqual(inspection['capture_constraints']['window_size_px'], [300, 450])

    def test_foreign_pid_is_rejected_before_metadata_is_accepted(self):
        observed = snapshot()
        inspection = {}
        with patch.object(server, 'inspect_window', return_value=observed), \
                patch.object(server, 'client_image') as pixels, \
                self.assertRaisesRegex(ValueError, 'proceso recién iniciado'):
            server._inspect_window_for_capture(
                123, 999, inspection, (0, 0, 1366, 720), (300, 450))
        self.assertEqual(inspection, {})
        pixels.assert_not_called()


class XamlContrastTests(unittest.TestCase):
    def test_duplicate_source_labels_are_not_claimed_as_two_conserved_buttons(self):
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, 'MainWindow.xaml').write_text('<Window><Button Content="C"/><Button Content="C"/></Window>', encoding='utf8')
            report = inspector.xaml_contrast(folder, snapshot())
        self.assertEqual([x['status'] for x in report['links']], ['ambiguous', 'ambiguous'])

    def test_named_binding_locates_by_id_without_evaluating_binding(self):
        data = snapshot()
        data['uia']['controls'][0]['automation_id'] = 'Display'
        data['uia']['controls'][0]['type'] = 'Text'
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, 'MainWindow.xaml').write_text(
                '<Window xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"><TextBlock x:Name="Display" Text="{Binding Value}"/></Window>', encoding='utf8')
            report = inspector.xaml_contrast(folder, data)
        self.assertEqual(report['links'][0]['status'], 'located')
        self.assertEqual(report['links'][0]['method'], 'AutomationId / x:Name')
        self.assertIn('No demuestra', report['detail'])


class NativePairTests(unittest.TestCase):
    def setUp(self):
        self.gui = SimpleNamespace(IsWindow=Mock(return_value=True), IsWindowVisible=Mock(return_value=True),
                                   GetWindowRect=Mock(return_value=(100, 100, 400, 550)),
                                   GetClientRect=Mock(return_value=(0, 0, 280, 410)),
                                   ClientToScreen=Mock(return_value=(110, 130)),
                                   GetWindowText=Mock(return_value='Calculator'))
        self.process = SimpleNamespace(GetWindowThreadProcessId=Mock(return_value=(1, 45)))
        self.button = Mock()
        self.button.element_info = SimpleNamespace(control_type='Button', automation_id='', framework_id='WPF', is_password=False)
        self.button.window_text.return_value = 'C'
        self.button.rectangle.return_value = SimpleNamespace(left=120, top=330, right=180, bottom=359,
                                                             width=lambda:60, height=lambda:29)
        self.button.children.return_value = []
        self.button.is_visible.return_value = True
        self.button.is_enabled.return_value = True
        window = Mock()
        window.process_id.return_value = 45
        window.children.return_value = [self.button]
        desktop = Mock()
        desktop.return_value.window.return_value.wrapper_object.return_value = window
        self.enterContext(patch.dict('sys.modules', {'win32gui':self.gui, 'win32process':self.process,
                                                    'pywinauto':SimpleNamespace(Desktop=desktop)}))
        self.dll = self.enterContext(patch('ctypes.WinDLL', create=True))
        self.dll.return_value.SetProcessDpiAwarenessContext.return_value = True
        self.dll.return_value.GetDpiForWindow.return_value = 144
        self.capture = self.enterContext(patch('window_capture.capture_window', return_value=(b'png', {
            'pid':45,'width':300,'height':450,'window_title':'Calculator','method':'PrintWindow'})))

    def test_same_hwnd_pid_and_physical_coordinate_conversion(self):
        data = inspector.native_snapshot(123)
        self.assertEqual(data['uia']['controls'][0]['rect_client_px'], [10, 200, 70, 229])
        self.assertEqual(data['window']['pid'], 45)
        self.assertEqual(data['window']['dpi'], 144)
        self.assertTrue(data['screenshot'].startswith('data:image/png;base64,'))
        self.assertEqual(self.capture.call_args_list[0].args, (123,))
        self.assertEqual(self.capture.call_args_list[1].args, (123,))

    def test_metadata_only_inspection_does_not_require_window_pixels(self):
        data = inspector.native_snapshot(123, metadata_only=True,
                                         required_controls=['C'])
        self.assertEqual(data['window']['pid'], 45)
        self.assertEqual(data['window']['title'], 'Calculator')
        self.assertEqual(data['capture']['method'], 'uia_metadata')
        self.assertNotIn('screenshot', data)
        self.assertNotIn('sha256', data['capture'])
        self.assertEqual(data['uia']['controls'][0]['name'], 'C')
        self.capture.assert_not_called()

    def test_moving_window_rejects_paired_evidence(self):
        self.gui.GetWindowRect.side_effect = [(100,100,400,550), (200,100,500,550)]
        with self.assertRaisesRegex(ValueError, 'movió'):
            inspector.native_snapshot(123)

    def test_changed_process_rejects_image(self):
        self.process.GetWindowThreadProcessId.side_effect = [(1,45), (1,46)]
        with self.assertRaises(ValueError):
            inspector.native_snapshot(123)

    def test_changed_control_rejects_image(self):
        self.button.window_text.side_effect = ['C', '7']
        with self.assertRaisesRegex(ValueError, 'controles cambiaron'):
            inspector.native_snapshot(123)

    def test_dpi_failure_does_not_publish_virtualized_measurements(self):
        self.dll.return_value.SetProcessDpiAwarenessContext.return_value = False
        with self.assertRaisesRegex(ValueError, 'DPI'):
            inspector.native_snapshot(123)
        self.capture.assert_not_called()


class EndpointTests(unittest.TestCase):
    def setUp(self):
        self.client = server.app.test_client()
        self.busy = self.enterContext(patch.dict(server.STATE, running=False))
        self.inspect = self.enterContext(patch.object(window_inspector_api, 'inspect_window', return_value=snapshot()))
        self.aider = self.enterContext(patch.object(server, 'run_process', side_effect=AssertionError('No execution')))
        self.model = self.enterContext(patch.object(server, 'post_ollama', side_effect=AssertionError('No model')))

    def test_local_read_only_response_no_cache(self):
        response = self.client.post('/api/inspector', json={'window_id':123, 'expected_order':'7 | 8\nC','square_bounds':True})
        self.assertEqual(response.status_code, 200)
        self.assertIn('no-store', response.headers['Cache-Control'])
        self.inspect.assert_called_once_with(123, '', [['7','8'],['C']], True)
        self.aider.assert_not_called()
        self.model.assert_not_called()

    def test_remote_cross_origin_invalid_json_and_busy_rejected(self):
        cases = [({'environ_overrides':{'REMOTE_ADDR':'10.0.0.1'},'json':{}},403),
                 ({'headers':{'Origin':'https://other.example'},'json':{}},403),
                 ({'json':[]},400), ({'json':{'square_bounds':'true'}},400)]
        for options, expected in cases:
            self.assertEqual(self.client.post('/api/inspector', **options).status_code, expected)
        with patch.dict(server.STATE, running=True):
            self.assertEqual(self.client.post('/api/inspector', json={}).status_code, 409)
        self.inspect.assert_not_called()

    def test_failure_does_not_return_old_image_and_releases_lock(self):
        self.inspect.side_effect = [ValueError('Ventana cambió'), snapshot()]
        failed = self.client.post('/api/inspector', json={'window_id':123})
        self.assertEqual(failed.status_code, 422)
        self.assertNotIn('screenshot', failed.json)
        self.assertEqual(self.client.post('/api/inspector', json={'window_id':123}).status_code, 200)


if __name__ == '__main__':
    unittest.main()

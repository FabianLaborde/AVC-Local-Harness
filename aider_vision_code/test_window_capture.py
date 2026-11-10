"""Selected-window pixels must come from its HWND, never from overlapping desktop pixels."""
import base64
import io
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PIL import Image
import server
import window_capture


def example_image():
    image = Image.new('RGB', (100, 100), 'white')
    image.paste('black', (0, 0, 50, 100))
    return image


class SelectedCaptureTests(unittest.TestCase):
    def setUp(self):
        self.gui = SimpleNamespace(
            IsWindow=Mock(return_value=True), IsWindowVisible=Mock(return_value=True),
            IsIconic=Mock(return_value=False), ShowWindow=Mock(),
            GetWindowText=Mock(return_value='Calculadora'),
            GetWindowRect=Mock(return_value=(20, 30, 120, 130)))
        self.process = SimpleNamespace(GetWindowThreadProcessId=Mock(return_value=(5, 678)))
        self.enterContext(patch.dict('sys.modules', {
            'win32gui': self.gui, 'win32process': self.process,
            'win32con': SimpleNamespace(SW_RESTORE=9)}))
        self.render = self.enterContext(patch.object(window_capture, '_render_window',
                                                   return_value=example_image()))
        self.sleep = self.enterContext(patch.object(window_capture.time, 'sleep'))
        # Any regression to a desktop crop must fail, even if it looks plausible.
        self.desktop = self.enterContext(patch('PIL.ImageGrab.grab',
                                              side_effect=AssertionError('Desktop capture forbidden')))

    def test_covered_window_uses_selected_hwnd_and_records_identity(self):
        png, facts = window_capture.capture_window('123')
        self.render.assert_called_once_with(123, 100, 100, 2)
        self.assertEqual(Image.open(io.BytesIO(png)).getpixel((0, 0)), (0, 0, 0))
        self.assertEqual(facts['window_title'], 'Calculadora')
        self.assertEqual(facts['hwnd'], 123)
        self.assertEqual(facts['pid'], 678)
        self.assertEqual(facts['method'], 'PrintWindow')
        self.desktop.assert_not_called()

    def test_minimized_window_is_restored_before_rendering(self):
        self.gui.IsIconic.side_effect = [True, False]
        _, facts = window_capture.capture_window(123)
        self.gui.ShowWindow.assert_called_once_with(123, 9)
        self.assertTrue(facts['restored'])
        self.render.assert_called_once()

    def test_window_still_minimized_is_not_sent_to_model(self):
        self.gui.IsIconic.return_value = True
        with self.assertRaisesRegex(ValueError, 'restaurar'):
            window_capture.capture_window(123)
        self.render.assert_not_called()

    def test_uniform_image_retries_rendering_without_desktop_fallback(self):
        self.render.side_effect = [Image.new('RGB', (100, 100)), example_image()]
        window_capture.capture_window(123)
        self.assertEqual([c.args[-1] for c in self.render.call_args_list], [2, 0])
        self.desktop.assert_not_called()

    def test_failed_render_never_substitutes_browser_pixels(self):
        for output in (None, Image.new('RGB', (100, 100))):
            with self.subTest(output=output):
                self.render.return_value = output
                with self.assertRaisesRegex(ValueError, 'No se usó una captura del escritorio'):
                    window_capture.capture_window(123)
        self.desktop.assert_not_called()

    def test_closed_or_changed_window_is_rejected(self):
        self.process.GetWindowThreadProcessId.side_effect = [(5, 678), (6, 999)]
        with self.assertRaisesRegex(ValueError, 'cambió'):
            window_capture.capture_window(123)

    def test_invalid_dimensions_do_not_allocate_bitmap(self):
        self.gui.GetWindowRect.return_value = (0, 0, 10000, 10000)
        with self.assertRaisesRegex(ValueError, 'Dimensiones'):
            window_capture.capture_window(123)
        self.render.assert_not_called()

    def test_both_visual_endpoints_use_same_target_capture(self):
        buffer = io.BytesIO()
        example_image().save(buffer, format='PNG')
        png = buffer.getvalue()
        reference = io.BytesIO()
        Image.new('RGB', (100, 100), 'red').save(reference, format='PNG')
        cycle = {'xaml': {'estado': 'ausente'}, 'diferencias_vinculadas': [],
                 'mediciones': {'relacion_aspecto_divergente': False}}
        with patch.object(server, 'capture_window', return_value=(png, {'hwnd': 123, 'method': 'PrintWindow'})) as capture, \
                patch.object(server, 'ask_vision', return_value='Hay botones.'), \
                patch.object(server, 'analyze_semantic', return_value={'estado': 'ok', 'diferencias': []}), \
                patch.object(server, 'build_visual_cycle', return_value=cycle), \
                patch.object(server, 'audit') as audit:
            client = server.app.test_client()
            response = client.post('/api/vision', json={'window_id': 123})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json['screenshot'], 'data:image/png;base64,' + base64.b64encode(png).decode())
            response = client.post('/api/vision/semantica', json={
                'window_id': 123, 'modo_actual': 'ventana',
                'referencia': base64.b64encode(reference.getvalue()).decode()})
            self.assertEqual(response.status_code, 200)
            self.assertEqual([c.args for c in capture.call_args_list], [(123,), (123,)])
            facts = [c.kwargs for c in audit.call_args_list if c.args[0] == 'selected_window_capture']
            self.assertEqual(len(facts), 2)
        self.desktop.assert_not_called()


class BitmapResourceTests(unittest.TestCase):
    def test_render_releases_gdi_resources_on_success_failure_and_exception(self):
        for result in (True, False, RuntimeError('PrintWindow failed')):
            with self.subTest(result=result):
                gui, ui = Mock(), Mock()
                source = ui.CreateDCFromHandle.return_value
                target = source.CreateCompatibleDC.return_value
                bitmap = ui.CreateBitmap.return_value
                bitmap.GetBitmapBits.return_value = bytes(2 * 2 * 4)
                previous = target.SelectObject.return_value
                printer = Mock(side_effect=result) if isinstance(result, Exception) else Mock(return_value=result)
                with patch.dict('sys.modules', {'win32gui': gui, 'win32ui': ui}), \
                        patch('ctypes.WinDLL', create=True) as dll:
                    dll.return_value.PrintWindow = printer
                    if isinstance(result, Exception):
                        with self.assertRaisesRegex(RuntimeError, 'PrintWindow failed'):
                            window_capture._render_window(123, 2, 2, 2)
                    else:
                        image = window_capture._render_window(123, 2, 2, 2)
                        self.assertEqual(image is not None, result)
                target.SelectObject.assert_called_with(previous)
                gui.DeleteObject.assert_called_once_with(bitmap.GetHandle.return_value)
                target.DeleteDC.assert_called_once()
                source.DeleteDC.assert_called_once()
                gui.ReleaseDC.assert_called_once_with(123, gui.GetWindowDC.return_value)


if __name__ == '__main__':
    unittest.main()

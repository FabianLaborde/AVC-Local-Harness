import unittest

from types import SimpleNamespace
from unittest.mock import Mock

from server import _visible_window_origin, _window_capture_candidate_score


class CapturePositionTests(unittest.TestCase):
    def test_window_inside_work_area_keeps_its_origin(self):
        self.assertEqual(_visible_window_origin((100, 80, 900, 700), (0, 0, 1920, 1040)),
                         (100, 80))

    def test_cascaded_window_is_moved_inside_work_area(self):
        self.assertEqual(_visible_window_origin((130, 130, 1130, 1165), (0, 0, 1920, 1040)),
                         (130, 5))

    def test_window_larger_than_work_area_is_aligned_to_origin(self):
        self.assertEqual(_visible_window_origin((40, 40, 2040, 1240), (0, 0, 1920, 1040)),
                         (0, 0))

    def test_application_surface_outranks_larger_untitled_shadow(self):
        gui = SimpleNamespace(
            GetWindowText=Mock(side_effect=lambda hwnd: 'Login' if hwnd == 10 else ''),
            EnumChildWindows=Mock(side_effect=lambda hwnd, callback, data: [
                callback(child, data) for child in (range(20, 25) if hwnd == 10 else [])]))
        form = _window_capture_candidate_score(gui, 10, (10, 10, 560, 410))
        shadow = _window_capture_candidate_score(gui, 11, (5, 5, 565, 415))
        self.assertGreater(form, shadow)

    def test_untitled_control_surface_outranks_childless_overlay(self):
        gui = SimpleNamespace(
            GetWindowText=Mock(return_value=''),
            EnumChildWindows=Mock(side_effect=lambda hwnd, callback, data: [
                callback(child, data) for child in ([20, 21] if hwnd == 10 else [])]))
        form = _window_capture_candidate_score(gui, 10, (10, 10, 560, 410))
        overlay = _window_capture_candidate_score(gui, 11, (5, 5, 565, 415))
        self.assertGreater(form, overlay)


if __name__ == '__main__':
    unittest.main()

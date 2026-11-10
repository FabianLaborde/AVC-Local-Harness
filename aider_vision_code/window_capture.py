"""Capture a chosen HWND into a bitmap, never crop the desktop behind its bounds."""
import io
import time

from PIL import Image, ImageStat


def _render_window(hwnd, width, height, flags):
    import ctypes
    from ctypes import wintypes
    import win32gui
    import win32ui

    window_dc = None
    source = target = bitmap = previous = None
    try:
        window_dc = win32gui.GetWindowDC(hwnd)
        if not window_dc:
            raise RuntimeError('No se pudo acceder a la ventana seleccionada.')
        source = win32ui.CreateDCFromHandle(window_dc)
        target = source.CreateCompatibleDC()
        bitmap = win32ui.CreateBitmap()
        bitmap.CreateCompatibleBitmap(source, width, height)
        previous = target.SelectObject(bitmap)
        print_window = ctypes.WinDLL('user32', use_last_error=True).PrintWindow
        # HWND and HDC are pointer-sized, including in 64-bit Python.
        print_window.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
        print_window.restype = wintypes.BOOL
        if not print_window(hwnd, target.GetSafeHdc(), flags):
            return None
        return Image.frombuffer('RGB', (width, height), bitmap.GetBitmapBits(True),
                                'raw', 'BGRX', 0, 1).copy()
    finally:
        # Release every acquired resource even when rendering or decoding fails.
        try:
            if target is not None and previous is not None:
                target.SelectObject(previous)
        finally:
            try:
                if bitmap is not None:
                    win32gui.DeleteObject(bitmap.GetHandle())
            finally:
                try:
                    if target is not None:
                        target.DeleteDC()
                finally:
                    try:
                        if source is not None:
                            source.DeleteDC()
                    finally:
                        if window_dc:
                            win32gui.ReleaseDC(hwnd, window_dc)


def capture_window(window_id):
    """Return PNG bytes and capture facts; unsupported surfaces fail explicitly."""
    import win32con
    import win32gui
    import win32process

    try:
        hwnd = int(window_id)
    except (TypeError, ValueError) as exc:
        raise ValueError('Seleccioná una ventana válida.') from exc
    if not win32gui.IsWindow(hwnd) or not win32gui.IsWindowVisible(hwnd):
        raise ValueError('Elegí una ventana visible y abierta; actualizá la lista si se cerró.')
    _, pid = win32process.GetWindowThreadProcessId(hwnd)
    title = win32gui.GetWindowText(hwnd)
    restored = bool(win32gui.IsIconic(hwnd))
    if restored:
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        time.sleep(0.25)
    if win32gui.IsIconic(hwnd):
        raise ValueError('No se pudo restaurar la ventana minimizada. Abrila e intentá nuevamente.')
    rect = win32gui.GetWindowRect(hwnd)
    width, height = rect[2] - rect[0], rect[3] - rect[1]
    if not (32 <= width <= 4000 and 32 <= height <= 4000):
        raise ValueError('Dimensiones de ventana inválidas (entre 32 y 4000 píxeles por lado).')
    errors = []
    for flags in (2, 0):
        try:
            image = _render_window(hwnd, width, height, flags)
            if image is None or max(ImageStat.Stat(image.resize((64, 64))).stddev) < 4:
                errors.append('imagen vacía o uniforme')
                continue
            # Detect closure/replacement of the selected window during capture.
            if not win32gui.IsWindow(hwnd) or win32process.GetWindowThreadProcessId(hwnd)[1] != pid:
                raise ValueError('La ventana seleccionada cambió durante la captura. Actualizá la lista.')
            buffer = io.BytesIO()
            image.save(buffer, format='PNG')
            return buffer.getvalue(), {'method': 'PrintWindow', 'hwnd': hwnd,
                                      'window_title': title, 'pid': pid,
                                      'width': width, 'height': height, 'restored': restored}
        except ValueError:
            raise
        except Exception as exc:
            errors.append(str(exc)[:180])
    raise ValueError('No se pudo capturar el contenido de la ventana seleccionada. '
                     'Adjuntá una imagen de diagnóstico. No se usó una captura del escritorio. '
                     'Detalle: ' + '; '.join(dict.fromkeys(errors)))

"""Read-only HWND capture + UIA evidence. Native work runs in a bounded child.

No model, Aider, project execution, builds, clicks, typing or project writes.
UIA rectangles describe control bounds, not painted shapes or functionality.
"""
import base64
from collections import Counter, deque
import datetime
import hashlib
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys

MAX_NODES = 400
TIMEOUT = 25


def expectations(text):
    if not isinstance(text, str) or len(text) > 2500:
        raise ValueError('El orden esperado debe ser texto de hasta 2500 caracteres.')
    rows = [[label.strip() for label in row.split('|')] for row in text.splitlines() if row.strip()]
    labels = [label for row in rows for label in row]
    if len(rows) > 20 or len(labels) > 100 or any(not label or len(label) > 100 for label in labels):
        raise ValueError('Usá hasta 20 filas y 100 nombres no vacíos, separados por |.')
    if len(labels) != len(set(labels)):
        raise ValueError('Los nombres del orden esperado deben ser únicos.')
    return rows


def measured_checks(snapshot, rows, square):
    available = snapshot['uia']['status'] == 'complete'
    controls = snapshot['uia']['controls']
    client_width, client_height = snapshot['window']['client_size_px']
    buttons = [c for c in controls if c['type'] == 'Button' and c['visible'] and c['width'] > 0 and c['height'] > 0
               and 0 <= c['rect_client_px'][0] < c['rect_client_px'][2] <= client_width
               and 0 <= c['rect_client_px'][1] < c['rect_client_px'][3] <= client_height]
    names = Counter(c['name'] for c in buttons)
    wanted = [label for row in rows for label in row]
    absent = [label for label in wanted if not names[label]]
    ambiguous = [label for label in wanted if names[label] > 1]
    selected = [c for c in buttons if c['name'] in wanted] if wanted else buttons
    result = {'visible_buttons': len(buttons), 'requested_missing': absent,
              'requested_ambiguous': ambiguous,
              'median_width': statistics.median(c['width'] for c in selected) if selected else None,
              'median_height': statistics.median(c['height'] for c in selected) if selected else None,
              'inventory_complete': available}
    if not rows:
        result['order'] = {'status': 'not_requested', 'detail': 'No se indicó un orden esperado.'}
    elif not available or absent or ambiguous:
        result['order'] = {'status': 'not_verifiable', 'detail': 'Faltan nombres únicos o el inventario UIA está incompleto.'}
    else:
        by_name = {c['name']: c for c in buttons}
        measured_rows = [[by_name[name] for name in row] for row in rows]
        # Separate rows must occupy non-overlapping vertical bands; columns must
        # occupy non-overlapping horizontal bands. Overlap is not approved as order.
        horizontal = all(all(a['rect_client_px'][2] <= b['rect_client_px'][0]
                             for a, b in zip(row, row[1:])) for row in measured_rows)
        vertical = all(max(c['rect_client_px'][3] for c in upper) <=
                       min(c['rect_client_px'][1] for c in lower)
                       for upper, lower in zip(measured_rows, measured_rows[1:]))
        result['order'] = {'status': 'matches' if horizontal and vertical else 'differs',
                           'detail': 'Orden por bandas rectangulares UIA; no verifica eventos ni funciones.'}
    if not square:
        result['square_bounds'] = {'status': 'not_requested', 'detail': 'No se solicitó proporción cuadrada.'}
    elif not available or not selected or absent or ambiguous:
        result['square_bounds'] = {'status': 'not_verifiable', 'detail': 'Inventario insuficiente para evaluar los botones solicitados.'}
    else:
        outside = [{'name': c['name'], 'ratio': round(c['width'] / c['height'], 3)}
                   for c in selected if not 0.85 <= c['width'] / c['height'] <= 1.15]
        result['square_bounds'] = {'status': 'differs' if outside else 'matches',
                                   'outside': outside, 'detail': 'Ancho/alto entre 0,85 y 1,15. Un rectángulo cuadrado no prueba un círculo pintado.'}
    if wanted and available and not absent and not ambiguous:
        tops = [c['rect_client_px'][1] for c in selected]
        bottom = snapshot['window']['client_size_px'][1]
        result['keyboard_top_fraction'] = round(min(tops) / bottom, 4) if bottom > 0 else None
    else:
        result['keyboard_top_fraction'] = None
    return result


def xaml_contrast(project, snapshot):
    if not project:
        return {'status': 'unavailable', 'detail': 'No se seleccionó proyecto.'}
    from visual_workflow import _xaml_path
    from xaml_structure import inspect_xaml
    try:
        path, error = _xaml_path(project)
        if path is None:
            return {'status': 'unavailable', 'detail': error}
        raw = path.read_bytes()
        data = inspect_xaml(path)
        if path.read_bytes() != raw:
            return {'status': 'unavailable', 'detail': 'El XAML cambió durante la lectura.'}
        controls = snapshot['uia']['controls']
        links = []
        source_labels = Counter((n['type'], n['properties'].get('Content') or n['properties'].get('Text') or '')
                                for n in data['elements'])
        for node in data['elements']:
            kind = {'Button': 'Button', 'TextBlock': 'Text', 'TextBox': 'Edit'}.get(node['type'])
            if not kind:
                continue
            props = node['properties']
            label = props.get('Content') or props.get('Text') or ''
            name = node['name']
            if not name and (not label or label.startswith('{')):
                continue
            same_type = [c for c in controls if c['type'] == kind]
            matches = [c for c in same_type if name and c['automation_id'] == name]
            method = 'AutomationId / x:Name'
            if not matches and label and not label.startswith('{'):
                matches = [c for c in same_type if c['name'] == label]
                method = 'tipo y texto literal'
            source_ambiguous = method == 'tipo y texto literal' and source_labels[(node['type'], label)] > 1
            links.append({'type': kind, 'source_name': name, 'source_label': label,
                          'status': ('not_verifiable' if snapshot['uia']['status'] != 'complete' else
                                     'ambiguous' if source_ambiguous and matches else
                                     'located' if len(matches) == 1 else 'ambiguous' if matches else 'not_located'),
                          'method': method, 'matches': len(matches)})
        return {'status': 'inspected', 'file': str(path.relative_to(Path(project).expanduser().resolve())),
                'sha256': hashlib.sha256(raw).hexdigest(), 'truncated': data['truncated'], 'links': links,
                'detail': 'Correspondencias candidatas. No demuestra que el ejecutable provenga de este XAML, ni conservación funcional.'}
    except (OSError, ValueError, RecursionError) as exc:
        return {'status': 'unavailable', 'detail': str(exc)[:250]}


def inspect_window(hwnd, project='', expected_rows=None, square=False, metadata_only=False,
                   required_controls=None):
    """Child boundary prevents COM or PrintWindow hanging the Flask request forever."""
    if isinstance(hwnd, bool) or not str(hwnd).isdigit() or not 0 < int(hwnd) < 2**64:
        raise ValueError('Seleccioná una ventana válida y actualizá la lista si se cerró.')
    required_controls = required_controls or []
    if (not isinstance(required_controls, list) or len(required_controls) > 16
            or any(not isinstance(item, str) or not item.strip() or len(item) > 180
                   for item in required_controls)):
        raise ValueError('Los controles requeridos deben ser una lista acotada de nombres.')
    environment = dict(os.environ, PYTHONIOENCODING='utf-8')
    try:
        worker_mode = '--worker-metadata' if metadata_only else '--worker'
        command = [sys.executable, str(Path(__file__).resolve()), worker_mode, str(hwnd)]
        if metadata_only:
            command.append(json.dumps(required_controls, ensure_ascii=True))
        run = subprocess.run(command,
                             capture_output=True, text=True, encoding='utf-8', timeout=TIMEOUT,
                             env=environment, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except subprocess.TimeoutExpired as exc:
        raise ValueError('La inspección superó 25 s. No se conservó una captura parcial; probá nuevamente.') from exc
    try:
        data = json.loads(run.stdout)
    except (ValueError, TypeError) as exc:
        raise ValueError('No se pudo leer la inspección UIA. Verificá pywinauto y los permisos de la aplicación.') from exc
    if run.returncode or data.get('error'):
        raise ValueError(data.get('error', 'Falló la inspección de la ventana.'))
    data['checks'] = measured_checks(data, expected_rows or [], square)
    data['expected_rows'] = expected_rows or []
    data['xaml'] = xaml_contrast(project, data)
    return data


def native_snapshot(hwnd, metadata_only=False, required_controls=None):
    import ctypes
    from ctypes import wintypes
    # Dedicated process only: coordinate units must agree between UIA and Win32.
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    user32.SetProcessDpiAwarenessContext.restype = wintypes.BOOL
    user32.GetDpiForWindow.argtypes = [wintypes.HWND]
    user32.GetDpiForWindow.restype = wintypes.UINT
    if not user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
        raise ValueError('No se pudo establecer DPI por monitor; no se mezclaron coordenadas virtualizadas.')
    import win32gui
    import win32process
    from window_capture import capture_window

    def identity():
        if not win32gui.IsWindow(hwnd) or not win32gui.IsWindowVisible(hwnd):
            raise ValueError('La ventana ya no está abierta y visible.')
        return {'pid': win32process.GetWindowThreadProcessId(hwnd)[1],
                'rect': list(win32gui.GetWindowRect(hwnd)),
                'client_origin': list(win32gui.ClientToScreen(hwnd, (0, 0))),
                'client': list(win32gui.GetClientRect(hwnd))}

    def collect(origin, expected_pid):
        from pywinauto import Desktop
        window = Desktop(backend='uia').window(handle=hwnd).wrapper_object()
        if window.process_id() != expected_pid:
            raise ValueError('La ventana UIA no coincide con el proceso seleccionado.')
        controls, queue, count, partial = [], deque([(window, 0)]), 0, False
        required = [item.casefold() for item in (required_controls or [])]
        found = set()
        while queue and count < MAX_NODES:
            parent, depth = queue.popleft()
            count += 1
            if depth >= 12:
                partial = True
                continue
            for control in parent.children():
                if len(queue) + count >= MAX_NODES:
                    partial = True
                    break
                queue.append((control, depth + 1))
                info = control.element_info
                if info.control_type not in {'Button', 'Text', 'Edit', 'CheckBox', 'RadioButton', 'ComboBox'}:
                    continue
                # Do not read password values or names that might echo their value.
                if getattr(info, 'is_password', False):
                    continue
                box = control.rectangle()
                screen = [box.left, box.top, box.right, box.bottom]
                name = control.window_text()[:120]
                controls.append({'type': info.control_type, 'name': name,
                                 'automation_id': str(info.automation_id)[:120], 'framework': info.framework_id,
                                 'rect_screen_px': screen,
                                 'rect_client_px': [screen[i] - origin[i % 2] for i in range(4)],
                                 'width': box.width(), 'height': box.height(),
                                 'visible': bool(control.is_visible()), 'enabled': bool(control.is_enabled())})
                for item in required:
                    if item in name.casefold():
                        found.add(item)
            if required and len(found) == len(required):
                partial = True
                break
        return {'status': 'partial' if partial or queue else 'complete', 'controls': controls,
                'nodes_visited': count, 'detail': 'Límite de 400 nodos y 12 niveles.' if partial or queue else ''}

    started = datetime.datetime.now(datetime.timezone.utc).isoformat()
    if metadata_only:
        before = identity()
        facts = {'pid': before['pid'], 'width': before['rect'][2] - before['rect'][0],
                 'height': before['rect'][3] - before['rect'][1],
                 'window_title': win32gui.GetWindowText(hwnd), 'method': 'uia_metadata'}
        png = None
    else:
        # Capture helper may restore a minimized target. First establish restored geometry.
        png, facts = capture_window(hwnd)
        before = identity()
        if before['pid'] != facts['pid']:
            raise ValueError('Cambió el proceso durante la captura.')
    try:
        uia = collect(before['client_origin'], before['pid'])
    except ImportError:
        uia = {'status': 'unavailable', 'controls': [], 'detail': 'Instalá pywinauto con el Python que ejecuta AVC.'}
    # Pair UIA reads around the pixel acquisition, or around each other when the
    # caller deliberately delegates pixels to a separate HWND-bound capturer.
    if not metadata_only:
        png, facts = capture_window(hwnd)
    if uia['status'] in {'complete', 'partial'}:
        second = collect(before['client_origin'], before['pid'])
        if uia != second:
            raise ValueError('Los controles cambiaron durante la captura. Esperá una vista estable y repetí.')
    after = identity()
    if before != after or facts['pid'] != before['pid'] or facts['width'] != before['rect'][2] - before['rect'][0] or facts['height'] != before['rect'][3] - before['rect'][1]:
        raise ValueError('La ventana se movió, cambió de tamaño o de proceso durante la inspección. Repetí.')
    end = datetime.datetime.now(datetime.timezone.utc).isoformat()
    result = {'status': 'measured' if uia['status'] == 'complete' else 'partial',
            'started_at': started, 'finished_at': end,
            'window': {'hwnd': hwnd, 'pid': facts['pid'], 'title': facts['window_title'],
                       'window_rect_screen_px': before['rect'], 'client_origin_screen_px': before['client_origin'],
                       'client_size_px': before['client'][2:], 'dpi': user32.GetDpiForWindow(hwnd)},
            'capture': {'method': facts['method'], 'width': facts['width'], 'height': facts['height'],
                        'includes_chrome': not metadata_only},
            'uia': uia,
            'limits': ['Lecturas sucesivas estables, no una captura atómica del estado interno.',
                       'Rectángulos físicos UIA, no contornos, colores ni prueba de funciones.',
                       'Sin aprobación visual global ni medición automática de una imagen de referencia.']}
    if png is not None:
        result['capture']['sha256'] = hashlib.sha256(png).hexdigest()
        result['screenshot'] = ('data:image/png;base64,'
                                + base64.b64encode(png).decode('ascii'))
    return result


if __name__ == '__main__':
    try:
        if len(sys.argv) not in {3, 4} or sys.argv[1] not in {'--worker', '--worker-metadata'}:
            raise ValueError('Invocación de inspección inválida.')
        required = json.loads(sys.argv[3]) if len(sys.argv) == 4 else []
        print(json.dumps(native_snapshot(
            int(sys.argv[2]), metadata_only=sys.argv[1] == '--worker-metadata',
            required_controls=required),
            ensure_ascii=True))
    except Exception as exc:
        print(json.dumps({'error': str(exc)[:350]}, ensure_ascii=True))
        sys.exit(1)

"""Independent, local-only, read-only inspector endpoint; no model calls."""
import threading
from flask import jsonify, request
from window_inspector import expectations, inspect_window


def register_inspector(app, is_busy=lambda: False):
    lock = threading.Lock()

    @app.post('/api/inspector')
    def window_inspector_endpoint():
        if request.remote_addr not in ('127.0.0.1', '::1'):
            return jsonify(error='El inspector solo funciona desde el equipo local.'), 403
        origin = request.headers.get('Origin')
        if origin and origin not in ('http://127.0.0.1:8765', 'http://localhost:8765'):
            return jsonify(error='Origen no autorizado.'), 403
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify(error='Se esperaba un objeto JSON.'), 400
        try:
            rows = expectations(data.get('expected_order', ''))
            square = data.get('square_bounds', False)
            project = data.get('project', '')
            if not isinstance(square, bool) or not isinstance(project, str):
                raise ValueError('Criterios o proyecto inválidos.')
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
        if is_busy() or not lock.acquire(blocking=False):
            return jsonify(error='Esperá a que termine la tarea o inspección actual.'), 409
        try:
            result = inspect_window(data.get('window_id'), project, rows, square)
            response = jsonify(result)
            response.headers['Cache-Control'] = 'no-store, max-age=0'
            return response
        except ValueError as exc:
            return jsonify(error=str(exc)), 422
        finally:
            lock.release()

"""Measured before/after evidence for an edit. No models, writes or execution."""
import base64
from collections import Counter
import hashlib
from io import BytesIO
import json
import math

from PIL import Image
from window_inspector import expectations, measured_checks


def criteria_from(data):
    result = {}
    for key, default in (('enabled', True), ('square_bounds', False), ('preserve_buttons', True)):
        value = data.get(key, default)
        if not isinstance(value, bool):
            raise ValueError('Los criterios de inspección deben usar valores booleanos.')
        result[key] = value
    result['expected_order'] = data.get('expected_order', '')
    result['rows'] = expectations(result['expected_order'])
    allowed = data.get('allowed_removals', '')
    if not isinstance(allowed, str) or len(allowed) > 1200:
        raise ValueError('Las eliminaciones autorizadas deben ser nombres separados por | (hasta 1200 caracteres).')
    names = [x.strip() for x in allowed.split('|')] if allowed.strip() else []
    if len(names) > 30 or any(not x or len(x) > 100 for x in names) or len(set(names)) != len(names):
        raise ValueError('Usá hasta 30 nombres únicos no vacíos en las eliminaciones autorizadas.')
    if set(names) & {name for row in result['rows'] for name in row}:
        raise ValueError('Criterios contradictorios: un botón no puede estar en el orden exigido y en las eliminaciones autorizadas.')
    result.update(allowed_removals=allowed, allowed_names=names)
    return result


def client_image(snapshot):
    """Crop the exact inspected pixels, never request a second unrelated capture."""
    raw = base64.b64decode(snapshot['screenshot'].split(',', 1)[1], validate=True)
    window = snapshot['window']
    origin = window['client_origin_screen_px']
    rect = window['window_rect_screen_px']
    width, height = window['client_size_px']
    left, top = origin[0] - rect[0], origin[1] - rect[1]
    with Image.open(BytesIO(raw)) as image:
        if (image.size != (snapshot['capture']['width'], snapshot['capture']['height']) or
                min(left, top) < 0 or min(width, height) <= 0 or
                left + width > image.width or top + height > image.height):
            raise ValueError('Las coordenadas cliente no coinciden con la captura inspeccionada.')
        output = BytesIO()
        image.crop((left, top, left + width, top + height)).save(output, format='PNG')
    raw = output.getvalue()
    snapshot['client_image_sha256'] = hashlib.sha256(raw).hexdigest()
    return raw


def button_inventory(snapshot):
    """Exclude native chrome and clipped controls. Retain multiplicity."""
    width, height = snapshot['window']['client_size_px']
    return [c for c in snapshot['uia']['controls'] if c['type'] == 'Button' and c['visible']
            and 0 <= c['rect_client_px'][0] < c['rect_client_px'][2] <= width
            and 0 <= c['rect_client_px'][1] < c['rect_client_px'][3] <= height]


def identity(control):
    return ('id', control['automation_id']) if control['automation_id'] else ('name', control['name'])


def describe(snapshot):
    if not snapshot or snapshot.get('status') == 'unavailable':
        return {'status': 'unavailable', 'error': (snapshot or {}).get('error', 'Sin inspección de esta versión.')}
    return {'status': snapshot['uia']['status'], 'window': snapshot['window'],
            'started_at': snapshot['started_at'], 'finished_at': snapshot['finished_at'],
            'client_image_sha256': snapshot.get('client_image_sha256'),
            'buttons': len(button_inventory(snapshot))}


def compare_inspections(before, after, criteria):
    report = {'status': 'not_verifiable', 'before': describe(before), 'after': describe(after),
              'limits': ['UIA verifica rectángulos y controles accesibles, no eventos Click ni funcionamiento.',
                         'Las versiones se abren en procesos distintos; los PID pueden diferir.',
                         'Cambios de geometría no demuestran por sí solos fidelidad a una referencia.']}
    if not criteria['enabled']:
        report['detail'] = 'Inspección medible desactivada para esta tarea.'
        return report
    if any(report[k]['status'] != 'complete' for k in ('before', 'after')):
        report['detail'] = 'Falta una inspección completa y comparable de la versión inicial o final.'
        return report
    for snapshot in (before, after):
        if not snapshot.get('client_image_sha256'):
            report['detail'] = 'No hay vínculo verificable entre medidas y captura cliente.'
            return report
    first, second = button_inventory(before), button_inventory(after)
    if not first or not second:
        report['detail'] = 'No hay botones accesibles suficientes para la comparación.'
        return report
    counts_before, counts_after = Counter(map(identity, first)), Counter(map(identity, second))
    missing = list((counts_before - counts_after).elements())
    added = list((counts_after - counts_before).elements())
    # A removal exemption uses an explicit exact label and a unique baseline
    # identity; a duplicate/anonymous control never receives implicit permission.
    label_counts = Counter(c['name'] for c in first)
    allowed_keys = {identity(c) for c in first if c['name'] in criteria.get('allowed_names', [])
                    and label_counts[c['name']] == 1 and counts_before[identity(c)] == 1}
    permitted = [key for key in missing if key in allowed_keys]
    unexpected = [key for key in missing if key not in allowed_keys]
    report['buttons'] = {'status': 'differs' if unexpected else 'matches',
                         'missing': missing, 'added': added, 'required': criteria['preserve_buttons'],
                         'allowed_removed': permitted, 'unexpected_missing': unexpected,
                         'detail': 'Identidad por AutomationId o nombre; conserva multiplicidad, no acredita lógica.'}
    # Anonymous names and duplicated IDs do not identify individual controls reliably.
    uncertain = any(not key[1] or (key[0] == 'id' and count > 1)
                    for counts in (counts_before, counts_after) for key, count in counts.items())
    if uncertain:
        report['buttons']['status'] = 'not_verifiable'
    checks = measured_checks(after, criteria['rows'], criteria['square_bounds'])
    report['criteria'] = checks
    paired = []
    a = {identity(c): c for c in first if counts_before[identity(c)] == 1}
    b = {identity(c): c for c in second if counts_after[identity(c)] == 1}
    same_dpi = before['window']['dpi'] == after['window']['dpi']
    for key in sorted(a.keys() & b.keys()):
        left, right = a[key], b[key]
        aw, ah = before['window']['client_size_px']
        bw, bh = after['window']['client_size_px']
        old = [round(left['rect_client_px'][i] / (aw if i % 2 == 0 else ah), 5) for i in range(4)]
        new = [round(right['rect_client_px'][i] / (bw if i % 2 == 0 else bh), 5) for i in range(4)]
        paired.append({'identity': key, 'name': right['name'],
                       'before_px': left['rect_client_px'], 'after_px': right['rect_client_px'],
                       'before_ratio': round(left['width'] / left['height'], 4),
                       'after_ratio': round(right['width'] / right['height'], 4),
                       'relative_rect_changed': any(not math.isclose(x, y, abs_tol=0.002) for x, y in zip(old, new)),
                       'before_relative': old, 'after_relative': new})
    report['geometry'] = {'paired': paired, 'same_dpi': same_dpi,
                          'relative_changed': sum(c['relative_rect_changed'] for c in paired),
                          'detail': 'Coordenadas relativas a cada área cliente; distinto tamaño/DPI no implica mejora o regresión.'}
    required = [checks['order']['status'], checks['square_bounds']['status']]
    if criteria['preserve_buttons']:
        required.append(report['buttons']['status'])
    if 'differs' in required:
        report.update(status='differs', detail='Al menos un criterio medible exigido no coincide.')
    elif 'not_verifiable' in required:
        report.update(status='not_verifiable', detail='No se pudo comprobar algún criterio medible exigido.')
    else:
        report.update(status='measured', detail='Comparación medible disponible; no equivale a aprobación visual o funcional.')
    return report


def attach_inspections(verification, before, after, criteria):
    result = dict(verification)
    result['medicion_uia'] = compare_inspections(before, after, criteria)
    if result['medicion_uia']['status'] == 'differs':
        result.update(estado='no_cumple_medicion', revision_humana=True,
                      nota='No coincide un criterio medido con UIA. La interpretación del modelo no lo sustituye.')
        # Keep the reason for an invalidated/restored visual observation visible.
        if verification.get('nota', '').startswith(result['nota']):
            result['nota'] = verification['nota']
        elif verification.get('nota'):
            result['nota'] += ' ' + verification['nota']
    return result


def measured_failure(verification):
    return bool(verification and verification.get('medicion_uia', {}).get('status') == 'differs')


def audit_safe(report):
    """Persist aggregate evidence, not the per-control geometry kept in memory."""
    if not isinstance(report, dict) or 'medicion_uia' not in report:
        return report
    result = dict(report)
    measured = dict(report['medicion_uia'])
    if 'geometry' in measured:
        geometry = measured['geometry']
        measured['geometry'] = {k: v for k, v in geometry.items() if k != 'paired'}
        measured['geometry']['paired_count'] = len(geometry['paired'])
    if 'criteria' in measured:
        measured['criteria'] = {k: measured['criteria'][k] for k in ('order', 'square_bounds')}
        measured['criteria']['square_bounds'] = {k: v for k, v in measured['criteria']['square_bounds'].items() if k != 'outside'}
    result['medicion_uia'] = measured
    return result


def measurement_summary(report):
    data = report.get('medicion_uia', {})
    if not data:
        return ''
    lines = ['MEDICIÓN UIA: ' + data['status'], data['detail']]
    for role, label in (('before', 'Antes'), ('after', 'Después')):
        snap = data[role]
        window = snap.get('window', {})
        lines.append(label + ': ' + snap['status'] + ' · HWND ' + str(window.get('hwnd', '—')) +
                     ' · PID ' + str(window.get('pid', '—')) + ' · botones ' + str(snap.get('buttons', '—')))
    if data.get('buttons'):
        lines.append('Conservación de botones: ' + json.dumps(data['buttons'], ensure_ascii=False))
    if data.get('criteria'):
        lines.append('Orden y proporción exigidos: ' + json.dumps({k: data['criteria'][k] for k in ('order', 'square_bounds')}, ensure_ascii=False))
    if data.get('geometry'):
        lines.append('Controles únicos comparados: ' + str(len(data['geometry']['paired'])) +
                     ' · con cambios relativos de rectángulo: ' + str(data['geometry']['relative_changed']))
    lines.extend(data['limits'])
    return '\n'.join(lines)

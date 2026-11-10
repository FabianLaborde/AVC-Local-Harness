"""Measured visual gate plus vision and reasoning review for external candidates."""
import base64
import hashlib
import io
import json
import re
import unicodedata
from pathlib import Path

from PIL import Image, ImageDraw

from preview_reasoner import REASONER_MODEL


PIXEL_DELTA = 10
MIN_CHANGED_PIXELS = 20
MIN_TARGET_CHANGE_RATIO = 0.015
MIN_GLOBAL_CHANGE_RATIO = 0.001


class VisualObservationFormatError(ValueError):
    """Both bounded attempts returned unusable visual observation data."""


def _normalized(value):
    return ''.join(char for char in unicodedata.normalize('NFD', str(value).casefold())
                   if not unicodedata.combining(char))


def _image(raw):
    try:
        image = Image.open(io.BytesIO(raw))
        image.load()
    except Exception as exc:
        raise ValueError('Captura visual inválida: ' + str(exc)) from exc
    if image.width <= 0 or image.height <= 0 or image.width > 4096 or image.height > 4096:
        raise ValueError('Dimensiones de captura fuera de límite')
    return image.convert('RGB')


def _target_rectangles(inspection, required, size):
    controls = inspection.get('controls') or []
    expected = {_normalized(name): name for name in required}
    found, rectangles = set(), []
    width, height = size
    for control in controls:
        name = _normalized(control.get('name', ''))
        rect = control.get('rect_client_px')
        if name not in expected or not isinstance(rect, list) or len(rect) != 4:
            continue
        try:
            left, top, right, bottom = [int(value) for value in rect]
        except (TypeError, ValueError):
            continue
        left, top = max(0, left), max(0, top)
        right, bottom = min(width, right), min(height, bottom)
        if right > left and bottom > top:
            found.add(name)
            rectangles.append([left, top, right, bottom])
    return rectangles, [expected[name] for name in expected if name in found]


def _target_entries(inspection, required, size):
    """Return one clipped rectangle per requested accessible target."""
    controls = inspection.get('controls') or []
    expected = {_normalized(name): name for name in required}
    width, height = size
    entries, seen = [], set()
    for control in controls:
        normalized = _normalized(control.get('name', ''))
        rect = control.get('rect_client_px')
        if normalized not in expected or normalized in seen or not isinstance(rect, list) or len(rect) != 4:
            continue
        try:
            left, top, right, bottom = [int(value) for value in rect]
        except (TypeError, ValueError):
            continue
        left, top = max(0, left), max(0, top)
        right, bottom = min(width, right), min(height, bottom)
        if right > left and bottom > top:
            entries.append(dict(name=expected[normalized], rect=[left, top, right, bottom]))
            seen.add(normalized)
    return entries


def _zone_stats(changed, image_width, box):
    left, top, right, bottom = box
    pixels = max(0, right - left) * max(0, bottom - top)
    count = 0
    for y in range(top, bottom):
        offset = y * image_width
        count += sum(1 for x in range(left, right) if changed[offset + x])
    return dict(pixels=pixels, changed_pixels=count,
                changed_ratio=round(count / pixels, 6) if pixels else 0,
                observed=count >= 2)


def _target_spatial_profiles(changed, image_width, entries):
    """Describe where pixels changed without deciding what the user intended."""
    profiles = []
    for entry in entries:
        left, top, right, bottom = entry['rect']
        width, height = right - left, bottom - top
        band = max(2, min(width // 3, height // 3))
        corners = {
            'top_left': [left, top, left + band, top + band],
            'top_right': [right - band, top, right, top + band],
            'bottom_right': [right - band, bottom - band, right, bottom],
            'bottom_left': [left, bottom - band, left + band, bottom],
        }
        sides = {
            'top': [left + band, top, right - band, top + band],
            'right': [right - band, top + band, right, bottom - band],
            'bottom': [left + band, bottom - band, right - band, bottom],
            'left': [left, top + band, left + band, bottom - band],
        }
        center = [left + band, top + band, right - band, bottom - band]
        whole = _zone_stats(changed, image_width, entry['rect'])
        profiles.append(dict(
            name=entry['name'], rect=entry['rect'], size=[width, height],
            changed_pixels=whole['changed_pixels'], changed_ratio=whole['changed_ratio'],
            corners={name: _zone_stats(changed, image_width, box)
                     for name, box in corners.items()},
            sides={name: _zone_stats(changed, image_width, box)
                   for name, box in sides.items()},
            center=_zone_stats(changed, image_width, center)))
    return profiles


def measure_visual_delta(before, after, before_inspection, after_inspection, target):
    first, second = _image(before), _image(after)
    if first.size != second.size:
        return dict(status='not_comparable', reason='Las capturas tienen dimensiones diferentes.',
                    before_size=list(first.size), after_size=list(second.size))
    width, height = first.size
    total = width * height
    changed = []
    delta_sum = 0
    min_x, min_y, max_x, max_y = width, height, -1, -1
    for index, (left, right) in enumerate(zip(
            first.get_flattened_data(), second.get_flattened_data())):
        delta = max(abs(left[channel] - right[channel]) for channel in range(3))
        is_changed = delta >= PIXEL_DELTA
        changed.append(is_changed)
        delta_sum += sum(abs(left[channel] - right[channel]) for channel in range(3))
        if is_changed:
            x, y = index % width, index // width
            min_x, min_y, max_x, max_y = min(min_x, x), min(min_y, y), max(max_x, x), max(max_y, y)
    changed_pixels = sum(changed)
    required = list(dict.fromkeys(target.get('required_controls') or []))
    before_rects, found_before = _target_rectangles(before_inspection, required, first.size)
    after_rects, found_after = _target_rectangles(after_inspection, required, first.size)
    rectangles = before_rects + [rect for rect in after_rects if rect not in before_rects]
    mask = Image.new('1', first.size, 0)
    drawer = ImageDraw.Draw(mask)
    for left, top, right, bottom in rectangles:
        drawer.rectangle((left, top, right - 1, bottom - 1), fill=1)
    target_mask = list(mask.get_flattened_data())
    target_pixels = sum(target_mask)
    target_changed = sum(1 for flag, selected in zip(changed, target_mask) if flag and selected)
    outside_changed = changed_pixels - target_changed
    target_entries = _target_entries(before_inspection, required, first.size)
    spatial_profiles = _target_spatial_profiles(changed, width, target_entries)
    missing_before = [name for name in required if _normalized(name) not in {
        _normalized(item) for item in found_before}]
    missing_after = [name for name in required if _normalized(name) not in {
        _normalized(item) for item in found_after}]
    metrics = dict(
        status='measured', size=[width, height], pixel_delta_threshold=PIXEL_DELTA,
        changed_pixels=changed_pixels, changed_ratio=round(changed_pixels / total, 6),
        mean_absolute_channel_delta=round(delta_sum / (total * 3), 4),
        change_bbox=([min_x, min_y, max_x + 1, max_y + 1] if changed_pixels else None),
        required_controls=required, target_rectangles=rectangles,
        target_spatial_profiles=spatial_profiles,
        target_pixels=target_pixels, target_changed_pixels=target_changed,
        target_changed_ratio=round(target_changed / target_pixels, 6) if target_pixels else None,
        outside_changed_pixels=outside_changed,
        outside_changed_ratio=round(outside_changed / max(1, total - target_pixels), 6),
        missing_before=missing_before, missing_after=missing_after)
    if required and (missing_before or missing_after or not target_pixels):
        metrics.update(gate='not_verifiable',
                       gate_reason='No se localizaron todos los controles objetivo en ambas capturas.')
    elif changed_pixels < MIN_CHANGED_PIXELS:
        metrics.update(gate='not_fulfilled', gate_reason='Las capturas son iguales o casi iguales.')
    elif required and (target_changed < MIN_CHANGED_PIXELS
                       or target_changed / target_pixels < MIN_TARGET_CHANGE_RATIO):
        metrics.update(gate='not_fulfilled',
                       gate_reason='El cambio dentro de los controles objetivo es visualmente insignificante.')
    elif not required and changed_pixels / total < MIN_GLOBAL_CHANGE_RATIO:
        metrics.update(gate='not_fulfilled', gate_reason='El cambio global es visualmente insignificante.')
    else:
        metrics.update(gate='reviewable', gate_reason='Existe un cambio medible para revisión semántica.')
    return metrics


def _raw_content(response):
    if not isinstance(response, dict):
        raise ValueError('El modelo devolvió una respuesta de transporte inválida')
    value = response.get('response', '') or (response.get('message') or {}).get('content', '')
    if not isinstance(value, str) or not value.strip():
        raise ValueError('El modelo no devolvió contenido final')
    return re.sub(r'^```(?:json)?\s*|\s*```$', '', value.strip(), flags=re.I)


def _content(response):
    return json.loads(_raw_content(response))


def _safe_audit(api, event, **fields):
    try:
        api.audit(event, **fields)
    except Exception:
        pass


def _visual_observation(api, payload, attempt):
    """Run and parse one observation without retaining its potentially sensitive text."""
    try:
        response = api.post_ollama('/api/chat', payload, timeout=240)
    except Exception as exc:
        _safe_audit(api, 'visual_observation_attempt', attempt=attempt,
                    status='transport_error', error_type=type(exc).__name__,
                    error=str(exc)[:300])
        raise
    try:
        raw = _raw_content(response)
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise ValueError('La observación visual no es un objeto JSON')
        missing = [name for name in VISION_SCHEMA['required'] if name not in result]
        if missing:
            raise ValueError('La observación visual está incompleta; faltan: ' +
                             ', '.join(missing))
    except (json.JSONDecodeError, ValueError) as exc:
        raw_value = ''
        try:
            raw_value = _raw_content(response)
        except ValueError:
            pass
        _safe_audit(
            api, 'visual_observation_attempt', attempt=attempt,
            status='format_rejected', error_type=type(exc).__name__,
            error=str(exc)[:300], response_chars=len(raw_value),
            response_sha256=(hashlib.sha256(raw_value.encode('utf8')).hexdigest()
                             if raw_value else ''),
            done_reason=str(response.get('done_reason', ''))[:80])
        raise
    _safe_audit(api, 'visual_observation_attempt', attempt=attempt, status='accepted',
                response_chars=len(raw), response_sha256=hashlib.sha256(
                    raw.encode('utf8')).hexdigest(),
                done_reason=str(response.get('done_reason', ''))[:80])
    return result


def _focused_target_images(before, after, metrics):
    """Return enlarged target-only sheets for semantic review; metrics keep full images."""
    rectangles = metrics.get('target_rectangles') if isinstance(metrics, dict) else None
    if not isinstance(rectangles, list) or not rectangles:
        return before, after, False
    first, second = _image(before), _image(after)
    valid = []
    for rect in rectangles[:12]:
        if not isinstance(rect, list) or len(rect) != 4:
            continue
        try:
            left, top, right, bottom = [int(value) for value in rect]
        except (TypeError, ValueError):
            continue
        left, top = max(0, left - 4), max(0, top - 4)
        right, bottom = min(first.width, right + 4), min(first.height, bottom + 4)
        if right > left and bottom > top:
            normalized = [left, top, right, bottom]
            if normalized not in valid:
                valid.append(normalized)
    if not valid:
        return before, after, False
    max_width = max(right - left for left, top, right, bottom in valid)
    scale = max(3, min(8, 640 // max(1, max_width)))
    def sheet(image):
        crops = [image.crop(tuple(rect)).resize(
            ((rect[2] - rect[0]) * scale, (rect[3] - rect[1]) * scale),
            Image.Resampling.NEAREST) for rect in valid]
        width = max(crop.width for crop in crops)
        height = sum(crop.height for crop in crops) + 8 * (len(crops) - 1)
        result = Image.new('RGB', (width, height), (232, 236, 243))
        top = 0
        for crop in crops:
            result.paste(crop, (0, top))
            top += crop.height + 8
        output = io.BytesIO()
        result.save(output, format='PNG')
        return output.getvalue()
    return sheet(first), sheet(second), True


CONTRACT_SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'required': ['summary', 'conditions'],
    'properties': {
        'summary': {'type': 'string'},
        'conditions': {'type': 'array', 'minItems': 1, 'maxItems': 8, 'items': {
            'type': 'object', 'additionalProperties': False,
            'required': ['id', 'description', 'target_scope', 'spatial_mode',
                         'continuity', 'symmetry'],
            'properties': {
                'id': {'type': 'string'},
                'description': {'type': 'string'},
                'target_scope': {'type': 'string',
                                 'enum': ['all_targets', 'any_target', 'global']},
                'spatial_mode': {'type': 'string',
                                 'enum': ['any', 'surface', 'perimeter', 'corners',
                                          'edges', 'position', 'content']},
                'continuity': {'type': 'string',
                               'enum': ['required', 'preserved', 'not_applicable']},
                'symmetry': {'type': 'string',
                             'enum': ['required', 'preserved', 'not_applicable']},
            }}}}}

VISION_SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'required': ['status', 'observed_change', 'target_before', 'target_after',
                 'requested_effect_observed', 'target_effect', 'unintended_changes',
                 'condition_results', 'target_results'],
    'properties': {
        'status': {'type': 'string', 'enum': ['visible_change', 'no_visible_change', 'uncertain']},
        'observed_change': {'type': 'string'},
        'target_before': {'type': 'string'},
        'target_after': {'type': 'string'},
        'requested_effect_observed': {'type': 'string', 'enum': ['yes', 'no', 'uncertain']},
        'target_effect': {'type': 'string'},
        'unintended_changes': {'type': 'array', 'items': {'type': 'string'}, 'maxItems': 12},
        'condition_results': {'type': 'array', 'maxItems': 8, 'items': {
            'type': 'object', 'additionalProperties': False,
            'required': ['id', 'status', 'evidence'],
            'properties': {
                'id': {'type': 'string'},
                'status': {'type': 'string',
                           'enum': ['fulfilled', 'not_fulfilled', 'uncertain']},
                'evidence': {'type': 'string'}}}},
        'target_results': {'type': 'array', 'maxItems': 24, 'items': {
            'type': 'object', 'additionalProperties': False,
            'required': ['target', 'completeness', 'boundary', 'symmetry', 'defects'],
            'properties': {
                'target': {'type': 'string'},
                'completeness': {'type': 'string',
                                 'enum': ['complete', 'partial', 'absent', 'uncertain']},
                'boundary': {'type': 'string',
                             'enum': ['continuous', 'interrupted', 'unchanged',
                                      'not_applicable', 'uncertain']},
                'symmetry': {'type': 'string',
                             'enum': ['yes', 'no', 'not_applicable', 'uncertain']},
                'defects': {'type': 'array', 'items': {'type': 'string'}, 'maxItems': 8}}}}}}

REASON_SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'required': ['status', 'reason', 'evidence', 'retry_instruction'],
    'properties': {
        'status': {'type': 'string', 'enum': ['fulfilled', 'not_fulfilled', 'uncertain']},
        'reason': {'type': 'string'},
        'evidence': {'type': 'array', 'items': {'type': 'string'}, 'maxItems': 12},
        'retry_instruction': {'type': 'string'}}}

MATCH_SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'required': ['match', 'reason'],
    'properties': {
        'match': {'type': 'string', 'enum': ['yes', 'no', 'uncertain']},
        'reason': {'type': 'string'}}}


def _acceptance_contract(api, instruction, target, model):
    """Compile the request into observable conditions before inspecting the candidate."""
    names = list(dict.fromkeys((target or {}).get('required_controls') or []))
    prompt = (
        'Convertí el PEDIDO en un contrato de aceptación exclusivamente visual. No evalúes '
        'ningún candidato y no generes código. Cada condición debe ser observable en capturas. '
        'Usá all_targets cuando el pedido plural o global deba cumplirse en cada objetivo. '
        'spatial_mode describe dónde debe observarse el efecto: corners implica todas las '
        'esquinas, perimeter todo el contorno, edges todos los lados, surface el área, position '
        'una relación espacial y content texto/iconos. continuity=required si el resultado debe '
        'formar un trazo o límite continuo; symmetry=required cuando las partes equivalentes '
        'deben conservar una forma equilibrada. No conviertas una preferencia vaga en una '
        'medida inventada. Devolvé solo JSON.\nPEDIDO:\n' + instruction +
        '\nOBJETIVOS ACCESIBLES:\n' + json.dumps(names, ensure_ascii=False))
    payload = dict(model=model, stream=False, format=CONTRACT_SCHEMA, think='low',
                   prompt=prompt, options=dict(temperature=0, num_predict=1000))
    contract = _content(api.post_ollama('/api/generate', payload, timeout=240))
    conditions = contract.get('conditions')
    if not isinstance(conditions, list) or not conditions:
        raise ValueError('El contrato visual no contiene condiciones observables')
    ids = [str(item.get('id', '')).strip() for item in conditions
           if isinstance(item, dict)]
    if len(ids) != len(conditions) or any(not item for item in ids) or len(set(ids)) != len(ids):
        raise ValueError('El contrato visual contiene identificadores inválidos o repetidos')
    for condition in conditions:
        mode = condition.get('spatial_mode')
        if names:
            condition['target_scope'] = 'all_targets'
        if mode not in {'corners', 'perimeter', 'edges'}:
            condition['continuity'] = 'not_applicable'
        if mode not in {'corners', 'perimeter', 'edges', 'position'}:
            condition['symmetry'] = 'not_applicable'
    return contract


def _complete_spatial_mode(condition, profiles):
    mode = condition.get('spatial_mode')
    if mode not in {'corners', 'perimeter', 'edges'}:
        return []
    issues = []
    for profile in profiles:
        if mode == 'corners':
            missing = [name for name, value in profile.get('corners', {}).items()
                       if not value.get('observed')]
        else:
            missing = [name for name, value in profile.get('sides', {}).items()
                       if not value.get('observed')]
            if mode == 'perimeter':
                missing += [name for name, value in profile.get('corners', {}).items()
                            if not value.get('observed')]
        if missing:
            issues.append(f'{profile.get("name", "objetivo")}: cambio ausente en ' +
                          ', '.join(missing))
    return issues


def _deterministic_spatial_findings(contract, metrics):
    """Describe incomplete equivalent regions without depending on a vision model."""
    findings = []
    profiles = metrics.get('target_spatial_profiles') or []
    for condition in (contract or {}).get('conditions') or []:
        mode = condition.get('spatial_mode')
        if mode not in {'corners', 'edges'}:
            continue
        region_key = 'corners' if mode == 'corners' else 'sides'
        for profile in profiles:
            regions = profile.get(region_key) or {}
            observed = [name for name, value in regions.items()
                        if isinstance(value, dict) and value.get('observed')]
            missing = [name for name, value in regions.items()
                       if not isinstance(value, dict) or not value.get('observed')]
            if not missing:
                continue
            target = profile.get('name', 'objetivo')
            if observed:
                findings.append(
                    f'{target}: cobertura asimétrica; cambio presente en ' +
                    ', '.join(observed) + ' y ausente en ' + ', '.join(missing) + '.')
            else:
                findings.append(
                    f'{target}: cambio ausente en todas las zonas equivalentes: ' +
                    ', '.join(missing) + '.')
    return list(dict.fromkeys(findings))


def _contract_findings(contract, vision, metrics):
    """Fail closed on missing, partial or contradictory observable evidence."""
    issues = _deterministic_spatial_findings(contract, metrics)
    conditions = contract.get('conditions') or []
    results = {str(item.get('id', '')): item
               for item in (vision.get('condition_results') or [])
               if isinstance(item, dict)}
    targets = { _normalized(item.get('target', '')): item
               for item in (vision.get('target_results') or [])
               if isinstance(item, dict)}
    profiles = metrics.get('target_spatial_profiles') or []
    required = list(dict.fromkeys(metrics.get('required_controls') or []))
    if vision.get('requested_effect_observed') == 'yes' and vision.get('status') != 'visible_change':
        issues.append('La respuesta visual afirma cumplimiento pero no declara un cambio visible.')
    if vision.get('requested_effect_observed') != 'yes':
        issues.append('La observación no confirma el efecto solicitado.')
    for condition in conditions:
        condition_id = str(condition.get('id', ''))
        result = results.get(condition_id)
        if not result:
            issues.append(f'Falta evaluar la condición {condition_id}.')
        elif result.get('status') != 'fulfilled':
            detail = str(result.get('evidence', '')).strip()
            issues.append(f'Condición {condition_id} no cumplida' +
                          (': ' + detail if detail else '.'))
        if condition.get('target_scope') == 'all_targets':
            for name in required:
                target_result = targets.get(_normalized(name))
                if not target_result:
                    issues.append(f'Falta evaluar el objetivo {name}.')
                    continue
                if target_result.get('completeness') != 'complete':
                    issues.append(f'{name}: efecto {target_result.get("completeness", "incierto")}.')
                if (condition.get('continuity') == 'required'
                        and target_result.get('boundary') != 'continuous'):
                    issues.append(f'{name}: el contorno no es continuo.')
                if (condition.get('symmetry') == 'required'
                        and target_result.get('symmetry') != 'yes'):
                    issues.append(f'{name}: la forma no es simétrica.')
        issues.extend(_complete_spatial_mode(condition, profiles))
    if vision.get('unintended_changes'):
        issues.append('La observación informa cambios imprevistos.')
    return list(dict.fromkeys(issues))


def _reconcile_target_transition(api, instruction, vision):
    prompt = (
        'Actuá como adjudicador semántico, no como inspector de imágenes. Decidí únicamente si '
        'la transición textual entre ESTADO ANTES y ESTADO DESPUÉS corresponde al PEDIDO. No '
        'evalúes magnitud, código, métricas globales ni inventes cambios. Devolvé solo JSON.\n'
        'PEDIDO:\n' + instruction + '\nESTADO ANTES:\n' +
        str(vision.get('target_before', '')) + '\nESTADO DESPUÉS:\n' +
        str(vision.get('target_after', '')))
    payload = dict(model=REASONER_MODEL, stream=False, format=MATCH_SCHEMA, think='low',
                   prompt=prompt, options=dict(temperature=0, num_predict=500))
    return _content(api.post_ollama('/api/generate', payload, timeout=180))


def _vision_review(api, model, instruction, before, after, metrics, contract):
    before_review, after_review, focused = _focused_target_images(before, after, metrics)
    message = dict(role='user', content=(
            'Compará estas dos capturas de la misma interfaz. La primera es ANTES y la segunda '
            'CANDIDATO. ' + ('Son recortes ampliados de los controles objetivo; evaluá sus bordes y forma. '
            if focused else '') + 'Evaluá solamente lo observable respecto del pedido. No supongas código, '
            'eventos ni medidas ausentes. Separá el estado anterior del posterior y respondé '
            'requested_effect_observed=yes únicamente cuando todas las condiciones del contrato '
            'estén satisfechas. Evaluá cada condition id y cada objetivo accesible por separado. '
            'Un efecto parcial, una esquina ausente, un contorno interrumpido o una asimetría '
            'requerida son not_fulfilled, aunque existan píxeles distintos. Si '
            'podés describir inequívocamente el cambio solicitado, no uses uncertain solo por una '
            'reserva subjetiva sobre su intensidad. Devolvé solo JSON.\nPEDIDO:\n' + instruction +
            '\nCONTRATO DE ACEPTACIÓN:\n' + json.dumps(contract, ensure_ascii=False) +
            '\nMÉTRICAS Y COBERTURA ESPACIAL:\n' + json.dumps(metrics, ensure_ascii=False)),
            images=[base64.b64encode(before_review).decode(),
                    base64.b64encode(after_review).decode()])
    payload = dict(model=model, stream=False, format=VISION_SCHEMA, think=False,
                   options=dict(temperature=0, num_ctx=8192, num_predict=1800),
                   messages=[message])
    observation_attempts = 1
    try:
        result = _visual_observation(api, payload, observation_attempts)
    except (json.JSONDecodeError, ValueError) as first_error:
        observation_attempts += 1
        correction = dict(role='user', content=(
            'La respuesta anterior fue rechazada antes de evaluarla porque no contiene un JSON '
            'completo y utilizable. Error exacto: ' + str(first_error)[:300] +
            '. Volvé a observar las MISMAS dos imágenes y el MISMO contrato. Respondé una sola '
            'vez con el objeto JSON completo exigido por el esquema, sin Markdown, comentarios '
            'ni texto adicional. No reutilices ni completes valores de la respuesta parcial.'))
        retry_payload = dict(payload, messages=[message, correction],
                             options=dict(temperature=0, num_ctx=8192, num_predict=2600))
        try:
            result = _visual_observation(api, retry_payload, observation_attempts)
        except (json.JSONDecodeError, ValueError) as second_error:
            raise VisualObservationFormatError(
                'La observación visual devolvió JSON inválido o incompleto en dos intentos. '
                'Último error: ' + str(second_error)) from second_error
    before_state = _normalized(result.get('target_before', ''))
    after_state = _normalized(result.get('target_after', ''))
    if (result.get('status') == 'uncertain' and before_state and after_state
            and before_state != after_state and not result.get('unintended_changes')
            and observation_attempts < 2):
        correction = dict(role='user', content=(
            'Tu primera respuesta quedó semánticamente ambigua: declaraste estados distintos '
            'antes/después y ningún cambio imprevisto, pero no resolviste si esa diferencia cumple '
            'el pedido. Compará literalmente target_before y target_after con el PEDIDO y devolvé '
            'un JSON final coherente. Si existen controles objetivo, evaluá target_changed_ratio y '
            'outside_changed_pixels; no diluyas el efecto con changed_ratio de toda la ventana. '
            'Primera respuesta:\n' + json.dumps(result, ensure_ascii=False)))
        retry_payload = dict(payload, messages=[message, correction])
        result = _visual_observation(api, retry_payload, observation_attempts + 1)
        before_state = _normalized(result.get('target_before', ''))
        after_state = _normalized(result.get('target_after', ''))
        if (result.get('status') == 'uncertain' and before_state and after_state
                and before_state != after_state and not result.get('unintended_changes')):
            reconciliation = _reconcile_target_transition(api, instruction, result)
            result['semantic_reconciliation'] = reconciliation
            if reconciliation.get('match') == 'yes':
                result['status'] = 'visible_change'
                result['requested_effect_observed'] = 'yes'
    return result


def _reason_review(api, model, instruction, metrics, vision, contract, findings):
    prompt = (
        'Sos el supervisor final de un cambio visual. No generes código. Determiná si el pedido '
        'está visiblemente cumplido usando las métricas y la observación visual. Una compilación '
        'no demuestra cumplimiento. No contradigas métricas negativas: gate distinto de '
        'reviewable nunca puede ser fulfilled. Un cambio fuera del objetivo o no perceptible es '
        'not_fulfilled. El contrato previo es obligatorio: una condición parcial, un objetivo '
        'omitido o cualquier hallazgo determinista impide fulfilled. '
        'Si hay controles objetivo, usa target_changed_ratio y outside_changed_pixels para medir '
        'su efecto; changed_ratio global está diluido por el resto de la ventana. '
        'retry_instruction debe ser una corrección breve y concreta, o vacío si '
        'no corresponde. Devolvé solo JSON.\nPEDIDO:\n' + instruction + '\nMÉTRICAS:\n' +
        json.dumps(metrics, ensure_ascii=False) + '\nCONTRATO:\n' +
        json.dumps(contract, ensure_ascii=False) + '\nHALLAZGOS OBLIGATORIOS:\n' +
        json.dumps(findings, ensure_ascii=False) + '\nOBSERVACIÓN VISUAL:\n' +
        json.dumps(vision, ensure_ascii=False))
    payload = dict(model=model, stream=False, format=REASON_SCHEMA, think='low', prompt=prompt,
                   options=dict(temperature=0, num_predict=1800))
    return _content(api.post_ollama('/api/generate', payload, timeout=300))


def review_visual_candidate(api, instruction, before, after, before_inspection,
                            after_inspection, target, report_dir, vision_model,
                            reasoner_model=None):
    """Return a fail-closed verdict; never modifies sources or executes the project."""
    try:
        metrics = measure_visual_delta(before, after, before_inspection,
                                       after_inspection, target or {})
        if metrics.get('gate') == 'not_verifiable':
            result = dict(status='uncertain', reason=metrics['gate_reason'], metrics=metrics,
                          contract=None, contract_findings=[], vision=None, reasoning=None,
                          retry_instruction='')
        elif metrics.get('changed_pixels', 0) < MIN_CHANGED_PIXELS:
            result = dict(status='not_fulfilled', reason=metrics['gate_reason'], metrics=metrics,
                          contract=None, contract_findings=[], vision=None, reasoning=None,
                          retry_instruction='Realizá un cambio visual perceptible en el objetivo solicitado.')
        elif not vision_model:
            if metrics.get('gate') == 'not_fulfilled':
                result = dict(
                    status='not_fulfilled', reason=metrics['gate_reason'], metrics=metrics,
                    contract=None, contract_findings=[], vision=None, reasoning=None,
                    retry_instruction=(
                        'Corregí el candidato vigente para producir un cambio perceptible '
                        'en todos los objetivos solicitados.'))
            else:
                result = dict(status='uncertain', reason='No hay un modelo visual configurado.',
                              metrics=metrics, contract=None, contract_findings=[], vision=None,
                              reasoning=None, retry_instruction='')
        else:
            contract = _acceptance_contract(
                api, instruction, target or {}, reasoner_model or REASONER_MODEL)
            findings = _deterministic_spatial_findings(contract, metrics)
            if metrics.get('gate') == 'not_fulfilled' or findings:
                # A negative metric remains authoritative, but semantic evidence can
                # still explain defects (for example continuity or clipping) so the
                # next bounded attempt repairs the cause instead of only its magnitude.
                vision = _vision_review(
                    api, vision_model, instruction, before, after, metrics, contract)
                findings = list(dict.fromkeys(
                    findings + _contract_findings(contract, vision, metrics)))
                status = 'not_fulfilled'
                reason = (metrics.get('gate_reason')
                          if metrics.get('gate') == 'not_fulfilled' else
                          'La cobertura espacial medida está incompleta.')
                if findings:
                    reason += ' ' + findings[0]
                retry = ('Corregí el candidato vigente según la evidencia espacial: ' +
                         '; '.join(findings[:4]) if findings else
                         'Corregí el candidato vigente para producir un cambio perceptible '
                         'en todos los objetivos solicitados.')
                result = dict(status=status, reason=reason, metrics=metrics,
                              contract=contract, contract_findings=findings, vision=vision,
                              reasoning=None, retry_instruction=retry)
            else:
                vision = _vision_review(
                    api, vision_model, instruction, before, after, metrics, contract)
                findings = _contract_findings(contract, vision, metrics)
                reasoning = _reason_review(api, reasoner_model or REASONER_MODEL,
                                           instruction, metrics, vision, contract, findings)
                status = reasoning.get('status')
                if findings and status == 'fulfilled':
                    status = 'not_fulfilled'
                    reason = ('El contrato visual no está completamente satisfecho: ' +
                              findings[0])
                elif vision.get('status') != 'visible_change' and status == 'fulfilled':
                    status = 'uncertain'
                    reason = 'La observación visual no acredita un cambio visible.'
                elif status == 'fulfilled':
                    target_ratio = metrics.get('target_changed_ratio')
                    target_percent = (f'{target_ratio * 100:.2f} %'
                                      if isinstance(target_ratio, (int, float)) else 'medible')
                    reason = (
                        'Cambio visual confirmado en los controles objetivo: ' + target_percent +
                        f' ({metrics.get("target_changed_pixels", 0)} píxeles), con ' +
                        f'{metrics.get("outside_changed_pixels", 0)} píxeles fuera del objetivo. '
                        'La comparación enfocada y la adjudicación semántica coinciden con el pedido.')
                else:
                    reason = reasoning.get('reason', '')
                retry = reasoning.get('retry_instruction', '')
                if findings and not retry:
                    retry = 'Corregí el candidato vigente: ' + '; '.join(findings[:4])
                result = dict(status=status, reason=reason, metrics=metrics,
                              contract=contract, contract_findings=findings, vision=vision,
                              reasoning=reasoning, retry_instruction=retry)
    except Exception as exc:
        metrics = locals().get('metrics') or {}
        contract = locals().get('contract')
        findings = (_deterministic_spatial_findings(contract, metrics)
                    if contract else [])
        deterministic_rejection = metrics.get('gate') == 'not_fulfilled' or bool(findings)
        result = dict(
            status='not_fulfilled' if deterministic_rejection else 'uncertain',
            reason=(('La evidencia determinista rechaza el candidato. ' +
                     (metrics.get('gate_reason') or '') +
                     ((' ' + findings[0]) if findings else '')).strip()
                    if deterministic_rejection else
                    'La revisión visual falló: ' + str(exc)),
            metrics=metrics, contract=contract, contract_findings=findings,
            vision=locals().get('vision'), reasoning=None,
            retry_instruction=(
                'Corregí el candidato vigente según la evidencia espacial: ' +
                '; '.join(findings[:4]) if findings else
                ('Realizá un cambio visual perceptible en todos los objetivos solicitados.'
                 if deterministic_rejection else '')))
    Path(report_dir).mkdir(parents=True, exist_ok=True)
    (Path(report_dir) / 'visual_fulfillment.json').write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding='utf8')
    api.audit('external_visual_fulfillment', status=result['status'],
              reason=result['reason'][:300])
    return result

"""Resolve visual intent contracts against read-only WPF source evidence.

This adapter never executes XAML and never evaluates styles, resources, bindings,
templates, defaults, inheritance, DPI, or rendered geometry.  It may prove a direct
declaration, but absence of a declaration is always unknown rather than false.
"""
import math
import re
import unicodedata

from intent_contract import normalize_color


ROLE_TYPES = {
    'title': {'TextBlock', 'Label'},
    'button': {'Button'},
    'window': {'Window'},
    'field': {'TextBox', 'PasswordBox', 'ComboBox'},
    'panel': {'Border', 'Grid', 'StackPanel', 'DockPanel', 'WrapPanel', 'Canvas'},
    'interface': {'Window', 'Page', 'UserControl'},
}
COLLECTIVE_ROLES = {'controls', 'sections', 'content'}
PROPERTY_NAMES = {
    'horizontal_alignment': 'HorizontalAlignment',
    'background': 'Background',
    'foreground': 'Foreground',
}
ALIGNMENTS = {'left': 'left', 'right': 'right', 'center': 'center',
              'stretch': 'stretch'}


def _plain(value):
    return ''.join(character for character in unicodedata.normalize(
        'NFD', str(value or '').casefold()) if not unicodedata.combining(character)).strip()


def _records(inventories):
    if isinstance(inventories, dict):
        inventories = [inventories]
    result = []
    for inventory in inventories or []:
        for index, element in enumerate(inventory.get('elements', [])):
            result.append({'file': inventory.get('file', ''), 'index': index,
                           'truncated': bool(inventory.get('truncated')),
                           'element': element})
    return result


def _literal_labels(element):
    properties = element.get('properties', {})
    values = [element.get('name', '')]
    values.extend(properties.get(key, '') for key in ('Text', 'Content', 'Header', 'Title'))
    return {_plain(value) for value in values if str(value).strip()}


def _candidate_records(target, records):
    role = target.get('role')
    if role in COLLECTIVE_ROLES:
        return None
    allowed = ROLE_TYPES.get(role)
    if not allowed:
        return []
    candidates = [record for record in records if record['element'].get('type') in allowed]
    label = target.get('label')
    if label:
        expected = _plain(label)
        candidates = [record for record in candidates
                      if expected in _literal_labels(record['element'])]
    return candidates


def _target_summary(record):
    element = record['element']
    labels = sorted(_literal_labels(element))
    return {'file': record['file'], 'element_index': record['index'],
            'type': element.get('type'), 'name': element.get('name') or None,
            'labels': labels, 'properties': dict(element.get('properties', {}))}


def _direct_value(change, record):
    prop = change.get('property')
    properties = record['element'].get('properties', {})
    if prop in PROPERTY_NAMES:
        source_property = PROPERTY_NAMES[prop]
        raw = properties.get(source_property)
        if raw is None or raw.strip().startswith('{'):
            return None, source_property, raw
        if prop in ('background', 'foreground'):
            return normalize_color(raw), source_property, raw
        return ALIGNMENTS.get(_plain(raw)), source_property, raw
    if prop in {'window_bounds', 'element_bounds'}:
        values = {}
        for name in ('Width', 'Height'):
            raw = properties.get(name)
            if raw is None or not re.fullmatch(r'\d+(?:[.,]\d+)?', raw.strip()):
                return None, 'Width/Height', {'Width': properties.get('Width'),
                                              'Height': properties.get('Height')}
            values[name.casefold()] = float(raw.replace(',', '.'))
        return values, 'Width/Height', {'Width': properties['Width'],
                                        'Height': properties['Height']}
    return None, None, None


def _equal(actual, expected):
    if isinstance(expected, dict) and isinstance(actual, dict):
        try:
            return all(math.isclose(float(actual[key]), float(value), abs_tol=0.01)
                       for key, value in expected.items())
        except (KeyError, TypeError, ValueError):
            return False
    return actual == expected


def _unresolved(change, status, code, reason, candidates=None):
    return {'status': status, 'change': change, 'code': code, 'reason': reason,
            'matches': [_target_summary(item) for item in (candidates or [])]}


def _resolve_change(change, records):
    target = change.get('target') or {}
    candidates = _candidate_records(target, records)
    if candidates is None:
        return _unresolved(
            change, 'not_verifiable', 'collective_target_requires_geometry',
            'El objetivo es un conjunto; el inventario XAML no prueba su distribución renderizada.')
    if not candidates:
        truncated = any(record['truncated'] for record in records)
        return _unresolved(
            change, 'not_verifiable',
            'target_not_found_in_truncated_source' if truncated else 'target_not_found',
            'No se encontró un objetivo literal compatible en la evidencia XAML disponible.')
    if any(record['truncated'] for record in records):
        return _unresolved(
            change, 'not_verifiable', 'target_uniqueness_truncated',
            'El inventario XAML está truncado y no permite probar que el objetivo sea único.',
            candidates)
    if len(candidates) > 1:
        return _unresolved(
            change, 'ambiguous', 'multiple_targets',
            'Más de un elemento coincide; AVC no elige uno por proximidad ni orden.', candidates)

    record = candidates[0]
    actual, source_property, declared = _direct_value(change, record)
    if source_property is None:
        return _unresolved(
            change, 'not_verifiable', 'property_requires_other_evidence',
            'La propiedad solicitada requiere render, jerarquía adicional u otra evidencia.', candidates)
    if actual is None:
        return {
            **_unresolved(change, 'not_verifiable', 'effective_value_unknown',
                          'La propiedad no está declarada literalmente o depende de recursos, estilos o bindings.',
                          candidates),
            'source_property': source_property, 'declared': declared,
        }

    expected = change.get('expected', {}).get('after')
    satisfied = _equal(actual, expected)
    before = change.get('expected', {}).get('before')
    baseline_conflict = (not satisfied and before is not None and
                         not _equal(actual, before))
    result = {
        'status': ('satisfied' if satisfied else
                   'ambiguous' if baseline_conflict else 'requires_change'),
        'change': change,
        'code': ('direct_value_matches' if satisfied else
                 'declared_initial_differs' if baseline_conflict else
                 'direct_value_differs'),
        'reason': (
            'El atributo XAML directo coincide con el resultado solicitado.' if satisfied
            else 'El estado inicial declarado por el usuario no coincide con el XAML real.'
            if baseline_conflict
            else 'El atributo XAML directo difiere del resultado solicitado.'),
        'matches': [_target_summary(record)], 'source_property': source_property,
        'declared': declared, 'actual': actual, 'expected': expected,
    }
    if before is not None:
        result['declared_initial_matches_request'] = _equal(actual, before)
    return result


def resolve_intent_contract(contract, inventories):
    """Resolve a contract against bounded XAML inventories without editing files."""
    contract_status = contract.get('status')
    if contract_status != 'supported':
        status = 'ambiguous' if contract_status == 'ambiguous' else 'not_verifiable'
        return {'status': status, 'contract_status': contract_status, 'results': [],
                'invariants': [], 'limitations': [
                    'El contrato no está listo para resolverse contra el proyecto.']}

    records = _records(inventories)
    results = [_resolve_change(change, records) for change in contract.get('changes', [])]
    for postcondition in contract.get('postconditions', []):
        results.append({
            'status': 'not_verifiable', 'postcondition': postcondition,
            'code': 'postcondition_requires_runtime_evidence',
            'reason': 'La postcondición requiere geometría o estado de ejecución.',
            'matches': [],
        })
    statuses = {item['status'] for item in results}
    if 'ambiguous' in statuses:
        overall = 'ambiguous'
    elif 'requires_change' in statuses:
        overall = 'requires_change'
    elif statuses == {'satisfied'} and results:
        overall = 'satisfied'
    else:
        overall = 'not_verifiable'
    return {
        'status': overall, 'contract_status': contract_status, 'results': results,
        'invariants': [dict(item, status='not_verifiable')
                       for item in contract.get('invariants', [])],
        'limitations': [
            'Solo se comparan atributos XAML directos de un objetivo inequívoco.',
            'No se evalúan estilos, recursos, bindings, templates, herencia ni geometría renderizada.',
            'La ausencia de un atributo nunca se interpreta como incumplimiento.',
        ],
    }

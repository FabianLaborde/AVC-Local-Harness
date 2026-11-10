"""Conservative, framework-neutral contracts for explicit visual requests.

This module only normalizes what the user actually said.  It does not inspect a
project, call a model, or decide that a target uniquely exists.  Consumers must
resolve targets against real evidence before editing anything.
"""
import re

from catalog_instruction_intents import (permits_width_growth, plain,
                                         requests_compaction,
                                         requests_no_scroll,
                                         requests_preserve_sizes,
                                         requests_relayout)


SCHEMA_VERSION = 1

_ROLE_PATTERNS = (
    ('title', r'\b(?:titulos?|encabezados?|headings?)\b'),
    ('button', r'\b(?:boton(?:es)?|buttons?)\b'),
    ('window', r'\b(?:ventana|window|formulario)\b'),
    ('field', r'\b(?:campo|entrada|textbox|passwordbox|input)\b'),
    ('panel', r'\b(?:panel|tarjeta|contenedor)\b'),
    ('controls', r'\b(?:controles|campos|botones|componentes|elementos)\b'),
    ('sections', r'\b(?:secciones|bloques|areas|zonas)\b'),
    ('content', r'\bcontenido\b'),
    ('interface', r'\b(?:interfaz|interface|aplicacion|pantalla|formulario completo)\b'),
)

_COLORS = {
    'negro': '#000000', 'black': '#000000',
    'blanco': '#FFFFFF', 'white': '#FFFFFF',
    'azul': '#0000FF', 'blue': '#0000FF',
    'verde': '#008000', 'green': '#008000',
    'rojo': '#FF0000', 'red': '#FF0000',
    'gris': '#808080', 'gray': '#808080', 'grey': '#808080',
    'gris claro': '#D3D3D3', 'light gray': '#D3D3D3',
    'celeste': '#ADD8E6', 'light blue': '#ADD8E6',
    'amarillo': '#FFFF00', 'yellow': '#FFFF00',
    'naranja': '#FFA500', 'orange': '#FFA500',
    'violeta': '#800080', 'purple': '#800080',
}

_CHANGE_VERB = re.compile(
    r'\b(?:cambi|pon|aplic|convert|hac|dej|modific|aline|mov|ubic|coloc|'
    r'redimension|ajust|achic|redu|ampli|agrand|ensancha|reorganiz|redistrib|'
    r'reacomod|reubic|distribu|compact)\w*\b')


def normalize_color(value):
    """Normalize only exact named/hex colors known by the contract vocabulary."""
    text = plain(str(value or '').strip())
    color = _COLORS.get(text, str(value or '').strip())
    if not re.fullmatch(r'#[0-9a-fA-F]{3}|#[0-9a-fA-F]{6}', color):
        return None
    code = color[1:]
    if len(code) == 3:
        code = ''.join(character * 2 for character in code)
    return '#' + code.upper()


def _target(text, default=None, original=None):
    """Return only a role/label explicitly present in the instruction."""
    role = next((name for name, pattern in _ROLE_PATTERNS
                 if re.search(pattern, text)), default)
    quoted = re.search(r'["“]([^"”]{1,100})["”]', original or text)
    return {'role': role, 'label': quoted.group(1).strip() if quoted else None}


def _color_mentions(text):
    candidates = []
    for match in re.finditer(r'#[0-9a-f]{6}\b|#[0-9a-f]{3}\b', text):
        candidates.append((match.start(), match.end(), match.group(0).upper()))
    for literal, value in sorted(_COLORS.items(), key=lambda item: -len(item[0])):
        for match in re.finditer(rf'\b{re.escape(literal)}\b', text):
            tail = re.match(r'\s+([a-z]+)', text[match.end():])
            if tail and tail.group(1) not in {
                    'a', 'de', 'del', 'en', 'por', 'y', 'o', 'pero', 'conserva',
                    'conserve', 'conservando', 'mantene', 'mantenga'}:
                # "azul marino" is not the exact named color "azul".
                continue
            candidates.append((match.start(), match.end(), value))
    # Prefer the longest token at the same position and discard overlapping aliases
    # such as "gris" inside "gris claro".
    selected = []
    for start, end, value in sorted(candidates, key=lambda item: (item[0], -(item[1]-item[0]))):
        if any(start < old_end and end > old_start for old_start, old_end, _ in selected):
            continue
        selected.append((start, end, value))
    unique = []
    for _, _, value in sorted(selected):
        if not unique or unique[-1] != value:
            unique.append(value)
    return unique


def _invariants(text):
    if not re.search(r'\b(?:conserv|manten|preserv|no\s+cambi)\w*\b', text):
        return []
    found = []
    groups = (
        ('layout', r'\b(?:distribucion|estructura|layout|posiciones?)\b'),
        ('dimensions', r'\b(?:dimensiones|tamanos?|escala)\b'),
        ('colors', r'\b(?:colores?|paleta)\b'),
        ('content', r'\b(?:contenido|textos?|campos|controles|botones|elementos)\b'),
        ('behavior', r'\b(?:eventos?|funciones?|funcionamiento|logica|comportamiento)\b'),
        ('everything_else', r'\b(?:todo\s+lo\s+demas|el\s+resto|todo\s+lo\s+otro)\b'),
    )
    for name, pattern in groups:
        if re.search(pattern, text):
            found.append({'kind': 'preserve', 'property': name})
    return found


def _add_change(contract, kind, target, property_name, before, after, evidence):
    contract['changes'].append({
        'kind': kind,
        'target': target,
        'property': property_name,
        'expected': {'before': before, 'after': after},
        'evidence': list(evidence),
    })


def _ambiguity(contract, code, message):
    if not any(item['code'] == code for item in contract['ambiguities']):
        contract['ambiguities'].append({'code': code, 'message': message})


def derive_intent_contract(instruction):
    """Normalize explicit visual intent without inventing missing information.

    ``status`` describes only whether the sentence can be represented.  It does
    not mean that its target exists or that the request is already satisfied.
    """
    original = str(instruction or '').strip()
    text = plain(original)
    semantic_text = re.sub(r'["“][^"”]{1,100}["”]', ' ', text)
    contract = {
        'schema_version': SCHEMA_VERSION,
        'status': 'unsupported',
        'instruction': original,
        'changes': [],
        'postconditions': [],
        'invariants': _invariants(semantic_text),
        'required_evidence': [],
        'ambiguities': [],
    }
    if not original:
        _ambiguity(contract, 'empty_instruction', 'La instrucción está vacía.')
        contract['status'] = 'ambiguous'
        return contract

    changing = bool(_CHANGE_VERB.search(semantic_text))

    # Global theme is the target by definition; no control is inferred.
    themes = []
    if re.search(r'\b(?:tema|modo)\s+oscuro\b|\bdark\s+theme\b', semantic_text):
        themes.append('dark')
    if re.search(r'\b(?:tema|modo)\s+claro\b|\blight\s+theme\b', semantic_text):
        themes.append('light')
    if changing and len(set(themes)) == 1:
        _add_change(contract, 'set_theme', {'role': 'interface', 'label': None},
                    'theme', None, themes[0], ('source_style', 'rendered_pixels'))
    elif len(set(themes)) > 1:
        _ambiguity(contract, 'conflicting_theme', 'Se solicitaron temas incompatibles.')

    # Alignment requires a direction and an explicit target role.
    directions = []
    for pattern, value in ((r'\b(?:derecha|right)\b', 'right'),
                           (r'\b(?:izquierda|left)\b', 'left'),
                           (r'\b(?:centrad[oa]?|center(?:ed)?)\b', 'center')):
        if re.search(pattern, semantic_text):
            directions.append(value)
    alignment_words = re.search(r'\b(?:aline|mov|ubic|coloc)\w*\b', semantic_text)
    if alignment_words:
        target = _target(semantic_text, original=original)
        if len(set(directions)) != 1:
            _ambiguity(contract, 'alignment_value',
                       'La alineación no tiene una única dirección explícita.')
        elif not target['role']:
            _ambiguity(contract, 'alignment_target',
                       'La alineación no identifica el tipo de elemento objetivo.')
        else:
            _add_change(contract, 'align', target, 'horizontal_alignment', None,
                        directions[0], ('source_structure', 'render_geometry'))

    # Explicit color edits.  Unknown color prose remains ambiguous, never guessed.
    color_words = r'\b(?:color|fondo|background|foreground|texto|etiqueta)\b'
    color_action = bool(
        re.search(r'\b(?:cambi|pon|aplic|convert|hac|dej|modific)\w*\b[^.\n]{0,60}'
                  + color_words, semantic_text)
        or re.search(color_words + r'[^.\n]{0,50}\b(?:a|por|en)\b', semantic_text))
    if color_action and not themes:
        target = _target(semantic_text, original=original)
        colors = _color_mentions(semantic_text)
        if not target['role']:
            _ambiguity(contract, 'color_target',
                       'El cambio de color no identifica el tipo de elemento objetivo.')
        elif not colors:
            _ambiguity(contract, 'color_value',
                       'El color solicitado no tiene un valor reconocido o hexadecimal.')
        else:
            prop = ('background' if re.search(r'\b(?:fondo|background)\b', semantic_text)
                    else 'foreground')
            before = colors[-2] if len(colors) > 1 else None
            _add_change(contract, 'set_color', target, prop, before, colors[-1],
                        ('source_style', 'rendered_pixels'))

    # Explicit dimensions may target the window or one literally identified control.
    dimensions = re.findall(r'\b(\d{2,5})\s*(?:x|por)\s*(\d{2,5})\b', semantic_text)
    size_words = re.search(r'\b(?:tamano|dimensiones|ancho|alto|ventana|contenido)\b', semantic_text)
    resize_action = bool(re.search(
        r'\b(?:redimension|achic|redu|ajust|ampli|agrand|ensancha)\w*\b', semantic_text))
    fit_content = bool(re.search(
        r'\b(?:ajust|achic|redu|redimension)\w*\b[^.\n]{0,80}\bcontenido\b', semantic_text))
    if resize_action and dimensions:
        if len(set(dimensions)) != 1:
            _ambiguity(contract, 'window_dimensions',
                       'Hay más de un par de dimensiones explícitas.')
        else:
            width, height = dimensions[0]
            target = _target(semantic_text, original=original)
            if not target['role']:
                target = {'role': 'window', 'label': None}
            element_target = target['role'] not in {'window', 'interface'}
            if element_target and not target['label']:
                _ambiguity(contract, 'size_target_label',
                           'El control a redimensionar no tiene una etiqueta literal única.')
            _add_change(contract, 'resize', target,
                        'element_bounds' if element_target else 'window_bounds', None,
                        {'width': int(width), 'height': int(height)},
                        ('source_structure', 'render_geometry'))
    elif fit_content:
        _add_change(contract, 'resize', {'role': 'window', 'label': None},
                    'window_fit', None, 'content_with_margin',
                    ('window_bounds', 'render_geometry'))
    elif resize_action and size_words and re.search(
            r'\b(?:redimension|tamano|dimensiones)\b', semantic_text):
        _ambiguity(contract, 'size_value',
                   'El cambio de tamaño no define dimensiones ni ajuste al contenido.')

    # Relayout records only an explicitly requested axis.
    relayout = requests_relayout(semantic_text) or bool(re.search(
        r'\b(?:reorganiz|redistrib|reacomod|reubic)\w*\b'
        r'|\bdistribu(?:i|y|ir|ya|ye)\w*\b', semantic_text))
    if relayout:
        target = _target(semantic_text, original=original)
        axes = []
        if re.search(r'\b(?:fila|filas|horizontal)\b', semantic_text): axes.append('rows')
        if re.search(r'\b(?:columna|columnas|vertical)\b', semantic_text): axes.append('columns')
        if not target['role']:
            _ambiguity(contract, 'relayout_target',
                       'La redistribución no identifica el conjunto objetivo.')
        elif not axes:
            _ambiguity(contract, 'relayout_axis',
                       'La redistribución no indica filas, columnas u otro eje medible.')
        else:
            _add_change(contract, 'relayout', target, 'layout_axis', None,
                        'grid' if len(set(axes)) == 2 else axes[0],
                        ('source_structure', 'render_geometry', 'accessible_control_bounds'))

    if requests_compaction(semantic_text):
        _add_change(contract, 'set_spacing',
                    _target(semantic_text, 'content', original), 'spacing',
                    None, 'compact', ('source_structure', 'render_geometry'))

    if requests_no_scroll(semantic_text):
        contract['postconditions'].append({
            'property': 'scroll', 'expected': 'none',
            'evidence': ['window_bounds', 'render_geometry'],
        })
    if requests_preserve_sizes(semantic_text) and not any(
            item['property'] == 'dimensions' for item in contract['invariants']):
        contract['invariants'].append({'kind': 'preserve', 'property': 'dimensions'})
    if permits_width_growth(semantic_text):
        contract['postconditions'].append({
            'property': 'width_growth', 'expected': 'allowed',
            'evidence': ['window_bounds'],
        })

    evidence = {'source_diff'}
    for item in contract['changes'] + contract['postconditions']:
        evidence.update(item['evidence'])
    if any(item['property'] == 'behavior' for item in contract['invariants']):
        evidence.update(('build', 'interaction_tests'))
    contract['required_evidence'] = sorted(evidence) if contract['changes'] else []

    if contract['ambiguities']:
        contract['status'] = 'ambiguous'
    elif contract['changes']:
        contract['status'] = 'supported'
    return contract

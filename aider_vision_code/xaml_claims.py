"""Conservative, read-only verification of explicit property claims against parsed XAML.

Only assess deterministic property=value statements associated with an unambiguous
XAML element. Missing declarations are UNKNOWN, not contradictions (styles/bindings).
"""
import re

# Only claims about source-backed declarative attributes, not rendered pixel geometry.
CLAIM_PROPERTIES = (
    'Width', 'Height', 'MinWidth', 'MinHeight', 'MaxWidth', 'MaxHeight',
    'Background', 'Foreground', 'BorderBrush', 'BorderThickness', 'CornerRadius',
    'FontSize', 'FontWeight', 'FontFamily', 'Margin', 'Padding', 'Visibility',
    'Grid.Row', 'Grid.Column', 'Grid.RowSpan', 'Grid.ColumnSpan',
    'HorizontalAlignment', 'VerticalAlignment', 'Orientation', 'Title',
)
PROPERTY_PATTERN = re.compile(
    r'(?<![\w.])(' + '|'.join(re.escape(k) for k in sorted(CLAIM_PROPERTIES, key=len, reverse=True)) +
    r')\s*(?:=|:|(?:es|vale|tiene(?:\s+el\s+valor)?)\s+)\s*'
    r'(?:["\']([^"\'\n]{1,70})["\']|`([^`\n]{1,70})`|([#\w.*,+-]{1,70}))', re.I
)
# Less-technical sentence forms: "ancho de la ventana es 450" etc.
DIM_PATTERN = re.compile(
    r'\b(ancho|alto)\s+(?:de\s+(?:la|el)\s+)?(ventana|window)\s*(?:es|de|:|=)\s*'
    r'(\d+(?:[.,]\d+)?)\b', re.I
)


def _normalize(value):
    value = str(value).strip().strip('.,;:').strip('"\'`').strip()
    if re.fullmatch(r'\d+(?:[.,]\d+)?', value):
        return str(float(value.replace(',', '.')))
    return value.casefold()


def _element_label(item, index):
    return item.get('name') or ('#' + str(index) + ' ' + item['type'])


def _scope(segment, elements):
    """Find an explicitly named or unambiguously typed element in a short clause."""
    names = [(i, e) for i, e in enumerate(elements)
             if e.get('name') and re.search(r'(?<![\w])' + re.escape(e['name']) + r'(?![\w])', segment, re.I)]
    if len(names) == 1:
        return names[0]
    if len(names) > 1:
        return None
    types = [e['type'] for e in elements]
    mentioned = [kind for kind in set(types)
                 if re.search(r'(?<![\w])' + re.escape(kind) + r'(?![\w])', segment, re.I)]
    if len(mentioned) != 1:
        return None
    indices = [i for i, e in enumerate(elements) if e['type'] == mentioned[0]]
    if len(indices) == 1:
        return indices[0], elements[indices[0]]
    return None


def verify_xaml_claims(answer, data, limit=35):
    """Return explicitly scoped facts; do not interpret implied/default/style values.

    This is not a semantic correctness judge. Unscoped or ambiguous prose is left
    unassessed, and no source code is executed or written.
    """
    elements = data.get('elements', [])
    result = {'verified': [], 'contradictory': [], 'unverifiable': [],
              'unassessed_note': 'Se evalúan solo propiedades expresadas explícitamente y vinculadas a un elemento inequívoco.'}
    if not elements:
        return result
    found = set()
    # Split prose into lines and sentences; individual code fragments preserve context per line.
    segments = re.split(r'(?<=[.!?])\s+|\n+', str(answer or ''))
    for segment in segments:
        if len(found) >= limit:
            break
        scope = _scope(segment, elements)
        props = []
        for match in PROPERTY_PATTERN.finditer(segment):
            prop = next((p for p in CLAIM_PROPERTIES if p.casefold() == match.group(1).casefold()), None)
            value = next((v for v in match.groups()[1:] if v is not None), None)
            if prop and value:
                props.append((prop, value))
        for match in DIM_PATTERN.finditer(segment):
            prop = 'Width' if match.group(1).casefold() == 'ancho' else 'Height'
            props.append((prop, match.group(3)))
            if scope is None and len([e for e in elements if e['type'] == 'Window']) == 1:
                scope = next((i, e) for i, e in enumerate(elements) if e['type'] == 'Window')
        for prop, value in props:
            if len(found) >= limit:
                break
            if scope is None:
                continue  # No defensible assignment to a specific element.
            i, element = scope
            label = _element_label(element, i)
            key = (i, prop, _normalize(value))
            if key in found:
                continue
            found.add(key)
            actual = element.get('properties', {}).get(prop)
            entry = {'element': label, 'property': prop, 'claimed': value,
                     'declared': actual, 'element_index': i}
            if actual is None:
                result['unverifiable'].append(entry)
            elif _normalize(actual) == _normalize(value):
                result['verified'].append(entry)
            else:
                # Declared attribute differs, but effective property may be bound/styled.
                result['contradictory'].append(entry)
    return result


def claims_report(result):
    sections = ['CONTROL DE AFIRMACIONES XAML (verificación conservadora):']
    for key, label in (('verified', 'COMPROBADAS'),
                       ('contradictory', 'CONTRADICEN VALORES DECLARADOS'),
                       ('unverifiable', 'NO VERIFICABLES CON ATRIBUTOS DIRECTOS')):
        items = result.get(key, [])
        sections.append(label + ': ' + str(len(items)))
        for entry in items[:20]:
            message = ('- ' + entry['element'] + ': ' + entry['property'] +
                       ' afirmado=' + repr(entry['claimed']))
            if entry['declared'] is not None:
                message += ', declarado=' + repr(entry['declared'])
            sections.append(message)
    if not any(result.get(k) for k in ('verified', 'contradictory', 'unverifiable')):
        sections.append('No se encontraron afirmaciones de propiedad=value con sujeto inequívoco para contrastar.')
    sections.append('AVISO: no verifica capturas, valores efectivos heredados, estilos, recursos, bindings ni geometría renderizada. '
                    'Una propiedad ausente NO prueba que la afirmación sea falsa.')
    return '\n'.join(sections)

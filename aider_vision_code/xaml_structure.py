"""Read-only structural evidence for WPF XAML. Never executes markup or project code."""
from pathlib import Path
import re
import xml.etree.ElementTree as ET

PROPS = ('Width', 'Height', 'MinWidth', 'MinHeight', 'MaxWidth', 'MaxHeight',
         'Margin', 'Padding', 'Background', 'Foreground', 'BorderBrush', 'BorderThickness',
         'CornerRadius', 'FontSize', 'FontFamily',
         'FontWeight', 'HorizontalAlignment', 'VerticalAlignment', 'Grid.Row',
         'Grid.Column', 'Grid.RowSpan', 'Grid.ColumnSpan', 'Orientation',
         'DockPanel.Dock', 'Canvas.Left', 'Canvas.Top', 'Visibility', 'Title',
         'Content', 'Text', 'Header')
MAX_ELEMENTS = 160


def local_name(tag):
    return tag.rsplit('}', 1)[-1] if isinstance(tag, str) else ''


def inspect_xaml(path):
    """Return source-backed facts for a bounded, regular XML file; no external entities."""
    path = Path(path)
    return inspect_xaml_bytes(path.read_bytes(), path.name)


def inspect_xaml_bytes(raw, file_name='<memory>.xaml'):
    """Inspect in-memory XAML with the same limits as a filesystem source."""
    if not isinstance(raw, bytes):
        raise TypeError('El contenido XAML debe entregarse como bytes.')
    if len(raw) > 80_000:
        raise ValueError('El XAML supera el límite de lectura segura (80 KB).')
    if re.search(br'<!\s*(?:DOCTYPE|ENTITY)\b', raw, re.I):
        raise ValueError('XAML con DTD o entidades no admitido en análisis seguro.')
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise ValueError('XAML no válido: ' + str(exc)) from exc
    elements = []
    truncated = False

    def visit(node, parent=None, depth=0):
        nonlocal truncated
        if len(elements) >= MAX_ELEMENTS:
            truncated = True
            return
        kind = local_name(node.tag)
        if not kind:
            return
        attrs = {local_name(key): str(val) for key, val in node.attrib.items()}
        props = {key: attrs[key][:130] for key in PROPS if key in attrs}
        # Literal, direct element text is evidence too (e.g. <Button>Guardar</Button>).
        # Do not concatenate text from descendants or resolve markup extensions.
        if kind in ('Button', 'TextBlock', 'Label', 'MenuItem', 'CheckBox', 'RadioButton'):
            literal_text = (node.text or '').strip()
            if literal_text and 'Text' not in props and 'Content' not in props:
                props['Text' if kind == 'TextBlock' else 'Content'] = literal_text[:130]
        name = attrs.get('Name', '')[:100]
        record = {'type': kind, 'name': name, 'parent': parent,
                  'depth': depth, 'properties': props}
        index = len(elements)
        elements.append(record)
        for child in node:
            visit(child, index, depth+1)
    visit(root)
    return {'file': str(file_name), 'elements': elements, 'truncated': truncated}


def structural_report(data):
    """Deterministic facts, NOT guesses about rendered pixel coordinates."""
    lines = ['EVIDENCIA XAML EXTRAÍDA POR PARSER (fuente de verdad, no generada por IA):',
             'Archivo: ' + data['file'],
             'Elementos XML inspeccionados: ' + str(len(data['elements']))]
    for i, item in enumerate(data['elements']):
        parent = ('raíz' if item['parent'] is None else
                  '#' + str(item['parent']) + ' ' + data['elements'][item['parent']]['type'])
        props = ', '.join(k + '=' + repr(v) for k, v in item['properties'].items())
        lines.append(('#' + str(i) + ' ' + '  '*min(item['depth'], 12) + item['type'] +
                      (' x:Name=' + repr(item['name']) if item['name'] else '') +
                      ' | padre: ' + parent + (' | ' + props if props else '')))
    if data['truncated']:
        lines.append('AVISO: inventario limitado a los primeros 160 elementos.')
    lines.append('LÍMITE: Width/Height son valores declarados, no medidas de pantalla; '
                 'Auto, Star (*), márgenes, DPI, estilos, templates y bindings impiden '
                 'deducir posiciones o tamaños finales sin renderizar la interfaz.')
    return '\n'.join(lines)


def declared_layout_report(data):
    """Grid relationships from XML, without pretending to evaluate WPF layout."""
    elements = data['elements']
    lines = ['Distribución declarada en XAML; no coordenadas en píxeles.']
    for index, grid in enumerate(elements):
        if grid['type'] != 'Grid':
            continue
        tracks = {}
        for kind, definition, prop in (('filas', 'Grid.RowDefinitions', 'Height'),
                                        ('columnas', 'Grid.ColumnDefinitions', 'Width')):
            groups = [i for i, e in enumerate(elements) if e['parent'] == index and e['type'] == definition]
            tracks[kind] = [e['properties'].get(prop, '*') for e in elements if e['parent'] in groups]
        lines.append(f'Grid #{index}: filas={tracks["filas"]!r}, columnas={tracks["columnas"]!r}')
        for number, child in enumerate(elements):
            if child['parent'] != index or '.' in child['type']:
                continue
            props = child['properties']
            row = props.get('Grid.Row', '0 (predeterminada)')
            column = props.get('Grid.Column', '0 (predeterminada)')
            lines.append(f'{child["type"]} {child["name"] or "#"+str(number)}: fila={row}, columna={column}')
    return '\n'.join(lines)


def conflicting_dimensions(answer, data):
    """Warn only for explicit *labelled* numeric dimensions, not speculative geometry."""
    root = data['elements'][0] if data['elements'] else {}
    declared = root.get('properties', {})
    warnings = []
    for prop, terms in (('Width', r'(?:width|ancho)'), ('Height', r'(?:height|alto)')):
        value = declared.get(prop)
        if not value or not re.fullmatch(r'\d+(?:[.,]\d+)?', value):
            continue
        matches = re.findall(r'(?i)\b' + terms + r'\s*(?:es|de|=|:|\s)\s*(\d+(?:[.,]\d+)?)\b', answer)
        actual = float(value.replace(',', '.'))
        if any(float(x.replace(',', '.')) != actual for x in matches):
            warnings.append('La respuesta del modelo contradice ' + prop + '=' + value +
                            ' declarado en el elemento raíz del XAML.')
    return warnings

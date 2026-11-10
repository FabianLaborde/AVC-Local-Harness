"""Integrate source-backed XAML facts, observed images and semantic hypotheses.

No filesystem writes, builds or edits. Analysis deliberately does not infer pixels from
XAML layout nor claim that a language-model difference is already verified.
"""
from io import BytesIO
from pathlib import Path
import re
import unicodedata

from PIL import Image
from visual_evidence import compare_evidence
from xaml_structure import inspect_xaml

SKIP_DIRS = {'.git', '.venv', 'venv', 'bin', 'obj', 'node_modules',
             'avc_publish', '.vs', '__pycache__'}
MAX_REFERENCES = 45


def _normalize(text):
    text = unicodedata.normalize('NFKD', str(text or ''))
    text = ''.join(c for c in text if not unicodedata.combining(c))
    return ' '.join(re.findall(r'\w+', text.casefold()))[:120]


def _xaml_path(project):
    """Locate a single known entry point; never silently choose among ambiguities."""
    path = Path(project).expanduser().resolve()
    if not path.is_dir():
        return None, 'No se encontró la carpeta del proyecto.'
    preferred = path / 'MainWindow.xaml'
    if preferred.is_file() and preferred.resolve().is_relative_to(path):
        return preferred, None
    candidates = []
    for file in path.rglob('*.xaml'):
        try:
            relative = file.relative_to(path)
        except ValueError:
            continue
        if (not file.is_symlink() and file.resolve().is_relative_to(path) and
                not SKIP_DIRS.intersection(part.casefold() for part in relative.parts[:-1])):
            candidates.append(file)
            if len(candidates) > 12:
                break
    if len(candidates) == 1:
        return candidates[0], None
    return None, ('No hay XAML para contrastar.' if not candidates else
                  'Hay varios XAML posibles; no se eligió uno arbitrariamente.')


def _xaml_facts(project):
    if not project:
        return {'estado': 'no_seleccionado', 'mensaje': 'No se seleccionó proyecto; comparación solo visual.'}
    path, error = _xaml_path(project)
    if path is None:
        return {'estado': 'no_disponible', 'mensaje': error}
    try:
        data = inspect_xaml(path)
    except (OSError, ValueError) as exc:
        return {'estado': 'no_disponible', 'mensaje': 'No se puede inspeccionar XAML: ' + str(exc)[:180]}
    labels = []
    all_items = []
    for i, item in enumerate(data['elements']):
        props = item.get('properties', {})
        # Source-backed, never inferred from coordinates or model-proposed labels.
        labels.append({
            'indice': i,
            'tipo': item['type'],
            'nombre': item.get('name') or '',
            'padre': item['parent'],
            'propiedades': props,
        })
        for property_name in ('Content', 'Text', 'Header', 'Title'):
            if props.get(property_name) and not props[property_name].startswith('{'):
                all_items.append((_normalize(props[property_name]), i))
        if item.get('name'):
            all_items.append((_normalize(item['name']), i))
    return {
        'estado': 'inspeccionado', 'archivo': str(path.relative_to(Path(project).expanduser().resolve())),
        'elementos': len(labels), 'truncado': data['truncated'],
        'ventana': data['elements'][0].get('properties', {}) if labels else {},
        'controles': labels[:MAX_REFERENCES],
        '_terms': all_items,
    }


def _assign_sources(differences, xaml):
    """Only explicit, unique label/name matching. No fabricated coordinate mapping."""
    terms = xaml.get('_terms', [])
    results = []
    for item in differences:
        label = _normalize(item.get('elemento'))
        indices = {i for value, i in terms if value and label and label == value}
        row = dict(item)
        if len(indices) == 1:
            idx = next(iter(indices))
            ctrl = xaml['controles'][idx] if idx < len(xaml.get('controles', [])) else None
            row['vinculo_xaml'] = {'estado': 'etiqueta_coincidente', 'indice': idx,
                                  'nombre': (ctrl or {}).get('nombre', ''),
                                  'tipo': (ctrl or {}).get('tipo', '')}
        else:
            row['vinculo_xaml'] = {'estado': 'sin_vinculo_inequivoco'}
        row['nivel_evidencia'] = 'HIPOTESIS_VISUAL'  # even if a name is matched
        results.append(row)
    return results


def _thumbnail(image_data, max_side=700):
    """Preview in memory, no image files persisted on disk."""
    import base64
    with Image.open(BytesIO(image_data)) as image:
        copy = image.convert('RGB')
    copy.thumbnail((max_side, max_side))
    target = BytesIO()
    copy.save(target, format='PNG', optimize=True)
    return 'data:image/png;base64,' + base64.b64encode(target.getvalue()).decode('ascii')


def _proposal(differences, xaml, semantic_valid, geometry_review=None, pixel_observations=None):
    if not semantic_valid:
        return ''
    differences = [row for row in differences if row.get('utilizable_en_propuesta', True)]
    if not differences:
        return ''  # Inconclusive/incomplete inventories do not authorize an equality claim.
    lines = [
        'PROPUESTA PARA REVISIÓN HUMANA — NO EJECUTAR AUTOMÁTICAMENTE',
        'Objetivo: aproximar la interfaz existente a la imagen de referencia.',
        'Todas las diferencias siguientes son hipótesis visuales, no hechos confirmados.',
    ]
    if xaml.get('estado') == 'inspeccionado':
        lines.append('Archivo inspeccionado: ' + xaml['archivo'] + '.')
    if pixel_observations:
        lines.append('Observación de imagen en muestra reducida (no identifica controles):')
        for role in ('actual', 'referencia'):
            profile = pixel_observations.get(role, {})
            if profile.get('estado') == 'medido':
                position = ''
                if all(isinstance(profile.get(k), (int, float)) for k in ('inicio_relativo', 'fin_relativo')):
                    position = (f" Ubicación vertical: {profile['inicio_relativo'] * 100:.1f}%"
                                f" a {profile['fin_relativo'] * 100:.1f}% desde arriba.")
                lines.append(role + ': mayor franja horizontal de bajo contraste = '
                             + f"{profile['altura_relativa'] * 100:.1f}% de la altura." + position)
    if geometry_review:
        lines.append('Revisión global de geometría (interpretación de IA, sin medición calibrada):')
        for axis, item in geometry_review.items():
            lines.append(axis + ': ' + item['estado'] + ' — ' + item['evidencia'])
        lines.append('Revisar distribución y proporciones antes de detalles de apariencia.')
    for i, row in enumerate(differences[:12], 1):
        piece = (str(row.get('elemento', 'elemento'))[:65] + ' — ' +
                 str(row.get('tipo', 'visual'))[:35] + ': ' +
                 str(row.get('actual', ''))[:125] + ' → ' +
                 str(row.get('referencia', ''))[:125])
        lines.append(f'{i}. {piece}')
    lines += [
        'Antes de cambiar código, contrastá las hipótesis con XAML, estilos, bindings y captura real.',
        'No inventes medidas absolutas a partir del XAML o de píxeles sin calibración.',
        'Trabajá sobre una copia o checkpoint, conservá funciones y compilación, y verificá el resultado visual.',
        'Solicitá autorización explícita antes de modificar el proyecto.',
    ]
    return '\n'.join(lines)


def build_visual_cycle(project, current_bytes, reference_bytes, report, capture_source):
    """Compose evidence for a single end-to-end, read-only visual inspection."""
    measurements = compare_evidence(reference_bytes, current_bytes)
    current_size = measurements['resultado']['tamano_px']
    reference_size = measurements['referencia']['tamano_px']
    current_ratio = current_size[0] / current_size[1]
    reference_ratio = reference_size[0] / reference_size[1]
    ratio_difference = abs(current_ratio / reference_ratio - 1)
    xaml = _xaml_facts(project)
    differences = _assign_sources(report.get('diferencias') or [], xaml)
    xaml.pop('_terms', None)
    valid = bool(report.get('comparacion_valida'))
    warnings = [
        'La comparación semántica produce hipótesis; no verifica posiciones finales ni funcionalidad.',
        'La ventana de la aplicación debe estar capturada sin superposiciones ni recortes.',
        'El modo proyecto inicia el ejecutable publicado: la aplicación puede escribir sus propios datos al abrirse.',
    ]
    if ratio_difference > 0.08:
        warnings.append('La relación de aspecto difiere más del 8%; no se puede asumir alineación píxel a píxel.')
    if xaml.get('estado') != 'inspeccionado':
        warnings.append('No se pudo contrastar con estructura XAML: ' + xaml['mensaje'])
    elif xaml.get('truncado'):
        warnings.append('La inspección XAML alcanzó su límite; el inventario es parcial.')
    if not valid:
        warnings.append('La comparación de Ollama no fue válida: no se genera propuesta de cambio.')
    elif not differences:
        warnings.append('Sin diferencias devueltas no significa igualdad; comprobá inventarios y capturas.')
    if report.get('estado') == 'COMPARACION_PARCIAL':
        warnings.append('La revisión contiene aspectos no verificables; no se presume que coincidan.')
    if report.get('revision_controles', {}).get('estado') == 'no_verificable':
        warnings.append('No se completó la comparación de apariencia de controles; la propuesta es parcial.')
    blocked_shapes = sum(not row.get('utilizable_en_propuesta', True) for row in differences)
    if blocked_shapes:
        warnings.append(f'{blocked_shapes} diferencias sin evidencia suficiente se excluyeron de la propuesta.')
    proposal = _proposal(differences, xaml, valid, report.get('revision_geometrica'), report.get('observaciones_pixeles'))
    return {
        'version': 'integracion-visual-1', 'modo': 'SOLO_LECTURA',
        'etapas': [
            {'nombre': 'Origen y captura', 'estado': 'capturado', 'detalle': capture_source},
            {'nombre': 'Evidencia estructural', 'estado': xaml.get('estado'),
             'detalle': xaml.get('archivo') or xaml.get('mensaje', '')},
            {'nombre': 'Comparación semántica',
             'estado': ('parcial' if report.get('estado') == 'COMPARACION_PARCIAL' else 'completa') if valid else 'inconclusa',
             'detalle': f'{len(differences)} diferencias hipotéticas'},
            {'nombre': 'Preparación de correcciones',
             'estado': 'requiere_aprobacion' if proposal else 'sin_propuesta',
             'detalle': 'Sin escritura en el proyecto'},
        ],
        'xaml': xaml, 'diferencias_vinculadas': differences,
        'mediciones': {'referencia_px': reference_size, 'actual_px': current_size,
                       'relacion_aspecto_divergente': ratio_difference > 0.08,
                       'muestras_color': {
                           'actual': measurements['resultado']['general']['dominantes'],
                           'referencia': measurements['referencia']['general']['dominantes'],
                       }},
        'capturas': {'actual': _thumbnail(current_bytes),
                     'referencia': _thumbnail(reference_bytes)},
        'revision_geometrica': report.get('revision_geometrica') or {},
        'observaciones_pixeles': report.get('observaciones_pixeles') or {},
        'propuesta': proposal,
        'advertencias': warnings,
        'requiere_aprobacion': True,
        'ediciones_por_avc': False,
    }

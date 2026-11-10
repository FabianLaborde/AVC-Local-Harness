"""Small, deterministic image measurements to complement (not replace) a vision model.

No hard-coded calculator labels; reusable for application screenshots. Coordinates
are normalized. Scores are deliberately NOT assigned from these measurements.
"""
from collections import Counter
from io import BytesIO
from PIL import Image, ImageStat


def _classify(r, g, b):
    hi, lo = max(r, g, b), min(r, g, b)
    if hi - lo < 19:
        if hi > 235: return 'blanco'
        if hi > 185: return 'gris claro'
        if hi > 90: return 'gris medio'
        if hi > 42: return 'gris oscuro'
        return 'negro'
    if g > r * 1.27 and g > b * 1.20: return 'verde'
    if r > g * 1.36 and r > b * 1.28: return 'rojo'
    if b > r * 1.23 and b > g * 1.15: return 'azul'
    if r > b * 1.32 and g > b * 1.24: return 'amarillo/naranja'
    return 'otro color'


def _summary(img, box):
    crop = img.crop(box)
    crop.thumbnail((130, 130))
    values = list(crop.getdata())
    if not values:
        return {'dominantes': [], 'brillo': 0, 'variacion': 0}
    categories = Counter(_classify(*p) for p in values)
    stat = ImageStat.Stat(crop)
    dominant = [f'{k} {round(100*v/len(values))}%' for k, v in categories.most_common(4)]
    return {'dominantes': dominant,
            'brillo': round(sum(stat.mean)/3),
            'variacion': round(sum(stat.stddev)/3)}


def image_evidence(image_bytes):
    """Sample whole image and normalized regions, returning compact JSON-able evidence."""
    with Image.open(BytesIO(image_bytes)) as raw:
        img = raw.convert('RGB')
    w, h = img.size
    # Samples avoid relying on exact device pixels; regions are evidence, not elements.
    zones = {
        'superior': (0, 0, w, round(h*.30)),
        'central': (0, round(h*.30), w, round(h*.63)),
        'inferior': (0, round(h*.63), w, h),
    }
    report = {'tamano_px': [w, h], 'relacion_ancho_alto': round(w / h, 3),
              'general': _summary(img, (0, 0, w, h)),
              'regiones': {name: _summary(img, rect) for name, rect in zones.items()}}
    # Per-cell non-central samples: they usually reveal a button fill rather than a glyph.
    # This does NOT assume that every app contains a keypad.
    cells = []
    for row in range(5):
        line = []
        for col in range(4):
            # Uniform grid in lower 60%; merely surface color cues for the vision model.
            cx = (col+.5) / 4
            cy = .40 + (row+.5) * .60/5
            x, y = int(cx*w), int(cy*h)
            rad = max(2, int(min(w/4, h*.60/5)*.10))
            patch = img.crop((max(0,x-rad), max(0,y-rad),min(w,x+rad),min(h,y+rad)))
            pixel = patch.getpixel((max(0, int(patch.width*.75)-1), max(0, int(patch.height*.25)-1)))
            line.append(_classify(*pixel))
        cells.append(line)
    report['muestra_cuadricula_4x5_orientativa'] = cells
    report['advertencia'] = 'Muestreo orientativo, no identifica controles ni da una puntuación de similitud.'
    return report


def compare_evidence(reference_bytes, result_bytes):
    return {'referencia': image_evidence(reference_bytes),
            'resultado': image_evidence(result_bytes)}

"""Bounded local source discovery. Evidence, not a compiler or execution tool."""
import os
import re
from pathlib import Path

EXCLUDED = {'.git', '.venv', 'venv', 'node_modules', 'bin', 'obj', '__pycache__',
            'logs', 'dist', 'build', 'artifacts', 'avc_publish', 'recovery'}
SOURCE = {'.cs', '.xaml', '.py', '.js', '.jsx', '.ts', '.tsx', '.vb', '.fs', '.css'}
SECRET = re.compile(r'(?i)(^\.|secret|credential|password|passwd|token|private|\.env|id_rsa|key\.|lock$)')
INLINE_SECRET = re.compile(r'(?i)(?:password|passwd|api[_-]?key|secret|access[_-]?token|private[_-]?key|connectionstring)\s*[:=]')


def source_evidence(project, targets, max_files=3, max_chars=22000, max_depth=2):
    root = Path(project).resolve()
    notes, edges, sources, unresolved = [], [], {}, set()

    def safe(relative):
        path = root / relative
        if path.is_absolute() and not path.is_relative_to(root):
            return None
        if any(part in {'.', '..'} or part.lower() in EXCLUDED or part.startswith('.')
               for part in Path(relative).parts):
            return None
        if SECRET.search(path.name):
            return None
        try:
            if not path.resolve().is_relative_to(root):
                return None
            current = path
            while current != root:
                if current.is_symlink() or (hasattr(current, 'is_junction') and current.is_junction()):
                    return None
                current = current.parent
            if path.is_file() and path.stat().st_size <= 80000:
                return path
        except OSError:
            pass
        return None

    index = {}
    count = 0
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d.lower() not in EXCLUDED and not d.startswith('.')
                         and not (Path(directory)/d).is_symlink()
                         and not (hasattr(Path(directory)/d, 'is_junction') and (Path(directory)/d).is_junction()))
        for name in sorted(files):
            count += 1
            if count > 10000:
                break
            path = Path(directory)/name
            if path.suffix.lower() in SOURCE and not SECRET.search(name):
                rel = path.relative_to(root).as_posix()
                index.setdefault(path.stem, []).append(rel)
        if count > 10000:
            notes.append('Inventario limitado a 10000 archivos; puede haber dependencias sin localizar.')
            break

    queue = [(target, 0) for target in targets]
    while queue and len(sources) < max_files:
        name, depth = queue.pop(0)
        if name in sources:
            continue
        path = safe(name)
        if path is None:
            notes.append('No se pudo leer de forma segura: ' + name)
            continue
        try:
            text = path.read_text(encoding='utf-8-sig', errors='replace')
        except OSError:
            notes.append('Lectura no disponible: ' + name)
            continue
        sources[name] = text
        # Identifiers and module names can identify local companion files across
        # frameworks. A match is only a candidate, never proof of symbol binding.
        symbols = set(re.findall(r'\b[A-Za-z_]\w*\b', text))
        unresolved.update(set(re.findall(r'\b([A-Z]\w*)\s*\.', text)) - index.keys())
        for symbol in sorted(symbols & index.keys(), key=lambda s: (-len(re.findall(r'\b' + re.escape(s) + r'\s*\.', text)), s)):
            matches = [p for p in index[symbol] if p != name]
            if not matches:
                continue
            if len(matches) != 1:
                notes.append('Referencia ambigua ' + symbol + ': ' + ', '.join(matches[:5]))
                continue
            target = matches[0]
            edges.append(name + ' → ' + symbol + ' → ' + target)
            if target not in sources:
                if depth < max_depth:
                    queue.append((target, depth + 1))
                else:
                    notes.append('Límite de profundidad; no inspeccionado: ' + target)
    if queue:
        notes.append('Límite de archivos; no todas las referencias se inspeccionaron.')

    if any(target not in sources for target in targets):
        raise ValueError('No se pudo leer el archivo solicitado de forma segura.')
    header = ('REFERENCIAS LOCALES CANDIDATAS (coincidencia de identificadores y nombres, no resolución de compilador):\n'
              + '\n'.join(dict.fromkeys(edges))[:2500]
              + '\nLÍMITES: No se ejecutó código. No se resuelven bibliotecas externas, alias ni todas las importaciones. '
                'No deducir propiedades ni comportamiento sin la definición correspondiente.\n')
    sections = [header]
    allowance = max(0, (max_chars - len(header) - 2500) // max(1, len(sources)) - 250)
    for name, text in sources.items():
        lines = [f'{i}: ' + ('[línea omitida: posible secreto]' if INLINE_SECRET.search(line) else line[:360])
                 for i, line in enumerate(text.splitlines(), 1)]
        body = '\n'.join(lines)
        if len(body) > allowance:
            body = body[:allowance].rsplit('\n', 1)[0]
            notes.append('Fragmento parcial, no se leyó completo en el contexto: ' + name)
        sections.append('\n--- CONTENIDO: ' + name + ' ---\n' + body)
    if unresolved:
        notes.append('Símbolos sin archivo local identificado (pueden ser externos): ' + ', '.join(sorted(unresolved)[:30]))
    limitations = '\n'.join(dict.fromkeys(notes))[:2400]
    sections.append('\nLIMITACIONES DE RECUPERACIÓN:\n' + (limitations or 'Sin incidencias adicionales detectadas.'))
    return '\n'.join(sections), {'files': list(sources), 'references': edges,
                                 'limitations': list(dict.fromkeys(notes))}

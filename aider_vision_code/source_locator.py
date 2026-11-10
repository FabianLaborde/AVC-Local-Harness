"""Bounded lexical source locator. It returns evidence, never semantic certainty."""
import os
import re
import unicodedata
from collections import Counter
from pathlib import Path

from project_validation import select_adapter


EXCLUDED = {'.git', '.venv', 'venv', 'node_modules', 'bin', 'obj', '__pycache__',
            'logs', 'dist', 'build', 'artifacts', 'avc_publish', 'recovery', '.vs',
            'output', 'outputs', 'publish', 'deployment'}
EXTENSIONS = {'.cs', '.xaml', '.vb', '.fs', '.py', '.html', '.htm', '.css',
              '.js', '.mjs', '.cjs'}
SECRET = re.compile(r'(?i)(^\.|secret|credential|password|passwd|token|private|\.env|id_rsa|key\.|lock$)')
STRING_LITERAL = re.compile(
    r'"((?:\\.|[^"\\]){1,200})"|\'((?:\\.|[^\'\\]){1,200})\'',
    re.DOTALL)
STOPWORDS = {
    'the', 'and', 'for', 'with', 'from', 'this', 'that', 'please',
    'que', 'como', 'con', 'del', 'desde', 'este', 'esta', 'estos', 'estas',
    'para', 'pero', 'por', 'sin', 'sobre', 'una', 'uno', 'unos', 'unas',
    'el', 'la', 'los', 'las', 'al', 'demas',
    'quiero', 'necesito', 'puedes', 'podes', 'favor', 'solo', 'todo', 'todos',
    'cambia', 'cambiar', 'cambie', 'modifica', 'modificar', 'agrega', 'agregar',
    'elimina', 'eliminar', 'conserva', 'conservar', 'mueve', 'mover',
    'color', 'fondo', 'texto', 'control', 'boton', 'campo', 'formulario',
}


class SourceLocationError(ValueError):
    def __init__(self, message, evidence):
        super().__init__(message)
        self.evidence = evidence


def normalized_tokens(value):
    value = re.sub(r'(?<=[a-z0-9])(?=[A-Z])', ' ', str(value))
    value = unicodedata.normalize('NFKD', value)
    value = ''.join(c for c in value if not unicodedata.combining(c)).casefold()
    return [token for token in re.findall(r'[a-z0-9]+', value)
            if len(token) >= 3 and token not in STOPWORDS]


def literal_phrases(text):
    """Extract bounded quoted strings as normalized token sequences."""
    phrases = set()
    for match in STRING_LITERAL.finditer(text):
        value = match.group(1) if match.group(1) is not None else match.group(2)
        tokens = tuple(normalized_tokens(value))
        if tokens:
            phrases.add(tokens)
    return phrases


def _contains_sequence(sequence, phrase):
    length = len(phrase)
    return bool(length and any(tuple(sequence[index:index + length]) == phrase
                               for index in range(len(sequence) - length + 1)))


def _inventory(root):
    root = root.resolve()
    candidates = []
    visited = 0
    truncated = False
    for directory, dirs, files in os.walk(root, followlinks=False):
        parent = Path(directory)
        dirs[:] = sorted(name for name in dirs
                         if name.casefold() not in EXCLUDED and not name.startswith('.')
                         and not (parent / name).is_symlink()
                         and not (hasattr(parent / name, 'is_junction') and (parent / name).is_junction()))
        for name in sorted(files):
            visited += 1
            if visited > 10000:
                truncated = True
                break
            path = parent / name
            if (path.suffix.lower() not in EXTENSIONS or SECRET.search(name)
                    or path.is_symlink()
                    or (hasattr(path, 'is_junction') and path.is_junction())):
                continue
            try:
                if path.stat().st_size > 80000 or not path.resolve().is_relative_to(root):
                    continue
                relative = path.relative_to(root).as_posix()
                select_adapter(root, relative)
                text = path.read_text(encoding='utf-8-sig')
            except (OSError, UnicodeError, ValueError):
                continue
            candidates.append(dict(path=relative, path_tokens=set(normalized_tokens(relative)),
                                   content_tokens=set(normalized_tokens(text)),
                                   literal_phrases=literal_phrases(text), text=text))
        if truncated:
            break
    return candidates, visited, truncated


def locate_source(root, instruction):
    root = Path(root).resolve()
    query_sequence = normalized_tokens(instruction)
    query = set(query_sequence)
    phrases = [phrase.strip().casefold() for phrase in
               re.findall(r'[\"\']([^\"\']{2,80})[\"\']', str(instruction))]
    candidates, visited, truncated = _inventory(root)
    evidence = dict(schema_version=1, method='bounded_lexical', query_tokens=sorted(query),
                    quoted_phrases=phrases, inventory_files=visited,
                    inventory_truncated=truncated, candidates=[])
    if not query and not phrases:
        raise SourceLocationError('El pedido no contiene evidencia para localizar un archivo.', evidence)

    document_frequency = Counter(token for item in candidates for token in item['content_tokens'] & query)
    ranked = []
    for item in candidates:
        path_matches = sorted(item['path_tokens'] & query)
        content_matches = sorted(item['content_tokens'] & query)
        matched_literal_phrases = [phrase for phrase in item['literal_phrases']
                                   if _contains_sequence(query_sequence, phrase)]
        literal_matches = sorted({token for phrase in matched_literal_phrases
                                  for token in phrase})
        rare_matches = [token for token in content_matches if document_frequency[token] == 1]
        phrase_matches = sorted(phrase for phrase in phrases if phrase in item['text'].casefold())
        score = len(path_matches) * 10 + len(rare_matches) * 6
        score += sum(2 for token in content_matches if token not in rare_matches)
        # Exact string literals are much closer to visible UI identity than prose-like
        # words anywhere in a source.  Strength still requires two matches (or one of
        # the pre-existing independent signals), so one common label cannot decide.
        score += len(literal_matches) * 24
        score += len(phrase_matches) * 18
        if any(part.casefold() in {'test', 'tests', 'pruebas', 'preview', 'previews'}
               for part in Path(item['path']).parts):
            score = max(0, score - 8)
        if score:
            reasons = []
            if path_matches:
                reasons.append('ruta: ' + ', '.join(path_matches))
            if rare_matches:
                reasons.append('contenido unico: ' + ', '.join(rare_matches))
            common = [token for token in content_matches if token not in rare_matches]
            if common:
                reasons.append('contenido: ' + ', '.join(common))
            if phrase_matches:
                reasons.append('literal citado: ' + ', '.join(phrase_matches))
            if literal_matches:
                reasons.append('literales de interfaz: ' + ', '.join(literal_matches))
            ranked.append(dict(path=item['path'], score=score, reasons=reasons,
                               strong=bool(path_matches or rare_matches or phrase_matches
                                           or len(literal_matches) >= 2)))
    ranked.sort(key=lambda item: (-item['score'], item['path'].casefold()))
    evidence['candidates'] = ranked[:5]
    if not ranked:
        raise SourceLocationError('No se encontro un archivo relacionado de forma verificable.', evidence)
    winner = ranked[0]
    runner_score = ranked[1]['score'] if len(ranked) > 1 else 0
    margin = winner['score'] - runner_score
    required_margin = max(4, int(winner['score'] * .25))
    if not winner['strong'] or winner['score'] < 6 or margin < required_margin:
        names = ', '.join(item['path'] for item in ranked[:5])
        raise SourceLocationError(
            'No hay un archivo objetivo inequivoco. Candidatos: ' + names
            + '. Agrega el nombre, una ruta o un texto literal del control.', evidence)
    evidence.update(selected=winner['path'], selected_score=winner['score'], margin=margin,
                    required_margin=required_margin)
    return winner['path'], evidence

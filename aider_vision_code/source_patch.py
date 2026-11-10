"""Framework-neutral exact source replacements.

The engine treats source text as opaque data.  It does not parse a language, access
the filesystem, run code, or choose targets.  Callers must provide an explicit bounded
source map and exact replacement anchors.
"""


class PatchApplicationError(ValueError):
    """Report rejected edits while retaining an immutable partial candidate."""
    def __init__(self, issues, partial):
        super().__init__('; '.join(issues))
        self.issues = list(issues)
        self.partial = dict(partial)


def apply_patches(sources, edits):
    """Apply bounded exact replacements to a copy of an in-memory source map."""
    if not isinstance(edits, list) or not 1 <= len(edits) <= 12:
        raise ValueError('Edición vacía o demasiado extensa')
    result = dict(sources)
    issues = []
    for index, edit in enumerate(edits, 1):
        if (not isinstance(edit, dict) or
                not {'file', 'search', 'replace'} <= set(edit) <=
                    {'file', 'search', 'replace', 'replace_all'} or
                type(edit.get('replace_all', False)) is not bool):
            issues.append(f'Parche {index}: formato inválido')
            continue
        name = edit['file']
        search = edit['search']
        replacement = edit['replace']
        if name not in sources:
            issues.append(f'Parche {index}: archivo fuera del alcance: {name}')
            continue
        if not isinstance(search, str) or not search or not isinstance(replacement, str):
            issues.append(f'Parche {index} ({name}): texto inválido')
            continue
        count = result[name].count(search)
        replace_all = edit.get('replace_all', False)
        if count == 0 or (count != 1 and not replace_all):
            prefix = 'el fragmento no existe; ' if count == 0 else ''
            issues.append(f'Parche {index} ({name}): {prefix}search aparece {count} veces; '
                          'incluí contexto del contenedor hasta que sea único')
            continue
        if search == replacement:
            issues.append(f'Parche {index} ({name}): no produjo cambios; '
                          'replace es idéntico a search')
            continue
        result[name] = result[name].replace(
            search, replacement, -1 if replace_all else 1)
    if issues:
        raise PatchApplicationError(issues, result)
    if result == sources:
        raise ValueError('El editor no produjo cambios')
    return result

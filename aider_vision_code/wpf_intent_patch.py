"""WPF adapter for minimal deterministic XAML edits backed by intent evidence.

This module never parses instructions and never guesses effective WPF values.  It
only turns one already-resolved direct attribute mismatch into an exact textual
replacement while preserving the rest of the source byte-for-byte after decoding.
"""
import html
import re
import unicodedata


_ALIGNMENTS = {'left': 'Left', 'right': 'Right', 'center': 'Center',
               'stretch': 'Stretch'}
_LABEL_ATTRIBUTES = {'Name', 'Text', 'Content', 'Header', 'Title'}
_ATTRIBUTE = re.compile(
    r'(?P<name>[A-Za-z_][\w:.-]*)\s*=\s*(?P<quote>["\'])(?P<value>.*?)(?P=quote)',
    re.DOTALL)
_TAG_NAME = re.compile(r'<\s*(?P<name>[A-Za-z_][\w:.-]*)')


def _plain(value):
    return ''.join(character for character in unicodedata.normalize(
        'NFD', str(value or '').casefold()) if not unicodedata.combining(character)).strip()


def _tag_end(text, start):
    quote = None
    for index in range(start + 1, len(text)):
        character = text[index]
        if quote:
            if character == quote:
                quote = None
        elif character in ('"', "'"):
            quote = character
        elif character == '>':
            return index + 1
    return None


def _start_tags(text):
    """Yield regular XML start tags without treating quoted ``>`` as a boundary."""
    offset = 0
    while True:
        start = text.find('<', offset)
        if start < 0:
            return
        if text.startswith('<!--', start):
            end = text.find('-->', start + 4)
            if end < 0:
                return
            offset = end + 3
            continue
        if text.startswith('<![CDATA[', start):
            end = text.find(']]>', start + 9)
            if end < 0:
                return
            offset = end + 3
            continue
        end = _tag_end(text, start)
        if end is None:
            return
        offset = end
        prefix = text[start + 1:start + 2]
        if prefix and prefix not in '/!?':
            yield start, end, text[start:end]


def _attributes(tag):
    return [dict(name=match.group('name').rsplit(':', 1)[-1],
                 value=html.unescape(match.group('value')),
                 value_span=match.span('value'))
            for match in _ATTRIBUTE.finditer(tag)]


def _matching_tag(source, kind, label, source_property, declared):
    matches = []
    expected_label = _plain(label)
    for start, end, tag in _start_tags(source):
        name = _TAG_NAME.match(tag)
        if not name or name.group('name').rsplit(':', 1)[-1] != kind:
            continue
        attributes = _attributes(tag)
        labels = {_plain(item['value']) for item in attributes
                  if item['name'] in _LABEL_ATTRIBUTES}
        direct = [item for item in attributes if item['name'] == source_property]
        if expected_label in labels and len(direct) == 1 and direct[0]['value'] == declared:
            matches.append((start, end, tag, direct[0]['value_span']))
    if len(matches) != 1:
        return None
    return matches[0]


def deterministic_required_patch(payload, sources):
    """Return one exact alignment edit, or ``None`` when any proof is incomplete."""
    if not isinstance(payload, dict) or payload.get('error') or payload.get('source_errors'):
        return None
    contract = payload.get('contract') or {}
    resolution = payload.get('resolution') or {}
    changes = contract.get('changes') or []
    results = resolution.get('results') or []
    if (contract.get('status') != 'supported' or contract.get('ambiguities') or
            contract.get('postconditions') or resolution.get('status') != 'requires_change' or
            len(changes) != 1 or len(results) != 1):
        return None
    change, result = changes[0], results[0]
    target = change.get('target') or {}
    matches = result.get('matches') or []
    if (change.get('property') != 'horizontal_alignment' or not target.get('label') or
            result.get('status') != 'requires_change' or
            result.get('code') != 'direct_value_differs' or len(matches) != 1 or
            result.get('source_property') != 'HorizontalAlignment'):
        return None
    expected = _ALIGNMENTS.get(result.get('expected'))
    declared = result.get('declared')
    match = matches[0]
    file_name = match.get('file')
    source = sources.get(file_name)
    if not expected or not isinstance(declared, str) or not isinstance(source, str):
        return None
    located = _matching_tag(source, match.get('type'), target['label'],
                            result['source_property'], declared)
    if not located:
        return None
    _, _, tag, value_span = located
    replacement = tag[:value_span[0]] + expected + tag[value_span[1]:]
    if replacement == tag:
        return None
    return {
        'decision_source': 'intent_evidence_patch',
        'reason': ('Un objetivo literal único declara una alineación directa distinta; '
                   'se preparó un reemplazo mínimo verificable.'),
        'edits': [{'file': file_name, 'search': tag, 'replace': replacement,
                   'replace_all': False}],
        'facts': [{'file': file_name, 'type': match.get('type'),
                   'label': target['label'], 'property': 'horizontal_alignment',
                   'before': result.get('actual'), 'after': result.get('expected')}],
    }

"""Bounded, user-supplied conversation facts; no inferred assistant decisions."""
import hashlib
import re
from pathlib import Path

TECH = re.compile(r'(?i)(?<!\w)(C\#|C\+\+|Python|JavaScript|TypeScript|Java|WPF|Windows Forms|WinForms|Flutter|PySide6?|PyQt[56]?|Tkinter|React|Electron|Avalonia)(?!\w)')
REFERENCE = re.compile(r'(?i)\b(imagen|referencia|maqueta|diseño|modelo adjunto)\b')
CONTINUE = re.compile(r'(?i)^\s*(?:ok[, ]*)?(?:creala|créala|crealo|créalo|hacelo|hazlo|adelante|continu[aá]|sigue|sigamos)\b')
FORGET = re.compile(r'(?i)(?:sin (?:imagen|referencia)|(?:olvid[aá]|ignor[aá]|descart[aá])\w* (?:la )?(?:imagen|referencia))')
VISUAL_ONLY = re.compile(r'(?i)(?:sin (?:ninguna )?l[oó]gica|no necesito.*l[oó]gica|solo (?:la )?parte visual|s[oó]lo (?:la )?parte visual)')
LOGIC_CHANGE = re.compile(r'(?i)\b(?:agreg|implement|conect|incorpor)\w*\b[^.;!\n]*\b(?:l[oó]gica|funcionalidad|comportamiento)\b')


def visual_only_intent(instruction):
    """A negated request cannot authorize business logic; unknown leaves scope alone."""
    intent = None
    for clause in re.split(r'(?i)[.;!\n]|\bpero\b', instruction):
        if VISUAL_ONLY.search(clause):
            intent = True
            continue
        change = LOGIC_CHANGE.search(clause)
        if change:
            prefix = clause[:change.start()]
            intent = bool(re.search(r'(?i)\b(?:no|sin|ni)\b', prefix))
    return intent


def chosen_technology(instruction):
    values = []
    for match in TECH.finditer(instruction):
        prefix = instruction[max(0, match.start()-45):match.start()]
        if re.search(r'(?i)\b(?:no|sin|evit[aá]|descart[aá])\s+(?:(?:uses|usar|utilices|utilizar|quiero|usar el framework)\s+)?$', prefix):
            continue
        values.append(match.group())
    return values[-1] if values else None


def persistent_reference_intent(instruction):
    """A project-level saved image is opt-in; a new chat must not activate it silently."""
    return bool(isinstance(instruction,str) and REFERENCE.search(instruction) and not FORGET.search(instruction))


class ConversationContext:
    def __init__(self):
        self.clear()

    def clear(self):
        self.scope = None
        self.facts = {}

    def resolve(self, chat, project, instruction, image=None):
        scope = (chat, str(Path(project).resolve()).casefold())
        facts = dict(self.facts) if scope == self.scope else {}
        technology = chosen_technology(instruction)
        if technology:
            # Last explicit technology wins; never adopt a model recommendation.
            facts['technology'] = technology
        if FORGET.search(instruction):
            facts.pop('image', None)
        if image:
            facts['image'] = image
        visual_only = visual_only_intent(instruction)
        if visual_only is not None:
            facts['visual_only'] = visual_only
        use_reference = bool(image or (not FORGET.search(instruction) and
                             (REFERENCE.search(instruction) or CONTINUE.search(instruction))))
        selected = facts.get('image') if use_reference else None
        additions = []
        if facts.get('technology'):
            additions.append('Tecnología elegida por el usuario: ' + facts['technology'] + '. No sustituirla por otra.')
        if facts.get('visual_only'):
            additions.append('Alcance vigente: crear o modificar solo la parte visual; no agregar lógica de negocio.')
        if selected:
            additions.append('La imagen suministrada es la referencia vigente para este pedido.')
        effective = instruction + ('\n\nCONTEXTO VIGENTE DEL USUARIO:\n' + '\n'.join(additions) if additions else '')
        metadata = dict(technology=facts.get('technology'), visual_only=bool(facts.get('visual_only')),
                        reference_reused=bool(selected and not image),
                        reference_sha256=hashlib.sha256(selected.encode()).hexdigest() if selected else None)
        return dict(scope=scope, facts=facts, instruction=effective, image=selected, metadata=metadata)

    def commit(self, resolved):
        self.scope, self.facts = resolved['scope'], resolved['facts']

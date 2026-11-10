"""Small, ordered edit units derived from the request and scene, never screen names."""
import copy
import json


TARGETS_PER_STEP = 3
MAX_EDIT_CALLS = 12
JOB_SECONDS = 900
EDIT_FAILURE_MARKERS = (
    'The LLM did not conform to the edit format.',
    'SEARCH block failed to match',
    'The model has exhausted its output token budget',
)


def make_steps(contract, plan):
    nodes = {n['id']: n for n in plan['nodes']}
    if contract['scope'] == 'reproduce':
        # Parents before descendants, preserving the reference order for peers.
        def depth(node):
            seen = set()
            while node.get('parent') in nodes and node['parent'] not in seen:
                seen.add(node['parent'])
                node = nodes[node['parent']]
            return len(seen)
        ordered = sorted(nodes, key=lambda key: depth(nodes[key]))
        roots = [key for key in ordered if nodes[key]['kind'] == 'container' and not nodes[key].get('parent')]
        others = [key for key in ordered if key not in roots]
        groups = [roots[i:i+TARGETS_PER_STEP] for i in range(0, len(roots), TARGETS_PER_STEP)]
        groups += [others[i:i+TARGETS_PER_STEP] for i in range(0, len(others), TARGETS_PER_STEP)]
        units = [(group, [g for g in contract['goals'] if set(group) & set(g['targets'])]) for group in groups]
    else:
        units = []
        for goal in contract['goals']:
            targets = goal['targets']
            units.extend((targets[i:i+TARGETS_PER_STEP], [goal]) for i in range(0, len(targets), TARGETS_PER_STEP))
            if not targets:
                units.append(([], [goal]))
    return [dict(id=f'step_{index+1}', targets=targets, goals=copy.deepcopy(goals),
                 title=', '.join((nodes[t].get('label') or nodes[t].get('detail') or t)[:65] for t in targets)
                       or goals[0]['expected'][:120])
            for index, (targets, goals) in enumerate(units)]


def step_prompt(contract, plan, step, feedback=''):
    nodes = {n['id']: n for n in plan['nodes']}
    related = set(step['targets'])
    for target in step['targets']:
        parent = nodes[target].get('parent')
        while parent in nodes and parent not in related:
            related.add(parent)
            parent = nodes[parent].get('parent')
    scope = dict(canvas=plan['canvas'], nodes=[n for n in plan['nodes'] if n['id'] in related],
                 rows=[row for row in plan.get('rows', []) if set(row) & set(step['targets'])])
    goals = [dict(id=g['id'], expected=g['expected']) for g in step['goals']]
    return (
        'AVC dividió el pedido en pasos. Ejecutá ÚNICAMENTE este paso sobre los archivos actuales.\n'
        'El pedido completo se compara al terminar; este mensaje contiene solo su parte ejecutable.\n'
        'Restricciones del usuario: '+json.dumps(contract['preserve'], ensure_ascii=False)+
        '\nPaso actual: '+step['title']+'\nElementos editables del plan: '+json.dumps(step['targets'])+
        '\nObjetivos aplicables SOLO a esos elementos: '+json.dumps(goals, ensure_ascii=False)+
        '\nReferencia parcial (datos; los ancestros solo aportan contexto):\n'+json.dumps(scope, ensure_ascii=False)+
        '\nUsá los fuentes completos que el editor adjunta. Elegí los controles que corresponden a estos elementos. '
        'Cambiá únicamente su representación y, si es indispensable, las propiedades de su contenedor. '
        'No reemplaces el árbol completo de la ventana. Los demás componentes se atienden en otros pasos. '
        'Conservá nombres, bindings, eventos y lógica. No cambies code-behind para un ajuste visual. '
        'El editor integrará operaciones pequeñas mediante anclas únicas, sin solapamiento ni duplicados. '
        'No repitas archivos completos, no incluyas bloques sin cambios ni planes extensos. '
        'Completá solo este paso dentro del presupuesto del editor. AVC compilará y capturará luego. '
        'Si no podés localizar un control, explicá brevemente el límite sin inventar una edición.\n'+
        ('Diagnóstico comprobado de este paso: '+feedback if feedback else ''))


def edit_diagnostic(code, output):
    if code == -1:
        return 'cancelled', 'Detenido por el usuario.'
    if code == -3 or any(marker in output for marker in EDIT_FAILURE_MARKERS):
        return 'format', 'Aider rechazó bloques incompletos o que no coinciden con el archivo.'
    if code == -2:
        return 'timeout', 'El editor agotó el tiempo disponible.'
    if code:
        return 'error', f'El editor terminó con código {code}. ' + output[:1200]
    return '', ''

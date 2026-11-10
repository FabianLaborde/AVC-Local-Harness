"""Non-authoritative intent diagnostics for managed WPF edit requests."""
import json

from intent_contract import derive_intent_contract
from intent_evidence import resolve_intent_contract
from xaml_structure import inspect_xaml_bytes


PROMOTABLE_PROPERTIES = {'horizontal_alignment', 'background', 'foreground',
                         'window_bounds', 'element_bounds'}


def diagnose_sources(sources, instruction):
    """Build a read-only diagnostic without executing or modifying project sources."""
    contract = derive_intent_contract(instruction)
    inventories = []
    errors = []
    for name, content in sorted(sources.items()):
        if not str(name).lower().endswith('.xaml'):
            continue
        try:
            inventories.append(inspect_xaml_bytes(content.encode('utf-8'), str(name)))
        except (TypeError, ValueError, UnicodeError) as exc:
            errors.append({'file': str(name), 'error': str(exc)[:500]})
    resolution = resolve_intent_contract(contract, inventories)
    return {
        'mode': 'shadow', 'authoritative': False,
        'contract': contract, 'resolution': resolution,
        'source_errors': errors,
        'limitations': [
            'Este diagnóstico no altera el enrutamiento, la edición ni la aprobación.',
            'Solo usa fuentes XAML ya recuperados por el recorrido administrado.',
        ],
    }


def promotable_initial_satisfaction(payload):
    """Return a deterministic no-op fact only for complete direct source evidence."""
    if not isinstance(payload, dict) or payload.get('error') or payload.get('source_errors'):
        return None
    contract = payload.get('contract') or {}
    resolution = payload.get('resolution') or {}
    changes = contract.get('changes') or []
    results = resolution.get('results') or []
    if (contract.get('status') != 'supported' or contract.get('ambiguities') or
            contract.get('postconditions') or resolution.get('status') != 'satisfied' or
            not 1 <= len(changes) <= 4 or len(results) != len(changes)):
        return None
    facts = []
    for change, result in zip(changes, results):
        target = change.get('target') or {}
        if (change.get('property') not in PROMOTABLE_PROPERTIES or
                result.get('status') != 'satisfied' or
                result.get('code') != 'direct_value_matches' or
                len(result.get('matches') or []) != 1):
            return None
        # A semantic control role needs the literal label supplied by the user.
        # Window/interface targets are intrinsically singular in a valid resolution.
        if target.get('role') not in {'window', 'interface'} and not target.get('label'):
            return None
        match = result['matches'][0]
        facts.append({'property': change['property'], 'expected': result.get('expected'),
                      'file': match.get('file'), 'element_index': match.get('element_index'),
                      'type': match.get('type'), 'label': target.get('label')})
    return {
        'decision_source': 'intent_evidence_preflight',
        'reason': ('Los fuentes prueban que cada objetivo literal único ya posee '
                   'el valor directo solicitado.'),
        'facts': facts,
    }


def run_shadow_diagnostic(api, sources, instruction, folder):
    """Persist and announce shadow evidence; never fail the productive workflow."""
    try:
        payload = diagnose_sources(sources, instruction)
        destination = folder / 'intent_shadow.json'
        destination.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                               encoding='utf-8')
        status = payload['resolution']['status']
        api.log('Diagnóstico de intención en sombra: ' + status +
                '. Es informativo; no decide ni modifica el recorrido actual.')
        api.audit('intent_shadow', status=status,
                  contract_status=payload['contract']['status'],
                  changes=len(payload['contract']['changes']),
                  source_errors=len(payload['source_errors']),
                  evidence=str(destination))
        return payload
    except Exception as exc:  # Shadow evidence must never break an existing edit.
        try:
            api.log('Diagnóstico de intención en sombra no disponible; el recorrido '
                    'continúa sin cambios: ' + str(exc)[:500])
            api.audit('intent_shadow_error', error=str(exc)[:500])
        except Exception:
            pass
        return {'mode': 'shadow', 'authoritative': False, 'error': str(exc)[:500]}

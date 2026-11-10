"""Execute small edits with fresh evidence and one shared repair budget."""
import shlex
import os
import time

from visual_steps import step_prompt, edit_diagnostic, MAX_EDIT_CALLS, JOB_SECONDS
from visual_review import repair_prompt, pixel_change


def execute_steps(api, adapter, contract, reference, steps, current, evidence, checkpoint,
                  attempts, test_command='', clock=time.monotonic):
    deadline = clock()+JOB_SECONDS
    calls = 0
    repairs = attempts-1
    failures = 0
    stopped_by_editor = False
    records = [dict(id=s['id'], title=s['title'], targets=s['targets'], state='pending') for s in steps]
    evidence.data['edit_steps'] = records
    evidence.flush()

    def stage(name, **fields):
        with api.STATE_LOCK:
            api.STATE['phase'] = name
        evidence.stage(name, **fields)
        api.audit('visual_pipeline_stage', stage=name, **fields)

    def edit(prompt, repair=False):
        nonlocal calls
        if api.CANCEL.is_set() or calls >= MAX_EDIT_CALLS or clock() >= deadline:
            return None
        calls += 1
        with api.STATE_LOCK:
            api.STATE.update(attempt=calls, attempts=min(MAX_EDIT_CALLS,len(steps)+attempts-1))
        stage('fixing' if repair else 'coding', attempt=calls, step=step['id'])
        code, output = adapter.edit(prompt, timeout=min(api.AIDER_PROCESS_TIMEOUT, max(1,deadline-clock())), repair=repair)
        kind, detail = edit_diagnostic(code, output)
        evidence.stage('editor_result', step=step['id'], call=calls, exit_code=code, issue=kind, detail=detail)
        return kind, detail

    def validate():
        stage('building', step=step['id'])
        candidate = adapter.build_capture()
        if candidate['ok'] and test_command and not api.CANCEL.is_set():
            stage('testing', step=step['id'])
            code, output = api.run_process(shlex.split(test_command,posix=os.name!='nt'),adapter.project,
                                          timeout=min(300,max(1,deadline-clock())))
            if code:
                return dict(ok=False,stage='pruebas',detail=output[-9000:])
        return candidate

    for index, step in enumerate(steps):
        if api.CANCEL.is_set() or calls >= MAX_EDIT_CALLS or clock() >= deadline:
            break
        record = records[index]
        api.log(f'Paso {index+1}/{len(steps)}: {step["title"]}. Se edita solo este grupo de elementos.')
        source_before = adapter.sources()
        shot_before = current['shot'] if current else None
        checkpoint_sources = adapter.checkpoint(checkpoint)
        outcome = edit(step_prompt(contract,reference.plan,step))
        if outcome is None or api.CANCEL.is_set():
            break
        kind, detail = outcome
        changed = source_before != adapter.sources()
        record.update(editor_issue=kind,detail=detail,changed=changed)
        if not changed:
            record['state'] = 'editor_failed' if kind else 'unchanged'
            api.log(f'Paso {index+1}: '+(detail or 'El editor no modificó archivos; el objetivo queda pendiente de revisión.'))
            failures = failures+1 if kind else 0
            evidence.flush()
            if failures >= 2:
                api.log('Dos pasos seguidos fallaron en el editor. Se detiene para evitar más reintentos sin evidencia de avance.')
                stopped_by_editor = True
                break
            continue
        candidate = validate()
        if api.CANCEL.is_set():
            break
        if not candidate['ok'] and repairs > 0 and calls < MAX_EDIT_CALLS and clock() < deadline:
            repairs -= 1
            diagnostic = 'FALLO COMPROBADO en '+candidate['stage']+':\n'+candidate['detail']
            record['repair'] = diagnostic
            repair_sources = adapter.sources()
            api.log('Se usa una reparación técnica del presupuesto compartido, sin repetir el rediseño.')
            repair_outcome = edit(repair_prompt('Paso afectado: '+step['title'],diagnostic,
                                                adapter.repair_context(diagnostic)),repair=True)
            if repair_outcome is not None:
                record['repair_issue'] = repair_outcome[0]
                kind = kind or repair_outcome[0]
            if api.CANCEL.is_set():
                break
            if repair_outcome is not None and repair_sources != adapter.sources():
                candidate = validate()
        if api.CANCEL.is_set():
            break
        if not candidate['ok']:
            record.update(state='failed',detail=candidate['detail'])
            api.remember_visual_failure(calls,candidate['stage'],candidate['detail'])
            restored, reason = adapter.restore(checkpoint,checkpoint_sources)
            current = None
            api.remember_visual_capture('despues',None)
            if restored:
                api.remember_visual_restoration(reason)
                rebuilt = adapter.build_capture()
                if rebuilt['ok']:
                    current = rebuilt
                    api.remember_visual_capture('despues',current['shot'])
                    evidence.capture('restored_step',current['shot'],current['sources'],calls)
            evidence.flush()
            failures += 1
            if not current or failures >= 2:
                return dict(current=current,records=records,calls=calls,failed=True,
                            reason='Se detuvo la secuencia tras fallos técnicos; '+reason)
            # Each next step reads the restored, buildable sources. It must not
            # assume that the rejected parent's proposed changes were applied.
            api.log('Se recuperó el estado compilable anterior a este paso. El paso queda pendiente; '
                    'se continúa con el siguiente desde los archivos recuperados.')
            continue
        current = candidate
        failures = 0
        record.update(state='partial' if kind else 'built',pixel_change=pixel_change(shot_before,current['shot']))
        api.remember_visual_attempt(calls,raw=current['shot'])
        api.remember_visual_capture('despues',current['shot'])
        evidence.capture('step',current['shot'],current['sources'],calls)
        api.log(f'Paso {index+1}: compilación y captura correctas. Cambio de píxeles: {record["pixel_change"]}. '
                'La fidelidad se evaluará al comparar el pedido completo.')
        evidence.flush()
    technical_failed = any(r['state']=='failed' for r in records)
    return dict(current=current,records=records,calls=calls,failed=stopped_by_editor or technical_failed,
                reason='Dos pasos fallaron en el editor; se detuvo la secuencia.' if stopped_by_editor else
                       'Presupuesto agotado; quedaron pasos pendientes.' if any(r['state']=='pending' for r in records)
                       else 'Secuencia terminada con pasos fallidos recuperados; se conservaron los demás cambios.' if technical_failed
                       else 'Secuencia de pasos terminada; requiere comparación visual.')

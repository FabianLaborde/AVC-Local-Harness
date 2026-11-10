"""Reference-driven editing lifecycle with a bounded, rendered feedback loop.

The orchestration is independent of a particular screen. The WPF adapter below
uses AVC's build/capture/editor services; language-specific facts stay in it.
"""
import base64
import shlex
import os
import re
import time

from visual_reference import ReferenceStore, RunEvidence
from visual_review import prepare_contract, compare_task, corrective_feedback, edit_prompt, repair_prompt
from visual_schemas import contract_schema, review_schema, CONFIRM, INTENT
from xaml_structure import inspect_xaml, structural_report, declared_layout_report
from visual_steps import make_steps, edit_diagnostic
from visual_sequence import execute_steps


class WpfAdapter:
    def __init__(self, api, project, code_model, criteria):
        self.api, self.project, self.code_model = api, project, code_model
        self.criteria = criteria

    def sources(self):
        return self.api.source_snapshot(self.project)

    def context(self):
        path = self.project / 'MainWindow.xaml'
        try:
            data = inspect_xaml(path)
            return declared_layout_report(data) + '\n' + structural_report(data)
        except (ValueError, OSError):
            return 'No hay un XAML inicial legible; usar los fuentes existentes.'

    def build_capture(self):
        api = self.api
        sources = self.sources()
        code, detail = api.build_wpf(self.project)
        if code or api.CANCEL.is_set():
            return dict(ok=False, stage='compilacion', detail=detail[-9000:])
        code, detail, exes = api.publish_wpf(self.project)
        if code or not exes or api.CANCEL.is_set():
            return dict(ok=False, stage='publicacion', detail=detail[-9000:])
        observation = {}
        shot, detail = api.capture_published_window(
            exes[0], inspection=observation if self.criteria['enabled'] else None)
        if not shot or api.CANCEL.is_set():
            return dict(ok=False, stage='captura', detail=detail)
        if sources != self.sources():
            return dict(ok=False, stage='captura', detail='Los fuentes cambiaron durante la compilación/captura; evidencia invalidada.')
        return dict(ok=True, shot=shot, observation=observation, sources=sources, exe=str(exes[0]))

    def edit(self, prompt, timeout=None, repair=False):
        from wpf_source_edit import edit_sources
        try:
            output = edit_sources(self.api, self.project, self.code_model, prompt,
                                  timeout or self.api.AIDER_PROCESS_TIMEOUT, repair,
                                  getattr(self, 'user_request', ''))
            self.api.log(output)
            return 0, output
        except Exception as exc:
            detail = 'Editor local WPF rechazó el candidato: ' + str(exc)
            self.api.log(detail)
            self.api.audit('wpf_staged_editor_rejected', repair=repair, reason=str(exc))
            return (-1 if self.api.CANCEL.is_set() else 1), detail

    def checkpoint(self, path):
        if not self.api.save_working_checkpoint(self.project, path):
            raise RuntimeError('No se pudo respaldar el proyecto; edición detenida.')
        return self.sources()

    def repair_context(self, diagnostic):
        from pathlib import Path
        fragments = []
        seen = set()
        for name, line in re.findall(r'([A-Za-z]:[^\r\n]*?\.xaml)\((\d+),\d+\)', diagnostic):
            path = Path(name).resolve()
            if not path.is_relative_to(self.project.resolve()) or not path.is_file() or path in seen:
                continue
            seen.add(path)
            lines = path.read_text(encoding='utf-8-sig').splitlines()
            index = int(line)-1
            if 0 <= index < len(lines):
                fragments.append(str(path.relative_to(self.project.resolve()))+f' · línea {line}\n'+
                                 '\n'.join(lines[max(0,index-2):index+3]))
            if len(fragments) >= 2:
                break
        return '\n\n'.join(fragments)[:6000]

    def restore(self, path, sources):
        # Delete only source files created during this job's failed candidate.
        # Existing files (including user data) are restored by the checkpoint service.
        created = set(self.sources()) - set(sources)
        ok, detail = self.api.restore_working_checkpoint(self.project, path)
        if ok:
            root = self.project.resolve()
            for relative in created:
                target = (root / relative).resolve()
                if target.is_relative_to(root) and target.is_file() and not target.is_symlink():
                    target.unlink()
            if self.sources() != sources:
                return False, 'La recuperación no coincide con el respaldo; revisar archivos antes de continuar.'
        return ok, detail


def run_reference_job(api, project, instruction, attempts, code_model, image=None,
                      vision_model=None, quality_mode=False, inspection_criteria=None, test_command=''):
    """Single owner of reference, contract, candidates and terminal evidence."""
    api.USAGE_CONTEXT.set(str(project.resolve()))
    criteria = api.criteria_from(inspection_criteria or {})
    # Optional UIA measurements keep their previous opt-in quality-mode boundary.
    criteria['enabled'] = bool(quality_mode and criteria['enabled'])
    adapter = WpfAdapter(api, project, code_model, criteria)
    adapter.user_request = instruction
    store = ReferenceStore(api.ROOT / 'recovery' / 'visual_projects', project)
    evidence = None
    result = 'No se completó el recorrido visual'
    phase = 'error'
    report = None
    before = None
    current = None
    last_valid_sources = None
    checkpoint = None
    attempts = max(1, min(3, attempts))

    def stage(name, **fields):
        with api.STATE_LOCK:
            api.STATE['phase'] = name
        api.audit('visual_pipeline_stage', stage=name, **fields)
        if evidence:
            evidence.stage(name, **fields)

    def ask(images, prompt, name, budget):
        if api.CANCEL.is_set():
            raise ValueError('Tarea detenida por el usuario')
        schema = (INTENT if name == 'intent' else
                  contract_schema([n['id'] for n in reference.plan['nodes']]) if name == 'contract' else
                  review_schema([g['id'] for g in contract['goals']]) if name == 'comparison' else CONFIRM)
        response = api.ask_vision(vision_model or api.VISION_MODEL, images, prompt,
                                 'visual_pipeline', name, budget=budget, structured=schema)
        if evidence:
            evidence.stage('model_response', purpose=name, response=response)
        return response

    def show_report(review, compiled=True):
        checks = review.get('checks', [])
        verification = dict(estado=review.get('status', 'no_verificable'),
            nota=review['summary'], revision_humana=True, pipeline=True,
            compilado=compiled, pixel_change=review.get('pixel_change'), criterios=checks)
        if criteria['enabled'] and before and current:
            verification = api.attach_inspections(verification, before['observation'], current['observation'], criteria)
        with api.STATE_LOCK:
            api.STATE['verification'] = verification
        api.log('Resultado visual: ' + verification['estado'] + '. ' + verification['nota'])
        return verification

    def recover():
        nonlocal current, report
        path = checkpoint if last_valid_sources is not None else original_backup
        sources = last_valid_sources if last_valid_sources is not None else original_sources
        restored, detail = adapter.restore(path, sources)
        api.remember_visual_capture('despues', None)
        current = None
        if restored:
            api.remember_visual_restoration(detail)
            restored_state = adapter.build_capture()
            if restored_state['ok']:
                current = restored_state
                api.remember_visual_capture('despues', current['shot'])
                evidence.capture('restored', current['shot'], current['sources'])
        report = show_report(dict(status='restaurado' if restored else 'recuperacion_incompleta',
            summary=('Se restauró el respaldo después de un fallo comprobado. ' if restored else '') + detail,
            checks=[]), compiled=bool(current))
        return restored

    try:
        with api.STATE_LOCK:
            api.VISUAL_CAPTURES.clear()
            api.VISUAL_CAPTURES.update(api.empty_evidence(api.STATE['job_id']))
        stage('reference')
        if image:
            raw = base64.b64decode(image.split(',', 1)[-1], validate=True)
            plan = api.visual_specification(base64.b64encode(raw).decode('ascii'),
                                            vision_model or api.VISION_MODEL, return_plan=True)
            reference = store.save(raw, plan)
        else:
            reference = store.load() or store.migrate_legacy()
        if reference is None:
            raise ValueError('Hay una descripción anterior, pero no se encontró su imagen original. '
                             'Adjuntá la referencia una vez para guardarla y poder compararla en las siguientes tareas.')
        api.remember_visual_capture('referencia', reference.raw)
        with api.STATE_LOCK:
            api.VISUAL_CAPTURES['referencia_origen'] = reference.origin
        api.log('Referencia ' + reference.origin + ' · SHA-256 ' + reference.identity[:12] + '. Se conserva la imagen original para futuras comparaciones.')
        evidence = RunEvidence(store, instruction, reference, api.STATE['job_id'])
        evidence.data['reference_plan'] = reference.plan
        evidence.data['editor'] = 'local_staged'
        api.audit('visual_editor_selected', editor='local_staged', route='wpf_reference')
        stage('scaffolding')
        ok, detail = api.ensure_wpf_project(project)
        api.log(detail)
        if not ok:
            raise RuntimeError(detail)
        checkpoint = evidence.folder / 'last_valid.zip'
        # Save before editing even if the existing app does not compile yet.
        original_backup = evidence.folder / 'before.zip'
        original_sources = adapter.checkpoint(original_backup)
        stage('baseline')
        api.log('Capturando el estado inicial desde los fuentes actuales…')
        baseline = adapter.build_capture()
        if baseline['ok']:
            before = baseline
            current = baseline
            api.remember_visual_capture('antes', before['shot'])
            evidence.capture('before', before['shot'], before['sources'])
            last_valid_sources = adapter.checkpoint(checkpoint)
        else:
            api.log('Base no disponible: ' + baseline['detail'])
            evidence.stage('baseline_unavailable', detail=baseline['detail'])
        if api.CANCEL.is_set():
            return
        stage('planning')
        contract = prepare_contract(ask, instruction, reference, before['shot'] if before else None, adapter.context())
        evidence.data['contract'] = contract
        evidence.flush()
        api.log('Pedido interpretado como ' + ('reproducción global' if contract['scope'] == 'reproduce' else 'corrección parcial') +
                ':\n' + '\n'.join('- ' + g['expected'] for g in contract['goals']))
        steps = make_steps(contract, reference.plan)
        if len(steps) > 1 and before:
            api.log(f'AVC dividió el pedido en {len(steps)} pasos pequeños. '
                    f'Presupuesto compartido: {attempts-1} reparaciones, máximo 12 llamadas y 15 minutos de edición.')
            outcome = execute_steps(api, adapter, contract, reference, steps, current, evidence, checkpoint,
                                    attempts, test_command)
            current = outcome['current']
            if api.CANCEL.is_set():
                return
            if current:
                api.remember_visual_capture('despues', current['shot'])
                stage('visual')
                review = compare_task(ask, contract, reference, before['shot'], current['shot'])
                report = show_report(review)
                unresolved = [r for r in outcome['records'] if r['state'] != 'built']
                report['pasos'] = outcome['records']
                if unresolved:
                    report['nota'] += f' {len(unresolved)} pasos pendientes, sin cambios o con edición incompleta.'
                    if report['estado'] == 'coincidencia_orientativa':
                        report['estado'] = 'no_verificable'
                with api.STATE_LOCK:
                    api.STATE['verification'] = report
                evidence.stage('compared_sequence',report=report)
                api.audit('visual_pipeline_comparison',report=report)
                phase = 'error' if outcome['failed'] else 'review'
                result = outcome['reason']+' · '+report['estado']
                if api.measured_failure(report):
                    # Expected final geometry need not hold during intermediate steps.
                    last_valid_sources = original_sources
                    checkpoint = original_backup
                    recover()
                    phase = 'error'
                    result = 'Criterios medibles finales incumplidos; se intentó recuperar la base inicial.'
            else:
                phase, result = 'error', outcome['reason']
            return
        feedback = '' if before else 'Error previo comprobado:\n' + baseline['detail']
        technical_failure = not bool(before)
        previous_shot = before['shot'] if before else None
        for attempt in range(1, attempts + 1):
            if api.CANCEL.is_set():
                break
            with api.STATE_LOCK:
                api.STATE.update(attempt=attempt, attempts=attempts)
            stage('fixing' if technical_failure else 'coding', attempt=attempt)
            api.log(('Reparación técnica' if technical_failure else 'Edición visual') +
                    f' {attempt}/{attempts}. Se comprobarán código, arranque y resultado visible.')
            source_before = adapter.sources()
            command_prompt = (repair_prompt(instruction,feedback) if technical_failure else
                              edit_prompt(contract, reference.plan, adapter.context(), feedback))
            code, output = adapter.edit(command_prompt, repair=technical_failure)
            editor_issue, editor_detail = edit_diagnostic(code, output)
            changed = source_before != adapter.sources()
            evidence.stage('edited', attempt=attempt, exit_code=code, changed=changed)
            if api.CANCEL.is_set():
                break
            if not changed:
                api.log('El editor no cambió archivos. Se detiene el ciclo; no se repite el mismo pedido en otro formato.')
                if ((current and current['sources'] != adapter.sources()) or
                        (report and api.measured_failure(report))):
                    phase = 'error'
                    recover()
                    result = 'La reparación no cambió el candidato fallido; se intentó recuperar el respaldo'
                    break
                if current:
                    api.remember_visual_capture('despues', current['shot'])
                result = ('Sin cambios de código; '+editor_detail if editor_issue else
                          'Sin cambios de código; revisión visual pendiente')
                phase = 'error' if editor_issue else 'review'
                break
            stage('building', attempt=attempt)
            candidate = adapter.build_capture()
            if api.CANCEL.is_set():
                break
            if not candidate['ok']:
                phase = 'error'
                technical_failure = True
                feedback = 'FALLO COMPROBADO en ' + candidate['stage'] + ':\n' + candidate['detail']
                evidence.stage('candidate_failed', attempt=attempt, detail=feedback)
                api.remember_visual_failure(attempt, candidate['stage'], candidate['detail'])
                result = 'No se pudo validar el candidato: ' + candidate['stage']
                if attempt < attempts:
                    continue
                # Restore the last buildable candidate, not an AI-scored aesthetic choice.
                if recover():
                    result += '; recuperado el respaldo'
                break
            if test_command:
                stage('testing', attempt=attempt)
                args = shlex.split(test_command, posix=os.name != 'nt')
                test_code, test_output = api.run_process(args, project, timeout=300)
                if test_code:
                    phase = 'error'
                    technical_failure = True
                    feedback = 'PRUEBAS FALLIDAS:\n' + test_output[-9000:]
                    api.remember_visual_failure(attempt, 'pruebas', test_output)
                    result = 'Pruebas fallidas después de compilar'
                    if attempt < attempts:
                        continue
                    recover()
                    break
            current = candidate
            technical_failure = False
            api.remember_visual_attempt(attempt, raw=current['shot'])
            api.remember_visual_capture('despues', current['shot'])
            evidence.capture('candidate', current['shot'], current['sources'], attempt)
            stage('visual', attempt=attempt)
            api.log('Comparando el resultado renderizado con el pedido y la referencia original…')
            review = compare_task(ask, contract, reference, before['shot'] if before else None, current['shot'])
            report = show_report(review)
            api.remember_visual_attempt(attempt, report=report)
            evidence.stage('compared', attempt=attempt, report=report)
            api.audit('visual_pipeline_comparison', attempt=attempt, report=report)
            result = 'Aplicación compilada y publicada: ' + current['exe'] + ' · ' + report['estado']
            phase = 'review'
            feedback = corrective_feedback(review)
            if api.measured_failure(report):
                feedback += '\n' + api.measurement_summary(report)
                phase = 'error'
                if attempt >= attempts:
                    recover()
                    result = 'Criterios medibles incumplidos; se recuperó el respaldo si estaba disponible'
                    break
            else:
                last_valid_sources = adapter.checkpoint(checkpoint)
            from visual_review import pixel_change
            progress = pixel_change(previous_shot, current['shot'])
            previous_shot = current['shot']
            # One identical first result may get the remaining corrective pass. Never loop on it.
            if attempt > 1 and progress == 'identical':
                api.log('La nueva pasada produjo los mismos píxeles. Se detiene por falta de avance.')
                break
            if not feedback or attempt >= attempts:
                break
            api.log('La comparación confirmó diferencias del pedido. Se usará la pasada restante para corregirlas.')
        if report is None:
            report = show_report(dict(status='no_verificable', summary=result, checks=[]), compiled=bool(current and current.get('ok')))
        evidence.data['result'] = result
    except Exception as exc:
        result = 'Recorrido visual interrumpido: ' + str(exc)
        api.log(result)
        api.audit('visual_pipeline_error', error=str(exc))
    finally:
        if api.CANCEL.is_set():
            result, phase = 'Detenido; los cambios parciales quedan disponibles para revisar', 'stopped'
            # A previously displayed capture cannot describe partially edited sources.
            api.remember_visual_capture('despues', None)
            with api.STATE_LOCK:
                api.STATE['verification'] = None
        elif current and current.get('sources') != adapter.sources():
            result, phase = 'Los fuentes finales no corresponden a la última captura; revisión necesaria', 'error'
            api.remember_visual_capture('despues', None)
            with api.STATE_LOCK:
                api.STATE['verification'] = dict(estado='no_verificable', pipeline=True, compilado=False,
                    nota='Los fuentes finales no corresponden a la última captura; evidencia invalidada.', revision_humana=True)
        if evidence:
            evidence.data.update(result=result, phase=phase)
            try:
                evidence.flush()
            except OSError as exc:
                api.log('No se pudo guardar el informe local: ' + str(exc))
        with api.STATE_LOCK:
            api.STATE.update(result=result, phase=phase, running=False, finished=time.time())
        elapsed = time.time() - api.STATE['started'] if api.STATE.get('started') else 0
        try:
            api.record_job(api.USAGE_FILE, str(project.resolve()), elapsed, phase)
        except OSError:
            pass
        api.audit('job_finished', result=result, phase=phase, elapsed_seconds=round(elapsed, 1))
        api.LOCK.release()

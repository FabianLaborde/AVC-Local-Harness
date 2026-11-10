"""Session-bound candidates; only explicit acceptance writes the live project."""
import base64
import difflib
import threading
import time
import unicodedata
import uuid
import hashlib

_pending = {}
_lock = threading.RLock()


def project_fingerprint(project):
    excluded = {'.git','.venv','venv','node_modules','bin','obj','avc_publish','recovery','__pycache__'}
    result = {}
    for path in project.rglob('*'):
        relative = path.relative_to(project)
        if any(part.casefold() in excluded or part.startswith('.aider') for part in relative.parts):
            continue
        if path.is_symlink():
            raise ValueError('El proyecto contiene enlaces simbólicos')
        if path.is_file():
            with path.open('rb') as source:
                result[str(relative)] = hashlib.file_digest(source,'sha256').hexdigest()
    return result


def key(api, project):
    return (api.STATE['chat_id'], str(project.resolve()).casefold())


def get_preview(api, project):
    with _lock:
        return _pending.get(key(api, project))


def clear_previews():
    with _lock:
        _pending.clear()


def save_preview(api, project, candidate):
    if candidate.get('kind') == 'external':
        validation = candidate.get('validation')
        evidence = (validation.get('visual_evidence')
                    if isinstance(validation, dict) else None)
        if isinstance(evidence, dict) and evidence.get('requested'):
            if (not evidence.get('available') or not candidate.get('before')
                    or not candidate.get('after')):
                raise ValueError(
                    'La vista previa visual no tiene la pareja antes/después completa; '
                    'el candidato queda como diagnóstico y no puede ofrecerse para aplicar.')
    candidate = dict(candidate, id=uuid.uuid4().hex)
    with _lock:
        _pending[key(api, project)] = candidate
    api.audit('code_preview_ready', preview_id=candidate['id'], files=list(candidate['changed']),
              evidence=str(candidate['folder']))


def manual_visual_override_allowed(candidate):
    """Allow an explicit human decision only after technical and visual evidence exists."""
    if not isinstance(candidate, dict) or candidate.get('kind') != 'external':
        return False
    validation = candidate.get('validation')
    evidence = validation.get('visual_evidence') if isinstance(validation, dict) else None
    review = evidence.get('review') if isinstance(evidence, dict) else None
    return bool(
        candidate.get('blocked') and isinstance(validation, dict)
        and validation.get('passed') and isinstance(evidence, dict)
        and candidate.get('before') and candidate.get('after')
        and evidence.get('available') and isinstance(review, dict)
        and review.get('status') in {'not_fulfilled', 'uncertain'})


def action(instruction):
    text = ''.join(c for c in unicodedata.normalize('NFD', instruction.lower())
                   if not unicodedata.combining(c)).strip(' .!¡')
    if text in ('aplicalo', 'aplica la vista previa', 'aplicar vista previa', 'si, aplicalo', 'si aplicalo'):
        return 'apply'
    if text in ('descartalo', 'descarta la vista previa', 'descartar vista previa'):
        return 'discard'
    return 'adjust'


def public_preview(api, project):
    candidate = get_preview(api, project)
    if not candidate:
        return None
    if candidate.get('kind') == 'external':
        diff = '\n'.join(''.join(difflib.unified_diff(
            [line+'\n' for line in candidate['source_bytes'][name].decode('utf-8-sig').splitlines()],
            [line+'\n' for line in raw.decode('utf-8-sig').splitlines()], fromfile=name, tofile=name))
            for name, raw in candidate['changed'].items())
        validation = candidate['validation']
        before = candidate.get('before')
        after = candidate.get('after')
        has_visual_evidence = bool(before and after)
        manual_review = manual_visual_override_allowed(candidate)
        return dict(id=candidate['id'],
                    kind='external_visual' if has_visual_evidence else 'external',
                    project=str(project.resolve()),
                    files=list(candidate['changed']), instruction=candidate['instruction'], diff=diff,
                    before=('data:image/png;base64,'+base64.b64encode(before).decode()
                            if before else None),
                    after=('data:image/png;base64,'+base64.b64encode(after).decode()
                           if after else None),
                    blocked=bool(candidate.get('blocked')) and not manual_review,
                    manual_review=manual_review,
                    block_reason=candidate.get('block_reason',''),
                    limitation=validation['limitation'], fulfillment=validation.get('fulfillment'), trial=str(candidate['trial']),
                    validation=validation['summary'], artifacts=validation['artifacts'])
    diff = '\n'.join(''.join(difflib.unified_diff(
        candidate['source_bytes'][name].decode('utf-8-sig').splitlines(keepends=True),
        raw.decode('utf8').splitlines(keepends=True), fromfile=name, tofile=name))
        for name, raw in candidate['changed'].items())
    return dict(id=candidate['id'], kind=candidate.get('kind','code'),
                project=str(project.resolve()), files=list(candidate['changed']),
                instruction=candidate['instruction'], diff=diff,
                before='data:image/png;base64,'+base64.b64encode(candidate['before']).decode(),
                after='data:image/png;base64,'+base64.b64encode(candidate['after']).decode(),
                blocked=bool(candidate.get('blocked')),
                block_reason=candidate.get('block_reason',''))


def run_preview_action(api, project, preview_id, operation):
    phase = 'error'
    result = 'No se pudo completar la acción de vista previa.'
    token = api.USAGE_CONTEXT.set(str(project.resolve()))
    try:
        candidate = get_preview(api, project)
        if not candidate or candidate['id'] != preview_id:
            raise ValueError('La vista previa cambió o ya no existe. Revisá la versión actual.')
        if operation == 'discard':
            with _lock:
                _pending.pop(key(api, project), None)
            result = ('Vista previa descartada. El proyecto conserva su versión vigente. '
                      'Los próximos pedidos partirán de esa versión. Para ajustar un candidato '
                      'pendiente, escribí el ajuste sin descartarlo.')
            phase = 'done'
        elif operation == 'apply':
            manual_review = manual_visual_override_allowed(candidate)
            if candidate.get('blocked') and not manual_review:
                raise ValueError('Este intento es solo diagnóstico y no puede aplicarse: '
                                 + candidate.get('block_reason','no superó la validación'))
            if manual_review:
                review = candidate['validation']['visual_evidence']['review']
                api.audit('code_preview_manual_override', preview_id=preview_id,
                          visual_status=review.get('status'),
                          visual_reason=str(review.get('reason', ''))[:300])
            if candidate.get('kind') == 'external':
                from project_candidate import apply_candidate
                result = apply_candidate(api, project, candidate)
            elif candidate.get('kind') == 'catalog':
                from catalog_plan_edit import apply_catalog_candidate
                api.remember_visual_capture('antes', candidate['before'])
                result = apply_catalog_candidate(api, project, candidate)
            else:
                from catalog_code_edit import apply_candidate
                api.remember_visual_capture('antes', candidate['before'])
                result = apply_candidate(api, project, candidate['model'], candidate['folder'],
                                         candidate['baseline'], candidate['marker_before'],
                                         candidate['source_bytes'], candidate['changed'],candidate['input_hashes'])
            with _lock:
                _pending.pop(key(api, project), None)
            result = (('Aplicación manual completada bajo revisión visual. '
                       if manual_review else 'Aplicación completada correctamente. ') + result)
            phase = 'done'
        else:
            raise ValueError('Acción de vista previa inválida')
        api.audit('code_preview_action', preview_id=preview_id, action=operation)
    except Exception as exc:
        result = 'Vista previa no aplicada: '+str(exc)
        api.audit('code_preview_error', error=str(exc), preview_id=preview_id)
    finally:
        api.log(result)
        if api.CANCEL.is_set():
            phase = 'stopped'
        with api.STATE_LOCK:
            api.STATE.update(result=result, phase=phase, running=False, finished=time.time())
        elapsed = time.time()-(api.STATE.get('started') or time.time())
        try:
            api.record_job(api.USAGE_FILE, str(project.resolve()), elapsed, phase)
        except OSError:
            pass
        api.audit('job_finished', result=result, phase=phase, elapsed_seconds=round(elapsed, 1))
        api.USAGE_CONTEXT.reset(token)
        api.LOCK.release()

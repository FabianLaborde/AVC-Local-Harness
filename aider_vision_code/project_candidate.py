"""Shared project-copy, validation and explicit acceptance lifecycle."""
import hashlib
import json
import os
import unicodedata
from pathlib import Path

from project_validation import resolve_capabilities
from source_editor import requested_visual_categories

EXCLUDED = {'.git', '.venv', 'venv', 'node_modules', 'bin', 'obj', '.vs',
            'artifacts', 'deployment', 'publish', 'dist', 'recovery', 'avc_publish', '__pycache__', '.pytest_cache'}


def visual_preview_requested(instruction):
    """Require visual evidence for visual edits or an explicit preview request."""
    text = ''.join(char for char in unicodedata.normalize('NFD', instruction.casefold())
                   if not unicodedata.combining(char))
    return ('vista previa' in text or 'antes y despues' in text
            or 'antes/despues' in text or 'before and after' in text
            or bool(requested_visual_categories(instruction)))


def capture_artifact(api, validation, project, runtime_contract):
    """Capture one unambiguous Windows executable produced by a validated copy."""
    artifacts = [Path(name) for name in validation.get('artifacts', [])
                 if str(name).lower().endswith('.exe')]
    if len(artifacts) != 1:
        return None, ('La validación no produjo un único ejecutable capturable.' if artifacts
                      else 'La validación no produjo un ejecutable capturable.'), {}
    capture = getattr(api, 'capture_published_window', None)
    if not callable(capture):
        return None, 'El capturador visual no está disponible en este recorrido.', {}
    if not isinstance(runtime_contract, dict) or runtime_contract.get('status') != 'ready':
        reason = ((runtime_contract or {}).get('reason')
                  if isinstance(runtime_contract, dict) else None)
        return None, reason or 'No existe un contrato verificable para iniciar la vista previa.', {}
    from preview_runtime import materialized_environment, target_errors, inspection_summary
    inspection = {}
    try:
        with materialized_environment(project, runtime_contract) as environment:
            shot, error = capture(artifacts[0], respect_cancel=False,
                                  inspection=inspection, launch_environment=environment,
                                  inspection_targets=list(dict.fromkeys([
                                      *runtime_contract.get('target', {}).get(
                                          'required_controls', []),
                                      *runtime_contract.get('target', {}).get(
                                          'forbidden_text', []),
                                  ])))
    except Exception as exc:
        return None, 'No se pudo preparar el entorno temporal: ' + str(exc), {}
    errors = target_errors(inspection, runtime_contract) if shot and not error else []
    if errors:
        return None, ' '.join(errors), inspection_summary(inspection)
    return shot, error or '', inspection_summary(inspection)


def inventory(root, scope=None):
    root = root.resolve()
    result = {}
    size = 0
    scope = scope or dict(directories=['.'], files=[])
    allowed_dirs = [safe_path(root, n) for n in scope['directories']]
    explicit = {safe_path(root, n) for n in scope['files']}
    def relevant(path):
        return (path in explicit or any(path == d or path.is_relative_to(d) or d.is_relative_to(path) for d in allowed_dirs)
                or any(p.is_relative_to(path) for p in explicit))
    for parent, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = [n for n in dirs if n.casefold() not in EXCLUDED and relevant(Path(parent)/n)]
        files = [n for n in files if relevant(Path(parent)/n)]
        for name in dirs + files:
            path = Path(parent) / name
            if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
                raise ValueError('La copia no admite enlaces: ' + str(path))
        dirs[:] = [n for n in dirs if n.casefold() not in EXCLUDED]
        for name in files:
            if name.startswith(('.aider', '.env')) or name.lower().endswith(('.db', '.db-wal', '.db-shm', '.pfx', '.key', '.zip', '.7z', '.rar', '.msi')):
                continue
            path = Path(parent) / name
            size += path.stat().st_size
            if size > 250_000_000 or len(result) >= 15000:
                raise ValueError('Proyecto demasiado grande para la copia acotada (250 MB / 15000 archivos)')
            result[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def safe_path(root, name):
    path = root / name
    if Path(name).is_absolute() or '..' in Path(name).parts or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('Ruta fuera de la copia')
    return path


def prepare_candidate(api, project, report_dir, instruction, changed, original,
                      runtime_contract=None, vision_model=None,
                      comparison_changed=None):
    """No model calls here: build exactly the reviewed editor output in a full copy."""
    if api.CANCEL.is_set():
        raise ValueError('Validación cancelada')
    adapter, capabilities = resolve_capabilities(
        project.resolve(), next(iter(changed)), additional_files=original)
    entry = capabilities['entry']
    scope = capabilities['copy_scope']
    baseline = inventory(project, scope)
    if set(scope['files']) - set(baseline):
        raise ValueError('Una entrada requerida está excluida por la política de copia')
    if any((project / n).read_bytes() != raw for n, raw in original.items()):
        raise ValueError('Los fuentes originales cambiaron antes de copiar')
    trial = report_dir / 'trial'
    trial.mkdir()
    (report_dir / 'copy_policy.json').write_text(json.dumps(dict(
        scope=scope,
        excluded_directories=sorted(EXCLUDED),
        excluded_files=['.aider*', '.env*', '*.db', '*.db-wal', '*.db-shm', '*.pfx', '*.key', '*.zip', '*.7z', '*.rar', '*.msi'],
        note='Sin datos de ejecución ni secretos; si la compilación necesita un archivo omitido debe revisarse la copia.'
    ), ensure_ascii=False, indent=2), encoding='utf8')
    (report_dir / 'capabilities.json').write_text(
        json.dumps(capabilities, ensure_ascii=False, indent=2), encoding='utf8')
    for name, expected in baseline.items():
        raw = safe_path(project, name).read_bytes()
        if hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError('El proyecto cambió al copiarlo')
        path = safe_path(trial, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    if comparison_changed is not None:
        if (not isinstance(comparison_changed, dict)
                or set(comparison_changed) - set(original)
                or any(not isinstance(raw, bytes)
                       for raw in comparison_changed.values())):
            raise ValueError('Candidato de comparación inválido')
        for name, raw in comparison_changed.items():
            safe_path(trial, name).write_bytes(raw)
    visual_requested = visual_preview_requested(instruction)
    before = None
    before_error = ''
    before_inspection = {}
    if visual_requested:
        api.log('Vista previa solicitada: validando y capturando la versión vigente en la copia.')
        baseline_validation = adapter.validate(trial, entry, api.run_process)
        if baseline_validation.get('passed'):
            before, before_error, before_inspection = capture_artifact(
                api, baseline_validation, project, runtime_contract)
        else:
            before_error = 'La versión vigente no pudo compilarse en la copia aislada.'
    if comparison_changed is not None:
        for name in comparison_changed:
            safe_path(trial, name).write_bytes(original[name])
    for name, raw in changed.items():
        if name not in baseline:
            raise ValueError('Archivo candidato fuera del inventario')
        safe_path(trial, name).write_bytes(raw)
    api.log('Copia acotada: ' + str(len(baseline)) + ' archivos; proyectos: ' + ', '.join(scope['projects']))
    trial_hashes = inventory(trial)
    api.log('Validación en copia: ' + adapter.name + '.' +
            (' La aplicación se iniciará únicamente para obtener las capturas solicitadas.'
             if visual_requested else ' No se inicia la aplicación.'))
    validation = adapter.validate(trial, entry, api.run_process)
    after = None
    after_error = ''
    after_inspection = {}
    if visual_requested and validation.get('passed'):
        after, after_error, after_inspection = capture_artifact(
            api, validation, project, runtime_contract)
    visual_errors = [message for message in (before_error, after_error) if message]
    visual_blocked = bool(visual_requested and (not before or not after))
    visual_review = None
    if visual_requested and before and after:
        from preview_visual_review import review_visual_candidate
        api.log('Revisión visual: midiendo píxeles y evaluando el cumplimiento del pedido.')
        visual_review = review_visual_candidate(
            api, instruction, before, after, before_inspection, after_inspection,
            (runtime_contract or {}).get('target', {}), report_dir, vision_model)
        if visual_review.get('status') != 'fulfilled':
            visual_blocked = True
            visual_errors.append(visual_review.get('reason') or
                                 'El cambio visual no acreditó el cumplimiento del pedido.')
    if visual_blocked and not visual_errors:
        visual_errors = ['El capturador no devolvió ambas imágenes requeridas.']
    if visual_blocked:
        validation['visual_evidence'] = dict(
            requested=True, available=bool(before and after), errors=visual_errors,
            summary=('Las capturas existen, pero el cumplimiento visual fue rechazado.'
                     if before and after else
                     'No se obtuvieron ambas capturas; el candidato no puede aplicarse.'),
            before=before_inspection, after=after_inspection,
            runtime_contract=runtime_contract, review=visual_review)
    elif visual_requested:
        validation['visual_evidence'] = dict(
            requested=True, available=True, errors=[],
            summary='Capturas reales de la versión vigente y del candidato disponibles.',
            before=before_inspection, after=after_inspection,
            runtime_contract=runtime_contract, review=visual_review)
    if visual_requested:
        validation['limitation'] = (
            'La aplicación se inició únicamente para capturar ambas versiones. '
            'Las imágenes no verifican eventos, navegación ni acceso a datos.')
    if visual_review and visual_review.get('status') == 'fulfilled':
        validation['fulfillment'] = dict(
            status='visually_verified', visual='fulfilled', functional='not_verified',
            summary='El cambio visual superó medición, revisión visual y razonamiento. '
                    'Las interacciones siguen pendientes de revisión humana.')
    elif visual_review:
        validation['fulfillment'] = dict(
            status=visual_review.get('status', 'uncertain'), visual='not_fulfilled',
            functional='not_verified', summary=visual_review.get('reason', ''))
    else:
        validation['fulfillment'] = dict(status='not_verified', visual='not_verified', functional='not_verified',
            summary='Cumplimiento del pedido pendiente de revisión. La compilación o sintaxis no demuestran el efecto solicitado.')
    (report_dir / 'validation.json').write_text(json.dumps(validation, ensure_ascii=False, indent=2), encoding='utf8')
    api.log(validation['summary'] + '\n' + validation['output'] + '\n' + validation['limitation'] + '\n' + validation['fulfillment']['summary'])
    if api.CANCEL.is_set():
        raise ValueError('Validación cancelada')
    if not validation['passed']:
        raise ValueError('La validación falló; candidato conservado en ' + str(report_dir))
    if inventory(trial) != trial_hashes:
        raise ValueError('La validación modificó fuentes o recursos de la copia')
    if inventory(project, scope) != baseline:
        raise ValueError('El proyecto original cambió durante la validación')
    return dict(kind='external', folder=report_dir, instruction=instruction,
                source_bytes=original, changed=dict(changed), input_hashes=baseline,
                validation=validation, trial=trial, trial_hashes=trial_hashes,
                capabilities=capabilities, scope=scope, entry=entry,
                before=before, after=after, blocked=visual_blocked,
                block_reason=(' '.join(visual_errors) if visual_blocked else ''))


def apply_candidate(api, project, candidate):
    """Apply reviewed bytes with full input concurrency check and recoverable backup."""
    if api.CANCEL.is_set():
        raise ValueError('Aplicación cancelada')
    if not candidate['validation']['passed']:
        raise ValueError('Candidato no validado')
    if 'scope' in candidate:
        _, capabilities = resolve_capabilities(
            project.resolve(), next(iter(candidate['changed'])),
            additional_files=candidate['source_bytes'])
        recorded_capabilities = candidate.get('capabilities')
        if (not isinstance(recorded_capabilities, dict)
                or capabilities != recorded_capabilities
                or capabilities['copy_scope'] != candidate['scope']
                or capabilities['entry'] != candidate['entry']):
            raise ValueError('Cambió la selección de dependencias; prepará un candidato nuevo')
    if inventory(project, candidate.get('scope')) != candidate['input_hashes']:
        raise ValueError('El proyecto cambió después de preparar el candidato; prepará uno nuevo')
    if inventory(candidate['trial']) != candidate['trial_hashes']:
        raise ValueError('La copia de prueba cambió; prepará un candidato nuevo')
    for name, raw in candidate['changed'].items():
        if hashlib.sha256(raw).hexdigest() != candidate['trial_hashes'].get(name):
            raise ValueError('Los bytes a aplicar no coinciden con el candidato validado')
    backup = candidate['folder'] / 'before_apply'
    backup.mkdir(exist_ok=False)
    written = []
    try:
        for name, raw in candidate['changed'].items():
            dest = safe_path(project, name)
            saved = safe_path(backup, name)
            saved.parent.mkdir(parents=True, exist_ok=True)
            saved.write_bytes(dest.read_bytes())
            temp = dest.with_name(dest.name + '.avc-applying')
            with temp.open('xb') as stream:
                stream.write(raw)
            try:
                os.replace(temp, dest)
            finally:
                if temp.exists():
                    temp.unlink()
            written.append(name)
    except Exception:
        for name in reversed(written):
            safe_path(project, name).write_bytes(safe_path(backup, name).read_bytes())
        raise
    report = candidate['folder'] / 'report.json'
    if report.exists():
        try:
            manifest = json.loads(report.read_text(encoding='utf8'))
            manifest.update(status='applied', applied=True, backup=str(backup))
            report.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf8')
        except (OSError, ValueError) as exc:
            api.log('Cambio aplicado; no se pudo actualizar el informe: ' + str(exc))
    return ('Cambios aplicados. Respaldo: ' + str(backup) + '. ' +
            candidate['validation']['summary'] + ' en copia. ' + candidate['validation']['limitation'])

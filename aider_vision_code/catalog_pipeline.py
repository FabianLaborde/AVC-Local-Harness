"""Create forms from typed data and a deterministic WPF renderer."""
import json
import time
import uuid
from wpf_catalog import PROMPT, SCHEMA, normalize, validate, render, save_owned
from conversation_context import visual_only_intent
from visual_pipeline import WpfAdapter


def run_catalog_job(api, project, instruction, code_model, image=None, vision_model=None):
    token=api.USAGE_CONTEXT.set(str(project.resolve()))
    result='Formulario pendiente';phase='error';original={};written={}
    folder=api.ROOT/'recovery'/('catalog_'+uuid.uuid4().hex)
    try:
        folder.mkdir(parents=True)
        from catalog_code_edit import edit_code
        existing=(project/'.avc_catalog.json').is_file()
        with api.STATE_LOCK:
            api.VISUAL_CAPTURES.clear()
            api.VISUAL_CAPTURES.update(api.empty_evidence(api.STATE['job_id']))
            api.STATE['phase']='planning'
        if existing:
            from code_preview import get_preview
            marker=json.loads((project/'.avc_catalog.json').read_text(encoding='utf8'))
            previous=get_preview(api,project)
            if marker.get('mode')=='code':
                result=edit_code(api,project,instruction,code_model,folder,image,vision_model,
                                 preview=True,previous=previous)
            else:
                from catalog_plan_edit import edit_plan, StructuredEditUnsupported
                try:
                    result=edit_plan(api,project,instruction,code_model,folder,image,
                                     vision_model,previous=previous)
                except StructuredEditUnsupported as exc:
                    api.audit('catalog_general_editor_fallback', reason=str(exc), evidence=str(folder))
                    result=edit_code(api,project,instruction,code_model,folder,image,vision_model,
                                     preview=True,previous=previous)
            phase=('done' if result.startswith('El proyecto ya cumple el pedido;') else 'review')
            return
        api.log('--- Interfaz desde componentes WPF: descripción → jerarquía → layout calculado → código ---')
        message=instruction
        if image:raise ValueError('La edición con captura requiere un formulario del catálogo existente')
        response=api.post_ollama('/api/chat', {'model':code_model,'stream':False,'format':SCHEMA,
            'options':{'temperature':0,'num_predict':7500,'num_ctx':16384},'messages':[
                {'role':'system','content':PROMPT},{'role':'user','content':message}]},timeout=180)
        (folder/'response.json').write_text(json.dumps(response,ensure_ascii=False),encoding='utf8')
        if response.get('done_reason')=='length':raise ValueError('Descripción cortada por límite de generación; no se modificó el proyecto.')
        plan=normalize(json.loads(response.get('message',{}).get('content','')),
                       visual_only=visual_only_intent(instruction) is True)
        plan=validate(plan)
        files=render(plan)
        (folder/'plan.json').write_text(json.dumps(plan,ensure_ascii=False,indent=2),encoding='utf8')
        api.audit('catalog_plan',plan=plan)
        api.log('Estructura: '+', '.join(n['text'] or n['kind'] for n in plan['nodes']))
        if api.CANCEL.is_set():raise ValueError('Cancelado antes de escribir')
        # A new project's folder may have changed during inference.
        from wpf_catalog import creation_eligible
        if not creation_eligible(project) or (project/'.avc_catalog.json').exists():
            raise ValueError('La carpeta cambió durante la planificación')
        ok,detail=api.ensure_wpf_project(project)
        if not ok:raise ValueError(detail)
        adapter=WpfAdapter(api,project,code_model,{'enabled':False})
        adapter.checkpoint(folder/'before.zip')
        if api.CANCEL.is_set():raise ValueError('Cancelado antes de escribir')
        for name,code in files.items():
            path=project/name;original[name]=path.read_bytes() if path.exists() else None
            written[name]=code.encode('utf8');path.write_bytes(written[name])
        current=adapter.build_capture()
        if not current['ok']:raise ValueError('Falló '+current['stage']+': '+current['detail'])
        if api.CANCEL.is_set():raise ValueError('Cancelado durante la verificación')
        save_owned(project,plan)
        api.remember_visual_capture('despues',current['shot'])
        (folder/'result.png').write_bytes(current['shot'])
        result='Interfaz compilada y publicada: '+current['exe']
        phase='review'
        api.log(result)
        api.log('Componentes generados desde una jerarquía tipada. AVC calculó filas, columnas y proporciones sin coordenadas libres. Compilación y captura comprobadas; revisá el diseño. La validación es de campos, no autenticación. Los enlaces son leyendas sin navegación.')
        api.audit('catalog_complete',exe=current['exe'],evidence=str(folder))
        with api.STATE_LOCK:
            api.STATE['verification']={'estado':'no_verificable','pipeline':True,'compilado':True,
                'nota':'Interfaz de catálogo compilada y capturada. Cumplimiento pendiente de revisión. '+('La imagen guió operaciones sobre componentes existentes.' if image else 'Creada desde la descripción textual, sin imagen de referencia.'),
                'revision_humana':True}
    except Exception as exc:
        result='Formulario no completado: '+str(exc)
        for name,raw in original.items():
            path=project/name
            if path.exists() and path.read_bytes()==written[name]:
                if raw is None:path.unlink()
                else:path.write_bytes(raw)
            else:api.log('Archivo cambiado concurrentemente; no se sobrescribe: '+name)
        api.remember_visual_capture('despues',None)
        api.log(result)
        api.audit('catalog_error',error=str(exc),evidence=str(folder))
    finally:
        if api.CANCEL.is_set():phase='stopped'
        with api.STATE_LOCK:
            api.STATE.update(result=result,phase=phase,running=False,finished=time.time())
        elapsed=time.time()-api.STATE.get('started',time.time())
        try:api.record_job(api.USAGE_FILE,str(project.resolve()),elapsed,phase)
        except OSError:pass
        api.audit('job_finished',result=result,phase=phase,elapsed_seconds=round(elapsed,1))
        api.USAGE_CONTEXT.reset(token)
        api.LOCK.release()

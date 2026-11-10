"""Preview and apply bounded edits to a catalog plan without abandoning the catalog."""
import copy
import ctypes
import json
import re
import shutil
import unicodedata

from catalog_image_edit import apply_edits, edit_existing, respect_instruction
from catalog_dual_review import review_candidate, feedback_text
from catalog_instruction_intents import (permits_width_growth, requests_compaction,
                                         requests_relayout)
from visual_pipeline import WpfAdapter
from wpf_catalog import read_owned, render, save_owned, validate


class StructuredEditUnsupported(ValueError):
    """The request needs the general code editor rather than catalog operations."""


STRUCTURAL_LIMIT_ERRORS = (
    'No se eliminan contenedores ni sus controles en cascada',
    'No se aplicó el cambio: la propuesta de distribución no corresponde a controles del mismo grupo.',
    'La agrupación requiere controles consecutivos en su orden actual',
)


def _leaf_minimum_width(node):
    kind=node.get('kind');text=str(node.get('text',''))
    if kind in ('field','password'):
        icon=34 if node.get('icon','none')!='none' else 0
        return max(72,min(220,34+icon+round(len(text)*7.5)))
    if kind=='button':return max(64,min(220,28+round(len(text)*7.5)))
    if kind=='checkbox':return max(72,min(260,24+round(len(text)*7.5)))
    return 0


def _intrinsic_widths(plan):
    """Return minimum widths implied by non-wrapping descendants."""
    nodes=plan.get('nodes',[]);children={node['id']:[] for node in nodes}
    for node in nodes:
        if node.get('parent') in children:children[node['parent']].append(node)
    card_padding={'comfortable':24,'compact':12,'tight':6}.get(
        plan.get('density','compact'),12)
    widths={}
    def measure(node):
        direct=children.get(node['id'],[]);kind=node.get('kind')
        if kind=='row' and direct:value=sum(measure(child) for child in direct)+8*len(direct)
        elif direct:value=max((measure(child) for child in direct),default=0)
        else:value=_leaf_minimum_width(node)
        if kind=='card':value+=2*card_padding
        widths[node['id']]=value
        return value
    for node in nodes:
        if node.get('parent')=='root':measure(node)
    return widths


def _balance_new_rows(before, after):
    """Allocate new row columns by their measured content demand."""
    old_ids={node['id'] for node in before.get('nodes',[])}
    result=copy.deepcopy(after);widths=_intrinsic_widths(result)
    for row in result.get('nodes',[]):
        if row['id'] in old_ids or row.get('kind')!='row':continue
        direct=[node for node in result['nodes'] if node.get('parent')==row['id']]
        demands=[max(1,widths.get(node['id'],1)) for node in direct]
        available=_allocated_widths(result).get(row['id'],0)
        fitted=_fit_row_weights(demands,available)
        if fitted:
            for node,weight in zip(direct,fitted):node['weight']=weight
        else:
            peak=max(demands,default=1)
            for node,demand in zip(direct,demands):
                node['weight']=max(1,round(12*demand/peak))
    return result


def _compressed_leaf_ids(plan):
    """Estimate newly unreadable single-line controls from the typed layout.

    This is deliberately conservative and framework-independent at the plan
    level: rows divide the available width by weight, while columns and cards
    retain it.  Text blocks may wrap, but fields and buttons may not silently
    lose their labels.
    """
    nodes=plan.get('nodes',[]);children={'root':[]}
    for node in nodes:children.setdefault(node['id'],[])
    for node in nodes:children.setdefault(node.get('parent'),[]).append(node)
    density=plan.get('density','compact')
    card_padding={'comfortable':24,'compact':12,'tight':6}.get(density,12)
    outer=plan.get('outer_margin',24)
    compressed=set()

    def visit(node,width):
        kind=node.get('kind');direct=children.get(node.get('id'),[])
        if kind=='card':width=max(0,width-2*card_padding)
        if kind=='row' and direct:
            usable=max(0,width-8*len(direct))
            total=sum(max(1,child.get('weight',1)) for child in direct)
            for child in direct:
                visit(child,usable*max(1,child.get('weight',1))/total)
            return
        required=_leaf_minimum_width(node)
        if required and width+0.5<required:compressed.add(node['id'])
        for child in direct:visit(child,width)

    available=max(0,plan.get('width',0)-2*outer)
    for root in children.get('root',[]):visit(root,available)
    return compressed


def _allocated_widths(plan):
    """Propagate the current window width through typed rows and cards."""
    nodes=plan.get('nodes',[]);children={'root':[]};allocated={}
    for node in nodes:children.setdefault(node['id'],[])
    for node in nodes:children.setdefault(node.get('parent'),[]).append(node)
    card_padding={'comfortable':24,'compact':12,'tight':6}.get(
        plan.get('density','compact'),12)
    def visit(node,width):
        kind=node.get('kind');direct=children.get(node['id'],[])
        if kind=='card':width=max(0,width-2*card_padding)
        allocated[node['id']]=width
        if kind=='row' and direct:
            usable=max(0,width-8*len(direct));total=sum(
                max(1,child.get('weight',1)) for child in direct)
            for child in direct:
                visit(child,usable*max(1,child.get('weight',1))/total)
        else:
            for child in direct:visit(child,width)
    available=max(0,plan.get('width',0)-2*plan.get('outer_margin',24))
    for root in children.get('root',[]):visit(root,available)
    return allocated


def _fit_row_weights(demands, width):
    """Find bounded integer weights that give every child its minimum width."""
    usable=max(0,width-8*len(demands))
    if not demands or sum(demands)>usable:return None
    for total in range(len(demands),12*len(demands)+1):
        weights=[max(1,int((demand*total+usable-1)//usable)) for demand in demands]
        if max(weights)<=12 and sum(weights)<=total:
            remaining=total-sum(weights)
            for index in sorted(range(len(demands)),key=lambda i:demands[i],reverse=True):
                add=min(12-weights[index],remaining)
                weights[index]+=add;remaining-=add
                if not remaining:break
            if not remaining:return weights
    return None


def _primary_work_area():
    """Return the primary Windows work area without opening a UI."""
    try:
        class Rect(ctypes.Structure):
            _fields_=[('left',ctypes.c_long),('top',ctypes.c_long),
                      ('right',ctypes.c_long),('bottom',ctypes.c_long)]
        rect=Rect()
        if not ctypes.windll.user32.SystemParametersInfoW(48,0,ctypes.byref(rect),0):
            return None
        return rect.right-rect.left,rect.bottom-rect.top
    except (AttributeError,OSError):return None


def _wrap_leaf_rows(before, after, max_width, instruction):
    """Wrap overflowing field rows while preserving leaf identity and order."""
    if not (permits_width_growth(instruction) and requests_compaction(instruction)):
        return after
    result=copy.deepcopy(after)
    result['width']=int(max_width)
    result['outer_margin']=0
    for _ in range(min(32,max(6,len(result.get('nodes',[]))))):
        result=_balance_new_rows(before,result)
        allocated=_allocated_widths(result);widths=_intrinsic_widths(result)
        split=False
        for row in list(result['nodes']):
            if row.get('kind')!='row':continue
            direct=[node for node in result['nodes'] if node.get('parent')==row['id']]
            if len(direct)<2 or any(node.get('kind') not in ('field','password') for node in direct):
                continue
            available=allocated.get(row['id'],0);chunks=[];current=[];used=0
            demands=[max(1,widths.get(child['id'],_leaf_minimum_width(child)))
                     for child in direct]
            if any(allocated.get(child['id'],0)+0.5<demand
                   for child,demand in zip(direct,demands)):
                fitted=_fit_row_weights(demands,available)
                if fitted and fitted!=[child.get('weight',1) for child in direct]:
                    for child,weight in zip(direct,fitted):child['weight']=weight
                    split=True;break
            required=sum(max(1,widths.get(child['id'],_leaf_minimum_width(child)))
                         for child in direct)+8*len(direct)
            if required<=available:continue
            for child in direct:
                demand=max(1,widths.get(child['id'],_leaf_minimum_width(child)))
                extra=demand+8
                if current and used+extra>available:
                    chunks.append(current);current=[];used=0
                current.append(child);used+=extra
            if current:chunks.append(current)
            if len(chunks)<2:continue
            existing={node['id'] for node in result['nodes']};parent=row['parent']
            for chunk in chunks[1:]:
                ident=next('n'+str(i) for i in range(1,1000) if 'n'+str(i) not in existing)
                existing.add(ident)
                insertion=result['nodes'].index(chunk[0])
                result['nodes'].insert(
                    insertion,dict(id=ident,parent=parent,kind='row',text='',weight=1))
                for child in chunk:child['parent']=ident
            split=True;break
        if not split:break
    result=_balance_new_rows(before,result);validate(result)
    return result


def _require_no_new_horizontal_compression(before, after, instruction=''):
    """Fit an explicitly permitted width growth or reject new clipping."""
    baseline=_compressed_leaf_ids(before)
    introduced=_compressed_leaf_ids(after)-baseline
    if not introduced:return after
    if permits_width_growth(instruction):
        low=max(1,int(after.get('width',0)));high=low
        while high<4096:
            high=min(4096,max(high+1,high*2))
            probe=copy.deepcopy(after);probe['width']=high
            if not (_compressed_leaf_ids(probe)-baseline):break
        else:probe=None
        if probe is not None and not (_compressed_leaf_ids(probe)-baseline):
            while low+1<high:
                middle=(low+high)//2;candidate=copy.deepcopy(after);candidate['width']=middle
                if _compressed_leaf_ids(candidate)-baseline:low=middle
                else:high=middle
            result=copy.deepcopy(after);result['width']=high
            return result
        raise ValueError(
            'La redistribución requiere un ancho superior al límite verificable de AVC. '
            'Permití reducir los controles o conservá las secciones en filas.')
    by_id={node['id']:node for node in after.get('nodes',[])}
    labels=[str(by_id[ident].get('text') or ident) for ident in sorted(introduced)]
    sample=', '.join(labels[:4])+('…' if len(labels)>4 else '')
    raise ValueError(
        'La redistribución solicitada no cabe en el ancho conservado sin comprimir '
        'controles de una sola línea ('+sample+'). Permití aumentar el ancho de la '
        'ventana, reducir el tamaño de los controles o conservar esas secciones en filas.'
    )


def _requires_general_editor(exc):
    """Only known catalog capability limits may trigger the broader editor."""
    message = str(exc)
    return (message.startswith('Edición visual no soportada:')
            or message in STRUCTURAL_LIMIT_ERRORS)


def _instruction_requires_general_editor(instruction):
    """Recognize capabilities absent from the catalog, not project-specific layouts."""
    text = ''.join(c for c in unicodedata.normalize('NFD', instruction.lower())
                   if not unicodedata.combining(c))
    scrolling = re.search(r'\b(?:desplaz\w*|scroll\w*)\b', text)
    central = re.search(r'\b(?:zona|region|area)\s+(?:central|del centro)\b|\bcentro\b', text)
    persistent = re.search(
        r'\b(?:mant\w*|dej\w*|conserv\w*)\b[^.\n]{0,100}'
        r'\b(?:visible\w*|fij\w*)\b[^.\n]{0,100}'
        r'\b(?:cabecera|encabezado|header|pie|footer|boton\w*)\b', text)
    if not persistent:
        persistent = re.search(
            r'\b(?:cabecera|encabezado|header|pie|footer|boton\w*)\b[^.\n]{0,100}'
            r'\b(?:visible\w*|fij\w*)\b', text)
    return bool(scrolling and central and persistent)


def _independent_scroll_fallback(plan, instruction):
    """Resolve an explicit persistent-footer request from the typed hierarchy."""
    if not _instruction_requires_general_editor(instruction):
        return None
    nodes=plan.get('nodes',[]);by_id={n['id']:n for n in nodes}
    children={n['id']:[] for n in nodes}
    for n in nodes:
        if n.get('parent') in children:children[n['parent']].append(n)

    def button_subtree(node):
        direct=children.get(node['id'],[])
        if not direct:return node.get('kind')=='button'
        return all(button_subtree(child) for child in direct)

    candidates=[]
    for container in nodes:
        if container.get('kind')!='column':continue
        direct=children.get(container['id'],[]);fixed=[]
        for child in reversed(direct):
            if button_subtree(child):fixed.append(child['id'])
            else:break
        fixed.reverse()
        if fixed and len(fixed)<len(direct):candidates.append((container['id'],fixed))
    if len(candidates)!=1:return None
    result=copy.deepcopy(plan)
    result['independent_scroll']={'container':candidates[0][0],
                                  'fixed_children':candidates[0][1]}
    return result


def _relayout_fallback(plan, instruction):
    """Group one unambiguous run of sibling cards without asking the model to guess ids."""
    if not requests_relayout(instruction):return None
    text=''.join(c for c in unicodedata.normalize('NFD',instruction.lower())
                 if not unicodedata.combining(c))
    central=bool(re.search(r'\b(?:centro|central)\b',text))
    preferred=(plan.get('independent_scroll') or {}).get('container') if central else None
    parents=[preferred] if preferred else [node['id'] for node in plan.get('nodes',[])
                                            if node.get('kind')=='column']
    groups=[]
    for parent in parents:
        if not parent:continue
        direct=[node for node in plan.get('nodes',[]) if node.get('parent')==parent]
        run=[]
        for node in direct+[{'kind':None}]:
            if node.get('kind')=='card':run.append(node['id'])
            else:
                if len(run)>=2:groups.append(run)
                run=[]
    if len(groups)!=1:return None
    operations=[{'kind':'row','ids':groups[0],'value':'Stretch'}]
    if requests_compaction(instruction) and plan.get('density')!='tight':
        operations.insert(0,{'kind':'set_density','value':'tight'})
    edits={'supported':True,'reason':'','operations':operations}
    result=apply_edits(plan,respect_instruction(plan,edits,instruction))
    return _balance_new_rows(plan,result)


def _require_visible_catalog(plan, capture):
    """Reject a no-scroll preview when its real window cannot fit the monitor."""
    if plan.get('window_fit') != 'content_height':
        return
    limits = capture.get('observation', {}).get('capture_constraints', {})
    if limits.get('fits_work_area') is False:
        window = limits.get('window_size_px', ['?', '?'])
        work = limits.get('work_area_size_px', ['?', '?'])
        raise ValueError(
            'El candidato necesita una ventana de '
            f'{window[0]}×{window[1]} px y el área útil del monitor es '
            f'{work[0]}×{work[1]} px. No es posible mostrar todo sin '
            'desplazamiento ni reducción. Permití desplazamiento, ajuste '
            'proporcional o una compactación mayor.'
        )


def _density_fallback(plan, instruction):
    """Repair a harmless no-op at the layout-token level, never per control."""
    text=''.join(c for c in unicodedata.normalize('NFD',instruction.lower())
                 if not unicodedata.combining(c))
    current=plan.get('density','compact')
    if re.search(r'\b(compact|reduc|achic|menos espacio)\w*\b',text):
        target='compact' if current=='comfortable' else 'tight'
    elif re.search(r'\b(ampli|aument)\w*\b[^.\n]*\b(espacio|separacion|aire)\w*\b',text):
        target='comfortable'
    else:
        return None
    fit=bool(re.search(r'\b(sin (?:desplazamiento|scroll)|todo\w* visible)\b',text))
    if target==current and (not fit or plan.get('viewport_fit')=='fit'):return None
    result=copy.deepcopy(plan);result['density']=target
    if fit:result['viewport_fit']='fit'
    return result


def edit_plan(api, project, instruction, model, folder, image=None, vision_model=None, previous=None):
    from code_preview import project_fingerprint, save_preview
    if previous:
        raise ValueError('Ya existe una vista previa. Aplicala o descartala antes de preparar otra.')
    folder.mkdir(parents=True,exist_ok=True)
    marker=project/'.avc_catalog.json'
    marker_before=marker.read_bytes()
    plan=read_owned(project)
    if 'density' not in plan:
        plan=copy.deepcopy(plan)
        roots=[n for n in plan['nodes'] if n['parent']=='root']
        plan['density']='compact' if len(roots)==1 and roots[0]['kind']=='column' else 'comfortable'
    input_hashes=project_fingerprint(project)
    baseline=WpfAdapter(api,project,model,{'enabled':False}).sources()
    api.log('--- Edición estructurada del catálogo en copia; el proyecto original sigue intacto ---')
    candidate_plan=_relayout_fallback(plan,instruction)
    if candidate_plan is not None:
        api.log('AVC redistribuyó una única secuencia inequívoca de secciones hermanas '
                'mediante la jerarquía tipada del catálogo.')
    else:
        candidate_plan=_independent_scroll_fallback(plan,instruction)
    try:
        if candidate_plan is not None:
            if not requests_relayout(instruction):
                api.log('AVC resolvió la región central desplazable desde la jerarquía tipada; '
                        'cabecera, menú y acciones quedan fuera del desplazamiento.')
        else:
            candidate_plan=edit_existing(api,plan,instruction,image,
                                         vision_model if image and vision_model else model,folder)
    except ValueError as exc:
        if _requires_general_editor(exc):
            api.log('El cambio requiere reorganizar la estructura fuera de las operaciones del catálogo; '
                    'AVC continúa con el editor general en copia.')
            raise StructuredEditUnsupported(str(exc)) from exc
        candidate_plan=_density_fallback(plan,instruction) if str(exc)=='La interpretación no produjo cambios' else None
        if candidate_plan is None:raise
        api.log('La propuesta estructurada repetía la densidad vigente; AVC aplicó el siguiente nivel general de compactación.')
    work_area=_primary_work_area()
    if work_area:
        adjusted=_wrap_leaf_rows(plan,candidate_plan,work_area[0],instruction)
        if adjusted!=candidate_plan:
            api.log('AVC ajustó filas de campos y proporciones internas al ancho útil '
                    'del monitor antes de compilar la vista previa.')
        candidate_plan=adjusted
    candidate_plan=_require_no_new_horizontal_compression(plan,candidate_plan,instruction)
    review=review_candidate(api,plan,candidate_plan,instruction,folder,attempt=1)
    if not review['approved']:
        feedback=feedback_text(review)
        api.log('LocalAI rechazó el primer candidato por cobertura incompleta. '
                'Ollama realizará un único intento de reparación.')
        partial_plan=candidate_plan
        candidate_plan=edit_existing(
            api,partial_plan,instruction,image,vision_model if image and vision_model else model,folder,
            reviewer_feedback=feedback,response_name='image_edit_response_retry.json')
        review=review_candidate(api,plan,candidate_plan,instruction,folder,attempt=2)
        if not review['approved']:
            raise ValueError('Revisión dual rechazó el candidato después del único reintento: '+
                             feedback_text(review))
    api.log('Revisión dual aprobada: comprobaciones estructurales y LocalAI coinciden.')
    if project_fingerprint(project)!=input_hashes or marker.read_bytes()!=marker_before:
        raise ValueError('El proyecto cambió durante la planificación')
    base_dir=folder/'base';candidate_dir=folder/'candidate'
    ignore=shutil.ignore_patterns('.git','.venv','bin','obj','avc_publish','recovery','.aider*')
    shutil.copytree(project,base_dir,ignore=ignore)
    shutil.copytree(project,candidate_dir,ignore=ignore)
    base=WpfAdapter(api,base_dir,model,{'enabled':False}).build_capture()
    if not base['ok']:raise ValueError('No se pudo verificar la base: '+base['detail'])
    files=render(candidate_plan)
    source_bytes={name:(project/name).read_bytes() for name in files}
    changed={name:text.encode('utf8') for name,text in files.items()
             if text.encode('utf8')!=source_bytes[name]}
    if not changed:raise ValueError('La edición estructurada no produjo cambios')
    for name,raw in changed.items():(candidate_dir/name).write_bytes(raw)
    candidate=WpfAdapter(api,candidate_dir,model,{'enabled':True}).build_capture()
    if not candidate['ok']:raise ValueError('Candidato estructurado rechazado: '+candidate['detail'])
    preview=dict(kind='catalog',model=model,folder=folder,
        baseline=baseline,marker_before=marker_before,source_bytes=source_bytes,
        changed=changed,instruction=instruction,before=base['shot'],after=candidate['shot'],
        input_hashes=input_hashes,plan=candidate_plan,dual_review=review)
    (folder/'plan_candidate.json').write_text(json.dumps(candidate_plan,ensure_ascii=False,indent=2),encoding='utf8')
    (folder/'result.png').write_bytes(candidate['shot'])
    try:
        _require_visible_catalog(candidate_plan,candidate)
    except ValueError as exc:
        preview.update(blocked=True,block_reason=str(exc))
        save_preview(api,project,preview)
        raise
    save_preview(api,project,preview)
    api.remember_visual_capture('antes',base['shot']);api.remember_visual_capture('despues',candidate['shot'])
    with api.STATE_LOCK:
        api.STATE['verification']={'estado':'no_verificable','pipeline':True,'compilado':True,
            'preview':True,'nota':'Vista previa estructurada compilada en copia; el catálogo y el proyecto siguen intactos.'}
    return 'Vista previa estructurada lista; el proyecto sigue intacto. Escribí aplicalo, descartalo o pedí otro ajuste.'


def apply_catalog_candidate(api, project, candidate):
    from code_preview import project_fingerprint
    marker=project/'.avc_catalog.json'
    adapter=WpfAdapter(api,project,candidate['model'],{'enabled':False})
    if project_fingerprint(project)!=candidate['input_hashes']:
        raise ValueError('El proyecto cambió; vista previa no aplicada')
    if marker.read_bytes()!=candidate['marker_before'] or adapter.sources()!=candidate['baseline']:
        raise ValueError('La base cambió; vista previa no aplicada')
    written=[]
    try:
        for name,raw in candidate['changed'].items():
            (project/name).write_bytes(raw);written.append(name)
        adapter.criteria={'enabled':True}
        current=adapter.build_capture()
        if not current['ok']:raise ValueError('Falló la verificación final: '+current['detail'])
        _require_visible_catalog(candidate['plan'],current)
        save_owned(project,candidate['plan'])
    except Exception:
        for name in written:
            path=project/name
            if path.is_file() and path.read_bytes()==candidate['changed'][name]:
                path.write_bytes(candidate['source_bytes'][name])
        if marker.exists():marker.write_bytes(candidate['marker_before'])
        raise
    api.remember_visual_capture('despues',current['shot'])
    (candidate['folder']/'applied.png').write_bytes(current['shot'])
    api.audit('catalog_plan_applied',files=list(candidate['changed']),evidence=str(candidate['folder']),exe=current['exe'])
    return 'Vista previa estructurada aplicada y publicada: '+current['exe']

"""Fail-closed semantic review for staged source-code candidates."""
import difflib
import json
import re
import unicodedata
import xml.etree.ElementTree as ET

from catalog_dual_review import (LOCALAI_MODEL, LocalAIReviewError,
                                 _call_localai, _json_content)


MAX_DIFF_CHARS = 28000
MAX_ADJUDICATION_CHARS = 46000
REVIEW_KEYS = {'approved': bool, 'initial_satisfies': bool,
               'candidate_satisfies': bool, 'reason': str,
               'missing_intents': list, 'contradictions': list}

THEME_VISUAL_ATTRIBUTES={'Background','Foreground','BorderBrush','BorderThickness',
                         'Fill','Stroke','Opacity','CornerRadius','FontFamily',
                         'FontWeight','FontStyle','TextDecorations'}
NAMED_COLORS={'black':'#000000','white':'#FFFFFF','lightblue':'#ADD8E6',
              'lavender':'#E6E6FA','gray':'#808080','grey':'#808080',
              'darkgray':'#A9A9A9','darkgrey':'#A9A9A9','green':'#008000',
              'red':'#FF0000','blue':'#0000FF'}


def _plain_text(value):
    return ''.join(character for character in unicodedata.normalize('NFD', value.lower())
                   if unicodedata.category(character) != 'Mn')


def _color_rgb(value):
    value=value.strip()
    if value.lower() == 'transparent':return None
    value=NAMED_COLORS.get(value.lower(),value)
    if not value.startswith('#'):return None
    code=value[1:]
    if len(code) in (3,4):code=''.join(character*2 for character in code)
    if len(code)==8:code=code[2:]
    if len(code)!=6 or not re.fullmatch(r'[0-9a-fA-F]{6}',code):return None
    return tuple(int(code[index:index+2],16) for index in (0,2,4))


def _luminance(value):
    rgb=_color_rgb(value)
    if rgb is None:return None
    channels=[]
    for component in rgb:
        level=component/255
        channels.append(level/12.92 if level<=0.04045 else ((level+0.055)/1.055)**2.4)
    return 0.2126*channels[0]+0.7152*channels[1]+0.0722*channels[2]


def _local_name(name):
    return name.rsplit('}',1)[-1]


def _resolve_color(value,resources):
    match=re.fullmatch(r'\{StaticResource\s+([^}]+)\}',value.strip())
    return resources.get(match.group(1),value) if match else value


def _color_resources(sources):
    resources={}
    for name,text in sources.items():
        if not str(name).lower().endswith('.xaml'):continue
        try:root=ET.fromstring(text)
        except ET.ParseError:continue
        for node in root.iter():
            if _local_name(node.tag)!='SolidColorBrush':continue
            key=next((value for attribute,value in node.attrib.items()
                      if _local_name(attribute)=='Key'),None)
            if key and node.get('Color'):
                resources[key]=node.get('Color')
    return resources


def _element_inventory(text,resources):
    root=ET.fromstring(text)
    inventory={};visual=[]
    def visit(node,path,inherited_background=None):
        tag=_local_name(node.tag)
        background=node.get('Background')
        for child in node:
            if _local_name(child.tag).endswith('.Background'):
                brush=next(iter(child),None)
                if brush is not None:
                    if brush.get('Color'):background=brush.get('Color')
                    elif _local_name(brush.tag)=='StaticResource' and brush.get('ResourceKey'):
                        background=resources.get(brush.get('ResourceKey'))
        if background:background=_resolve_color(background,resources)
        active_background=background or inherited_background
        attrs={_local_name(key):value for key,value in node.attrib.items()}
        inventory[path]=(tag,attrs)
        if background:
            visual.append(dict(path=path,kind='surface',property='Background',
                               value=background,background=background))
        for prop in ('Foreground','Stroke','Fill'):
            value=node.get(prop)
            if value:
                value=_resolve_color(value,resources)
                visual.append(dict(path=path,kind='ink',property=prop,value=value,
                                   background=active_background))
        counts={}
        for child in node:
            child_tag=_local_name(child.tag)
            if '.' in child_tag:continue
            counts[child_tag]=counts.get(child_tag,0)+1
            visit(child,f'{path}/{child_tag}[{counts[child_tag]}]',active_background)
    visit(root,f'/{_local_name(root.tag)}[1]')
    return inventory,visual


def _dark_theme_state(sources):
    inventories={};visual=[];unknown=[]
    resources=_color_resources(sources)
    for name,text in sources.items():
        if not str(name).lower().endswith('.xaml'):continue
        try:inventory,items=_element_inventory(text,resources)
        except ET.ParseError as exc:
            return None,[f'{name}: XML inválido: {exc}'],{},[]
        inventories[name]=inventory
        for item in items:
            item=dict(item,file=name);visual.append(item)
    issues=[]
    for item in visual:
        level=_luminance(item['value'])
        label=f'{item["file"]} {item["path"]} {item["property"]}={item["value"]}'
        if level is None:
            unknown.append(label);continue
        if item['kind']=='surface' and level>0.55:
            issues.append(label+' permanece como superficie clara')
        if item['kind']=='ink' and level<0.35:
            issues.append(label+' permanece oscuro para una superficie de tema oscuro')
        elif item['kind']=='ink' and item['background']:
            background=_luminance(item['background'])
            if background is None:continue
            contrast=(max(level,background)+0.05)/(min(level,background)+0.05)
            if contrast<3:
                issues.append(label+f' tiene contraste {contrast:.2f}:1 con '
                              f'Background={item["background"]}')
    return not issues and not unknown,issues,inventories,unknown


def deterministic_dark_theme_fact(before, after, instruction):
    """Measure global dark-theme coverage and protected XAML structure generically."""
    plain=_plain_text(instruction)
    if not ('tema oscuro' in plain or 'modo oscuro' in plain or 'dark theme' in plain):
        return None
    initial,initial_issues,before_inventory,initial_unknown=_dark_theme_state(before)
    candidate,candidate_issues,after_inventory,candidate_unknown=_dark_theme_state(after)
    invariant_issues=[]
    for name in sorted(set(before_inventory)|set(after_inventory)):
        old=before_inventory.get(name,{});new=after_inventory.get(name,{})
        if set(old)!=set(new):
            invariant_issues.append(f'{name}: cambió la estructura o cantidad de elementos')
            continue
        for path in old:
            old_tag,old_attrs=old[path];new_tag,new_attrs=new[path]
            if old_tag!=new_tag:
                invariant_issues.append(f'{name} {path}: cambió {old_tag} por {new_tag}')
                continue
            keys=(set(old_attrs)|set(new_attrs))-THEME_VISUAL_ATTRIBUTES
            for key in sorted(keys):
                if old_attrs.get(key)!=new_attrs.get(key):
                    invariant_issues.append(
                        f'{name} {path} {key}: {old_attrs.get(key)!r} → {new_attrs.get(key)!r}')
    if any(before.get(name)!=after.get(name) for name in set(before)|set(after)
           if not str(name).lower().endswith('.xaml')):
        invariant_issues.append('Cambió código no XAML durante una edición exclusivamente visual')
    candidate=bool(candidate and not invariant_issues)
    approved=bool(candidate and not initial)
    missing=list(candidate_issues)+[item+' no se pudo medir' for item in candidate_unknown]
    contradictions=invariant_issues
    if approved:
        reason=('Todas las superficies explícitas son oscuras, los pares de color medibles '
                'alcanzan contraste mínimo 3:1 y la estructura protegida no cambió.')
    elif initial:
        reason='La base ya acreditaba un tema oscuro completo y el candidato no era necesario.'
    else:
        reason='La medición de paleta o la conservación estructural aún no cumplen el pedido.'
    return {'approved':approved,'initial_satisfies':bool(initial),
            'candidate_satisfies':candidate,'reason':reason,
            'missing_intents':missing,'contradictions':contradictions,
            'fact':{'kind':'dark_theme','base_issues':initial_issues,
                    'base_unknown':initial_unknown,'candidate_unknown':candidate_unknown}}


def _explicit_xaml_intent(instruction):
    """Extract only a literal, directly measurable XAML property request."""
    text = _plain_text(instruction)
    if 'horizontalalignment' not in text and 'aline' not in text:
        return None
    directions = (
        (r'\b(derecha|right)\b', 'Right'),
        (r'\b(izquierda|left)\b', 'Left'),
        (r'\b(centrad[oa]?|center(?:ed)?)\b', 'Center'),
    )
    found = [value for pattern, value in directions if re.search(pattern, text)]
    if len(found) != 1:
        return None
    return {'property': 'HorizontalAlignment', 'expected': found[0],
            'allowed': ('Left', 'Center', 'Right', 'Stretch')}


def _single_exact_property_change(before, after, intent):
    """Prove that the complete source set differs by one XAML value only."""
    if set(before) != set(after):
        return None
    changed = [name for name in before if before[name] != after[name]]
    if len(changed) != 1 or not str(changed[0]).lower().endswith('.xaml'):
        return None
    name = changed[0]
    old_text, new_text = before[name], after[name]
    allowed = '|'.join(re.escape(value) for value in intent['allowed'])
    pattern = re.compile(
        rf'\b{re.escape(intent["property"])}\s*=\s*(?P<quote>["\'])(?P<value>{allowed})(?P=quote)')
    matches = []
    for match in pattern.finditer(old_text):
        old_value = match.group('value')
        for new_value in intent['allowed']:
            if new_value == old_value:
                continue
            proposed = (old_text[:match.start('value')] + new_value +
                        old_text[match.end('value'):])
            if proposed == new_text:
                matches.append({'file': str(name), 'property': intent['property'],
                                'before': old_value, 'after': new_value,
                                'expected': intent['expected']})
    return matches[0] if len(matches) == 1 else None


def deterministic_xaml_fact(before, after, instruction):
    """Return an authoritative fact only for an exact, explicit property edit."""
    intent = _explicit_xaml_intent(instruction)
    if not intent:
        return None
    change = _single_exact_property_change(before, after, intent)
    if not change:
        return None
    initial = change['before'] == change['expected']
    candidate = change['after'] == change['expected']
    approved = candidate and not initial
    if approved:
        reason = ('El conjunto completo de fuentes solo cambia '
                  f'{change["property"]} de {change["before"]} a '
                  f'{change["after"]}, exactamente como se pidió.')
        contradictions = []
    elif initial:
        reason = ('La base ya tenía '
                  f'{change["property"]}={change["expected"]} y el candidato lo aleja del pedido.')
        contradictions = [f'{change["property"]} cambia en sentido contrario al pedido']
    else:
        reason = (f'El candidato deja {change["property"]}={change["after"]}; '
                  f'el pedido exige {change["expected"]}.')
        contradictions = [f'{change["property"]} no alcanza el valor solicitado']
    return {'approved': approved, 'initial_satisfies': initial,
            'candidate_satisfies': candidate, 'reason': reason,
            'missing_intents': [] if candidate else [f'{change["property"]}={change["expected"]}'],
            'contradictions': contradictions, 'fact': change}


def deterministic_initial_satisfaction(sources, instruction):
    """Prove a request is already satisfied without generating a candidate.

    This is deliberately one-sided: inconclusive or negative evidence returns None
    and the normal model/review pipeline remains authoritative.
    """
    dark=deterministic_dark_theme_fact(sources,sources,instruction)
    if dark and dark['initial_satisfies']:
        return dict(dark,decision_source='deterministic_dark_theme_preflight')
    intent=_explicit_xaml_intent(instruction)
    if not intent:return None
    matches=[]
    for name,text in sources.items():
        if not str(name).lower().endswith('.xaml'):continue
        try:root=ET.fromstring(text)
        except ET.ParseError:return None
        for node in root.iter():
            value=node.get(intent['property'])
            if value in intent['allowed']:
                matches.append(dict(file=str(name),tag=_local_name(node.tag),value=value,
                                    text=node.get('Text') or node.get('Content') or ''))
    if len(matches)!=1 or matches[0]['value']!=intent['expected']:return None
    plain=_plain_text(instruction);tag=matches[0]['tag']
    target_kinds=[]
    if 'titul' in plain:target_kinds=['TextBlock','Label']
    elif 'boton' in plain or 'button' in plain:target_kinds=['Button']
    elif any(word in plain for word in ('campo','textbox','entrada')):
        target_kinds=['TextBox','PasswordBox','ComboBox']
    elif 'horizontalalignment' not in plain:
        return None
    if target_kinds and tag not in target_kinds:return None
    match=matches[0]
    reason=(f'La única propiedad {intent["property"]} explícita y compatible del proyecto '
            f'pertenece a {tag} y ya vale {intent["expected"]}.')
    return {'approved':False,'initial_satisfies':True,'candidate_satisfies':True,
            'reason':reason,'missing_intents':[],'contradictions':[],
            'decision_source':'deterministic_xaml_preflight',
            'fact':dict(kind='initial_alignment',**match,expected=intent['expected'])}


def source_diff(before, after):
    """Describe only changed source lines, bounded for the independent reviewer."""
    chunks=[]
    for name in sorted(set(before) | set(after)):
        old=before.get(name,'').splitlines(keepends=True)
        new=after.get(name,'').splitlines(keepends=True)
        if old==new:continue
        chunks.extend(difflib.unified_diff(old,new,fromfile='base/'+name,
                                           tofile='candidate/'+name,n=4))
    text=''.join(chunks)
    if len(text)>MAX_DIFF_CHARS:
        raise LocalAIReviewError('El diff es demasiado grande para una revisión semántica confiable')
    return text


def _changed_base_sources(before, after):
    """Provide the recheck with actual BASE sources, not an inferred description."""
    chunks=[]
    for name in sorted(set(before) | set(after)):
        if before.get(name,'') == after.get(name,''):
            continue
        chunks.append(f'--- BASE SOURCE: {name} ---\n{before.get(name, "")}\n')
    text=''.join(chunks)
    if len(text)>MAX_DIFF_CHARS:
        raise LocalAIReviewError(
            'La fuente base es demasiado grande para confirmar que ya cumple el pedido')
    return text


def _changed_sources(before, after, label):
    """Expose literal changed files to the adjudicator, within its context budget."""
    chunks=[]
    source=before if label == 'BASE' else after
    for name in sorted(set(before) | set(after)):
        if before.get(name,'') == after.get(name,''):
            continue
        chunks.append(f'--- {label} SOURCE: {name} ---\n{source.get(name, "")}\n')
    return ''.join(chunks)


def _review_verdict(raw):
    content=raw.get('choices',[{}])[0].get('message',{}).get('content','')
    verdict=_json_content(content)
    if not isinstance(verdict,dict) or any(type(verdict.get(key)) is not kind
                                           for key,kind in REVIEW_KEYS.items()):
        raise LocalAIReviewError('LocalAI devolvió un veredicto semántico incompleto')
    return verdict


def _ollama_verdict(raw):
    content=raw.get('message',{}).get('content','')
    verdict=_json_content(content)
    if not isinstance(verdict,dict) or any(type(verdict.get(key)) is not kind
                                           for key,kind in REVIEW_KEYS.items()):
        raise LocalAIReviewError('Ollama devolvió una adjudicación semántica incompleta')
    if not verdict['candidate_satisfies'] and not (
            verdict['missing_intents'] or verdict['contradictions']):
        raise LocalAIReviewError(
            'Ollama rechazó el candidato sin identificar una corrección concreta')
    return verdict


def _adjudicate_with_ollama(api, model, before, after, instruction, diff,
                            localai_verdict, caller=None):
    """Resolve a demonstrably unreliable LocalAI no-op claim with concrete evidence."""
    sources=_changed_sources(before,after,'BASE')+_changed_sources(before,after,'CANDIDATE')
    if len(sources)+len(diff)>MAX_ADJUDICATION_CHARS:
        raise LocalAIReviewError(
            'El contexto es demasiado grande para adjudicar el desacuerdo entre revisores')
    prompt=(
        'Actuás como adjudicador técnico de una edición WPF. LocalAI afirmó que la BASE '
        'ya cumplía el pedido, pero no aportó evidencia literal suficiente. Revisá desde cero '
        'la BASE, el CANDIDATO y el diff. No confíes en el dictamen previo. Separá las '
        'mutaciones pedidas de las propiedades que deben conservarse. Si rechazás el '
        'candidato, missing_intents o contradictions debe enumerar al menos una corrección '
        'concreta indicando elemento, propiedad y valor observado. No uses frases genéricas. '
        'Los colores hexadecimales son valores WPF válidos: evaluá su luminosidad y contraste, '
        'no su nombre. No afirmes cambios ausentes del diff. Respondé solo JSON exacto con '
        '{"approved":boolean,"initial_satisfies":boolean,'
        '"candidate_satisfies":boolean,"reason":string,'
        '"missing_intents":[string],"contradictions":[string]}.\n'
        'PEDIDO:\n'+instruction+'\n'+sources+'DIFF BASE→CANDIDATO:\n'+diff+
        '\nDICTAMEN LOCALAI CUESTIONADO:\n'+
        json.dumps(localai_verdict,ensure_ascii=False))
    schema={'type':'object','additionalProperties':False,
            'required':list(REVIEW_KEYS),
            'properties':{
                'approved':{'type':'boolean'},
                'initial_satisfies':{'type':'boolean'},
                'candidate_satisfies':{'type':'boolean'},
                'reason':{'type':'string'},
                'missing_intents':{'type':'array','items':{'type':'string'}},
                'contradictions':{'type':'array','items':{'type':'string'}}}}
    payload={'model':model,'messages':[{'role':'user','content':prompt}],
             'stream':False,'format':schema,
             'options':{'temperature':0,'num_predict':1000,'num_ctx':16384}}
    call=caller or (lambda request: api.post_ollama(
        '/api/chat',request,timeout=180))
    raw=call(payload)
    return payload,raw,_ollama_verdict(raw)


def review_code_candidate(api, before, after, instruction, folder, attempt=1,
                          caller=None, ollama_model=None, adjudicator=None):
    """Require an independent verdict before a compiled candidate can be previewed."""
    diff=source_diff(before,after)
    if not diff:raise LocalAIReviewError('El candidato no contiene cambios para revisar')
    prompt=(
        'Sos el segundo revisor independiente de una edición de código de interfaz. '
        'Compará literalmente el pedido con el diff entre BASE y CANDIDATO. '
        'No evalúes si compila: eso ya se comprobó. Determiná por separado si la BASE '
        'ya satisfacía todos los objetivos explícitos y si el CANDIDATO los satisface. '
        'Separá primero MUTACIONES SOLICITADAS de INVARIANTES A CONSERVAR. Una propiedad '
        'modificada tal como pide una mutación es evidencia favorable, nunca una contradicción. '
        'Un invariante solo se viola si el diff muestra que cambió; la ausencia de cambios lo '
        'conserva. Un cambio opuesto, un objetivo omitido o alterar un invariante obliga '
        'approved=false. initial_satisfies describe exclusivamente la BASE antes del diff, no '
        'si era una interfaz válida. Si la base ya cumplía todos los objetivos, usá '
        'initial_satisfies=true y approved=false. No afirmes cambios de layout, dimensiones, '
        'eventos o funcionamiento si no aparecen en el diff. '
        'Respondé únicamente JSON exacto con: '
        '{"approved":boolean,"initial_satisfies":boolean,"candidate_satisfies":boolean,'
        '"reason":string,"missing_intents":[string],"contradictions":[string]}.\n'
        'PEDIDO:\n'+instruction+'\nDIFF BASE→CANDIDATO:\n'+diff)
    payload={'model':LOCALAI_MODEL,'messages':[{'role':'user','content':prompt}],
             'temperature':0,'max_tokens':420,'stream':False,
             'response_format':{'type':'json_object'}}
    call=caller or _call_localai
    try:
        raw=call(payload)
        verdict=_review_verdict(raw)
        first_verdict=dict(verdict)
        confirmation_payload=None;confirmation_raw=None
        fact=(deterministic_xaml_fact(before,after,instruction) or
              deterministic_dark_theme_fact(before,after,instruction))
        rechecked=False
        if verdict['initial_satisfies'] and not fact:
            confirmation_prompt=(
                'REVISIÓN DE CONSISTENCIA OBLIGATORIA. El primer dictamen afirmó que la BASE '
                'ya cumplía el pedido. Revaluá desde cero usando la fuente BASE literal y el '
                'diff. Distinguí MUTACIONES SOLICITADAS de INVARIANTES A CONSERVAR. Cambiar '
                'una propiedad que el pedido ordena cambiar no viola la conservación; conservar '
                'layout, dimensiones, contenido, eventos o lógica solo falla si el diff muestra '
                'un cambio concreto de ese aspecto. No confundas una BASE válida con una BASE '
                'que ya posee el resultado solicitado. No inventes cambios ausentes del diff. '
                'Respondé únicamente el mismo JSON exacto: '
                '{"approved":boolean,"initial_satisfies":boolean,'
                '"candidate_satisfies":boolean,"reason":string,'
                '"missing_intents":[string],"contradictions":[string]}.\n'
                'PEDIDO:\n'+instruction+'\n'+_changed_base_sources(before,after)+
                'DIFF BASE→CANDIDATO:\n'+diff+'\nPRIMER DICTAMEN A REVISAR:\n'+
                json.dumps(first_verdict,ensure_ascii=False))
            confirmation_payload=dict(payload)
            confirmation_payload['messages']=[{'role':'user','content':confirmation_prompt}]
            confirmation_raw=call(confirmation_payload)
            verdict=_review_verdict(confirmation_raw)
            rechecked=True
        localai_final_verdict=dict(verdict)
        arbitration_payload=None;arbitration_raw=None;adjudicated=False
        initial_guard_overrode=bool(rechecked and verdict['initial_satisfies'] and not fact)
        if initial_guard_overrode and (ollama_model or adjudicator):
            arbitration_payload,arbitration_raw,arbitrated=_adjudicate_with_ollama(
                api,ollama_model or 'adjudicator',before,after,instruction,diff,
                verdict,caller=adjudicator)
            if not arbitrated['initial_satisfies']:
                verdict=arbitrated
                initial_guard_overrode=False
                adjudicated=True
        if initial_guard_overrode:
            verdict=dict(verdict)
            verdict.update(approved=False,initial_satisfies=False,
                candidate_satisfies=False,
                reason=('LocalAI no pudo acreditar de forma comprobable que la base ya '
                        'cumplía el pedido. AVC no cerrará la tarea como realizada.'),
                missing_intents=['Completar y acreditar todos los cambios solicitados'],
                contradictions=[])
        localai_approved=(localai_final_verdict['approved'] and
                        localai_final_verdict['candidate_satisfies'] and
                        not localai_final_verdict['initial_satisfies'] and
                        not localai_final_verdict['contradictions'] and
                        not localai_final_verdict['missing_intents'])
        local_contradictions=[str(item) for item in verdict['contradictions']]
        local_missing=[str(item) for item in verdict['missing_intents']]
        reviewed_approved=(verdict['approved'] and verdict['candidate_satisfies'] and
                        not verdict['initial_satisfies'] and
                        not local_contradictions and not local_missing)
        if fact:
            approved=fact['approved'];initial=fact['initial_satisfies']
            candidate=fact['candidate_satisfies'];reason=fact['reason']
            contradictions=fact['contradictions'];missing=fact['missing_intents']
            decision_source=('deterministic_dark_theme'
                if fact.get('fact',{}).get('kind')=='dark_theme'
                else 'deterministic_xaml')
        else:
            approved=reviewed_approved;initial=verdict['initial_satisfies']
            candidate=verdict['candidate_satisfies'];reason=verdict['reason']
            contradictions=local_contradictions;missing=local_missing
            decision_source=('ollama_adjudication' if adjudicated else
                             'localai_initial_guard' if initial_guard_overrode else
                             'localai_recheck' if rechecked else 'localai')
        reviewer_disagreed=bool((fact and (
            approved != reviewed_approved or initial != verdict['initial_satisfies'] or
            candidate != verdict['candidate_satisfies'])) or (rechecked and any(
            first_verdict[key] != verdict[key]
            for key in ('approved','initial_satisfies','candidate_satisfies'))) or
            initial_guard_overrode)
        result={'approved':approved,'initial_satisfies':initial,
                'candidate_satisfies':candidate,
                'reason':reason,'missing_intents':missing,
                'contradictions':contradictions,'diff':diff,'model':LOCALAI_MODEL,
                'attempt':attempt,'decision_source':decision_source,
                'deterministic_fact':fact,'reviewer_disagreed':reviewer_disagreed,
                'localai_verdict':dict(localai_final_verdict,
                                       approved_normalized=localai_approved),
                'adjudication_verdict':dict(verdict,
                    approved_normalized=reviewed_approved) if adjudicated else None,
                'localai_first_verdict':first_verdict,'recheck_performed':rechecked,
                'localai_final_verdict':localai_final_verdict,
                'adjudication_performed':adjudicated,
                'initial_guard_overrode':initial_guard_overrode}
        (folder/f'code_semantic_review_{attempt}.json').write_text(
            json.dumps({'request':payload,'response':raw,
                        'confirmation_request':confirmation_payload,
                        'confirmation_response':confirmation_raw,
                        'adjudication_request':arbitration_payload,
                        'adjudication_response':arbitration_raw,'result':result},
                       ensure_ascii=False,indent=2),encoding='utf8')
        if hasattr(api,'audit'):api.audit('code_semantic_review',attempt=attempt,
            approved=approved,initial_satisfies=initial,
            candidate_satisfies=candidate,decision_source=decision_source,
            reviewer_disagreed=reviewer_disagreed,
            contradictions=contradictions,missing_intents=missing)
        return result
    except Exception as exc:
        error=exc if isinstance(exc,LocalAIReviewError) else LocalAIReviewError(
            'LocalAI no está disponible para revisar el candidato')
        (folder/f'code_semantic_review_{attempt}.json').write_text(
            json.dumps({'error':str(error),'diff':diff},ensure_ascii=False,indent=2),
            encoding='utf8')
        if hasattr(api,'audit'):api.audit('code_semantic_review_unavailable',
                                          attempt=attempt,error=str(error))
        raise error from exc


def feedback_text(review):
    return ('Revisión semántica rechazada. Motivo: '+review['reason']+
            '. Objetivos omitidos: '+(', '.join(review['missing_intents']) or 'ninguno')+
            '. Contradicciones: '+(', '.join(review['contradictions']) or 'ninguna')+
            '. Corregí el candidato actual para cumplir literalmente el pedido y conservar '
            'todo lo no solicitado.')

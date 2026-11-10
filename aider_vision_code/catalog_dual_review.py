"""Independent semantic review plus deterministic intent coverage for catalog edits."""
import json
import os
import re
import unicodedata
import urllib.request

from catalog_instruction_intents import (permits_width_growth, requests_no_scroll,
                                         requests_preserve_sizes, requests_relayout)


LOCALAI = os.environ.get('LOCALAI_URL', 'http://127.0.0.1:8080').rstrip('/')
LOCALAI_MODEL = os.environ.get('LOCALAI_MODEL', 'qwen2.5-coder-3b')


class LocalAIReviewError(ValueError):
    """The independent reviewer could not produce a trustworthy verdict."""


def _plain(text):
    return ''.join(c for c in unicodedata.normalize('NFD', text.lower())
                   if not unicodedata.combining(c))


def _node_map(plan):
    return {node['id']: node for node in plan.get('nodes', [])}


def plan_diff(before, after):
    """Return a bounded, framework-neutral description of the proposed changes."""
    top=[]
    for key in sorted((set(before) | set(after)) - {'nodes'}):
        if before.get(key) != after.get(key):
            top.append({'property':key,'before':before.get(key),'after':after.get(key)})
    old=_node_map(before);new=_node_map(after);nodes=[]
    for ident in sorted(set(old) | set(new)):
        if ident not in old:
            nodes.append({'id':ident,'change':'added','after':new[ident]})
        elif ident not in new:
            nodes.append({'id':ident,'change':'removed','before':old[ident]})
        else:
            changes={key:{'before':old[ident].get(key),'after':new[ident].get(key)}
                     for key in sorted(set(old[ident]) | set(new[ident]))
                     if old[ident].get(key) != new[ident].get(key)}
            if changes:nodes.append({'id':ident,'change':'modified','properties':changes})
    return {'plan':top,'nodes':nodes}


def intent_checks(before, after, instruction):
    """Measure explicit high-risk intents; absence of a check is never success evidence."""
    text=_plain(instruction);checks=[];old=_node_map(before);new=_node_map(after)
    no_scroll=requests_no_scroll(text)
    if no_scroll:
        fit=(after.get('viewport_fit')=='fit' or after.get('window_fit')=='content_height')
        cleared=after.get('independent_scroll') is None
        checks.append({'intent':'no_scroll','passed':fit and cleared,
            'evidence':{'viewport_fit':after.get('viewport_fit','scroll'),
                        'window_fit':after.get('window_fit','fixed'),
                        'independent_scroll':after.get('independent_scroll')}})
    central_growth=bool(re.search(
        r'\b(?:ampli|aument|ensanch|agrand)\w*\b[^.\n]{0,80}\b(?:centro|central)\b'
        r'|\b(?:centro|central)\b[^.\n]{0,80}\b(?:ampli|aument|ensanch|agrand)\w*\b',text))
    if central_growth:
        target=(before.get('independent_scroll') or {}).get('container')
        previous=old.get(target,{}).get('weight') if target else None
        current=new.get(target,{}).get('weight') if target else None
        checks.append({'intent':'grow_central_region','passed':(
            type(previous) is int and type(current) is int and current>previous),
            'evidence':{'target':target,'before':previous,'after':current}})
    if requests_relayout(text):
        reflows=[]
        old_order={}
        for node in before.get('nodes',[]):old_order.setdefault(node.get('parent'),[]).append(node['id'])
        for ident,node in new.items():
            if ident in old or node.get('kind') not in ('row','column'):continue
            children=[item['id'] for item in after.get('nodes',[])
                      if item.get('parent')==ident and item['id'] in old]
            if len(children)<2:continue
            parents={old[child].get('parent') for child in children}
            if len(parents)!=1:continue
            siblings=old_order.get(next(iter(parents)),[])
            positions=sorted(siblings.index(child) for child in children if child in siblings)
            if len(positions)==len(children) and positions==list(range(positions[0],positions[0]+len(positions))):
                reflows.append({'container':ident,'kind':node.get('kind'),'children':children})
        checks.append({'intent':'reflow_sections','passed':bool(reflows),
                       'evidence':{'new_groups':reflows}})
    preserve_all=bool(re.search(r'\bconserv\w*\b[^.\n]{0,55}\b(?:todo|resto|demas)\b',text))
    requests={
        'preserve_controls':preserve_all or bool(re.search(r'\b(?:no\s+cambi\w*|conserv\w*)\b[^.\n]{0,70}\bcontrol\w*\b',text)),
        'preserve_texts':preserve_all or bool(re.search(r'\b(?:no\s+cambi\w*|conserv\w*)\b[^.\n]{0,70}\btexto\w*\b',text)),
        'preserve_colors':preserve_all or bool(re.search(r'\b(?:no\s+cambi\w*|conserv\w*)\b[^.\n]{0,70}\bcolor\w*\b',text)),
        'preserve_functions':preserve_all or bool(re.search(r'\b(?:no\s+cambi\w*|conserv\w*)\b[^.\n]{0,70}\b(?:funcion\w*|evento\w*|logica)\b',text)),
        'preserve_sizes':preserve_all or requests_preserve_sizes(text),
    }
    common=set(old)&set(new)
    old_controls={ident:node.get('kind') for ident,node in old.items()
                  if node.get('kind') not in ('row','column')}
    new_controls={ident:node.get('kind') for ident,node in new.items()
                  if node.get('kind') not in ('row','column')}
    evidence={
        'preserve_controls':old_controls==new_controls,
        'preserve_texts':all(old[i].get('text')==new[i].get('text') for i in common),
        'preserve_colors':before.get('background')==after.get('background') and all(
            old[i].get('background')==new[i].get('background') and
            old[i].get('foreground')==new[i].get('foreground') for i in common),
        'preserve_functions':all(old[i].get('action')==new[i].get('action') for i in common),
        'preserve_sizes':(
            before.get('width')==after.get('width') or
            (permits_width_growth(text) and after.get('width',0)>=before.get('width',0))) and all(
            old[i].get('size')==new[i].get('size') for i in common) and not (
                before.get('viewport_fit','scroll')!='fit' and after.get('viewport_fit')=='fit'),
    }
    for intent,requested in requests.items():
        if requested:checks.append({'intent':intent,'passed':evidence[intent]})
    return checks


def _json_content(text):
    value=text.strip()
    if value.startswith('```'):
        value=re.sub(r'^```(?:json)?\s*|\s*```$','',value,flags=re.I)
    try:return json.loads(value)
    except json.JSONDecodeError as exc:raise LocalAIReviewError('LocalAI no devolvió JSON válido') from exc


def _call_localai(payload, timeout=150):
    request=urllib.request.Request(
        LOCALAI+'/v1/chat/completions',data=json.dumps(payload).encode('utf8'),
        headers={'Content-Type':'application/json'})
    try:
        with urllib.request.urlopen(request,timeout=timeout) as response:return json.load(response)
    except Exception as exc:
        raise LocalAIReviewError('LocalAI no está disponible para revisar el candidato') from exc


def review_candidate(api, before, after, instruction, folder, attempt=1, caller=None):
    """Require deterministic coverage and an independent LocalAI approval."""
    checks=intent_checks(before,after,instruction);diff=plan_diff(before,after)
    failed_before=[check['intent'] for check in checks if not check['passed']]
    passed_before=[check['intent'] for check in checks if check['passed']]
    prompt=(
        'Sos el segundo revisor independiente de una edición de interfaz. Evaluá SOLO la '
        'instrucción y la evidencia estructurada. No inventes elementos visuales. Las '
        'comprobaciones deterministas son autoritativas: si alguna tiene passed=false, '
        'approved debe ser false. Nunca afirmes que falló una comprobación marcada passed=true. '
        'missing_intents solo puede contener objetivos explícitos que no estén ya representados '
        'por una comprobación. También rechazá objetivos omitidos o cambios '
        'que contradigan conservar el resto. Respondé únicamente JSON con esta forma exacta: '
        '{"approved":boolean,"reason":string,"missing_intents":[string],'
        '"contradictions":[string]}.\nINSTRUCCION:\n'+instruction+'\nEVIDENCIA:\n'+
        json.dumps({'failed_checks':failed_before,'passed_checks':passed_before,
                    'checks':checks,'diff':diff},ensure_ascii=False))
    payload={'model':LOCALAI_MODEL,'messages':[{'role':'user','content':prompt}],
             'temperature':0,'max_tokens':420,'stream':False,
             'response_format':{'type':'json_object'}}
    call=caller or _call_localai
    try:
        raw=call(payload)
        content=raw.get('choices',[{}])[0].get('message',{}).get('content','')
        verdict=_json_content(content)
        if (not isinstance(verdict,dict) or type(verdict.get('approved')) is not bool or
                not isinstance(verdict.get('reason'),str) or
                not isinstance(verdict.get('missing_intents'),list) or
                not isinstance(verdict.get('contradictions'),list)):
            raise LocalAIReviewError('LocalAI devolvió un veredicto incompleto')
        failed=[check['intent'] for check in checks if not check['passed']]
        approved=verdict['approved'] and not failed
        result={'approved':approved,'attempt':attempt,'checks':checks,'diff':diff,
                'localai':verdict,'failed_checks':failed,'model':LOCALAI_MODEL}
        (folder/f'localai_review_{attempt}.json').write_text(
            json.dumps({'request':payload,'response':raw,'result':result},
                       ensure_ascii=False,indent=2),encoding='utf8')
        if hasattr(api,'audit'):api.audit('catalog_dual_review',attempt=attempt,
            approved=approved,failed_checks=failed,localai_approved=verdict['approved'])
        return result
    except LocalAIReviewError as exc:
        (folder/f'localai_review_{attempt}.json').write_text(
            json.dumps({'error':str(exc),'checks':checks,'diff':diff},
                       ensure_ascii=False,indent=2),encoding='utf8')
        if hasattr(api,'audit'):api.audit('catalog_dual_review_unavailable',attempt=attempt,error=str(exc))
        raise


def feedback_text(review):
    missing=list(dict.fromkeys(review.get('failed_checks',[])+
        [str(item) for item in review.get('localai',{}).get('missing_intents',[])]))
    contradictions=[str(item) for item in review.get('localai',{}).get('contradictions',[])]
    remediation=[]
    if 'no_scroll' in review.get('failed_checks',[]):
        preserve_size=any(check.get('intent')=='preserve_sizes' for check in review.get('checks',[]))
        if preserve_size:
            remediation.append(
                'Para no_scroll conservando tamaños, emití clear_independent_scroll y '
                'fit_window con el margen exterior actual; no uses set_viewport_fit=fit porque escala todo.')
        else:
            remediation.append(
                'Para no_scroll el plan final debe quitar independent_scroll y usar '
                'set_viewport_fit=fit; emití clear_independent_scroll y set_viewport_fit=fit.')
    if 'preserve_sizes' in review.get('failed_checks',[]):
        remediation.append(
            'No uses set_viewport_fit=fit: conserva tamaños mediante fit_window y elimina el scroll independiente.')
    if 'grow_central_region' in review.get('failed_checks',[]):
        remediation.append(
            'Para grow_central_region aumentá el weight del contenedor central respecto del estado actual.')
    if 'reflow_sections' in review.get('failed_checks',[]):
        remediation.append(
            'Para reflow_sections usá row o column con los ids de al menos dos secciones hermanas '
            'consecutivas; cambiar densidad, ancho o weights no redistribuye la jerarquía.')
    return ('La revisión independiente rechazó el candidato. Objetivos faltantes: '+
            (', '.join(missing) or 'no especificados')+'. Contradicciones: '+
            (', '.join(contradictions) or 'ninguna informada')+'. Motivo: '+
            review.get('localai',{}).get('reason','Sin motivo')+'. '+(' '.join(remediation))+
            ' Corregí todos los objetivos '
            'en una única propuesta; no repitas el candidato rechazado.')

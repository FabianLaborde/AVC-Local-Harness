"""Persistent measured Ollama usage, scoped by project. Never estimates Aider tokens."""
from __future__ import annotations
import json
import re
import threading
import time
from pathlib import Path

LOCK = threading.Lock()
FIELDS = ('prompt_eval_count', 'eval_count', 'total_duration', 'prompt_eval_duration', 'eval_duration')


def record(path: Path, project: str, model: str, response: dict, seconds: float, source: str):
    if not isinstance(response, dict):
        return
    event = {'timestamp': time.time(), 'project': str(project or ''),
             'model': str(model or ''), 'source': str(source or 'ollama'),
             'elapsed_seconds': round(max(0., seconds), 3)}
    for key in FIELDS:
        value = response.get(key)
        event[key] = value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None
    path.parent.mkdir(parents=True, exist_ok=True)
    with LOCK, path.open('a', encoding='utf-8') as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + '\n')


def record_aider_report(path: Path, project: str, model: str, text: str):
    """Aider-reported usage; separate from measured Ollama API and potentially approximate."""
    matches=re.findall(r'(?im)^Tokens:\s*([0-9]+(?:\.[0-9]+)?[kKmM]?)\s+sent,\s*([0-9]+(?:\.[0-9]+)?[kKmM]?)\s+received\.',text or '')
    if not matches:
        return False
    def count(v):
        mult={'k':1000,'m':1000000}.get(v[-1].lower(),1)
        return round(float(v[:-1] if mult!=1 else v)*mult)
    input_tokens, output_tokens=matches[-1]
    event={'timestamp':time.time(),'kind':'aider_reported','project':str(project),
           'model':str(model),'input_tokens':count(input_tokens),'output_tokens':count(output_tokens),
           'approximate':any(x[-1].lower() in ('k','m') for x in (input_tokens,output_tokens))}
    path.parent.mkdir(parents=True,exist_ok=True)
    with LOCK,path.open('a',encoding='utf-8') as handle:
        handle.write(json.dumps(event,ensure_ascii=False)+'\n')
    return True


def summary(path: Path, project: str):
    totals = {'project': str(project or ''), 'input_tokens': 0, 'output_tokens': 0,
              'requests': 0, 'measured_requests': 0, 'duration_seconds': 0.,
              'job_seconds': 0., 'jobs': 0,
              'aider_tokens_measured': False, 'complete_token_total': False,
              'aider_reported_input_tokens':0,'aider_reported_output_tokens':0,
              'aider_reported_calls':0,'aider_reported_approximate':False,
              'by_model': {}}
    if not path.is_file() or not project:
        return totals
    with LOCK, path.open('r', encoding='utf-8') as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except (ValueError, TypeError):
                continue
            if row.get('project') != project:
                continue
            if row.get('kind') == 'job':
                totals['jobs'] += 1
                totals['job_seconds'] += max(0., float(row.get('job_seconds') or 0))
                continue
            if row.get('kind') == 'aider_reported':
                totals['aider_reported_calls'] += 1
                totals['aider_reported_input_tokens'] += max(0,int(row.get('input_tokens') or 0))
                totals['aider_reported_output_tokens'] += max(0,int(row.get('output_tokens') or 0))
                totals['aider_reported_approximate'] |= bool(row.get('approximate'))
                continue
            totals['requests'] += 1
            input_count, output_count = row.get('prompt_eval_count'), row.get('eval_count')
            model = str(row.get('model') or '?')
            group = totals['by_model'].setdefault(model, {'input_tokens': 0, 'output_tokens': 0, 'requests': 0})
            group['requests'] += 1
            if isinstance(input_count, int) and isinstance(output_count, int):
                totals['measured_requests'] += 1
                totals['input_tokens'] += input_count
                totals['output_tokens'] += output_count
                group['input_tokens'] += input_count
                group['output_tokens'] += output_count
            totals['duration_seconds'] += max(0., float(row.get('elapsed_seconds') or 0))
    totals['duration_seconds'] = round(totals['duration_seconds'], 1)
    totals['job_seconds'] = round(totals['job_seconds'], 1)
    return totals


def record_job(path: Path, project: str, seconds: float, status: str):
    event = {'timestamp': time.time(), 'kind': 'job', 'project': str(project),
             'job_seconds': round(max(0., seconds), 2), 'status': str(status)[:50]}
    path.parent.mkdir(parents=True, exist_ok=True)
    with LOCK, path.open('a', encoding='utf-8') as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + '\n')

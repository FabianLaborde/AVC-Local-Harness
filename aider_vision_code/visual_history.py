"""Bounded in-memory evidence: reference, baseline, last captured attempt, final."""
import base64
import copy


def empty_evidence(job_id=None):
    return {'job_id': job_id, 'antes': None, 'despues': None, 'referencia': None, 'referencia_origen': None,
            'intento': None, 'intento_numero': None, 'intento_report': None,
            'restaurado': False, 'restauracion_motivo': None, 'ultimo_fallo': None,
            'comparacion_referencia': None}


def image_url(raw):
    if not raw:
        return None
    mime = 'image/jpeg' if raw.startswith(b'\xff\xd8') else 'image/webp' if raw[:4] == b'RIFF' and raw[8:12] == b'WEBP' else 'image/png'
    return 'data:' + mime + ';base64,' + base64.b64encode(raw).decode('ascii')


def save_attempt(evidence, number, raw=None, report=None):
    if raw is not None:
        evidence.update(intento=image_url(raw), intento_numero=number, intento_report=None)
        if (evidence.get('ultimo_fallo') or {}).get('numero', 0) <= number:
            evidence['ultimo_fallo'] = None
    if report is not None and evidence.get('intento_numero') == number:
        evidence['intento_report'] = copy.deepcopy(report)

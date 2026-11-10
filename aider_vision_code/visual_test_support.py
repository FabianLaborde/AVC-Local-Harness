"""Deterministic model boundary for visual lifecycle tests; no Ollama requests."""
import base64
from io import BytesIO
import json
from pathlib import Path
import tempfile
from unittest.mock import patch
from PIL import Image
from visual_blueprint import parse_blueprint
import server


def plan_for(raw):
    with Image.open(BytesIO(raw)) as image:
        size = image.size
    return parse_blueprint(json.dumps(dict(canvas=dict(bounds=[0,0,1,1],background='white'),
        nodes=[dict(id='surface',kind='container',parent=None,bounds=[0,0,1,1],label='',fill='white')],
        rows=[],uncertainties=[])), raw, size)


def model_reply(model, images, prompt, purpose, stage, **kwargs):
    if stage == 'intent':
        return '{"scope":"partial","preserve":["Conservar la lógica"]}'
    if stage == 'contract':
        return json.dumps(dict(scope='partial',goals=[dict(id='g1',targets=['surface'],
            basis='request',expected='Aplicar el cambio solicitado')],preserve=['Conservar la lógica']))
    if stage == 'comparison':
        return json.dumps(dict(summary='Revisar el resultado',checks=[dict(goal_id='g1',state='observed',
            confidence='medium',evidence='Cambio visible según la respuesta simulada',suggestion='')]))
    if stage == 'confirm_difference':
        return '{"confirmed":false,"evidence":"No confirmado"}'
    raise AssertionError('Unexpected model stage: ' + stage)


def install_reference_mocks(case):
    root = Path(case.enterContext(tempfile.TemporaryDirectory()))
    case.enterContext(patch.object(server, 'ROOT', root))
    case.enterContext(patch.object(server, 'visual_specification',
        side_effect=lambda image, *a, **kw: plan_for(base64.b64decode(image))))
    case.vision = case.enterContext(patch.object(server, 'ask_vision', side_effect=model_reply))
    return root

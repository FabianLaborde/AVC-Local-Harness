"""Local, content-addressed references. Pixels and interpretation travel together.

Stored under AVC's ignored recovery directory, never inside the edited project.
A description alone is not a reference image.
"""
from dataclasses import dataclass
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import uuid

from PIL import Image


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


@dataclass(frozen=True)
class Reference:
    raw: bytes
    plan: dict
    origin: str

    @property
    def identity(self):
        return digest(self.raw)


class ReferenceStore:
    def __init__(self, root, project):
        self.project = Path(project).resolve()
        key = digest(os.path.normcase(str(self.project)).encode('utf-8'))
        self.folder = Path(root) / key

    @property
    def active(self):
        return self.folder / 'active.json'

    def validate(self, raw, plan):
        if len(raw) > 5_000_000:
            raise ValueError('Referencia demasiado grande')
        with Image.open(BytesIO(raw)) as image:
            if image.format not in {'PNG', 'JPEG', 'WEBP'} or image.width * image.height > 16_000_000:
                raise ValueError('Referencia no admitida')
            size = list(image.size)
            image.verify()
        if (not isinstance(plan, dict) or plan.get('schema') != 3 or
                plan.get('image_sha256') != digest(raw) or plan.get('image_size') != size or
                not isinstance(plan.get('nodes'), list) or not plan['nodes']):
            raise ValueError('La descripción no pertenece a esta imagen de referencia')

    def save(self, raw, plan, origin='adjunta'):
        self.validate(raw, plan)
        # The pointer is committed last. A failed interpretation never selects old pixels.
        identity = digest(raw)
        folder = self.folder / 'references' / identity
        folder.mkdir(parents=True, exist_ok=True)
        image_path = folder / 'original.image'
        if not image_path.exists() or digest(image_path.read_bytes()) != identity:
            temporary = folder / (uuid.uuid4().hex + '.tmp')
            temporary.write_bytes(raw)
            temporary.replace(image_path)
        write_json(folder / 'plan.json', plan)
        write_json(self.active, {'schema': 1, 'reference_sha256': identity})
        return Reference(raw, plan, origin)

    def load(self):
        if not self.active.exists():
            return None
        if self.active.stat().st_size > 4096:
            raise ValueError('Índice de referencia inválido')
        identity = json.loads(self.active.read_text(encoding='utf-8')).get('reference_sha256', '')
        if len(identity) != 64 or any(c not in '0123456789abcdef' for c in identity):
            raise ValueError('Identidad de referencia inválida')
        folder = self.folder / 'references' / identity
        if (folder / 'plan.json').stat().st_size > 100_000 or (folder / 'original.image').stat().st_size > 5_000_000:
            raise ValueError('Referencia guardada fuera de límites')
        raw = (folder / 'original.image').read_bytes()
        plan = json.loads((folder / 'plan.json').read_text(encoding='utf-8'))
        self.validate(raw, plan)
        if digest(raw) != identity:
            raise ValueError('La imagen guardada cambió; adjuntá la referencia de nuevo')
        return Reference(raw, plan, 'guardada')

    def migrate_legacy(self):
        """Only an exact hash match can recover an old image; no filename guessing."""
        path = self.project / '.avc_visual_spec.json'
        if not path.is_file() or path.is_symlink() or path.stat().st_size > 100_000:
            return None
        plan = json.loads(path.read_text(encoding='utf-8'))
        identity = plan.get('image_sha256') if isinstance(plan, dict) else None
        if not identity:
            return None
        candidates = (p for p in self.project.iterdir() if p.suffix.lower() in {'.png', '.jpg', '.jpeg', '.webp'})
        for number, candidate in enumerate(candidates):
            if number >= 30:
                break
            if candidate.is_symlink() or not candidate.is_file() or candidate.stat().st_size > 5_000_000:
                continue
            raw = candidate.read_bytes()
            if digest(raw) == identity:
                return self.save(raw, plan, 'recuperada_por_hash')
        return None


class RunEvidence:
    """One immutable image per role/attempt, linked to source and reference identities."""
    def __init__(self, store, instruction, reference, job_id):
        self.folder = store.folder / 'runs' / uuid.uuid4().hex
        self.folder.mkdir(parents=True)
        self.data = dict(schema=1, job_id=job_id, instruction=instruction,
                         reference_sha256=reference.identity if reference else None,
                         observations=[], stages=[], result=None)
        self.flush()

    def flush(self):
        write_json(self.folder / 'run.json', self.data)

    def stage(self, name, **fields):
        self.data['stages'].append(dict(stage=name, **fields))
        self.flush()

    def capture(self, role, raw, sources, attempt=0):
        name = f'{role}_{attempt}_{len(self.data["observations"])}.png'
        with Image.open(BytesIO(raw)) as image:
            image.convert('RGB').save(self.folder / name)
        metadata = dict(role=role, attempt=attempt, file=name,
                        image_sha256=digest((self.folder / name).read_bytes()),
                        captured_sha256=digest(raw), sources=sources,
                        source_sha256=digest(json.dumps(sources, sort_keys=True).encode('utf-8')),
                        reference_sha256=self.data['reference_sha256'])
        self.data['observations'].append(metadata)
        self.flush()
        return metadata

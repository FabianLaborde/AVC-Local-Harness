"""Framework-neutral candidate lineage for bounded iterative edits."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass


def _validate_sources(sources, label):
    if not isinstance(sources, dict) or not sources:
        raise ValueError(f'{label} inválido')
    for name, raw in sources.items():
        if not isinstance(name, str) or not name or not isinstance(raw, bytes):
            raise ValueError(f'{label} inválido')


def complete_snapshot(baseline, changes):
    """Return a complete source snapshot without mutating either input."""
    _validate_sources(baseline, 'Baseline')
    if not isinstance(changes, dict) or set(changes) - set(baseline):
        raise ValueError('El candidato contiene archivos fuera del baseline')
    if any(not isinstance(raw, bytes) for raw in changes.values()):
        raise ValueError('El candidato contiene fuentes inválidos')
    return {**baseline, **changes}


def changes_from_baseline(baseline, snapshot, editable):
    """Reduce a complete snapshot to editable files that differ from baseline."""
    complete = complete_snapshot(baseline, snapshot)
    allowed = set(editable)
    if not allowed or allowed - set(baseline):
        raise ValueError('Alcance editable inválido')
    changed = {}
    for name, raw in complete.items():
        if raw == baseline[name]:
            continue
        if name not in allowed:
            raise ValueError('El candidato modifica un archivo de solo lectura')
        changed[name] = raw
    if not changed:
        raise ValueError('El candidato no contiene cambios respecto del baseline')
    return changed


def compose_revision(baseline, current_changes, step_changes, editable):
    """Compose one incremental step and return the full delta against baseline."""
    current = complete_snapshot(baseline, current_changes)
    if not isinstance(step_changes, dict) or set(step_changes) - set(baseline):
        raise ValueError('La revisión contiene archivos fuera del baseline')
    if any(not isinstance(raw, bytes) for raw in step_changes.values()):
        raise ValueError('La revisión contiene fuentes inválidos')
    next_snapshot = {**current, **step_changes}
    if snapshot_hash(next_snapshot) == snapshot_hash(current):
        raise ValueError('La revisión repite exactamente el candidato vigente')
    return changes_from_baseline(baseline, next_snapshot, editable)


def snapshot_hash(snapshot):
    _validate_sources(snapshot, 'Snapshot')
    digest = hashlib.sha256()
    for name in sorted(snapshot):
        encoded = name.encode('utf-8')
        digest.update(len(encoded).to_bytes(8, 'big'))
        digest.update(encoded)
        digest.update(len(snapshot[name]).to_bytes(8, 'big'))
        digest.update(snapshot[name])
    return digest.hexdigest()


@dataclass(frozen=True)
class CandidateRevision:
    index: int
    label: str
    parent_hash: str
    content_hash: str
    changed_files: tuple[str, ...]


class CandidateLineage:
    """Bounded history: baseline protects; current candidate receives the next edit."""

    def __init__(self, baseline, editable, max_candidates=3):
        _validate_sources(baseline, 'Baseline')
        if type(max_candidates) is not int or max_candidates < 1:
            raise ValueError('Límite de candidatos inválido')
        self._baseline = dict(baseline)
        self._editable = tuple(dict.fromkeys(editable))
        if not self._editable or set(self._editable) - set(self._baseline):
            raise ValueError('Alcance editable inválido')
        self._max_candidates = max_candidates
        self._baseline_hash = snapshot_hash(self._baseline)
        self._current_changes = {}
        self._hashes = {self._baseline_hash}
        self._revisions = []

    @property
    def baseline(self):
        return dict(self._baseline)

    @property
    def current_changes(self):
        return dict(self._current_changes)

    @property
    def current_sources(self):
        return complete_snapshot(self._baseline, self._current_changes)

    @property
    def revisions(self):
        return tuple(self._revisions)

    def record(self, changes, label):
        if len(self._revisions) >= self._max_candidates:
            raise ValueError('Se agotó el límite de candidatos de esta tarea')
        snapshot = complete_snapshot(self._baseline, changes)
        normalized = changes_from_baseline(
            self._baseline, snapshot, self._editable)
        content_hash = snapshot_hash(snapshot)
        if content_hash in self._hashes:
            raise ValueError('El candidato repite un estado anterior de la tarea')
        parent_hash = (self._revisions[-1].content_hash
                       if self._revisions else self._baseline_hash)
        revision = CandidateRevision(
            index=len(self._revisions) + 1,
            label=str(label)[:80],
            parent_hash=parent_hash,
            content_hash=content_hash,
            changed_files=tuple(sorted(normalized)),
        )
        self._current_changes = normalized
        self._hashes.add(content_hash)
        self._revisions.append(revision)
        return revision

    def export(self):
        return {
            'schema_version': 1,
            'baseline_hash': self._baseline_hash,
            'max_candidates': self._max_candidates,
            'current_hash': (self._revisions[-1].content_hash
                             if self._revisions else self._baseline_hash),
            'revisions': [
                {
                    'index': item.index,
                    'label': item.label,
                    'parent_hash': item.parent_hash,
                    'content_hash': item.content_hash,
                    'changed_files': list(item.changed_files),
                }
                for item in self._revisions
            ],
        }

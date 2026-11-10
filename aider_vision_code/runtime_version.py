"""Read-only provenance; a running process must not claim newer on-disk code."""
import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess


def source_fingerprint(root):
    # Only deployed console sources, never conversations, projects or recovery data.
    root = Path(root)
    paths = list(root.glob('*.py')) + [root / 'version.json']
    for folder in ('web', 'prompts'):
        paths.extend(p for p in (root / folder).rglob('*') if p.is_file())
    digest = hashlib.sha256()
    for path in sorted(set(paths)):
        if path.name.startswith('test_'):
            continue
        digest.update(path.relative_to(root).as_posix().encode('utf-8') + b'\0')
        digest.update(path.read_bytes() + b'\0')
    return digest.hexdigest()


def git_value(root, *args):
    try:
        result = subprocess.run(['git', '-C', str(root), *args], capture_output=True,
                                timeout=3, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        return result.stdout.decode('utf-8', errors='replace').strip() if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


class RuntimeVersion:
    def __init__(self, root):
        self.root = Path(root).resolve()
        try:
            release = json.loads((self.root / 'version.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            release = {'version': 'desconocida', 'channel': 'sin manifiesto'}
        try:
            fingerprint = source_fingerprint(self.root)
            ui_sha = hashlib.sha256((self.root / 'web/index.html').read_bytes()).hexdigest()
        except OSError:
            fingerprint = ui_sha = None
        top = git_value(self.root, 'rev-parse', '--show-toplevel')
        own_repository = bool(top and Path(top).resolve() == self.root.parent)
        # An exported copy nested in recovery must not inherit its ancestor's Git identity.
        dirty = git_value(self.root, 'status', '--porcelain', '--untracked-files=normal') if own_repository else None
        self.started = {
            **release, 'commit': git_value(self.root, 'rev-parse', 'HEAD') if own_repository else None,
            'branch': git_value(self.root, 'branch', '--show-current') if own_repository else None,
            'worktree_modified': None if dirty is None else bool(dirty),
            'source_sha256': fingerprint, 'ui_sha256': ui_sha,
            'installation': str(self.root.parent), 'pid': os.getpid(),
            'started_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }

    def status(self):
        try:
            disk = source_fingerprint(self.root)
            changed = disk != self.started['source_sha256'] if self.started['source_sha256'] else None
        except OSError:
            disk, changed = None, None
        return {'running': dict(self.started), 'disk_sha256': disk,
                'restart_required': changed}

"""Deterministic materialization and UIA validation for preview launch contracts."""
import contextlib
import shutil
import sqlite3
import tempfile
import unicodedata
from pathlib import Path


class RuntimeProvider:
    """Deterministic boundary for one disposable runtime resource strategy."""

    name = ''
    resource_type = ''

    def normalize(self, project, item):
        raise NotImplementedError

    def materialize(self, project, item, destination_root):
        raise NotImplementedError


_PROVIDERS = {}


def register_provider(provider):
    """Register one explicit provider; duplicate names are never replaced silently."""
    if not isinstance(provider, RuntimeProvider) or not provider.name:
        raise ValueError('Proveedor runtime inválido')
    if provider.name in _PROVIDERS and _PROVIDERS[provider.name] is not provider:
        raise ValueError('Proveedor runtime duplicado: ' + provider.name)
    _PROVIDERS[provider.name] = provider


def provider_specs():
    return [dict(provider=item.name, resource_type=item.resource_type)
            for item in sorted(_PROVIDERS.values(), key=lambda value: value.name)]


def source_binding(project, environment_variable, source_relative):
    """Resolve one file binding using provider capabilities, never model preference."""
    source = _safe_source(project, source_relative)
    specific = [provider for provider in _PROVIDERS.values()
                if getattr(provider, 'suffixes', None)
                and source.suffix.casefold() in provider.suffixes]
    fallbacks = [provider for provider in _PROVIDERS.values()
                 if provider.resource_type == 'file'
                 and not getattr(provider, 'suffixes', None)]
    providers = specific or fallbacks
    accepted = []
    for provider in providers:
        item = dict(environment_variable=environment_variable,
                    source_relative=Path(source_relative).as_posix(),
                    provider=provider.name, resource_type=provider.resource_type)
        try:
            accepted.append(normalize_runtime_item(project, item))
        except ValueError:
            continue
    if len(accepted) != 1:
        raise ValueError('No existe un proveedor runtime inequívoco para: '
                         + str(source_relative))
    return accepted[0]


def normalize_runtime_item(project, item):
    if not isinstance(item, dict):
        raise ValueError('Cada recurso runtime debe ser un objeto')
    name = item.get('provider')
    if not isinstance(name, str) or not name:
        raise ValueError('Falta el proveedor del recurso runtime')
    provider = _PROVIDERS.get(name)
    if provider is None:
        raise ValueError('No hay un proveedor runtime instalado para: ' + name)
    resource_type = item.get('resource_type')
    if resource_type != provider.resource_type:
        raise ValueError('El proveedor ' + name + ' no admite el tipo ' + str(resource_type))
    normalized = provider.normalize(project, item)
    normalized['provider'] = provider.name
    normalized['resource_type'] = provider.resource_type
    return normalized


def _normalized(value):
    return ''.join(char for char in unicodedata.normalize('NFD', str(value).casefold())
                   if not unicodedata.combining(char))


def _safe_source(project, name):
    relative = Path(name)
    path = (project / relative).resolve()
    if (relative.is_absolute() or '..' in relative.parts
            or not path.is_relative_to(project.resolve()) or not path.is_file()
            or path.is_symlink()):
        raise ValueError('Recurso runtime fuera del proyecto: ' + str(name))
    return path


def _sqlite_snapshot(source, destination):
    origin = sqlite3.connect('file:' + source.as_posix() + '?mode=ro', uri=True)
    target = sqlite3.connect(str(destination))
    try:
        origin.backup(target)
    finally:
        target.close()
        origin.close()


class FileResourceProvider(RuntimeProvider):
    resource_type = 'file'
    maximum_bytes = 10_000_000

    def _normalize_file(self, project, item):
        source = item.get('source_relative')
        if not isinstance(source, str) or not source or len(source) > 500:
            raise ValueError('Ruta de runtime inválida')
        path = _safe_source(project, source)
        if path.stat().st_size > self.maximum_bytes:
            raise ValueError(source + ' supera el límite del proveedor ' + self.name)
        return path, dict(environment_variable=item['environment_variable'],
                          source_relative=Path(source).as_posix())

    def materialize(self, project, item, destination_root):
        source = _safe_source(project, item['source_relative'])
        destination = destination_root / source.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        return str(destination)


class BoundedFileCopyProvider(FileResourceProvider):
    name = 'bounded_file_copy'

    def normalize(self, project, item):
        _, normalized = self._normalize_file(project, item)
        return normalized


class SQLiteSnapshotProvider(FileResourceProvider):
    name = 'sqlite_snapshot'
    resource_type = 'sqlite'
    suffixes = {'.db', '.sqlite', '.sqlite3'}
    maximum_bytes = 100_000_000

    def normalize(self, project, item):
        path, normalized = self._normalize_file(project, item)
        if path.suffix.lower() not in self.suffixes:
            raise ValueError(item['source_relative'] + ' no es un archivo SQLite reconocido')
        return normalized

    def materialize(self, project, item, destination_root):
        source = _safe_source(project, item['source_relative'])
        destination = destination_root / source.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        _sqlite_snapshot(source, destination)
        return str(destination)


class LiteralEnvironmentProvider(RuntimeProvider):
    """Expose only inert boolean/test switches, never credentials or arbitrary values."""

    name = 'literal_environment'
    resource_type = 'literal'
    allowed_values = {'0', '1', 'false', 'true'}

    def normalize(self, project, item):
        value = item.get('value')
        if not isinstance(value, str) or value.casefold() not in self.allowed_values:
            raise ValueError('literal_environment solo admite valores booleanos seguros')
        return dict(environment_variable=item['environment_variable'],
                    value=value.casefold())

    def materialize(self, project, item, destination_root):
        return item['value']


register_provider(BoundedFileCopyProvider())
register_provider(SQLiteSnapshotProvider())
register_provider(LiteralEnvironmentProvider())


@contextlib.contextmanager
def materialized_environment(project, contract):
    """Yield environment variables pointing only at disposable runtime copies."""
    with tempfile.TemporaryDirectory(prefix='avc_preview_runtime_') as temporary:
        root = Path(temporary)
        environment = {}
        for index, item in enumerate(contract.get('runtime', [])):
            normalized = normalize_runtime_item(project, item)
            provider = _PROVIDERS[normalized['provider']]
            environment[normalized['environment_variable']] = provider.materialize(
                project, normalized, root / str(index))
        yield environment


def inspection_summary(inspection):
    uia = inspection.get('uia') or {}
    controls = [dict(type=item.get('type', ''), name=item.get('name', ''),
                     rect_client_px=item.get('rect_client_px'))
                for item in uia.get('controls', [])[:120]]
    return dict(status=inspection.get('status'), window=inspection.get('window', {}),
                controls=controls, detail=uia.get('detail', ''))


def target_errors(inspection, contract):
    target = contract.get('target') or {}
    window = inspection.get('window') or {}
    controls = (inspection.get('uia') or {}).get('controls', [])
    title = _normalized(window.get('title', ''))
    names = [_normalized(item.get('name', '')) for item in controls if item.get('name')]
    all_text = '\n'.join([title, *names])
    errors = []
    generic_error_titles = ('error', 'fatal', 'exception', 'fallo')
    generic_error_text = ('no se pudo iniciar', 'failed to start', 'unhandled exception',
                          'fatal error', 'error de inicio')
    if any(token in title for token in generic_error_titles):
        errors.append('La ventana capturada es un diálogo de error: ' + window.get('title', ''))
    if any(token in all_text for token in generic_error_text):
        errors.append('La ventana contiene un error de arranque.')
    for value in target.get('forbidden_titles', []):
        if _normalized(value) in title:
            errors.append('Título prohibido observado: ' + value)
    for value in target.get('forbidden_text', []):
        if _normalized(value) in all_text:
            errors.append('Texto prohibido observado: ' + value)
    missing = [value for value in target.get('required_controls', [])
               if not any(_normalized(value) in name for name in names)]
    if missing:
        errors.append('Controles objetivo no localizados: ' + ', '.join(missing))
    hints = target.get('title_hints', [])
    if hints and not target.get('required_controls') and not any(
            _normalized(value) in title for value in hints):
        errors.append('El título no coincide con la pantalla objetivo: ' + ', '.join(hints))
    return list(dict.fromkeys(errors))

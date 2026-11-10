"""Common, bounded technical evidence for source editors.

Providers inspect local metadata and source text. They never import project
modules, execute project interpreters, restore packages or run build tools.
"""
import ast
from importlib import metadata
import json
from pathlib import Path
import re
import sys

from sdk_evidence import request_evidence as request_dotnet_evidence


SCHEMA_VERSION = 1


def _provider_result(name, ecosystem, status, entries=None, **extra):
    if status not in {'available', 'partial', 'unavailable'}:
        raise ValueError('Estado de evidencia técnica inválido')
    result = dict(schema_version=SCHEMA_VERSION, kind='technical_evidence_provider',
                  provider=name, ecosystem=ecosystem, status=status,
                  entries=list(entries or []))
    result.update(extra)
    if len(json.dumps(result, ensure_ascii=False)) > 24000:
        raise ValueError('Evidencia técnica demasiado extensa')
    return result


class DotNetEvidenceProvider:
    name = 'dotnet_reference_xml'
    ecosystem = 'dotnet'

    def supports(self, sources, project=None):
        return any(name.lower().endswith(('.cs', '.xaml')) for name in sources)

    def collect(self, api, instruction, sources, model, draft=None, project=None):
        result = request_dotnet_evidence(api, instruction, sources, model, draft=draft)
        if result is None:
            return None, None
        evidence, response = result
        normalized = dict(evidence)
        normalized.update(kind='technical_evidence_provider', ecosystem=self.ecosystem)
        return normalized, response


def _python_signature(node):
    args = []
    positional = list(node.args.posonlyargs) + list(node.args.args)
    defaults = [None] * (len(positional) - len(node.args.defaults)) + list(node.args.defaults)
    for argument, default in zip(positional, defaults):
        args.append(argument.arg + ('=?' if default is not None else ''))
    if node.args.vararg:
        args.append('*' + node.args.vararg.arg)
    for argument, default in zip(node.args.kwonlyargs, node.args.kw_defaults):
        args.append(argument.arg + ('=?' if default is not None else ''))
    if node.args.kwarg:
        args.append('**' + node.args.kwarg.arg)
    return '(' + ', '.join(args) + ')'


def _distribution_modules(distribution):
    text = distribution.read_text('top_level.txt') or ''
    roots = {line.strip() for line in text.splitlines()
             if re.fullmatch(r'[A-Za-z_]\w*', line.strip())}
    if roots:
        return roots
    for item in distribution.files or ():
        first = str(item).replace('\\', '/').split('/', 1)[0]
        if first.endswith(('.dist-info', '.data')):
            continue
        if first.endswith('.py'):
            first = first[:-3]
        if re.fullmatch(r'[A-Za-z_]\w*', first):
            roots.add(first)
    return roots


def _installed_python_modules(project):
    if project is None:
        return {}
    root = Path(project)
    candidates = [root/'.venv'/'Lib'/'site-packages', root/'venv'/'Lib'/'site-packages']
    for environment in (root/'.venv', root/'venv'):
        candidates.extend(sorted(environment.glob('lib/python*/site-packages'))[:3])
    modules = {}
    for folder in candidates:
        if not folder.is_dir():
            continue
        for distribution in list(metadata.distributions(path=[str(folder)]))[:400]:
            package = distribution.metadata.get('Name') or 'unknown'
            version = distribution.version or 'unknown'
            for module in _distribution_modules(distribution):
                modules.setdefault(module, dict(package=package, version=version,
                                                path=str(folder.resolve())))
    return modules


def _declared_python_packages(project):
    if project is None:
        return set()
    root = Path(project)
    declared = set()
    for path in sorted(root.glob('requirements*.txt'))[:10]:
        if path.stat().st_size > 1_000_000:
            continue
        for line in path.read_text(encoding='utf8', errors='replace').splitlines():
            match = re.match(r'\s*([A-Za-z0-9_.-]+)', line)
            if match and not line.lstrip().startswith(('#', '-')):
                declared.add(re.sub(r'[-.]', '_', match.group(1)).lower())
    pyproject = root/'pyproject.toml'
    if pyproject.is_file() and pyproject.stat().st_size <= 1_000_000:
        try:
            import tomllib
            document = tomllib.loads(pyproject.read_text(encoding='utf8'))
            dependencies = document.get('project', {}).get('dependencies', [])
            for value in dependencies:
                match = re.match(r'\s*([A-Za-z0-9_.-]+)', value)
                if match:
                    declared.add(re.sub(r'[-.]', '_', match.group(1)).lower())
        except (OSError, ValueError):
            pass
    return declared


class PythonEvidenceProvider:
    name = 'python_static_metadata'
    ecosystem = 'python'

    def supports(self, sources, project=None):
        return any(name.lower().endswith('.py') for name in sources)

    def collect(self, api, instruction, sources, model, draft=None, project=None):
        imports, entries, parse_errors = set(), [], []
        local_roots = {Path(name).parts[0] for name in sources if Path(name).parts}
        local_roots.update(Path(name).stem for name in sources
                           if name.lower().endswith('.py') and '/' not in name.replace('\\', '/'))
        for name, raw in sources.items():
            if not name.lower().endswith('.py'):
                continue
            try:
                tree = ast.parse(raw.decode('utf-8-sig'), filename=name)
            except (SyntaxError, UnicodeError) as exc:
                parse_errors.append(f'{name}: {exc}')
                continue
            module = str(Path(name).with_suffix('')).replace('\\', '.').replace('/', '.')
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imports.update(alias.name.split('.', 1)[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                    imports.add(node.module.split('.', 1)[0])
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    symbol = module + '.' + node.name
                    entry = dict(symbol=symbol,
                                 kind='class' if isinstance(node, ast.ClassDef) else 'function',
                                 status='verified', source='static_ast', file=name,
                                 line=node.lineno)
                    if not isinstance(node, ast.ClassDef):
                        entry['signature'] = _python_signature(node)
                    entries.append(entry)
        installed = _installed_python_modules(project)
        declared = _declared_python_packages(project)
        stdlib = set(getattr(sys, 'stdlib_module_names', ())) | set(sys.builtin_module_names)
        unresolved = []
        for root in sorted(imports):
            if root in local_roots:
                info = dict(symbol=root, kind='module', status='verified', source='project_source')
            elif root in stdlib:
                info = dict(symbol=root, kind='module', status='verified', source='python_stdlib')
            elif root in installed:
                info = dict(symbol=root, kind='module', status='verified',
                            source='distribution_metadata', **installed[root])
            elif root.lower() in declared:
                info = dict(symbol=root, kind='module', status='declared',
                            source='project_dependency_manifest')
                unresolved.append(root)
            else:
                info = dict(symbol=root, kind='module', status='unresolved', source='static_ast')
                unresolved.append(root)
            entries.append(info)
        status = ('unavailable' if parse_errors and not entries else
                  'partial' if parse_errors or unresolved else 'available')
        return _provider_result(
            self.name, self.ecosystem, status, entries=entries[:160],
            provenance='AST de fuentes y metadatos locales; no se importaron módulos ni se ejecutó el proyecto.',
            unresolved_imports=unresolved, parse_errors=parse_errors,
            limitations=['La declaración de una dependencia no confirma que el módulo sea importable.',
                         'No se ejecutó Python ni código del proyecto.']), None


PROVIDERS = [DotNetEvidenceProvider(), PythonEvidenceProvider()]


def _aggregate(provider_results):
    if not provider_results:
        return None
    statuses = {item['status'] for item in provider_results}
    status = ('available' if statuses == {'available'} else
              'unavailable' if statuses == {'unavailable'} else 'partial')
    return dict(schema_version=SCHEMA_VERSION, kind='technical_evidence',
                provider='technical_evidence_registry', status=status,
                ecosystems=[item['ecosystem'] for item in provider_results],
                providers=provider_results,
                entries=[entry for item in provider_results for entry in item.get('entries', [])][:240],
                provenance='Registro común de proveedores técnicos locales y verificables.',
                limitations=['Cada proveedor acredita solo su ecosistema y su fuente declarada.'])


def request_evidence(api, instruction, original, model, draft=None, project=None):
    """Collect every applicable provider and return one technology-neutral contract."""
    results, responses = [], []
    for provider in PROVIDERS:
        if not provider.supports(original, project=project):
            continue
        evidence, response = provider.collect(api, instruction, original, model,
                                              draft=draft, project=project)
        if evidence is not None:
            results.append(evidence)
        if response is not None:
            responses.append(response)
    aggregate = _aggregate(results)
    return None if aggregate is None else (aggregate, responses[-1] if responses else None)


def provider_evidence(evidence, ecosystem):
    """Return applicable provider records, accepting the legacy .NET contract."""
    if not isinstance(evidence, dict):
        return []
    if evidence.get('kind') == 'technical_evidence':
        return [item for item in evidence.get('providers', [])
                if isinstance(item, dict) and item.get('ecosystem') == ecosystem]
    if (ecosystem == 'dotnet'
            and (evidence.get('provider') == 'dotnet_reference_xml'
                 or ('entries' in evidence and 'providers' not in evidence))):
        return [evidence]
    return []

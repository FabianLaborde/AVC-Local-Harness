"""Technology adapters: describe and validate, never edit or apply sources."""
import sys
from pathlib import Path
import xml.etree.ElementTree as ET


class DotnetAdapter:
    name = 'dotnet'
    technology = '.NET'
    validation_level = 'compilation'
    validation_executes_application = False
    validation_limitation = 'No comprueba apariencia, eventos ni acceso a datos. No se inició la aplicación.'

    def copy_scope(self, root, entry):
        """Follow literal build inputs; do not copy sibling applications/releases."""
        root = root.resolve()
        projects, files, parsed = set(), set(), set()
        config_names = ('Directory.Build.props', 'Directory.Build.targets',
                        'Directory.Packages.props', 'NuGet.Config', 'nuget.config', 'global.json')

        def local(base, value):
            path = (base / value.replace('\\', '/')).resolve()
            if any(token in value for token in ('$(', '@(', '*', '?')) or not path.is_relative_to(root):
                raise ValueError('Entrada de compilación no resuelta dentro del proyecto: ' + value)
            if not path.is_file():
                raise ValueError('Falta entrada de compilación: ' + value)
            return path

        def parse(path):
            if path in parsed:
                return
            parsed.add(path)
            files.add(path.relative_to(root).as_posix())
            for element in ET.parse(path).iter():
                tag = element.tag.split('}')[-1]
                if tag == 'ProjectReference':
                    visit(local(path.parent, element.get('Include', '')))
                elif tag == 'Import' and element.get('Project'):
                    parse(local(path.parent, element.get('Project')))
                elif tag in ('Compile', 'Content', 'EmbeddedResource', 'None', 'Reference'):
                    value = element.get('Include', '')
                    if value and ('..' in Path(value.replace('\\', '/')).parts or Path(value).is_absolute()):
                        files.add(local(path.parent, value).relative_to(root).as_posix())
                elif tag == 'HintPath' and element.text:
                    files.add(local(path.parent, element.text.strip()).relative_to(root).as_posix())

        def visit(path):
            name = path.relative_to(root).as_posix()
            if name in projects:
                return
            projects.add(name)
            parse(path)
            parent = path.parent
            while parent.is_relative_to(root):
                for config in config_names:
                    candidate = parent / config
                    if candidate.is_file():
                        files.add(candidate.relative_to(root).as_posix())
                        if candidate.suffix in ('.props', '.targets'):
                            parse(candidate)
                if parent == root:
                    break
                parent = parent.parent
        visit(root / entry)
        return dict(directories=sorted({Path(p).parent.as_posix() for p in projects}),
                    files=sorted(files), projects=sorted(projects))

    def detect(self, root, target):
        if Path(target).suffix.lower() not in {'.cs', '.xaml', '.vb', '.fs'}:
            return None
        parent = (root / target).parent
        while parent.is_relative_to(root):
            choices = list(parent.glob('*.*proj'))
            choices = [p for p in choices if p.suffix in {'.csproj', '.vbproj', '.fsproj'}]
            if len(choices) > 1:
                raise ValueError('Más de un proyecto junto al archivo; no se elige uno al azar')
            if choices:
                return choices[0].relative_to(root).as_posix()
            if parent == root:
                break
            parent = parent.parent
        return None

    def validate(self, root, entry, run):
        # Fail closed for project references which cannot be copied/resolved locally.
        seen = set()
        def references(path):
            if path in seen:
                return
            seen.add(path)
            for element in ET.parse(path).iter():
                if element.tag.split('}')[-1] != 'ProjectReference':
                    continue
                name = element.get('Include', '')
                child = (path.parent / name.replace('\\', '/')).resolve()
                if '$(' in name or not child.is_relative_to(root.resolve()) or not child.is_file():
                    raise ValueError('Referencia de proyecto no resuelta dentro de la copia: ' + name)
                references(child)
        references(root / entry)
        command = ['dotnet', 'build', entry, '-c', 'Release', '--nologo']
        code, output = run(command, root, timeout=300)
        artifacts = sorted(str(p) for p in (root / entry).parent.glob('bin/Release/**/*.exe')) if code == 0 else []
        return dict(adapter=self.name, passed=code == 0, level=self.validation_level,
                    summary='Compilación .NET ' + ('correcta' if code == 0 else 'fallida'),
                    command=command, exit_code=code, output=output, artifacts=artifacts,
                    limitation=self.validation_limitation)


class PythonAdapter:
    name = 'python'
    technology = 'Python'
    validation_level = 'syntax'
    validation_executes_application = False
    validation_limitation = 'No comprueba dependencias, ejecución, apariencia ni comportamiento.'

    def copy_scope(self, root, entry):
        # Until import resolution is implemented Python retains the selected root.
        return dict(directories=['.'], files=[], projects=[])

    def detect(self, root, target):
        return target if Path(target).suffix.lower() == '.py' else None

    def validate(self, root, entry, run):
        # Compile source in memory, without imports, application startup or bytecode writes.
        script = ("import pathlib, tokenize; "
                  "files=list(pathlib.Path('.').rglob('*.py')); "
                  "[(compile(tokenize.open(p).read(), str(p), 'exec')) for p in files]; "
                  "print('Sintaxis correcta:', len(files), 'archivos Python')")
        command = [sys.executable, '-I', '-S', '-c', script]
        code, output = run(command, root, timeout=60)
        return dict(adapter=self.name, passed=code == 0, level=self.validation_level,
                    summary='Sintaxis Python ' + ('correcta' if code == 0 else 'inválida'),
                    command=command, exit_code=code, output=output, artifacts=[],
                    limitation=self.validation_limitation)


class StaticWebAdapter:
    name = 'static_web'
    technology = 'Static Web'
    validation_level = 'syntax'
    validation_executes_application = False
    validation_limitation = ('Comprueba UTF-8, lectura HTML y sintaxis JavaScript. '
                             'No valida CSS, dependencias, bundlers, navegador, apariencia ni comportamiento.')
    extensions = {'.html', '.htm', '.css', '.js', '.mjs', '.cjs'}

    def _scope_root(self, root, entry):
        root = root.resolve()
        parent = (root / entry).parent.resolve()
        while parent.is_relative_to(root):
            manifest = parent / 'package.json'
            if manifest.is_file():
                return parent, manifest
            if parent == root:
                break
            parent = parent.parent
        return (root / entry).parent.resolve(), None

    def copy_scope(self, root, entry):
        base, manifest = self._scope_root(root, entry)
        directory = base.relative_to(root.resolve()).as_posix() or '.'
        projects = [manifest.relative_to(root.resolve()).as_posix()] if manifest else []
        return dict(directories=[directory], files=[], projects=projects)

    def detect(self, root, target):
        return target if Path(target).suffix.lower() in self.extensions else None

    def validate(self, root, entry, run):
        base, _ = self._scope_root(root, entry)
        relative_base = base.relative_to(root.resolve()).as_posix() or '.'
        reader = (
            "import pathlib; from html.parser import HTMLParser; "
            "base=pathlib.Path(" + repr(relative_base) + "); "
            "files=[p for p in base.rglob('*') if p.is_file() and p.suffix.lower() in "
            "{'.html','.htm','.css','.js','.mjs','.cjs'}]; "
            "texts=[(p,p.read_text(encoding='utf-8')) for p in files]; "
            "parsers=[HTMLParser(convert_charrefs=True) for p,t in texts if p.suffix.lower() in {'.html','.htm'}]; "
            "[(parser.feed(text),parser.close()) for parser,(p,text) in zip(parsers,[(p,t) for p,t in texts if p.suffix.lower() in {'.html','.htm'}])]; "
            "print('Static web files readable:',len(files))"
        )
        commands = [[sys.executable, '-I', '-S', '-c', reader]]
        outputs = []
        try:
            code, output = run(commands[0], root, timeout=60)
        except OSError as exc:
            code, output = 1, str(exc)
        outputs.append(output)
        if code == 0:
            scripts = sorted(p for p in base.rglob('*')
                             if p.is_file() and p.suffix.lower() in {'.js', '.mjs', '.cjs'})
            for path in scripts:
                command = ['node', '--check', path.relative_to(root.resolve()).as_posix()]
                commands.append(command)
                try:
                    current_code, current_output = run(command, root, timeout=60)
                except OSError as exc:
                    current_code, current_output = 1, str(exc)
                outputs.append(current_output)
                if current_code != 0:
                    code = current_code
                    break
        passed = code == 0
        return dict(adapter=self.name, passed=passed, level=self.validation_level,
                    summary='Validacion web estatica ' + ('correcta' if passed else 'fallida'),
                    command=commands, exit_code=code, output='\n'.join(outputs), artifacts=[],
                    limitation=self.validation_limitation)


ADAPTERS = (DotnetAdapter(), PythonAdapter(), StaticWebAdapter())


def select_adapter(root, target):
    matches = [(adapter, adapter.detect(root, target)) for adapter in ADAPTERS]
    matches = [(adapter, entry) for adapter, entry in matches if entry is not None]
    if len(matches) != 1:
        raise ValueError('No hay un validador inequívoco para este archivo. Candidato conservado sin aprobación automática.')
    return matches[0]


def resolve_capabilities(root, target, additional_files=()):
    """Describe the evidenced project contract without running user code or validation."""
    root = root.resolve()
    relative = Path(target)
    target_path = root / relative
    if (relative.is_absolute() or '..' in relative.parts
            or not target_path.resolve().is_relative_to(root)
            or not target_path.is_file()):
        raise ValueError('Archivo objetivo fuera del proyecto')
    target = relative.as_posix()
    adapter, entry = select_adapter(root, target)
    scope = adapter.copy_scope(root, entry)
    extra = set()
    for name in additional_files:
        relative = Path(name)
        path = root / relative
        if (relative.is_absolute() or '..' in relative.parts
                or not path.resolve().is_relative_to(root) or not path.is_file()):
            raise ValueError('Archivo adicional fuera del proyecto')
        extra.add(relative.as_posix())
    scope['files'] = sorted(set(scope['files']) | extra)
    descriptor = dict(
        schema_version=1,
        adapter=adapter.name,
        technology=adapter.technology,
        target=target,
        entry=entry,
        copy_scope=scope,
        validation=dict(
            level=adapter.validation_level,
            executes_application=adapter.validation_executes_application,
            limitation=adapter.validation_limitation,
        ),
    )
    return adapter, descriptor

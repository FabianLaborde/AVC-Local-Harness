"""Derive conservative execution settings from the project and conversation."""
import re
from wpf_catalog import creation_request

IMAGE_QUESTION = re.compile(r'(?i)^\s*[¿¡]?(?:qu[eé]\s+(?:ves|muestra|hay)|describ[iíe]|analiz[aá]|observ[aá])\b')


def automatic_options(project, instruction):
    wpf = False
    # Only inspect root project declarations; never execute discovery commands.
    for candidate in list(project.glob('*.csproj'))[:20]:
        try:
            if candidate.is_symlink() or candidate.stat().st_size > 262144:
                continue
            content = candidate.read_text(encoding='utf-8-sig')
        except (OSError, UnicodeError):
            continue
        if re.search(r'<UseWPF>\s*true\s*</UseWPF>', content, re.I):
            wpf = True
            break
    explicit_wpf = bool(re.search(r'(?i)\bWPF\b', instruction)) or creation_request(instruction)
    return dict(build_mode='wpf' if wpf or explicit_wpf else 'off',
                quality_mode=False, attempts=2 if wpf or explicit_wpf else 1, test_command='', tech_mode='auto',
                inspection_criteria={})

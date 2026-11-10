# Contributing to AVC

Thanks for helping improve AVC. The project benefits most from narrow,
reproducible contributions rather than large speculative rewrites.

## Before opening an issue

1. Remove credentials, customer data, local paths, databases, and private code.
2. Reduce the failure to the smallest project or fixture that still reproduces it.
3. State the expected result, the observed result, and what evidence supports each.
4. Indicate the framework, Windows version, Python version, model, and relevant SDK.

## Development setup

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r aider_vision_code\requirements.txt
.\.venv\Scripts\python.exe -m unittest discover -s aider_vision_code -p "test_*.py" -v
```

Tests must not require private projects, credentials, paid services, or the
maintainer's local filesystem. Model-dependent tests should use deterministic
fixtures or be clearly marked as optional integration tests.

## Pull requests

- Keep one problem per pull request.
- Add or update a regression test first when practical.
- Preserve read-only mode and human approval boundaries.
- Do not turn model output into proof of compilation, execution, or visual success.
- Do not add framework-specific behavior to the generic core without an adapter
  boundary and a technology-independent contract.
- Document commands actually executed and unresolved limitations.

Maintainers may ask to split large changes into smaller verifiable steps.

## Good first contributions

- minimal reproductions for visual mismatches;
- clearer error messages and progress states;
- deterministic validators and evidence collectors;
- framework adapters with isolated tests;
- benchmark cases that expose false positives or regressions;
- documentation corrections that remove ambiguity.

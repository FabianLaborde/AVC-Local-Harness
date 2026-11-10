# AVC — Local AI Development Harness

> Experimental, Windows-first alpha. AVC is not a replacement for human review
> and does not guarantee visual fidelity or functional correctness.

AVC explores a local-first workflow for changing software projects with natural
language and image references:

`request → inspect → prepare a candidate copy → validate → capture → compare → human decision`

The project combines a local web UI, Ollama-compatible models, Aider, source
inspection, isolated candidate copies, build/test commands, and visual evidence.
Its purpose is to learn how a local harness can make small code changes safely
while keeping the developer in control.

[Resumen en español](README.es.md)

## What currently works

- local conversation UI and project-folder selection;
- read-only analysis and document/image attachments;
- candidate edits prepared in copies before touching the original project;
- source, syntax, build, test, and runtime checks when an adapter is available;
- before/after captures for supported Windows desktop flows;
- explicit human apply, retry, or discard decisions;
- recovery artifacts kept outside version control;
- a substantial `unittest` regression suite.

## Known limitations

- Image-to-UI generation is approximate and can omit or distort controls.
- Local model reasoning is inconsistent; model claims are not treated as proof.
- Visual comparison is strongest for the flows already exercised on Windows.
- Framework support is uneven. WPF and some WinForms/static-web paths have more
  evidence than other technologies.
- Multi-file creation and autonomous architecture decisions remain experimental.
- Some operations require Ollama, Aider, .NET SDKs, or application-specific
  runtime resources that are not installed by this repository.
- The current full suite has 740 tests: 729 pass and 11 legacy visual-cycle
  tests remain failing (6 failures and 5 errors). These known failures are
  published deliberately rather than hidden; see the contribution roadmap.

These limitations are the main reason for publishing AVC: design reviews,
reproducible failure cases, adapter ideas, and small verified pull requests are
especially welcome.

## Requirements

- Windows 10 or 11;
- Python 3.11+;
- Ollama and a compatible local model for model-backed features;
- Aider for editing flows that delegate code generation;
- the SDK/toolchain required by the project being edited.

Optional features may use LocalAI. Models, databases, projects, screenshots,
logs, and credentials are deliberately not included.

## Quick start

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r aider_vision_code\requirements.txt
.\.venv\Scripts\python.exe aider_vision_code\server.py
```

Then open <http://127.0.0.1:8765/>.

Run the regression suite with:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s aider_vision_code -p "test_*.py" -v
```

The initial public snapshot is expected to reproduce the documented baseline
of 729 passing tests and 11 known visual-cycle failures. A contribution that
changes that baseline should explain whether it fixes or introduces behavior.

## Safety model

AVC treats model output as a proposal, not evidence. The host should decide
facts such as changed-file scope, compilation, tests, process exit status, and
whether captures exist. Applying a candidate is always a separate human action.

Do not point an experimental build at irreplaceable projects without an
independent backup and source control.

## Project map

- `aider_vision_code/server.py`: local Flask server and orchestration entry point;
- `aider_vision_code/web/`: browser interface;
- `aider_vision_code/*candidate*`, `source_*`, `project_*`: candidate and source flows;
- `aider_vision_code/visual_*`, `preview_*`, `window_*`: visual evidence pipeline;
- `aider_vision_code/test_*.py`: regression tests;
- `docs/ARCHITECTURE.md`: current boundaries and trust model;
- `docs/ROADMAP.md`: contribution priorities.

## Contributing

Start with [CONTRIBUTING.md](CONTRIBUTING.md). For a bug, include the smallest
reproduction and remove private project data. For a design proposal, explain
which decision should be deterministic and which one genuinely needs a model.

## License

Licensed under the [Apache License 2.0](LICENSE).

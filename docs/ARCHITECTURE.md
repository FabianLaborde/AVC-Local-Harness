# Architecture and trust boundaries

AVC is a local orchestration experiment. Its central design rule is that a
language or vision model may propose or interpret, but the host must measure
facts that tools can determine.

## Main flow

1. **Request intake** — natural-language instruction, selected project, and
   optional attachments.
2. **Inspection** — locate relevant files and collect bounded project evidence.
3. **Planning/generation** — ask a local model or Aider for a candidate change.
4. **Candidate isolation** — prepare edits in a copy whenever the flow supports it.
5. **Deterministic validation** — inspect changed files, parse source, compile,
   run configured tests, and record process results.
6. **Runtime/visual evidence** — launch supported applications and capture
   before/after states.
7. **Semantic review** — models may describe the evidence but cannot replace it.
8. **Human decision** — apply, retry, or discard.

## Evidence hierarchy

From strongest to weakest:

1. filesystem hashes and changed-file lists;
2. parser/compiler/test/process results;
3. accessibility/control-tree measurements;
4. pixel and geometric measurements;
5. model interpretation;
6. unsupported model claims.

A weaker layer must not override a contradictory stronger layer.

## Current modules

- `server.py` coordinates the local API and user-visible state.
- `project_candidate.py`, `candidate_lineage.py`, and `external_edit.py` manage
  candidate preparation and provenance.
- `source_*`, `sdk_evidence.py`, and `technical_evidence.py` inspect source and
  toolchain evidence.
- `visual_*`, `preview_*`, `window_capture.py`, and `window_inspector.py` collect
  and review visual/runtime evidence.
- `web/index.html` provides the local browser interface.

## Important open design questions

- How should a generic planner express multi-file intent without hard-coding
  every framework operation?
- Which framework facts belong in adapters, and which belong in the generic core?
- How should a failed visual comparison feed a bounded correction loop?
- How can local model selection be evaluated reproducibly on modest hardware?
- Which execution isolation is practical on Windows without hiding failures?

# Roadmap and contribution priorities

This roadmap is evidence-driven. An item is complete only when a reproducible
test demonstrates the behavior.

## Highest priority

1. **End-to-end creation benchmark**
   - image/text request → structured plan → source → build → launch → capture;
   - explicit completeness and visual-similarity measurements;
   - no approval without a viewable candidate.
2. **Generic edit contract**
   - target, preserved invariants, permitted files, expected evidence;
   - framework adapters translate the contract without redefining user intent.
3. **Bounded correction loop**
   - diagnose the failed criterion;
   - preserve verified improvements;
   - retry only the unresolved part;
   - stop on no progress or budget limits.
4. **Portable project adapters**
   - separate discovery, validation, build, run, and capture capabilities;
   - add Python/web and JavaScript examples beside Windows desktop adapters.

## Reliability

- deterministic candidate/apply state machine;
- crash-safe and restart-safe jobs;
- smaller user-facing reports with downloadable technical evidence;
- benchmarks for false positives, identical captures, missing controls, and
  changes outside the authorized scope.

## Community tasks

- provide minimal open test projects for additional frameworks;
- improve accessibility-based UI measurement;
- document supported local model/hardware combinations;
- review process isolation and secret-handling boundaries.

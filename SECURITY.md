# Security policy

AVC can execute build tools and applications from a selected project. Treat it
as a developer tool with local code-execution capability, not as a sandbox.

## Supported version

Only the latest public `main` branch is supported during the alpha phase.

## Reporting a vulnerability

Do not disclose credentials, private source code, or an exploitable report in a
public issue. Use GitHub private vulnerability reporting if it is enabled for
the repository. Otherwise, contact the maintainer through the GitHub profile
before sharing sensitive details.

Include the affected commit, impact, minimal reproduction, and suggested
mitigation. Please allow reasonable time for triage before public disclosure.

## Scope reminders

- Never attach production databases or confidential projects to a public issue.
- Do not run untrusted generated code outside an isolated test environment.
- AVC's candidate-copy workflow reduces accidental edits; it does not provide
  operating-system isolation from malicious code.

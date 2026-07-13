# RPI Core

`.rpi/` contains platform-neutral RPI definitions and engines. It must not
depend on Claude Code or Codex-specific hook payloads.

- `core/`: product intelligence and shared orchestration.
- `schemas/`: portable claims, evidence, task, and hook contracts.
- `skills/`: canonical skill sources used to generate `.agents/skills` and `.claude/skills` adapters.
- `adapters/`: platform-specific renderers and payload translation.

Runtime product facts remain under `.rpi-outfile/`; `.claude/`, `.codex/`, and
`.agents/` are adapter surfaces rather than sources of truth.

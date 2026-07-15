# RPI Core

`.rpi/` contains platform-neutral RPI definitions and engines. It must not
depend on Claude Code or Codex-specific hook payloads.

- `core/`: product intelligence and shared orchestration.
- `schemas/`: portable claims, change impacts, per-decision records, capabilities, invariants, reconciliation, evidence, task, and hook contracts.
- `skills/`: canonical skill sources used to generate `.agents/skills` and `.claude/skills` adapters, including task lifecycle, quality gates, UX/frontend quality, systematic debugging, and code review.
- `adapters/`: platform-specific renderers and payload translation.

Runtime product facts remain under `.rpi-outfile/`; `.claude/`, `.codex/`, and
`.agents/` are adapter surfaces rather than sources of truth.

Natural-language change governance is implemented by `core/change_intelligence.py`.
Project-specific registries and AGENTS routing are implemented by `core/project_governance.py`.
Change governance also captures an authority/design baseline, emits deterministic `CNF-*` conflict candidates, and requires explicit conflict resolution or rebase before production work when the authoritative baseline is stale.
Task-close design/implementation comparison is implemented by `core/reconciliation.py`.
Timed cross-platform locks, durable atomic writes, and recoverable bounded multi-file transactions are implemented by `core/state_store.py`.
Dependency-free write-time Schema enforcement is implemented by `core/schema_validation.py`.
Project audit and routing share one tree walk and keep a Schema-validated incremental content index under `.rpi-outfile/state/index/`; invalid caches rebuild automatically, while symlinks and oversized state are rejected at core boundaries.
Idempotent legacy-state upgrades are implemented by `core/state_migrations.py`.

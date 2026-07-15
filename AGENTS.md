# AGENTS.md

## Global Policy
- Treat user input as source material until it is promoted through `.rpi-outfile/product/claims.json`.
- Read current product facts from `.rpi-outfile/product/current_facts.json`; do not treat archived or rejected claims as current requirements.
- All code changes must be traceable to spec refs.
- Formal implementation requires an active task, applicable spec refs, and an explicit failure/pass evidence plan.
- Use Red -> Green -> Refactor for deterministic behavior and eval-driven development for non-deterministic AI behavior.
- Prefer deterministic checks before autonomous retries.
- Capture recurring failures as candidate guards; promote them only after validation.
- `.rpi/` is the platform-neutral core. `.claude/`, `.codex/`, and `.agents/` are adapters, not sources of truth.
- Natural-language feature requests must pass change analysis; unresolved product-model, ownership, authorization, billing, privacy, or invariant decisions block production implementation.
- Functional changes require design/implementation reconciliation before a passing task close. Code behavior must not silently redefine product facts or invariants.
- Preserve user-authored AGENTS.md content; project-specific routing is maintained only inside the RPI managed governance section.

## Learned Guards
- Promote a lesson here only after repeated reproduction or explicit high-risk validation.
- Every learned guard must state its trigger, scope, evidence, and removal/review condition.

<!-- RPI:PROJECT-GOVERNANCE:START -->
## RPI Project Governance

- Product facts: `.rpi-outfile/product/current_facts.json`.
- Capability registry: `.rpi-outfile/product/capabilities.json`.
- Invariant registry: `.rpi-outfile/product/invariants.json`.
- Current specifications: `.rpi-outfile/specs/`; implementation facts remain in code, migrations, configuration, tests, and runtime evidence.
- Natural-language feature requests are proposed changes. Resolve `.rpi-outfile/state/changes/latest.json` before production implementation.
- Change analysis captures a baseline and may emit pending `CNF-*` conflicts when a request contradicts current Spec or Invariant evidence.
- Resolve conflicts explicitly (`preserve`, `amend`, `coexist`, `deprecate`, `split`, `reject`, or `defer`); stale authority baselines require re-analysis or an evidence-backed rebase.
- Do not convert implementation drift into a product decision automatically.

### Project Knowledge Routing

- No project-specific domain route has enough evidence yet; read Discovery, current Spec, related code, migrations, and tests.

### Change Maintenance

- Local fixes update task evidence and tests; update design only when behavior or contract changes.
- Feature changes update the current Spec and capability references before implementation.
- Product-model or invariant changes require explicit decision evidence before implementation.
- Task closure requires design/implementation reconciliation; unresolved excess behavior must not be normalized into the Spec.
<!-- RPI:PROJECT-GOVERNANCE:END -->

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

## Learned Guards
- Promote a lesson here only after repeated reproduction or explicit high-risk validation.
- Every learned guard must state its trigger, scope, evidence, and removal/review condition.

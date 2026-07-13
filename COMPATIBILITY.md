# RPI CLI Compatibility

RPI shares product facts, specifications, tasks, test/eval evidence, and lifecycle state across agents. Agent-specific configuration files are adapters and are not interchangeable.

## Shared sources

- `AGENTS.md`: concise portable project guidance.
- `.rpi/`: platform-neutral engines, schemas, canonical skills, and adapters.
- `.rpi-outfile/`: current product facts, specs, tasks, evidence, and archives.

## Codex CLI

- Project guidance: `AGENTS.md` and nested `AGENTS.md` files.
- Project config: `.codex/config.toml` after project trust.
- Skills: `.agents/skills/<name>/SKILL.md`.
- Hooks: `.codex/hooks.json`; new or changed command hooks require review in `/hooks`.
- Security: sandbox and approval policy remain authoritative.

## Claude Code CLI

- Project guidance: `CLAUDE.md`, which imports `AGENTS.md` with `@AGENTS.md`.
- Project settings and hooks: `.claude/settings.json` after Workspace Trust.
- Skills: `.claude/skills/<name>/SKILL.md`.
- Security: permission modes and allow/ask/deny rules remain authoritative.

## Adapter setup

```bash
bash .claude/workflow/rpi.sh compat setup
bash .claude/workflow/rpi.sh compat doctor
```

`compat setup` renders Codex hooks and both skill directories from the canonical `.rpi/skills` source. Trust and hook approval remain explicit user actions and are never silently bypassed.

`compat doctor` reports capability states rather than only checking file existence:

- `configured`: adapter files exist but runtime behavior has not been observed.
- `verified`: a real lifecycle event was observed or explicit evidence was recorded.
- `stale`: CLI version or adapter/skill/hook content changed after verification.
- `missing`: the CLI or required adapter is unavailable.

```bash
bash .claude/workflow/rpi.sh compat verify codex all --evidence "hooks reviewed and lifecycle verified in current session"
```

The verification fingerprint includes the CLI version and relevant instruction, hook, skill, and bridge content. Changes invalidate previous verification.

## Hook contract

RPI normalizes lifecycle decisions to:

```json
{
  "action": "allow",
  "reason": "",
  "context": {},
  "evidence": [],
  "retryable": false
}
```

Platform adapters translate native payloads and tool names to the RPI core. Long-running PRD generation, test suites, and AI evals do not belong in a blocking hook; hooks inject state, enforce risk/spec policies, and record evidence.

## Degraded operation

If project trust, hook approval, or a lifecycle capability is unavailable, RPI must report the degraded state. A formal task may fall back to an explicit preflight, but it must not be automatically closed as fully governed when its required enforcement layer did not run.

- `auto-lab`: allows warnings without claiming complete governance.
- `balanced-enterprise`: reports missing required runtime verification as degraded.
- `strict-regulated`: requires all declared lifecycle capabilities to be verified before full governance can be claimed.

RPI currently targets Codex CLI and Claude Code CLI only.

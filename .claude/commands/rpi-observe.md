---
description: 观测与审计入口（logs/trace/evals/audit-pack/audit-report/recover）
argument-hint: <logs|trace|evals|audit-pack|audit-report|recover> [args]
---

执行：

```bash
bash .claude/workflow/rpi.sh observe $ARGUMENTS
```

示例：
- `/rpi-observe logs --task TASK-001 --limit 20`
- `/rpi-observe trace --quiet`
- `/rpi-observe evals --suite regression --quiet`
- `/rpi-observe audit-pack --task TASK-001`
- `/rpi-observe audit-report --days 30 --json`
- `/rpi-observe recover list --target .rpi-outfile/specs/l0/discovery.md --limit 10`
- `/rpi-observe recover restore .rpi-outfile/specs/l0/discovery.md --dry-run`

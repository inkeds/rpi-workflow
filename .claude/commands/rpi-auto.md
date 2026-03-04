---
description: 自动化入口（run/review/memory/entropy）
argument-hint: <run|review|memory|entropy> [args]
---

执行：

```bash
bash .claude/workflow/rpi.sh auto $ARGUMENTS
```

动作映射：
- `run` -> 受控自治执行（auto-rpi）
- `review` -> Agent-to-Agent 评审
- `memory` -> 经验沉淀到 AGENTS.md
- `entropy` -> 反熵扫描

示例：
- `/rpi-auto run --phase M0 --max-rounds 2`
- `/rpi-auto review --json`
- `/rpi-auto memory --task TASK-001 --force`
- `/rpi-auto entropy --strict`

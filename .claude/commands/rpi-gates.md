---
description: 门控入口（preview/setup/run）
argument-hint: <preview|setup|run> [args]
---

执行：

```bash
bash .claude/workflow/rpi.sh gates $ARGUMENTS
```

规则：
- 无参数时默认执行 `run`
- 示例：
  - `/rpi-gates preview standard`
  - `/rpi-gates setup strict`
  - `/rpi-gates run M0`

---
description: 检查入口（env/doctor/precode/bootstrap/full/risk/architecture 等）
argument-hint: <env|doctor|precode|bootstrap|discovery|contract|scope|ux|linkage|skeleton|skeleton-init|theory|entry|artifact|architecture|risk|full> [args]
---

执行：

```bash
bash .claude/workflow/rpi.sh check $ARGUMENTS
```

规则：
- 若无参数，默认执行：`bash .claude/workflow/rpi.sh check full`
- `risk` 用法示例：
  `bash .claude/workflow/rpi.sh check risk --tool Bash --value "terraform apply -auto-approve" --json`

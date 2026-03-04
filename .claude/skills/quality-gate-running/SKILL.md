---
name: quality-gate-running
description: Run and enforce monorepo quality gates by phase. Use when validating readiness for merge/release or before closing an RPI task.
---

# Quality Gate Running

1. 打开配置并确认命令已按仓库实际填写：`.claude/workflow/config/gates.json`
   - 可选 `verify` 层会在阶段 gate 前执行。
2. 按阶段运行门控：

```bash
bash .claude/workflow/rpi.sh gates run [M0|M1|M2]
```

3. 失败即阻断，不允许以“建议通过”替代。
4. 在日志中确认门控证据已记录。

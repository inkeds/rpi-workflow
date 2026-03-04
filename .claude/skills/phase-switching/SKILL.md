---
name: phase-switching
description: Switch project phase between M0, M1, and M2 with explicit reason and updated injection context. Use when milestones change or when quality/risk triggers require stronger spec control.
---

# Phase Switching

1. 明确切换原因（例如：回归失败、多人冲突、高风险能力引入）。
2. 执行切换：

```bash
bash .claude/workflow/rpi.sh task phase <M0|M1|M2> "<reason>"
```

3. 检查阶段文件：`.rpi-outfile/state/project_phase.json`
4. 新阶段下重新检查任务验收与门控要求。

---
name: rpi-task-closing
description: Close an active task with quality gates and root-cause classification. Use when coding is done and you need to finalize pass/fail, classify root cause, and archive trace evidence.
---

# RPI Task Closing

1. 运行阶段质量门控：

```bash
bash .claude/workflow/rpi.sh gates run
```

说明：若配置了 `verify`，会先执行 verify 再执行阶段 gate。

2. 归类结果：
   - `spec_missing`
   - `execution_deviation`
   - `both`
   - `unknown`
   - 或使用 `auto` 让脚本根据日志自动推断

3. 关闭任务并归档：

```bash
bash .claude/workflow/rpi.sh task close <pass|fail> <spec_missing|execution_deviation|both|unknown|auto> "<summary>"
```

说明：严格模式下，若检测到“代码改动后未回写 spec”，关闭会被阻断。

4. 确认 `current_task.json` 已回到 `idle`。

---
name: rpi-task-starting
description: Initialize a deterministic RPI task record before any implementation. Use when starting a new coding task, switching from requirement to execution, or when hooks block edits due to missing task context.
---

# RPI Task Starting

1. 提取任务 ID、目标阶段（M0/M1/M2）和规范引用路径；允许稀疏输入（脚本自动补全）。
2. 启动前确认 artifacts 已达到 apply-ready（discovery/spec/tasks done），严格模式下未就绪会阻断。
2. 执行：

```bash
bash .claude/workflow/rpi.sh task start <task_id> <M0|M1|M2> "<spec1,spec2,...>"
```

3. 校验 `current_task.json`：
   - `status` 必须是 `in_progress`
   - `spec_refs` 不可为空
   - `context_refs` 应已生成
4. 可选：在 `.rpi-outfile/specs/tasks/<TASK-ID>/` 放置任务级 `implement/check/debug.jsonl` 细化上下文注入。
5. 若规范引用不完整，先补规范再实现。

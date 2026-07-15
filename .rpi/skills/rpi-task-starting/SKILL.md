---
name: rpi-task-starting
description: 在任何正式实现前建立可追踪的 RPI Task。用于从需求或 Spec 进入编码、拆分实现计划、恢复暂停任务，或 Hooks 因缺少任务上下文而阻止编辑时。
---

# 启动 RPI Task

## 1. 确认任务可执行

读取 Change、Decision、当前 Spec、Capability/Invariant 和相关代码测试。确认：

- 高影响 Decision 已确认，没有 `pending_decision`。
- Discovery、Spec 和 Tasks 达到 apply-ready。
- 任务只交付一个可独立验证的结果；多个独立结果先拆 Task。
- 依赖任务已完成，或本任务明确只实现不依赖部分。
- M-1 实验与正式生产实现边界清楚。

规范缺失时先补规范；不得创建 Task 来绕过决策或 Spec 门禁。

## 2. 建立最小执行计划

记录目标结果、非目标、修改候选、测试范围、迁移/回滚风险和完成证据。每个实现步骤都应有对应验证，不写“补测试”“处理异常”等不可执行占位语句。

优先把计划放入现有 Task/Spec；只有复杂任务才在 `.rpi-outfile/specs/tasks/<TASK-ID>/` 增加 `implement/check/debug.jsonl`。

## 3. 启动或恢复

```bash
bash .claude/workflow/rpi.sh task start <task_id> <M-1|M0|M1|M2> "<spec1,spec2,...>"
```

暂停任务使用已有 resume 流程，不为同一工作重新创建 Task ID。

## 4. 验证任务记录

检查 `.rpi-outfile/state/current_task.json`：

- `status` 为 `in_progress`。
- `spec_refs`、`change_refs` 和必要的 `context_refs` 已关联。
- Phase 与当前交付成熟度一致。
- TDD 初始状态、风险和验收命令可识别。
- 没有把无关候选需求带入当前范围。

在这些条件满足前不要写生产代码。

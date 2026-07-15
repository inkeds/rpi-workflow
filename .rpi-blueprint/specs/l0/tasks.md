# L0 Tasks

每条任务必须包含：

1. 任务 ID 与目标
2. `spec_refs` 与适用的 `change_refs`
3. 关联 Capability/Invariant
4. 输入/输出与依赖
5. 实现边界（不做）
6. Red → Green 证据计划或 AI Eval 计划
7. 可执行验收标准
8. 设计更新、代码、测试执行与迁移证据
9. 关闭前 reconciliation 结果

任务不得因为存在无关候选变更而冻结；只有显式关联的 `change_refs` 约束该任务。
若 Change 基线的权威事实发生变化，任务必须重新分析或 rebase；存在 pending `CNF-*` 时不得进入生产实现或 Pass 关闭。

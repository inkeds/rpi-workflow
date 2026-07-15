---
name: rpi-task-closing
description: 在声明完成、提交或合并前关闭活动 RPI Task。用于执行最新验证、设计实现对账、根因分类、评审裁决和证据归档；没有新鲜验证证据时不得关闭为 pass。
---

# 关闭 RPI Task

## 1. 完成前审计

重新读取 Task、Spec、Change、修改 diff、测试和迁移。逐项确认：

- 所有验收要求都有实现和证据。
- 没有未声明的额外行为或无关文件扩散。
- 功能、失败、权限、资产和迁移路径与 Spec 一致。
- 新增测试曾观察到正确 Red，并在实现后 Green。
- 工作区不存在意外生成物、秘密或未解释的大改动。

## 2. 运行新鲜验证

先运行目标测试，再运行阶段要求的完整门禁：

```bash
bash .claude/workflow/rpi.sh gates run
```

若配置 `verify`，它会先于 Phase Gate 执行。必须阅读完整输出、退出码和失败数；旧结果、局部通过和“应该没问题”都不能支持完成声明。

## 3. 执行对账和评审

- 运行设计—实现—测试—迁移 Reconciliation。
- 重大或高风险变更启用 `code-reviewing`，处理 Blocking/Important 发现。
- `manual_review_required`、`rejected`、未解决 Decision 或 Reconciliation fail 均阻止 pass。

## 4. 分类真实结果

使用：

- `spec_missing`
- `execution_deviation`
- `both`
- `unknown`
- `auto`

测试失败时关闭为 fail 或继续修复，不为了归档而降低验收标准。

## 5. 关闭并核验

```bash
bash .claude/workflow/rpi.sh task close <pass|fail> <spec_missing|execution_deviation|both|unknown|auto> "<summary>"
```

确认：

- `current_task.json` 回到 `idle`。
- Task Capsule、Portable Contract、测试和 Gate 证据已归档。
- Change/Capability/Invariant 引用和真实状态已回写。
- 总结区分“实现了什么”“运行验证了什么”“仍未验证什么”。

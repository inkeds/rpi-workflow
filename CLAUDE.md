# CLAUDE.md

本文件是项目唯一主记忆入口（Single Entry）。

## 加载顺序

1. 先读本文件。
2. 再按顺序执行分层规则：
   - `.claude/rules/00-foundation.md`
   - `.claude/rules/01-spec-layering.md`
   - `.claude/rules/02-rpi-traceability.md`
   - `.claude/rules/03-tdd-quality-gates.md`
   - `.claude/rules/04-context-pack-injection.md`
   - `.claude/rules/05-discovery-first.md`
3. 若改动命中 monorepo 目录，再加载：
   - `.claude/rules/monorepo/apps.md`
   - `.claude/rules/monorepo/packages.md`
   - `.claude/rules/monorepo/platform.md`

## 硬约束摘要

1. 流程固定为 RPI：Requirement -> Plan -> Implement。
2. 未绑定活动任务和 `spec_refs`，禁止进入代码实现。
3. 代码改动前必须先有 Red 证据，再 Green，再 Refactor。
4. 严格模式下，`/rpi-task start` 仅在 artifacts `apply-ready` 时放行。
5. 任务关闭前必须通过质量门控，并完成根因分类与追溯记录。
6. 代码变更后必须回写 spec，避免实现与规范漂移。

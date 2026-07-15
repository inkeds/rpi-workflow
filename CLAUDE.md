# CLAUDE.md

@AGENTS.md

本文件是 Claude Code 适配入口；跨平台公共约束以 `AGENTS.md` 为准。

## 加载顺序

1. 先读导入的 `AGENTS.md` 与本文件。
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
6. 自然语言功能请求先经过 Change 分析；高影响 Decision 未解决时禁止生产实现。
7. 局部修复更新测试与任务证据；只有行为或契约变化才更新设计。
8. 功能任务关闭前必须完成设计/实现 reconciliation，禁止用越界代码反向定义产品事实。

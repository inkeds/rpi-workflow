# RPI 与可追溯（强制）

## RPI 定义

- `R`（Requirement）：绑定规范、提取事实、标记未知。
- `P`（Plan）：拆任务、列测试、定义验收。
- `I`（Implement）：按 TDD 实现，执行质量门控。

## 记录位置

- 当前阶段：`.rpi-outfile/state/project_phase.json`
- 当前任务：`.rpi-outfile/state/current_task.json`
- 注入上下文清单：`.claude/workflow/context/*.jsonl`
- 事件日志：`.rpi-outfile/logs/events.jsonl`
- 门控日志：`.rpi-outfile/logs/gate-results.jsonl`

## 任务状态最小字段（必追踪）

1. `spec_refs`：任务绑定规范来源
2. `context_refs`：本轮实际注入的上下文包
3. `phase_state.current_action`：当前动作（implement/check/close）
4. `phase_state.next_actions`：下一步动作队列
5. `quality_gate.last_run_status`：最近门控状态

## 根因分类（收口必填）

- `spec_missing`：规范遗漏或不明确导致失败
- `execution_deviation`：规范明确但执行偏差导致失败
- `both`：两者同时存在
- `unknown`：证据不足

## 判定要求

任务关闭时必须能追溯到：

1. 规范是否充分
2. 执行是否偏离
3. 哪个阶段引入风险
4. 哪个门控拦截/放行

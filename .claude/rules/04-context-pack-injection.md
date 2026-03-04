# Context Pack 分阶段注入

## 目标

避免“全量读仓库”导致的上下文噪声，把实现随机性收敛到可控范围。

## 规则

1. 不允许每次任务全量读取 `.rpi-outfile/specs/`。
2. 通过 `.claude/workflow/context/*.jsonl` 维护阶段上下文：
   - `implement.jsonl`
   - `check.jsonl`
   - `debug.jsonl`
3. `rpi.sh task start` 必须将 `spec_refs + context manifest` 合并为 `context_refs`。
4. `UserPromptSubmit` 注入时必须展示 `context_refs` 预览。
5. 阶段动作推进必须写入 `phase_state`，便于追溯“规范缺失 vs 执行偏差”。

## 回放要求

复盘任意任务时，至少能回答：

1. 当时注入了哪些上下文文件（`context_refs`）？
2. 哪个阶段动作执行失败（`phase_state.current_action`）？
3. 失败发生在 `verify` 还是 `gate`？

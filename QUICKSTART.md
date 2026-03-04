# 快速上手指南（5 分钟）

本指南帮助你在 5 分钟内跑通第一个 RPI 任务闭环。

## 前置条件

- 已安装 Claude Code CLI
- 项目目录已初始化 git（可选但推荐）

## 步骤 1：复制框架到项目（30 秒）

```bash
# 假设你的项目在 ~/my-project
cd ~/my-project

# 复制 rpi-workflow 框架
cp -r /path/to/rpi-workflow/.claude .
cp /path/to/rpi-workflow/CLAUDE.md .
```

## 步骤 2：环境检查（30 秒）

```bash
/rpi-check env
```

**期望输出**：显示平台信息和依赖状态。`jq` 为必需依赖，缺失时会报错并给出安装命令。`rg`（ripgrep）推荐安装，缺失时自动降级。

## 步骤 2.5：选择 Harness 档位（可选，30 秒）

```bash
/rpi-mode profile show
/rpi-mode profile balanced-enterprise
/rpi-mode harness show
```

**说明**：  
- `strict-regulated`：强审计、强约束  
- `balanced-enterprise`：效率与可控平衡（默认）  
- `auto-lab`：高自治实验模式
- `/rpi-mode harness on|off`：一键启用/关闭 Harness 增强能力包（风险约束、Spec Link、自动重试等）

## 步骤 3：初始化项目（1 分钟）

```bash
/rpi-init 我要做一个待办事项管理工具
```

**期望输出**：
1. 环境预检通过
2. 需求闸门通过
3. 生成完整 L0 基线（`discovery/spec/epic/milestones/tasks/00_master_spec`）
4. 生成 `.rpi-outfile/specs/l0/mvp-skeleton.md`（骨架模板，含 `{{...}}` 占位符）
5. MVP 骨架含 4 阶段画布、链路候选池、A/B/C 业务段选择（范围轴）、M0~M2 阶段扩展（深度轴）、覆盖率与不确定性预算、DDD-Lite 语义层
6. 提示执行 `/rpi-init deepen` 进行想法深化

## 步骤 3.5：想法深化（1 分钟）

```bash
/rpi-init deepen
```

**期望输出**：
1. 将骨架中的 `{{...}}` 占位符替换为从设想推导的具体内容
2. 输出方向 A/B/C（业务段选择）与 M0~M2 阶段扩展策略，推荐方向标注 ⭐
3. 输出 P0 覆盖率与低置信度链路占比
4. 输出 DDD-Lite 要素（统一语言、限界上下文、业务不变量、已选上下文）
5. 如用户要求提升非核心功能，输出调权结论（提升项/降权项/理由/加权覆盖率目标）
6. 生成 JSON 摘要（`.rpi-outfile/state/init_summary.json`）
7. 提示选择方向并确认链路 IDs 与范围

**确认方向**（选择后进入规范补全）：
```bash
/rpi-spec expand 确认方向B，Must链路L1,L2,L3，Won't补充外部联邦登录，已选上下文C1[Core],C2[Supporting]，提升L4降权L2并给出理由，加权覆盖率85%
```

## 步骤 4：健康检查（30 秒）

```bash
/rpi-check doctor
```

**期望输出**：`PASS`（如果是 `BLOCKED`，按提示补齐缺失项）

## 步骤 5：启动任务（30 秒）

```bash
/rpi-task start 001
```

**期望输出**：
- 创建 `current_task.json`
- 绑定 `spec_refs`
- 注入 `context_refs`

## 步骤 6：实现代码（2 分钟）

按 TDD 流程：

1. **Red**：先写失败测试
```bash
# 例如：npm test -- todos.test.js
```

2. **Green**：写最小实现
```bash
# 编辑代码文件
```

3. **Refactor**：重构（可选）

> 默认 `tdd_mode=recommended`：未先写 Red 测试时会告警但不硬拦截。可在 `runtime.json` 调整为 `strict` 或 `off`。

## 步骤 7：执行门控（30 秒）

```bash
# 如果没有配置 gates.json，先使用最小预设
cp .claude/workflow/config/gates.minimal.json .claude/workflow/config/gates.json

# 执行门控
/rpi-gates run M0
```

**期望输出**：`Quality gate passed for phase M0`

## 步骤 8：关闭任务（30 秒）

```bash
/rpi-task close pass auto 待办列表主链路通过
```

**期望输出**：
- 任务归档到 `.rpi-outfile/logs/tasks/`
- `current_task.json` 重置为 idle
- 根因分类和 spec 同步状态
- 若启用审计报表，会自动写入 `.rpi-outfile/audit/reports/`

## 验证完成

检查以下文件确认闭环完成：

```bash
# 当前任务状态（应为 idle）
cat .rpi-outfile/state/current_task.json

# 事件日志（应有 rpi_start 和 rpi_close）
tail -5 .rpi-outfile/logs/events.jsonl

# 门控日志（应有 M0 通过记录）
tail -5 .rpi-outfile/logs/gate-results.jsonl

# 任务归档（应有 TASK-001 目录）
ls .rpi-outfile/logs/tasks/
```

## 常见问题

### Q: `/rpi-check doctor` 返回 BLOCKED？
A: 按提示补齐缺失项，通常是 discovery/spec/tasks 未完成。

### Q: Hook 阻止我编辑代码？
A: 确保已执行 `/rpi-task start`，并且先运行失败测试（TDD Red 证据）。

### Q: 没有测试怎么办？
A: 建议把 `tdd_mode` 设为 `recommended`（告警不阻断）；基础设施/脚本类可通过 `tdd_exempt_path_regex` 和 `tdd_exempt_command_regex` 精准豁免。

### Q: 如何中止错误启动的任务？
A: 使用 `/rpi-task abort <reason>` 优雅退出。

### Q: 依赖 jq 和 rg 吗？
A: `jq` 是**必需依赖**，缺失时框架会明确报错并给出安装命令。`rg`（ripgrep）推荐安装，缺失时自动降级为 `grep -E`。

## 下一步

- 阅读完整 [README.md](./README.md) 了解所有命令
- 按项目类型选配：查看 README.md 中的[「按项目属性选配」](./README.md#按项目属性选配积木模型)章节
- 配置 runtime.json：参考 `.claude/workflow/config/runtime.example.md`（含「项目属性 → 建议值」映射）
- 配置自定义 `gates.json`：参考 `.claude/workflow/config/gates.example.md`
- 探索 L1/L2 规范分层：查看 `.rpi-blueprint/specs/l1/README.md`

## 获取帮助

```bash
/rpi-check full
/rpi-mode show
```

---

**预计完成时间**：5 分钟
**难度**：⭐⭐☆☆☆（简单）
**前置知识**：基础 Bash 命令

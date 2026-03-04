# RPI Workflow 全指令与参数手册

本文基于当前仓库实现（`.claude/workflow/rpi.sh` + `engine/*.py`）整理，覆盖所有对外命令组与子命令参数。

## 使用约定

- Claude Code 对话里优先使用斜杠命令：`/rpi-xxx ...`
- 终端等价写法：`bash .claude/workflow/rpi.sh <group> <action> [args...]`
- 记号说明：
  - `<必填>`：必填参数
  - `[可选]`：可选参数
  - `A|B`：二选一
  - `...`：可重复或多个值

## 命令总览（8 组）

| 斜杠命令 | 分组 | 作用 |
|---|---|---|
| `/rpi-init` | `init` | 初始化、深化、基线重建 |
| `/rpi-task` | `task` | 任务生命周期 |
| `/rpi-check` | `check` | 环境/规范/架构/风险检查 |
| `/rpi-spec` | `spec` | spec state 与 spec 扩展 |
| `/rpi-gates` | `gates` | 门控建议与执行 |
| `/rpi-mode` | `mode` | Harness 与 profile 模式切换 |
| `/rpi-observe` | `observe` | 日志、审计、恢复、评分 |
| `/rpi-auto` | `auto` | 受控自治、A2A 评审、反熵、经验沉淀 |

---

## 1) `/rpi-init`

### 1.1 setup（默认动作）

语法：

```bash
/rpi-init <idea> [platform]
# 等价
bash .claude/workflow/rpi.sh init setup "<idea>" [platform]
```

参数：

| 参数 | 含义 | 默认值 |
|---|---|---|
| `<idea>` | 一句话项目设想 | 无，必填 |
| `[platform]` | 运行形态（Web/CLI/Backend 等） | `Web` |

行为说明：

- 执行需求可执行性评估（过短、过于模糊或高风险会拦截）。
- 初始化 `.rpi-outfile` 状态文件。
- 生成 L0 基线与 `mvp-skeleton.md`。
- 刷新可移植约束产物：
  - `.rpi-outfile/state/portable/contract.latest.json`
  - `.rpi-outfile/state/portable/evidence_template.json`

示例：

```bash
/rpi-init 基于next.js开发一个网课系统
/rpi-init 开发统一用户管理平台 Web
```

### 1.2 deepen

语法：

```bash
/rpi-init deepen [idea] [platform]
# 等价
bash .claude/workflow/rpi.sh init deepen [idea] [platform]
```

参数：

| 参数 | 含义 | 默认值 |
|---|---|---|
| `[idea]` | 可覆盖当前设想重新深化 | 读取已有 `mvp-skeleton`/`init_summary` |
| `[platform]` | 可覆盖当前运行形态 | 继承当前记录 |

行为说明：

- 输出 A/B/C 业务段差异（范围、Must/Won't、覆盖门槛）。
- 回填 discovery 结论字段（方向、覆盖率、Must/Won't）。
- 给出可直接执行的 `/rpi-spec expand` 确认文本。

### 1.3 bootstrap

语法：

```bash
/rpi-init bootstrap [--force] [idea] [platform]
# 等价
bash .claude/workflow/rpi.sh init bootstrap [--force] [idea] [platform]
```

参数：

| 参数 | 含义 | 默认值 |
|---|---|---|
| `[--force]` | 强制覆盖/重建基线文件（会先快照） | `false` |
| `[idea]` | 基线设想文本 | 自动猜测历史设想 |
| `[platform]` | 运行形态 | `Web` |

---

## 2) `/rpi-task`

### 2.1 start

语法（推荐显式）：

```bash
/rpi-task start <task_id> <M0|M1|M2> "<spec_ref1,spec_ref2,...>" [owner]
```

语法（简写）：

```bash
/rpi-task start 001
/rpi-task start M0
/rpi-task start
```

参数：

| 参数 | 含义 | 默认值 |
|---|---|---|
| `<task_id>` | 任务号，可写 `001`/`TASK-001` | 自动从 `tasks.md` 推断首个任务，否则 `TASK-001` |
| `<M0|M1|M2>` | 目标阶段 | 当前 `project_phase` |
| `"<spec_refs_csv>"` | 逗号分隔 spec 引用（支持 `#锚点`） | 自动推断 |
| `[owner]` | 任务负责人标识 | `claude` |

自动推断规则：

- `phase` 优先级：显式参数 > 文本中提取 > 当前阶段 > `M0`
- `task_id` 优先级：显式参数 > 文本中提取 > `tasks.md` 首个任务 > `TASK-001`
- `spec_refs` 未提供时会从 `00_master_spec/discovery/tasks` 推断

附加产物：

- 启动任务时会刷新 portable contract（供外部 AI/工具消费）：
  - `.rpi-outfile/state/portable/contract.latest.json`

### 2.2 pause

语法：

```bash
/rpi-task pause "<reason>"
```

参数：

| 参数 | 含义 |
|---|---|
| `<reason>` | 暂停原因，必填 |

附加产物：

- `pause` 会写：
  - `.rpi-outfile/state/context/task_capsule.json`
  - `.rpi-outfile/state/portable/contract.latest.json`

### 2.3 resume

语法：

```bash
/rpi-task resume [task_id]
```

参数：

| 参数 | 含义 | 默认值 |
|---|---|---|
| `[task_id]` | 指定恢复某个已暂停任务 | 不填则恢复最近暂停任务 |

### 2.4 abort

语法：

```bash
/rpi-task abort "<reason>"
```

参数：

| 参数 | 含义 |
|---|---|
| `<reason>` | 中止原因，必填 |

### 2.5 close

语法：

```bash
/rpi-task close <pass|fail> <spec_missing|execution_deviation|both|unknown|auto> "<note>"
```

参数：

| 参数 | 含义 | 默认值 |
|---|---|---|
| `<pass|fail>` | 任务结果 | 无，必填 |
| `<root_cause>` | 根因分类，`auto` 表示自动推断 | 无，必填 |
| `"<note>"` | 关闭说明 | 可为空字符串 |

根因分类说明：

- `spec_missing`：规范不完整/不一致导致失败
- `execution_deviation`：实现质量/测试执行偏差
- `both`：两者都有
- `unknown`：无法归类
- `auto`：从 gate/event 自动推断

附加产物：

- `close` 会写：
  - `.rpi-outfile/state/context/task_capsule.json`
  - `.rpi-outfile/state/portable/contract.latest.json`

### 2.6 phase

语法：

```bash
/rpi-task phase <M0|M1|M2> <reason>
```

### 2.7 status

语法：

```bash
/rpi-task status
```

输出当前活跃任务（或 `No active task`）。

---

## 3) `/rpi-check`

无参数时默认执行：

```bash
/rpi-check
# 等价于
/rpi-check full
```

### 3.1 env

语法：

```bash
/rpi-check env [--auto-fix]
```

参数：

| 参数 | 含义 |
|---|---|
| `--auto-fix` | 尝试自动修复可修复的环境项 |

说明：框架会固定附加 `--require-jq --include-recommended`。

### 3.2 doctor

```bash
/rpi-check doctor
```

项目整体健康检查（含产物质量评分）。

### 3.3 precode

```bash
/rpi-check precode
```

顺序执行 discovery/contract/scope + spec verify(all)。

### 3.4 bootstrap

```bash
/rpi-check bootstrap
```

检查 L0 必需文件是否齐全。

### 3.5 discovery / contract / scope / linkage / ux

语法：

```bash
/rpi-check discovery [--quiet]
/rpi-check contract [--quiet]
/rpi-check scope [--quiet]
/rpi-check linkage [--quiet]
/rpi-check ux [--quiet]
```

参数：

| 参数 | 含义 |
|---|---|
| `--quiet` | 安静模式，减少输出 |

### 3.6 skeleton / skeleton-init

语法：

```bash
/rpi-check skeleton [--quiet]
/rpi-check skeleton-init [--frontend|--no-frontend] [--force]
```

参数：

| 参数 | 含义 | 默认值 |
|---|---|---|
| `--frontend` | 强制初始化前端骨架（含 `ux-flow.md`） | 自动判断 |
| `--no-frontend` | 强制按非前端模式初始化骨架 | 自动判断 |
| `--force` | 覆盖已存在骨架文件 | `false` |
| `--quiet` | skeleton 检查安静模式 | `false` |

### 3.7 theory / entry

语法：

```bash
/rpi-check theory [--quiet]
/rpi-check entry [--quiet]
```

用途：

- `theory`：校验 Vibe-Spec+RPI 理论约束是否仍然成立
- `entry`：校验命令入口/Hook 路径一致性与遗留残留

### 3.8 artifact

语法：

```bash
/rpi-check artifact [--json]
```

参数：

| 参数 | 含义 |
|---|---|
| `--json` | 输出机器可读状态 |

### 3.9 architecture

语法：

```bash
/rpi-check architecture [--quiet] [--json] [--require-rules]
```

参数：

| 参数 | 含义 |
|---|---|
| `--quiet` | 安静输出 |
| `--json` | JSON 输出 |
| `--require-rules` | 强制要求存在架构规则文件 |

### 3.10 risk

语法：

```bash
/rpi-check risk --tool <tool> --value "<value>" [--profile <profile>] [--json]
```

参数：

| 参数 | 含义 | 默认值 |
|---|---|---|
| `--tool` | 工具类型，如 `Bash`、`Edit`、`Write` | 无，必填 |
| `--value` | 被评估内容（命令或路径） | 无，必填 |
| `--profile` | 风险配置 profile（覆盖默认选择） | 空 |
| `--json` | 输出完整评估结果 | `false` |

### 3.11 full

```bash
/rpi-check full
```

执行 doctor + discovery + contract + scope + spec verify + theory + entry。

---

## 4) `/rpi-spec`

### 4.1 build

语法：

```bash
/rpi-spec build [--quiet] [--print-path] [--force]
```

参数：

| 参数 | 含义 |
|---|---|
| `--quiet` | 安静输出 |
| `--print-path` | 输出 state 文件路径 |
| `--force` | 强制重建（忽略缓存） |

### 4.2 verify

语法：

```bash
/rpi-spec verify [--scope all|discovery|scope_guard] [--quiet] [--json]
```

参数：

| 参数 | 含义 | 默认值 |
|---|---|---|
| `--scope` | 校验范围 | `all` |
| `--quiet` | 安静输出 | `false` |
| `--json` | JSON 输出 | `false` |

### 4.3 sync

语法：

```bash
/rpi-spec sync [--quiet]
```

从 `state.json` 同步生成 `spec-source.json`。

### 4.4 link

语法：

```bash
/rpi-spec link [--quiet]
```

构建/校验 spec-link 关系。

### 4.5 expand

语法：

```bash
/rpi-spec expand ["<confirmation text>"]
```

参数：

| 参数 | 含义 | 默认值 |
|---|---|---|
| `"<confirmation text>"` | 方向、Must/Won't、覆盖率等确认文本 | 为空时自动从 discovery/init_summary 生成 |

附加产物：

- 每次 `spec expand` 后会刷新 portable contract，便于外部工具在“无活动任务”时也能读取最新范围与门控要求。

---

## 5) `/rpi-gates`

### 5.1 preview

语法：

```bash
/rpi-gates preview [minimal|standard|strict]
```

说明：预览推荐门控配置，不写文件。

### 5.2 setup

语法：

```bash
/rpi-gates setup [minimal|standard|strict]
```

说明：写入推荐门控到 `.claude/workflow/config/gates.json`。

### 5.3 run

语法：

```bash
/rpi-gates run [M0|M1|M2] [--max-retries N] [--auto-fix|--no-auto-fix] [--quiet]
```

参数：

| 参数 | 含义 | 默认值 |
|---|---|---|
| `[M0|M1|M2]` | 指定执行阶段 | 当前阶段 |
| `--max-retries N` | 失败自动重试次数 | 取 runtime（若未启用重试则强制 0） |
| `--auto-fix` | 失败后尝试自动修复 | 按 runtime |
| `--no-auto-fix` | 禁用自动修复 | 按 runtime |
| `--quiet` | 安静输出 | `false` |

---

## 6) `/rpi-mode`

### 6.1 show

```bash
/rpi-mode show
```

显示当前 profile 与 harness 状态。

### 6.2 harness

语法：

```bash
/rpi-mode harness [show|on|off]
```

参数：

| 参数 | 含义 | 默认值 |
|---|---|---|
| `show` | 查看 harness 关键开关 | 默认 |
| `on` | 启用强化约束包 |  |
| `off` | 关闭强化约束，偏手工模式 |  |

别名：

```bash
/rpi-mode on
/rpi-mode off
```

### 6.3 profile

语法：

```bash
/rpi-mode profile list
/rpi-mode profile show
/rpi-mode profile apply <profile>
/rpi-mode profile <profile>
```

当前内置 profile：

- `strict-regulated`
- `balanced-enterprise`
- `auto-lab`

快捷别名：

```bash
/rpi-mode strict-regulated
/rpi-mode balanced-enterprise
/rpi-mode auto-lab
```

---

## 7) `/rpi-observe`

### 7.1 logs

语法：

```bash
/rpi-observe logs [--task <task_id>] [--event <event>] [--phase <M0|M1|M2>] [--limit <n>] [--format json|text]
```

参数：

| 参数 | 含义 | 默认值 |
|---|---|---|
| `--task` | 按任务过滤 | 空 |
| `--event` | 按事件名过滤 | 空 |
| `--phase` | 按阶段过滤 | 空 |
| `--limit` | 返回条数 | `20` |
| `--format` | 输出格式 | `text` |

### 7.2 trace

语法：

```bash
/rpi-observe trace [--quiet]
```

根据日志打分 trace 质量（A/B/C/D）。

### 7.3 evals

语法：

```bash
/rpi-observe evals [--suite all|capability|regression] [--quiet]
```

参数：

| 参数 | 含义 | 默认值 |
|---|---|---|
| `--suite` | 执行评测套件 | `all` |
| `--quiet` | 安静输出 | `false` |

### 7.4 audit-pack

语法：

```bash
/rpi-observe audit-pack [--task <TASK-001>] [--output <dir>] [--tar] [--limit-events <n>]
```

参数：

| 参数 | 含义 | 默认值 |
|---|---|---|
| `--task` | 指定任务号 | 当前任务；无则 `SESSION` |
| `--output` | 输出目录 | `.rpi-outfile/audit/<task>-<timestamp>` |
| `--tar` | 额外打包 `.tar.gz` | `false` |
| `--limit-events` | tail 日志保留条数 | `2000` |

### 7.5 audit-report

语法：

```bash
/rpi-observe audit-report [--task <TASK-ID>] [--days <n>] [--output <dir>] [--json]
```

参数：

| 参数 | 含义 | 默认值 |
|---|---|---|
| `--task` | 仅统计某任务 | 全部任务 |
| `--days` | 统计窗口（天） | `30` |
| `--output` | 报表目录 | `.rpi-outfile/audit/reports` |
| `--json` | 直接输出 JSON 到 stdout | `false` |

### 7.6 recover

语法：

```bash
/rpi-observe recover list [--target <rel-path>] [--limit <n>] [--json]
/rpi-observe recover restore <rel-path> [--snapshot <snapshot-ref>] [--reason <text>] [--dry-run]
```

参数（list）：

| 参数 | 含义 | 默认值 |
|---|---|---|
| `--target` | 仅查看某文件快照 | 空（全部） |
| `--limit` | 返回条数 | `30` |
| `--json` | JSON 输出 | `false` |

参数（restore）：

| 参数 | 含义 | 默认值 |
|---|---|---|
| `<rel-path>` | 要恢复的相对路径 | 必填 |
| `--snapshot` | 指定快照 ID/引用 | 最新匹配快照 |
| `--reason` | 恢复原因 | `manual_restore` |
| `--dry-run` | 只预览不执行恢复 | `false` |

---

## 8) `/rpi-auto`

### 8.1 run（受控自治）

语法：

```bash
/rpi-auto run [--phase M0|M1|M2] [--max-rounds N] [--max-minutes M] [--max-failures N] [--max-tool-events N] [--auto-fix|--no-auto-fix] [--force]
```

参数：

| 参数 | 含义 | 默认值（未显式传参时） |
|---|---|---|
| `--phase` | 执行阶段 | 当前阶段 |
| `--max-rounds` | 最大循环轮数 | runtime `auto_rpi_max_rounds` |
| `--max-minutes` | 时间预算（分钟） | runtime `auto_rpi_max_minutes` |
| `--max-failures` | 允许失败次数 | runtime `auto_rpi_max_failures` |
| `--max-tool-events` | 工具事件预算 | runtime `auto_rpi_max_tool_events` |
| `--auto-fix` / `--no-auto-fix` | 是否自动反熵修复后重试 | runtime `auto_rpi_auto_fix` |
| `--force` | 即使 runtime 关闭 auto_rpi 也强制执行 | `false` |

### 8.2 review（A2A 评审）

语法：

```bash
/rpi-auto review [--base <ref>] [--head <ref>] [--auto-merge] [--quiet] [--json]
```

参数：

| 参数 | 含义 | 默认值 |
|---|---|---|
| `--base` | diff 基线引用（如分支/commit） | 工作区变更 |
| `--head` | diff 终点引用 | `HEAD`（当设置 `--base` 时） |
| `--auto-merge` | 评审通过后尝试自动提交非核心变更 | `false` |
| `--quiet` | 安静输出 | `false` |
| `--json` | JSON 输出完整报告 | `false` |

### 8.3 memory（经验沉淀）

语法：

```bash
/rpi-auto memory [--task <TASK-ID>] [--result <pass|fail>] [--root-cause <value>] [--note "<text>"] [--archive <file>] [--force] [--quiet]
```

参数：

| 参数 | 含义 | 默认值 |
|---|---|---|
| `--task` | 指定任务号，用于回溯归档 | 自动取当前/最新归档 |
| `--result` | 任务结果 | 若缺失默认按 `fail` 处理 |
| `--root-cause` | 根因 | `unknown` |
| `--note` | 说明 | 空 |
| `--archive` | 显式归档文件路径 | 自动选择 |
| `--force` | 即使 `pass` 也强制写入经验 | `false` |
| `--quiet` | 安静输出 | `false` |

### 8.4 entropy（反熵）

语法：

```bash
/rpi-auto entropy [--auto-fix] [--strict] [--json]
```

参数：

| 参数 | 含义 | 默认值 |
|---|---|---|
| `--auto-fix` | 自动修复可修复问题 | `false` |
| `--strict` | 存在高危未修复项时返回非 0 | `false` |
| `--json` | JSON 输出报告 | `false` |

---

## 9) 内部 Hook 命令（一般无需手动调用）

以下由 `.claude/settings.json` 自动触发，不建议人工执行：

- `hook-session-start`
- `hook-user-prompt-submit`
- `hook-pre-tool-use`
- `hook-post-tool-use`
- `hook-stop`

---

## 10) 常见参数速查

### 阶段值

- `M0`：核心闭环交付
- `M1`：成长迭代
- `M2`：成熟扩展

### Profile 值

- `strict-regulated`
- `balanced-enterprise`
- `auto-lab`

### 输出路径参数建议

- 路径参数优先使用项目相对路径（相对仓库根目录）。
- 路径里有空格时请加引号。

示例：

```bash
/rpi-observe recover restore ".rpi-outfile/specs/l0/discovery.md"
/rpi-observe audit-pack --output ".rpi-outfile/audit/custom-pack"
```

### 跨工具协同产物

外部 AI/工具接入优先读取：

1. `.rpi-outfile/state/portable/contract.latest.json`
2. `.rpi-outfile/state/context/task_capsule.json`
3. `.rpi-outfile/state/portable/evidence_template.json`

# RPI Workflow — 产品需求文档

## 1. 文档定位

本 PRD 用于帮助用户学习并理解 RPI Workflow 的完整设计，而不是仅描述某个版本的增量功能。

学习目标：

- 理解 RPI Workflow 的理论基础与设计边界
- 理解从规范到实现再到审计的完整闭环
- 掌握命令分层、配置档位、门控策略与自治能力
- 能在不同项目类型中正确选配和落地

## 2. 产品概述

RPI Workflow 是一个面向 Claude Code 的工程化 AI 开发框架，核心目标是把 AI 编码从“可用但不稳定”升级为“可控、可追溯、可审计、可复现”。

它解决的核心问题：

| 问题 | 典型表现 | 对应机制 |
|------|----------|----------|
| AI 发散 | 同需求多次生成差异大 | Vibe-Spec + RPI + 架构约束 |
| 规范漂移 | 代码偏离 Spec 且无人感知 | spec-state + spec-link + anti-entropy |
| 决策黑箱 | 失败后无法定位根因 | events/gates/task-archive + root cause |
| 风险不可控 | 高危命令或改动未受管控 | risk matrix + pre-tool guard |
| 长周期失真 | 迭代越久越偏离最初目标 | 周期性反熵 + 评测回归 |
| 合规证据不足 | 审计时缺少过程证据链 | audit-pack + audit-report |
| 热路径延迟累积 | 高频 Edit/Write 导致 Hook 重复开销 | 任务级签名缓存 + 增量校验 |

## 3. 理论体系

### 3.1 Vibe-Spec 分阶段理论

| 阶段 | Vibe:Spec | 重心 | 禁止项 |
|------|-----------|------|--------|
| M0 | 6:4 | 覆盖已选核心业务链路闭环 | 过早抽象、过度设计 |
| M1 | 3:7 | 契约、异常、集成稳定 | 新增 Must |
| M2 | 2:8 | 上线、安全、可观测、审计 | 新增功能 |

### 3.2 RPI 执行闭环

- `R`（Requirement）：绑定规范、确认事实、标记未知
- `P`（Plan）：拆解任务、定义测试与验收
- `I`（Implement）：按 TDD 实现并通过门控

### 3.3 Harness Engineering（受控自治）

核心思想：不是追求“完全自动化”，而是让自动化运行在工程护栏内。

关键护栏：

- 风险分级（R0-R3）
- 架构边界规则
- Spec Link 绑定校验
- 自治预算（时长/工具事件）
- 可开关、可分档位的运行时策略

### 3.4 Agentic Execution（有限自治）

RPI Workflow 支持自治，但必须满足：

- 有预算上限
- 有失败上限
- 有门控可回退
- 有审计可追溯

结论：RPI Workflow 是“确定性优先 + 受控自治增强”的融合范式。

### 3.5 MVP 范围框选理论（业务段选择 + 阶段扩展）

MVP 的范围单位不是“单个功能点”，而是“跨阶段业务链路”。

框选流程：

1. 把一句话需求拆成 4 阶段画布（S1 入口建模 → S2 核心决策 → S3 执行交付 → S4 回执复用）
2. 生成链路候选池（L1/L2/L3...），并标注优先级（P0/P1/P2）与置信度
3. 用 A/B/C 选择业务段（范围轴）：
   - A：选择 S0（MVP 运营段）
   - B：选择 S0 + S1（成长期）
   - C：选择 S0 + S1 + S2（成熟期，S3 进入路线图）
4. 用 M0/M1/M2 做阶段扩展（深度轴）：
   - M0：交付已选业务段的可运营闭环（非演示版）
   - M1：成长迭代优化，或新增 1 条受控业务方向
   - M2：成熟规模化与治理完善，可小范围生态试点
5. 覆盖率门槛用于约束“已选业务段”的质量：
   - A：P0 覆盖率 >= 40%，至少 1 条主链路 + 1 条关键异常链路
   - B：P0 覆盖率 >= 80%，主路径链路可用且可复测
   - C：P0 覆盖率 = 100%，并补齐运营治理链路
   - 以上阈值可在 `runtime.json` 通过 `mvp_coverage_threshold_*` 配置覆盖
6. 前端“完整可用 UX”只覆盖已选 Must 链路，不覆盖 Won't 或后续阶段功能
7. 允许“用户优先级调权”：
   - 可提升非核心功能优先级（默认最多 1 项）
   - 必须同步降权至少 1 项并记录理由
   - 需要给出加权覆盖率目标，且不能突破调权容差上限

### 3.6 DDD-Lite 融合理论（语义与边界治理层）

RPI Workflow 融入 DDD-Lite，而非全量 DDD 仪式化落地。

DDD-Lite 只保留三项高价值能力：

1. 统一语言（Ubiquitous Language）：把需求口语化表达收敛为稳定术语
2. 限界上下文（Bounded Context）：明确 Core/Supporting/Governance 边界
3. 业务不变量（Domain Invariants）：把关键规则转为可校验约束

与 A/B/C 业务段选择的映射：

- A：至少覆盖 1 个 Core 上下文
- B：覆盖 Core + 1 个 Supporting 上下文
- C：覆盖全部 P0 上下文并包含治理上下文

非目标：

- 不默认强制 Repository/Factory/Domain Service 全套战术模式
- 不默认强制 Event Sourcing/CQRS
- 不把建模仪式替代交付节奏

## 4. 设计目标与非目标

### 4.1 设计目标

1. 全流程可追溯：每次变更都能追溯到任务与规范
2. 长周期可稳定：迭代后仍保持 Spec/代码一致性
3. 自动化可控：支持提效，不牺牲工程边界
4. 合规可审计：能导出完整证据链

### 4.2 非目标

1. 不追求无约束的全自动编码
2. 不替代团队架构设计职责
3. 不绕过质量门控直接交付

## 5. 核心能力设计（完整强化版）

### 5.1 规范即事实源（Spec as Source of Truth）

- L0/L1/L2 分层规范体系
- `spec-state`：把 Markdown 规范编译为机器可读状态
- `spec-verify`：对 discovery/scope/spec/tasks 做结构化校验
- `spec_aliases.json`：字段别名外置配置（支持中英/团队定制标签），降低模板语言耦合

### 5.2 Spec Link 关联图谱

- `/rpi-spec link` 构建 Task ↔ Spec 关系图
- pre-tool 阶段校验当前任务是否绑定有效 spec_refs
- 防止“无规范绑定的代码变更”进入实现流程

### 5.3 架构约束引擎

- `architecture.rules.json` 定义依赖边界与规则
- `/rpi-check architecture` 在编码前/门控前执行
- 违反规则时阻断并输出可修复信息

### 5.4 风险治理

- `risk_matrix.json`：按 tool/path/command/branch 选择风险档位
- `/rpi-check risk`：可独立评估命令/路径风险
- `pre_tool_use`：高风险动作 deny/ask/allow

### 5.5 质量门控增强

- `/rpi-gates run` 升级为 auto wrapper（重试 + 根因标签）
- 可按根因触发自动修复（可开关）
- 保留 verify 层（规范完整性）与 phase_gates（代码质量）分层

### 5.6 反熵机制

- `/rpi-auto entropy` 定期扫描并处理偏移：
  - 规范与实现不一致
  - 架构规则偏离
  - 门控长期失败热点

### 5.7 受控自治闭环

- `/rpi-auto run` 支持单任务受控自治
- 预算维度：回合数/时长/失败次数/工具事件
- 失败后保留证据并可人工接管

### 5.8 Agent-to-Agent 评审

- `/rpi-auto review` 对变更执行二次评估
- 结合风险评估、规范校验、架构校验
- 可选对非核心改动自动合并（受 runtime 开关控制）

### 5.9 Agent 经验沉淀

- `/rpi-auto memory` 将失败案例写入 `AGENTS.md`
- 去重写入（fingerprint）
- 形成“犯一次错，长期规避”的工程记忆

### 5.10 审计闭环

- `/rpi-observe audit-pack` 导出任务证据包
- `/rpi-observe audit-report` 生成统计报表（任务、门控、风险、trace）
- close 流程可自动触发记忆沉淀与审计输出

### 5.11 MVP 范围引擎（业务段选择 + 阶段扩展 + DDD-Lite）

- `/rpi-init deepen` 生成：
  - 4 阶段画布（S1-S4）
  - 链路候选池（链路 ID + 优先级 + 置信度）
  - A/B/C 业务段选择（S0 / S0+S1 / S0+S1+S2）
  - M0/M1/M2 阶段扩展策略（运营闭环 / 成长迭代 / 成熟扩展）
  - 覆盖率与不确定性预算
  - DDD-Lite 语义层（统一语言/限界上下文/业务不变量/已选上下文）
  - 用户优先级调权槽位（提升项/降权项/理由/加权覆盖率目标）
- `/rpi-init deepen` 与 `/rpi-spec expand` 要求用户确认：
  - 业务段方向（A/B/C）
  - 链路 IDs（Must/Won't）
  - 上下文 IDs（M0）及 Core/Supporting/Governance 角色
  - 统一语言与业务不变量
  - 调权项（如使用）
  - 覆盖率目标与低置信度链路处理策略
- `/rpi-spec expand` 在确认后自动执行：
  - 实化 `specs/phases/m1.md`、`specs/phases/m2.md`（写入当前项目上下文与阶段门禁建议）
  - 补全 `gates.json` 的 `verify.M1` / `verify.M2` 基线校验项（按前端/非前端差异生成）
  - 更新 `state/init_summary.json` 的决策快照（direction/coverage/Must/Won't/DDD-Lite/phase_artifacts/gate_matrix）
- `M0 Must` 仍保持 1-3 项，但每项必须映射到链路 ID，而非泛化功能点
- 对“应用类项目”（非纯 CLI/脚本）强制要求：M0 交付必须包含与已选 Must 链路对齐的完整可用 UX（仅覆盖已选链路，不要求覆盖 Won't）

### 5.12 能力状态（避免愿景/现状混淆）

当前 PRD 中提到的核心增强能力均已在仓库内实现，并具备对应命令入口：

- 运行时治理：`/rpi-mode harness`、`/rpi-mode profile`
- 规范状态与关联：`/rpi-spec build`、`/rpi-spec verify`、`/rpi-spec sync`、`/rpi-spec link`
- 架构与风险：`/rpi-check architecture`、`/rpi-check risk`
- 反熵与评测：`/rpi-auto entropy`、`/rpi-observe evals`、`/rpi-observe trace`
- 审计闭环：`/rpi-observe audit-pack`、`/rpi-observe audit-report`
- 受控自治：`/rpi-auto run`、`/rpi-auto review`、`/rpi-auto memory`、`/rpi-task pause`、`/rpi-task resume`、`/rpi-task abort`
- 产物质量度量：`/rpi-check doctor` 输出 artifact quality score（completeness/semantic/traceability）

规划中能力：

- 无（后续新增能力将显式标记为“规划中”）。

### 5.13 热路径性能与稳定性设计

- pre-tool 热路径采用任务级签名缓存：
  - precode 护栏结果按 `runtime + spec + rules` 签名缓存
  - spec-link 校验按 `task + spec_refs + links.json` 签名缓存
- 架构扫描支持限流与排除目录：
  - `architecture_scan_max_files`
  - `architecture_scan_exclude_dirs`
- `spec-link` 重建判断使用高精度 mtime，避免同秒写入导致的误判拦截。
- 目标：在保持强约束前提下，降低高频 Edit/Write/Bash 调用时的额外延迟。

### 5.14 Hook API 契约与故障隔离

- Hook 输出严格对齐 Claude Code 当前 schema：
  - `PreToolUse` 使用 `hookSpecificOutput.PreToolUse`
  - `SessionStart/UserPromptSubmit/PostToolUse` 使用对应 `hookSpecificOutput` 结构
  - `Stop` 使用顶层 `decision/reason`，不输出无效 `hookSpecificOutput.Stop`
- 当解析失败或字段缺失时，优先降级为“可解释阻断”而非脚本崩溃，避免静默破坏 Agent 循环。
- 关键状态写入采用原子写策略，降低并发场景下的空文件/半写入风险。

### 5.15 `rpi-outfile` 生命周期与可移植契约

- `.rpi-outfile` 属于运行期产物，不是框架静态仓库内容。
- 初始化前（尚未创建 `.rpi-outfile`）：
  - 以 `.rpi-blueprint/specs` 作为模板与学习入口。
- 初始化后（执行初始化创建步骤起，例如 `/rpi-init <idea>` 或 `rpi.sh init setup ...`）：
  - 以 `.rpi-outfile/specs/*` 与 `.rpi-outfile/state/*` 作为当前项目事实源。
- 框架会在关键节点刷新可移植执行契约（供外部模型/工具消费）：
  - `spec expand`
  - `task start / pause / resume / close / abort`
- 可移植契约核心产物：
  - `.rpi-outfile/state/portable/contract.latest.json`
  - `.rpi-outfile/state/context/task_capsule.json`
  - `.rpi-outfile/state/portable/evidence_template.json`
- 目标：即使外部工具不运行 Claude Hooks，也能按同一流程、边界和证据模板执行。

## 6. 生命周期流程（学习视角）

标准路径：

1. 初始化：`/rpi-check env` → `/rpi-init` → `/rpi-init deepen`
2. 健康检查：`/rpi-check doctor`
3. 启动任务：`/rpi-task start <task>`
4. 执行实现：R → P → I（TDD）
5. 质量门控：`/rpi-gates run <phase>`
6. 关闭归档：`/rpi-task close pass|fail auto <note>`

初始化前后边界：

- 初始化前：没有 `.rpi-outfile`，以 `.rpi-blueprint/specs` 为参考。
- 初始化后（执行初始化创建步骤后）：以 `.rpi-outfile` 为运行事实源，并可向外部工具分发 portable contract。

增强路径（按需）：

- `/rpi-mode harness on|off`：切换增强能力包
- `/rpi-mode profile <profile>`：切换运行档位
- `/rpi-auto entropy`：定期反熵
- `/rpi-auto review`：二次评审
- `/rpi-observe audit-report`：运营/审计统计
- `/rpi-task pause` / `/rpi-task resume` / `/rpi-task abort`：多任务切换与可控中断

## 7. 命令体系（学习索引）

### 7.1 主命令面（压缩后仅 8 个入口）

- `/rpi-init`：初始化与范围深化（setup/deepen/bootstrap）
- `/rpi-task`：任务生命周期（start/pause/resume/abort/close/phase/status）
- `/rpi-check`：检查入口（env/doctor/precode/bootstrap/full/discovery/contract/scope/ux/linkage/skeleton/skeleton-init/theory/entry/artifact/architecture/risk）
- `/rpi-spec`：规范工程（build/verify/sync/link/expand）
- `/rpi-gates`：门控（preview/setup/run）
- `/rpi-mode`：运行模式（show/harness/profile/on/off/profile-name）
- `/rpi-observe`：观测与审计（logs/trace/evals/audit-pack/audit-report/recover）
- `/rpi-auto`：自动化（run/review/memory/entropy）

### 7.2 学习顺序（建议）

1. `/rpi-check env`
2. `/rpi-init` + `/rpi-init deepen`
3. `/rpi-check doctor`
4. `/rpi-task start`
5. `/rpi-gates run`
6. `/rpi-task close`

### 7.3 能力映射（子动作）

- 规范检查：`/rpi-check discovery|contract|scope|theory`
- 架构与风险：`/rpi-check architecture|risk`
- 骨架治理：`/rpi-check skeleton-init|skeleton|linkage|ux`
- Spec 状态链路：`/rpi-spec build|verify|sync|link|expand`
- 自治与反熵：`/rpi-auto run|review|memory|entropy`
- 审计观测：`/rpi-observe logs|trace|evals|audit-pack|audit-report|recover`

## 8. 配置体系

### 8.1 运行档位（Profile）

- `strict-regulated`：高约束、高审计、低自治
- `balanced-enterprise`：效率与可控平衡（默认）
- `auto-lab`：高自治实验模式

补充说明：

- `strict-regulated` 下，`/rpi-task start` 会严格校验 discovery 的 DDD-Lite 最低条目；不满足会阻断启动（预期行为）。
- `balanced-enterprise` 默认以 warn 策略运行，适合先落地再逐步收紧。

### 8.2 runtime 关键配置分组

- 护栏开关：`harness_enabled`、`strict_mode`
- 风险治理：`risk_matrix_enabled`、`risk_profile_override`
- 架构与规范：`architecture_enforce`、`architecture_require_rules`、`architecture_scan_max_files`、`architecture_scan_exclude_dirs`、`spec_state_required`、`spec_link_enforce`
- 多语言字段映射：`.claude/workflow/config/spec_aliases.json`（discovery 字段别名配置）
- 语义与边界（DDD-Lite）：`ddd_lite_mode`、`ddd_min_glossary_terms`、`ddd_min_bounded_contexts`、`ddd_min_invariants`
- 调权策略：`mvp_priority_override_mode`、`mvp_weighted_coverage_tolerance`、`mvp_max_promote_non_core`
- 实施策略：`precode_guard_mode`、`tdd_mode`
- 门控增强：`gates_auto_retry_enabled`、`gates_auto_retry_max`、`gates_auto_fix_on_fail`
- 自治能力：`auto_rpi_*`
- 评审与记忆：`agent_review_enabled`、`a2a_*`、`agent_memory_auto_update`
- 审计能力：`audit_report_enabled`、`audit_pack_required_on_close`
- MVP 覆盖策略：`mvp_coverage_threshold_a`、`mvp_coverage_threshold_b`、`mvp_coverage_threshold_c`、`mvp_low_confidence_ratio_max`

### 8.3 门控预设

- `gates.minimal.json`
- `gates.frontend.json`
- `gates.multi-module.json`

## 9. 数据与可观测设计

### 9.1 关键状态

- `.rpi-outfile/state/current_task.json`
- `.rpi-outfile/state/project_phase.json`
- `.rpi-outfile/state/spec/state.json`
- `.rpi-outfile/state/spec/links.json`
- `.rpi-outfile/state/recovery/index.jsonl`
- `.rpi-outfile/state/context/task_capsule.json`
- `.rpi-outfile/state/portable/contract.latest.json`
- `.rpi-outfile/state/portable/evidence_template.json`

### 9.2 关键日志

- `.rpi-outfile/logs/events.jsonl`
- `.rpi-outfile/logs/gate-results.jsonl`
- `.rpi-outfile/logs/tasks/*.json`
- `.rpi-outfile/logs/trace-grades.jsonl`

### 9.3 审计输出

- `.rpi-outfile/audit/` 下证据包
- `.rpi-outfile/audit/reports/` 下统计报表（JSON/MD）
- `.rpi-outfile/state/recovery/snapshots/` 下覆盖前快照（可通过 `/rpi-observe recover` 查看/恢复）

### 9.4 跨工具交接包（推荐）

外部 AI/编码工具最小读取集合：

1. `.rpi-outfile/state/portable/contract.latest.json`（执行约束）
2. `.rpi-outfile/state/context/task_capsule.json`（任务最小上下文）
3. `.rpi-outfile/state/portable/evidence_template.json`（证据结构模板）

建议保持“裁决后置”：

- 外部工具负责实现；
- 框架侧以 `/rpi-check` + `/rpi-gates` + `/rpi-task close` 做最终判定。

## 10. 学习路径（面向用户）

### 阶段 A：先会用（1-2 天）

- 跑通 `/rpi-init → /rpi-task start → /rpi-gates run → /rpi-task close`
- 了解 `discovery/spec/tasks` 三个核心规范文件

### 阶段 B：再会控（2-4 天）

- 学会 `/rpi-mode profile` 与 `/rpi-mode harness` 切换
- 学会 `/rpi-check risk` 与 `/rpi-check architecture` 联合使用

### 阶段 C：最后会扩（持续）

- 接入 `/rpi-auto entropy` 周期扫描
- 接入 `/rpi-auto review`、`/rpi-auto memory`
- 接入 `/rpi-observe audit-pack`、`/rpi-observe audit-report`

## 11. 成功指标

| 指标 | 目标 |
|------|------|
| 首次门控通过率 | ≥ 80% |
| P0 链路覆盖率达标率 | 100%（A>=40% / B>=80% / C=100%） |
| 链路测试绑定率 | 100%（每条已选 P0 链路有 E2E + 异常用例） |
| 低置信度链路预算达标率 | ≥ 90%（占比 <= 30%） |
| Spec 绑定覆盖率 | 100%（任务有 spec_refs） |
| 根因可追溯率 | 100%（close 有分类） |
| 高风险动作可控率 | 100%（走风险矩阵策略） |
| 审计证据完整率 | 100%（可导出任务证据链） |

## 12. 演进方向

- 加强多 Agent 任务分工与协作协议
- 丰富架构规则类型与自动修复策略
- 增强运行时信号驱动的动态门控
- 持续提升评测集覆盖与回归能力

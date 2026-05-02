# runtime.json 配置说明

本文件说明 `runtime.json` 中各配置项的含义和使用场景。

## 基础配置项

### profile_name

**类型**：`string`  
**默认值**：`balanced-enterprise`  
**说明**：Harness 运行档位名称，建议通过 `/rpi-mode profile` 切换而不是手改。

---

### harness_enabled

**类型**：`boolean`  
**默认值**：`true`  
**说明**：Harness 增强总开关（可通过 `/rpi-mode harness on|off` 一键切换）。

**作用**：
- 开启后：启用风险治理、架构约束、Spec Link 约束、门控自动重试等增强能力
- 关闭后：降级为人工驱动模式，减少自动拦截和自动修复动作

---

### strict_mode

**类型**：`boolean`  
**默认值**：`false`  
**说明**：严格模式开关

**作用**：
- 开启后，强制执行所有规范检查
- 关闭后，部分检查降级为警告

**适用场景**：
- `true`：团队协作、长期迭代、生产项目
- `false`：单人快速原型、实验性项目

---

### start_require_ready

**类型**：`boolean`  
**默认值**：`false`  
**说明**：启动任务前是否要求 artifacts 为 `apply-ready` 状态

**作用**：
- 开启后，`/rpi-task start` 仅在 artifacts 完全就绪时放行
- 关闭后，允许在规范未完全就绪时启动任务

**适用场景**：
- `true`：需要严格规范约束的项目
- `false`：快速迭代、边做边补规范的项目

---

### close_require_spec_sync

**类型**：`boolean`  
**默认值**：`false`  
**说明**：关闭任务前是否要求 spec 同步

**作用**：
- 开启后，任务关闭前必须将代码变更回写到 spec
- 关闭后，允许不同步 spec 直接关闭任务

**适用场景**：
- `true`：需要保持规范与代码一致的项目
- `false`：快速原型、临时任务

---

### allow_generic_red

**类型**：`boolean`  
**默认值**：`true`  
**说明**：是否允许通用的 TDD Red 证据

**作用**：
- 开启后，允许通用的测试失败作为 Red 证据
- 关闭后，要求测试路径/模式明确的 Red 证据

**适用场景**：
- `true`：快速原型、无测试的项目
- `false`：严格 TDD 的项目

---

### risk_matrix_enabled

**类型**：`boolean`  
**默认值**：`true`  
**说明**：是否启用风险分级矩阵（R0-R3）拦截/审批。

---

### risk_profile_override

**类型**：`string`（可为空）  
**默认值**：`""`  
**说明**：强制指定当前会话的风险档位（`dev`/`staging`/`prod`）。

**作用**：
- 非空时，覆盖 `risk_matrix.json` 的 branch/path/command 动态选择器
- 便于在演练环境临时提升或降低风险审批强度

**选择优先级**（从高到低）：
- `rpi-risk-check --profile <...>`
- `runtime.json.risk_profile_override`
- `risk_matrix.json.selectors`（`path`/`command`/`branch`）
- `risk_matrix.json.default_profile`

---

### risk_high_requires_approval

**类型**：`boolean`  
**默认值**：`true`  
**说明**：是否对高风险动作（R2/R3）强制人工审批。

**作用**：
- 开启后，即使规则决策为 `allow`，高风险级别仍会触发人工确认流程
- 关闭后，按风险矩阵原始决策执行

---

### autonomy_budget_mode

**类型**：`string` (`off`/`warn`/`enforce`)  
**默认值**：`warn`  
**说明**：任务自治预算超限时的处理策略（关闭/询问/强制拦截）。

---

### autonomy_max_minutes / autonomy_max_tool_events

**类型**：`number / number`  
**默认值**：`240 / 300`  
**说明**：任务自治硬预算（时长与工具调用事件量）。

---

### architecture_enforce

**类型**：`boolean`  
**默认值**：`false`  
**说明**：是否在编码前强制执行架构边界检查（`/rpi-check architecture`）。

---

### architecture_require_rules

**类型**：`boolean`  
**默认值**：`false`  
**说明**：开启后，若未配置有效架构规则，直接拦截编码。

**说明补充**：`architecture.rules.json` 支持 `import_forbid` 与 `source_allowlist` 两种规则类型。

---

### architecture_scan_max_files

**类型**：`number`  
**默认值**：`2000`  
**说明**：单条架构规则的最大扫描文件数。达到上限时会输出警告并截断扫描，以降低大仓库热路径延迟。

---

### architecture_scan_exclude_dirs

**类型**：`string[]`  
**默认值**：`[".git","node_modules","vendor","dist","build",".next","coverage","tmp",".venv","venv","__pycache__"]`  
**说明**：架构扫描时默认跳过的目录名（按目录名匹配，不区分大小写）。

---

### spec_state_required

**类型**：`boolean`  
**默认值**：`true`  
**说明**：`/rpi-task start` 前是否要求机器可读 Spec 状态可生成且可校验。

---

### spec_link_enforce

**类型**：`boolean`  
**默认值**：`false`  
**说明**：编码前是否强制校验 Spec Link 图谱（`/rpi-spec link`）已绑定当前任务 `spec_refs`。

---

### gates_auto_retry_enabled / gates_auto_retry_max / gates_auto_fix_on_fail

**类型**：`boolean / number / boolean`  
**默认值**：`true / 3 / true`  
**说明**：`/rpi-gates run` 失败后的自动重试与自动修复策略。

**行为**：
- 失败后按根因分类（spec/architecture/implementation）记录重试事件
- 最多重试 `gates_auto_retry_max` 次
- `gates_auto_fix_on_fail=true` 时，会先尝试反熵修复再重跑

---

### auto_rpi_enabled / auto_rpi_max_rounds / auto_rpi_auto_fix

**类型**：`boolean / number / boolean`  
**默认值**：`false / 1 / false`  
**说明**：受控自治闭环能力开关、最大自动回合数、是否允许自动修正。

---

### auto_rpi_max_minutes / auto_rpi_max_failures / auto_rpi_max_tool_events

**类型**：`number / number / number`  
**默认值**：`20 / 1 / 120`  
**说明**：`/rpi-auto run` 的硬预算（时长、失败次数、事件数量）。

---

### agent_memory_auto_update

**类型**：`boolean`  
**默认值**：`true`  
**说明**：任务关闭时（主要是失败场景）自动沉淀经验到仓库 `AGENTS.md`。

---

### agent_review_enabled / a2a_auto_merge_non_core / a2a_allow_commit

**类型**：`boolean / boolean / boolean`  
**默认值**：`true / true / false`  
**说明**：Agent-to-Agent 自动评审与非核心变更自动合并策略。

**说明补充**：
- `/rpi-auto review` 会输出评审报告到 `.rpi-outfile/state/agent-review/latest.json`
- `/rpi-auto review` 同时输出裁决卡到 `.rpi-outfile/state/agent-review/review_card.latest.json`
- 仅当 `a2a_allow_commit=true` 时，`--auto-merge` 才允许自动提交

---

### opsx_enabled / auto_rpi_run_review / review_decision_mode

**类型**：`boolean / boolean / string`  
**默认值**：`true / true / advisory`  
**说明**：是否启用 OPSX 可移植契约、`/rpi-auto run` 成功后是否自动串联 review，以及 review 的裁决级别。

**作用**：
- `opsx_enabled=true`：在 `contract.latest.json` / `evidence.latest.json` 中输出 `Objective-Policy-Spec-Execution` 结构
- `auto_rpi_run_review=true`：`/rpi-auto run` 成功后自动执行 `/rpi-auto review`
- `review_decision_mode=advisory`：review 失败只记录结果，不阻断 auto-rpi 成功返回
- `review_decision_mode=enforce`：review 未通过或需要人工审批时，auto-rpi 视为未完成最终裁决

---

### anti_entropy_auto_fix

**类型**：`boolean`  
**默认值**：`false`  
**说明**：`/rpi-auto entropy` 默认是否启用自动修正。

---

### audit_report_enabled

**类型**：`boolean`  
**默认值**：`true`  
**说明**：是否启用审计统计报表能力（`/rpi-observe audit-report`）。

---

### trace_grade_required

**类型**：`boolean`  
**默认值**：`false`  
**说明**：任务关闭前是否强制存在执行轨迹评分结果（`/rpi-observe trace`）。

**作用**：
- 开启后：关闭任务前需产出可审计的 trace grade 结果
- 关闭后：trace grade 作为推荐项，不阻断关闭

---

### audit_pack_required_on_close

**类型**：`boolean`  
**默认值**：`false`  
**说明**：任务关闭时是否强制生成审计证据包。

---

### mvp_coverage_threshold_a

**类型**：`number`（0-100）  
**默认值**：`40`  
**说明**：MVP 方向 A（核心可证级）的 P0 链路覆盖率阈值（百分比）。

---

### mvp_coverage_threshold_b

**类型**：`number`（0-100）  
**默认值**：`80`  
**说明**：MVP 方向 B（核心可用级）的 P0 链路覆盖率阈值（百分比）。

---

### mvp_coverage_threshold_c

**类型**：`number`（0-100）  
**默认值**：`100`  
**说明**：MVP 方向 C（核心可运营级）的 P0 链路覆盖率阈值（百分比）。

---

### mvp_low_confidence_ratio_max

**类型**：`number`（0-100）  
**默认值**：`30`  
**说明**：低置信度链路占比上限（百分比），超过时建议先建立验证任务。

---

### ddd_lite_mode

**类型**：`string` (`off`/`warn`/`enforce`)  
**默认值**：`warn`  
**说明**：DDD-Lite 语义与边界校验策略（统一语言/限界上下文/业务不变量/已选上下文）。

**作用**：
- `off`：关闭 DDD-Lite 校验
- `warn`：校验不达标只告警，不阻断
- `enforce`：校验不达标直接阻断 discovery 检查

---

### ddd_min_glossary_terms

**类型**：`number`  
**默认值**：`6`  
**说明**：统一语言（Ubiquitous Language）最少条目数。

---

### ddd_min_bounded_contexts

**类型**：`number`  
**默认值**：`2`  
**说明**：限界上下文（Bounded Context）最少条目数。

---

### ddd_min_invariants

**类型**：`number`  
**默认值**：`3`  
**说明**：业务不变量（Domain Invariants）最少条目数。

---

### mvp_priority_override_mode

**类型**：`string` (`off`/`warn`/`enforce`)  
**默认值**：`warn`  
**说明**：MVP 用户优先级调权策略（提升/降权/加权覆盖率目标）的校验模式。

**作用**：
- `off`：不校验调权结构
- `warn`：结构不完整时告警
- `enforce`：结构不完整时阻断 discovery 校验

---

### mvp_weighted_coverage_tolerance

**类型**：`number`（0-100）  
**默认值**：`10`  
**说明**：当使用调权策略时，允许原始覆盖率低于方向阈值的最大容差（百分点）。

---

### mvp_max_promote_non_core

**类型**：`number`  
**默认值**：`1`  
**说明**：单次 MVP 方向中允许提升的非核心功能最大数量。

---

### precode_guard_mode

**类型**：`string` (`off`/`warn`/`enforce`)  
**默认值**：`warn`  
**说明**：编码前置规范检查（discovery/contract/scope/architecture）的执行策略。

**作用**：
- `off`：不执行前置规范检查
- `warn`：检查失败只告警，不阻断编码
- `enforce`：检查失败直接阻断编码

---

### tdd_mode

**类型**：`string` (`off`/`recommended`/`strict`)  
**默认值**：`recommended`  
**说明**：TDD 门控策略。

**作用**：
- `off`：不检查 Red 证据
- `recommended`：缺少 Red 证据仅告警
- `strict`：缺少 Red 证据直接阻断

---

### stop_loop_max_blocks

**类型**：`number`  
**默认值**：`4`  
**说明**：Stop Hook 循环阻断的最大次数

**作用**：
- 达到阈值后，Stop Hook 放行退出
- 避免无限循环阻断

**适用场景**：
- 默认值 `4` 适用于大多数项目
- 可根据项目复杂度调整

---

### stop_loop_timeout_minutes

**类型**：`number`  
**默认值**：`30`  
**说明**：Stop Hook 循环阻断的超时时间（分钟）

**作用**：
- 超时后，Stop Hook 放行退出
- 避免长时间阻断

**适用场景**：
- 默认值 `30` 适用于大多数项目
- 大型项目可适当增加

---

## 前端 UX 相关配置项

### frontend_ux_strict

**类型**：`boolean`  
**默认值**：`false`  
**说明**：前端 UX 严格模式

**作用**：
- 开启后，前端任务必须通过 UX 合规性门控
- 关闭后，UX 检查降级为警告

**适用场景**：
- `true`：中后台管理系统、表单密集型项目
- `false`：简单页面、展示型项目

---

### require_ux_spec

**类型**：`boolean`  
**默认值**：`false`  
**说明**：前端任务是否必须有 UX 规范

**作用**：
- 开启后，前端任务启动前必须有 `ux-spec.md`
- 关闭后，允许无 UX 规范启动任务

**适用场景**：
- `true`：需要统一 UX 标准的前端项目
- `false`：后端项目、简单前端项目

---

### require_reference_module

**类型**：`boolean`  
**默认值**：`false`  
**说明**：多模块项目是否必须有标杆模块

**作用**：
- 开启后，多模块项目必须先开发标杆模块
- 关闭后，允许直接开发所有模块

**适用场景**：
- `true`：需要统一实现风格的多模块项目
- `false`：单模块项目、模块间无关联的项目

---

### auto_inject_ux_context

**类型**：`boolean`  
**默认值**：`true`  
**说明**：是否自动注入 UX 上下文包

**作用**：
- 开启后，前端任务启动时自动注入 UX 规范和参考模块
- 关闭后，需要手动指定上下文

**适用场景**：
- `true`：大多数前端项目（推荐）
- `false`：需要精确控制上下文的项目

---

## 多模块协同相关配置项

### require_linkage_spec

**类型**：`boolean`  
**默认值**：`false`  
**说明**：多模块任务是否必须有联动规范

**作用**：
- 开启后，多模块任务启动前必须有 `module-linkage.md`
- 关闭后，允许无联动规范启动任务

**适用场景**：
- `true`：≥3 个模块且存在跨模块联动的项目
- `false`：单模块项目、模块间无联动的项目

---

### auto_inject_linkage_context

**类型**：`boolean`  
**默认值**：`true`  
**说明**：是否自动注入联动上下文包

**作用**：
- 开启后，多模块任务启动时自动注入联动规范和相关模块代码
- 关闭后，需要手动指定上下文

**适用场景**：
- `true`：大多数多模块项目（推荐）
- `false`：需要精确控制上下文的项目

---

### linkage_strict_mode

**类型**：`boolean`  
**默认值**：`false`  
**说明**：联动严格模式

**作用**：
- 开启后，多模块任务必须通过联动完整性门控
- 关闭后，联动检查降级为警告

**适用场景**：
- `true`：复杂业务的多模块项目
- `false`：简单联动的多模块项目

---

## 按项目属性推荐配置

### 单人快速原型

```json
{
  "strict_mode": false,
  "start_require_ready": false,
  "close_require_spec_sync": false,
  "allow_generic_red": true,
  "stop_loop_max_blocks": 4,
  "stop_loop_timeout_minutes": 30,
  "frontend_ux_strict": false,
  "require_ux_spec": false,
  "require_linkage_spec": false,
  "require_reference_module": false,
  "auto_inject_ux_context": true,
  "auto_inject_linkage_context": true,
  "linkage_strict_mode": false
}
```

### 团队长期迭代项目

```json
{
  "strict_mode": true,
  "start_require_ready": true,
  "close_require_spec_sync": true,
  "allow_generic_red": false,
  "stop_loop_max_blocks": 4,
  "stop_loop_timeout_minutes": 30,
  "frontend_ux_strict": false,
  "require_ux_spec": false,
  "require_linkage_spec": false,
  "require_reference_module": false,
  "auto_inject_ux_context": true,
  "auto_inject_linkage_context": true,
  "linkage_strict_mode": false
}
```

### 中后台管理系统（单模块）

```json
{
  "strict_mode": true,
  "start_require_ready": true,
  "close_require_spec_sync": true,
  "allow_generic_red": false,
  "stop_loop_max_blocks": 4,
  "stop_loop_timeout_minutes": 30,
  "frontend_ux_strict": true,
  "require_ux_spec": true,
  "require_linkage_spec": false,
  "require_reference_module": false,
  "auto_inject_ux_context": true,
  "auto_inject_linkage_context": true,
  "linkage_strict_mode": false
}
```

### 中后台管理系统（多模块）

```json
{
  "strict_mode": true,
  "start_require_ready": true,
  "close_require_spec_sync": true,
  "allow_generic_red": false,
  "stop_loop_max_blocks": 4,
  "stop_loop_timeout_minutes": 30,
  "frontend_ux_strict": true,
  "require_ux_spec": true,
  "require_linkage_spec": true,
  "require_reference_module": true,
  "auto_inject_ux_context": true,
  "auto_inject_linkage_context": true,
  "linkage_strict_mode": true
}
```

### 生产上线项目

```json
{
  "strict_mode": true,
  "start_require_ready": true,
  "close_require_spec_sync": true,
  "allow_generic_red": false,
  "stop_loop_max_blocks": 4,
  "stop_loop_timeout_minutes": 30,
  "frontend_ux_strict": true,
  "require_ux_spec": true,
  "require_linkage_spec": true,
  "require_reference_module": true,
  "auto_inject_ux_context": true,
  "auto_inject_linkage_context": true,
  "linkage_strict_mode": true
}
```

---

## 配置调整建议

### 从宽松到严格的渐进路径

1. **阶段 1：快速原型**
   - 所有严格检查关闭
   - 专注核心功能实现

2. **阶段 2：功能稳定**
   - 开启 `strict_mode`
   - 开启 `start_require_ready`
   - 开启 `close_require_spec_sync`

3. **阶段 3：团队协作**
   - 前端项目开启 `frontend_ux_strict` 和 `require_ux_spec`
   - 多模块项目开启 `require_linkage_spec` 和 `require_reference_module`

4. **阶段 4：上线准备**
   - 开启所有严格模式
   - 补全 L2 工程护栏

### 常见问题

**Q: 开启严格模式后，任务启动被阻断怎么办？**

A: 按提示补全缺失的规范文件：
- 前端项目：补全 `ux-spec.md`
- 多模块项目：补全 `module-linkage.md`
- 使用模板快速生成：`.rpi-blueprint/specs/l0/*.template.md`

**Q: 如何临时关闭某个检查？**

A: 修改 `runtime.json` 对应配置项为 `false`，完成任务后再改回 `true`。

**Q: 配置项太多，如何快速上手？**

A: 使用推荐配置模板，根据项目类型直接复制对应的配置。

---

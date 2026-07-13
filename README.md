<div align="center">

# RPI Workflow

**把模糊创意转化为可信规范，再通过 Spec、TDD 与 Eval 可靠交付。**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3](https://img.shields.io/badge/Python-3.8%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Codex CLI](https://img.shields.io/badge/Codex_CLI-compatible-111111)](COMPATIBILITY.md)
[![Claude Code](https://img.shields.io/badge/Claude_Code-compatible-D97757)](COMPATIBILITY.md)

[快速开始](QUICKSTART.md) · [命令手册](COMMANDS.md) · [兼容说明](COMPATIBILITY.md) · [产品设计](prd.md)

</div>

---

## RPI Workflow 是什么？

RPI Workflow 是一个面向普通创意者的 AI 产品定义与可靠交付框架。

用户可以只提供一段模糊想法、复制的产品功能描述，或者来自不同平台的功能组合。RPI 不会直接把这些内容当成正式需求，而是先区分原始素材、系统推断、待验证假设和当前产品事实，再进入规范与开发流程。

```text
原始素材
  → 冲突与平台分析
  → 产品方向与验证
  → PRD / Spec
  → RPI 任务
  → TDD / Eval
  → 质量门控
  → 证据与持续演进
```

RPI Core 与具体 Agent 解耦。目前为 **Codex CLI** 和 **Claude Code CLI** 提供适配层，两端共享同一份产品事实、Spec、任务状态和交付证据。

## 为什么需要它？

普通 AI 编码往往从一句需求直接进入实现。短期很快，长期容易出现需求误解、局部修补、架构漂移和无法复盘的问题。

RPI 针对这些问题提供完整闭环：

| 问题 | RPI 的处理方式 |
|---|---|
| 输入只是复制文案或功能拼盘 | 保留原文，拆分功能，识别营销词和隐含假设 |
| Web、桌面端、驱动能力互相混合 | 推断运行平台、权限、依赖和技术冲突 |
| AI 推断被误当成用户需求 | 使用主张成熟度，未经证据不能晋升为产品事实 |
| Agent 未理解规范就开始写代码 | 正式实现必须绑定任务和 `spec_refs` |
| 测试只是实现后补写 | 要求 Red → Green → Refactor 及真实执行证据 |
| AI 功能输出不确定 | 使用固定 Eval、成本、延迟和模型升级回归 |
| 多轮迭代后规范失真 | Spec 同步、任务归档、根因分类和反熵检查 |
| 不同 Agent 行为不一致 | 平台无关 Core + Codex/Claude Adapter |

## 核心原则

### 输入不是需求

用户原始输入会先进入产品主张模型：

```text
raw → inferred → hypothesis → selected / validated → fact
```

只有经过选择或验证、并提供证据的内容，才有资格成为当前产品事实。

### Spec 驱动正式开发

进入正式开发后，Agent 不能只依据最后一句对话修改代码。当前产品事实、Spec、任务验收和安全边界共同构成执行约束。

### TDD 驱动实现

确定性行为遵循：

```text
Red → Green → Refactor → Regression
```

AI 非确定性行为采用 Eval-Driven Development，通过固定数据集、质量指标、成本、延迟、拒答和模型升级回归进行验证。

### 平台配置不是事实源

`.claude/`、`.codex/` 和 `.agents/` 只是平台适配面。共享事实和状态位于 `.rpi-outfile/`，平台无关定义位于 `.rpi/`。

## 快速开始

### 环境要求

- Python 3
- `jq`
- Codex CLI 或 Claude Code CLI
- `rg`（推荐，缺失时可降级）

### 安装到项目

```bash
git clone https://github.com/inkeds/rpi-workflow.git /tmp/rpi-workflow
cd /path/to/your-project

cp -R /tmp/rpi-workflow/.rpi .
cp -R /tmp/rpi-workflow/.rpi-blueprint .
cp -R /tmp/rpi-workflow/.claude .
cp -R /tmp/rpi-workflow/.codex .
cp -R /tmp/rpi-workflow/.agents .
cp /tmp/rpi-workflow/AGENTS.md .
cp /tmp/rpi-workflow/CLAUDE.md .
```

生成并检查 Agent 适配：

```bash
bash .claude/workflow/rpi.sh compat setup
bash .claude/workflow/rpi.sh compat doctor
```

> Codex 需要信任项目并审核 `/hooks`；Claude Code 需要接受 Workspace Trust。RPI 不会静默绕过未启用的门控。

### 1. 保存原始创意

```bash
bash .claude/workflow/rpi.sh idea capture \
  "做一个无需安装的网页工具，可以监控所有 Windows 应用流量并支持团队协作" \
  copied_description
```

RPI 会分析：

- “无需安装”“所有应用”等不可直接验收的表达；
- Web、Windows 桌面端和系统网络能力的运行环境差异；
- 本地数据与在线协作等潜在冲突；
- 可以继续澄清、验证和晋升的产品主张。

查看当前状态：

```bash
bash .claude/workflow/rpi.sh idea status
```

### 2. 初始化产品规范

在 Claude Code 中可以使用斜杠命令：

```text
/rpi-check env
/rpi-init 我要做一个待办事项管理工具
/rpi-init deepen
/rpi-spec expand
/rpi-check doctor
```

终端统一入口为：

```bash
bash .claude/workflow/rpi.sh <group> <action> [args...]
```

### 3. 启动并交付任务

```text
/rpi-task start 001

# Agent 按 Requirement → Plan → Implement 推进
# 正式实现使用 Red → Green → Refactor

/rpi-gates run M0
/rpi-task close pass auto 主链路通过
```

完整教程见 [QUICKSTART.md](QUICKSTART.md)。

## 工作流

```mermaid
flowchart LR
    A[原始素材] --> B[解释与冲突分析]
    B --> C[产品假设与验证]
    C --> D[当前产品事实]
    D --> E[PRD / Spec]
    E --> F[R: Requirement]
    F --> G[P: Plan]
    G --> H[I: Implement]
    H --> I[TDD / Eval]
    I --> J[Quality Gates]
    J --> K[证据与归档]
    K -->|反馈与复评| B
```

### 阶段与里程碑模型

阶段描述默认投入方向，不降低风险底线。涉及隐私、支付、系统权限或不可逆操作时，即使处于 M-1/M0 也必须执行必要的安全、确认和恢复措施。

| 阶段 | 产品目标 | 工程目标 | 典型交付 |
|---|---|---|---|
| M-1 Explore | 找到可信产品方向 | 验证最高风险能力，隔离实验代码 | 原型、Spike、实验结论 |
| M0 Validate | 验证核心用户价值 | 用 Spec + TDD 保护一条真实核心链路 | 可用 MVP、核心测试与 Eval |
| M1 Stabilize | 让产品可重复稳定使用 | 固化契约、异常、集成和回归 | 稳定版本、集成测试、基础可观测 |
| M2 Operate | 支撑长期运营 | 建立安全、审计、容量和成本治理 | SLA/SLO、恢复、审计和规模治理 |

`Vibe : Spec` 比例仅保留为关注趋势，不作为机械质量指标。Roadmap 管理未来选项，Milestone 管理当前承诺；每个 Milestone 都必须同时包含产品结果、工程证据、退出标准和明确非目标。

## 主要能力

- **Product Intelligence**：原始素材保真、去营销化、功能拆分、平台推断和冲突识别。
- **Direction Decision**：生成 0～3 个可解释方向、反对理由、验证实验和 Markdown 决策卡。
- **Claim Lifecycle**：管理推断、假设、选择、验证、事实、否定、过期和替代关系。
- **Spec Engineering**：构建、验证、同步和关联 Discovery、PRD、Spec 与 Task。
- **RPI Execution**：Requirement → Plan → Implement 的可追溯任务执行。
- **TDD & Eval**：Red/Green 证据、契约测试、E2E、三个 Eval 模板和模型回归比较。
- **Quality Gates**：按阶段执行测试、规范、架构、UX 和模块联动检查。
- **Governance**：风险矩阵、权限边界、审计包、恢复和反熵。
- **Cross-Agent Adapters**：Codex/Claude 指令、Skills、Hooks、运行时能力验证和显式降级。

## 跨 Agent 兼容

| 能力 | Codex CLI | Claude Code CLI |
|---|---|---|
| 项目指令 | `AGENTS.md` | `CLAUDE.md` 导入 `AGENTS.md` |
| 项目配置 | `.codex/config.toml` | `.claude/settings.json` |
| Skills | `.agents/skills/` | `.claude/skills/` |
| Hooks | `.codex/hooks.json` | `.claude/settings.json` |
| 共享事实 | `.rpi-outfile/` | `.rpi-outfile/` |
| 平台内核 | `.rpi/` | `.rpi/` |

详细说明和降级策略见 [COMPATIBILITY.md](COMPATIBILITY.md)。

## 常用命令

| 场景 | 命令 |
|---|---|
| 保存和分析创意 | `rpi.sh idea capture "<素材>"` |
| 查看主张状态 | `rpi.sh idea status` |
| 生成产品方向与决策卡 | `rpi.sh idea directions` |
| 选择候选方向 | `rpi.sh idea select <DIR-ID> --reason "..."` |
| 生成 Agent Adapter | `rpi.sh compat setup` |
| 检查兼容状态 | `rpi.sh compat doctor` |
| 显式验证能力 | `rpi.sh compat verify codex all --evidence "..."` |
| 查看 Eval 模板 | `rpi.sh eval list` |
| 初始化 Eval Suite | `rpi.sh eval init grounded-generation docs-qa` |
| 比较模型回归 | `rpi.sh eval compare baseline.json candidate.json` |
| 环境检查 | `/rpi-check env` |
| 初始化项目 | `/rpi-init <idea>` |
| 深化产品范围 | `/rpi-init deepen` |
| 展开规范 | `/rpi-spec expand` |
| 启动任务 | `/rpi-task start 001` |
| 运行门控 | `/rpi-gates run M0` |
| 关闭任务 | `/rpi-task close pass auto <说明>` |
| 运行 AI Eval | `/rpi-observe evals` |
| 反熵检查 | `/rpi-auto entropy --strict` |

完整参数见 [COMMANDS.md](COMMANDS.md)。

## 项目结构

```text
rpi-workflow/
├── .rpi/                 # 平台无关 Core、Schema、Skills 源和 Adapter
├── .rpi-blueprint/       # Discovery、Spec、Task 和阶段模板
├── .rpi-outfile/         # 项目运行事实源，使用时生成且默认忽略提交
├── .agents/skills/       # Codex Skills 适配产物
├── .codex/               # Codex 配置与 Hooks
├── .claude/              # Claude 配置、命令、规则、Hooks 与现有执行引擎
├── AGENTS.md             # 跨 Agent 公共入口
├── CLAUDE.md             # Claude Code 入口
├── QUICKSTART.md         # 完整入门教程
├── COMMANDS.md           # 全命令参数
├── COMPATIBILITY.md      # CLI 兼容与降级策略
└── prd.md                # 产品理论、范围和设计细节
```

## 运行档位

| Profile | 适用场景 | 特点 |
|---|---|---|
| `balanced-enterprise` | 默认 | 在效率和可控性之间平衡 |
| `strict-regulated` | 高风险、合规项目 | 强规范、强审计、低自治 |
| `auto-lab` | 实验与探索 | 更高自治，但仍保留风险边界 |

```text
/rpi-mode profile balanced-enterprise
```

## 测试

```bash
python3 -m unittest discover -s tests -v
bash -n .claude/workflow/rpi.sh
bash .claude/workflow/rpi.sh compat doctor
```

测试覆盖产品主张状态、跨平台冲突分析、Codex Hook 转换以及现有工作流回归。

## 文档

- [快速上手](QUICKSTART.md)
- [完整命令手册](COMMANDS.md)
- [Codex / Claude 兼容说明](COMPATIBILITY.md)
- [产品需求与理论设计](prd.md)
- [L1 模块选择指南](.rpi-blueprint/specs/l1/README.md)
- [运行配置示例](.claude/workflow/config/runtime.example.md)
- [质量门控示例](.claude/workflow/config/gates.example.md)

## Roadmap

- [x] M-1 Explore → M0 Validate → M1 Stabilize → M2 Operate 阶段模型
- [x] Spec、Task、TDD、质量门控和审计闭环
- [x] 产品素材与主张成熟度模型
- [x] Codex CLI / Claude Code CLI 双适配
- [x] 0～3 个可解释产品方向、反对意见和 Markdown 决策卡
- [x] Eval Suite 协议与结构化提取/来源生成/工具调用模板
- [x] Codex/Claude 运行时能力状态、版本失效和显式降级

当前不计划扩展 Codex CLI 和 Claude Code CLI 之外的 Agent 平台。

## 贡献

欢迎提交 Issue 和 Pull Request。修改正式执行逻辑时，请同时提供：

- 对应的 Spec 或设计依据；
- Red/Green 或其他失败/通过证据；
- 自动化测试；
- 对 Codex 和 Claude Adapter 的兼容性影响说明。

## License

[MIT](LICENSE)

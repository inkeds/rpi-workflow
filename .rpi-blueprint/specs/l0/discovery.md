# L0 Discovery（项目未定案先填这里）

## 一句话设想
- 目标：
- 目标用户：
- 高频使用场景：
- 时间窗口：

## Facts / Assumptions / Open Questions

### Facts
1.

### Assumptions
1.

### Open Questions
1.

## 4 阶段业务画布（S1-S4）

| 阶段ID | 阶段目标 | 主要输入 | 可验证输出 |
|---|---|---|---|
| S1 | 入口建模 |  |  |
| S2 | 核心决策 |  |  |
| S3 | 执行交付 |  |  |
| S4 | 回执复用 |  |  |

## 核心链路候选池

| 链路ID | 链路描述 | 覆盖阶段 | 优先级 | 置信度 |
|---|---|---|---|---|
| L1 |  | S1->S2->S3->S4 | P0 | 高/中/低 |
| L2 |  | S2->S3->S4 | P0 | 高/中/低 |
| L3 |  | S1->S2->S3->S4 | P0/P1 | 高/中/低 |
| L4 |  | S3->S4 | P1/P2 | 高/中/低 |

## MVP 候选方向评分（A/B/C）

| 维度 | A | B | C |
|---|---:|---:|---:|
| 核心业务覆盖度 |  |  |  |
| 用户价值 |  |  |  |
| 交付速度 |  |  |  |
| 技术风险 |  |  |  |
| 依赖复杂度 |  |  |  |
| 可验证性 |  |  |  |
| 运营可持续性 |  |  |  |

## 覆盖率与不确定性预算
- 覆盖率公式：已选 P0 链路数 / P0 总链路数
- A：>=40%（至少 1 主链路 + 1 异常链路，默认值）
- B：>=80%（主路径可用且可复测，默认值）
- C：=100%（并补齐治理链路，默认值）
- 低置信度链路占比建议 <= 30%（默认值）
- 可在 `.claude/workflow/config/runtime.json` 调整：
  - `mvp_coverage_threshold_a`
  - `mvp_coverage_threshold_b`
  - `mvp_coverage_threshold_c`
  - `mvp_low_confidence_ratio_max`

## DDD-Lite 语义与边界
- 统一语言（Ubiquitous Language）：
  - 术语1：
  - 术语2：
  - 术语3：
  - 术语4：
  - 术语5：
  - 术语6：
- 限界上下文（Bounded Context）：
  - C1 [Core]：
  - C2 [Supporting]：
- 业务不变量（Domain Invariants）：
  - R1：
  - R2：
  - R3：
- 已选上下文（M0）：
  - C1 [Core]

## 结论
- 选择方向：
- 覆盖率目标：
- 优先级调权（可选）：
  - 提升 Lx:
  - 降权 Ly:
- 加权覆盖率目标：
- M0 Must（1-3）：
  - L1:
- M0 Won't（>=3）：
  - L3:
- 成功指标（2-4）：

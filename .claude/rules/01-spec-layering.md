# 规范分层存放（解决全量阅读噪声）

## 问题

Spec Coding 通常单文件，AI 开发会全量读取，易带入无关上下文，导致注意力稀释。

## 结构

将规范拆为事实源索引 + 分层模块：

- `.rpi-outfile/specs/00_master_spec.md`：唯一事实源索引（Single Source of Truth）
- `.rpi-outfile/specs/l0/discovery.md`：未定案项目先做方向与边界锁定
- `.rpi-outfile/specs/l0/`：必选底座（Epic/Spec/Milestone/Tasks）
- `.rpi-outfile/specs/l1/`：可选功能模块（按痛点选 **1-2 个**，同类痛点只选一个）
- `.rpi-outfile/specs/l2/`：上线前工程护栏（M2 阶段启用）
- `.rpi-outfile/specs/phases/`：阶段注入材料（M-1/M0/M1/M2）

**L1 选择约束**：同类痛点只加一个模块，总数 ≤ 2。稳定的行为与契约结论应整合到 `l0/spec.md`；产品事实、能力、不变量、变更和决策分别读取其治理状态，不得把所有治理信息压缩进单一 Spec。
模块清单及选择指南见 `.rpi-blueprint/specs/l1/README.md`。

## 读取策略

1. 默认只读 `00_master_spec.md` + 当前任务相关子文档。
2. 禁止每次任务全量读取 `.rpi-outfile/specs/`。
3. 若发现冲突，先判断其属于 Fact、Capability、Invariant、Change/Decision 还是 Spec，再更新对应权威位置。

## 回写策略

行为或契约变化完成后，至少维护：

1. 实际变更
2. 验收结果
3. 风险与例外
4. 下一步动作

局部修复不强制修改 Spec，但必须保留测试、执行与关闭证据。

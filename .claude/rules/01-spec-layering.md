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
- `.rpi-outfile/specs/phases/`：阶段注入材料（M0/M1/M2）

**L1 选择约束**：同类痛点只加一个模块，总数 ≤ 2。L1 内容最终必须整合到 `l0/spec.md`，AI 只认 spec。
模块清单及选择指南见 `.rpi-blueprint/specs/l1/README.md`。

## 读取策略

1. 默认只读 `00_master_spec.md` + 当前任务相关子文档。
2. 禁止每次任务全量读取 `.rpi-outfile/specs/`。
3. 若发现规范冲突，只允许回写到事实源索引后再执行。

## 回写策略

每次实现完成后，至少回写：

1. 实际变更
2. 验收结果
3. 风险与例外
4. 下一步动作

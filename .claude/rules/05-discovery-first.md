# Discovery First（未定案项目）

## 触发条件

当项目方向、目标用户、核心功能尚未明确时，禁止直接进入实现。

## 强制步骤

1. 先完善 `.rpi-outfile/specs/l0/discovery.md`
2. 再确认 `.rpi-outfile/specs/l0/mvp-skeleton.md`
3. 用户确认方向 + 核心功能 + Won't 后，才允许 `/rpi-spec expand`
4. 未确认前，禁止进入 `/rpi-task start` 的真实实现任务

## 验证点

- 方向 A/B/C 已选定
- M0 Must（1-3）已明确
- M0 Won't（>=3）已明确
- 成功指标（2-4）已明确

## 多模块项目强制骨架阶段

### 触发条件

当项目包含 ≥3 个功能模块且存在跨模块联动时，必须先完成全局骨架阶段。

### 强制步骤

1. 先完成《模块边界与联动关系说明书》（`.rpi-outfile/specs/l0/module-linkage.md`）
2. 前端项目必须完成《UX 交互规范》（`.rpi-outfile/specs/l0/ux-spec.md`）
3. 多模块项目必须完成《全局 UX 业务流转规范》（`.rpi-outfile/specs/l0/ux-flow.md`）
4. 用户确认模块边界 + 联动关系 + UX 流转后，才允许拆分单模块任务
5. 未完成骨架前，禁止进入单模块的 `/rpi-task start`

### 验证点

- 所有模块的职责边界已明确
- 模块间的联动关系已定义
- 数据流向规则已确定
- 前端项目的 UX 交互标准已定义
- 跨模块 UX 流转规范已明确

### 检查命令

使用 `/rpi-check skeleton` 检查全局骨架完整性。

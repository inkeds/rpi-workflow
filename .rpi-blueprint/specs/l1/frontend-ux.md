# L1 模块：前端 UX 规范增强

> 本模块为前端项目提供系统化的 UX 规范增强，解决 AI 开发中常见的 UX 实现偏差问题。

## 适用场景

- 中后台管理系统
- 表单密集型项目
- 需要统一 UX 交互标准的前端项目
- 多人协作的前端项目

## 不适用场景

- 纯后端项目
- 简单的静态页面
- 无交互的展示型页面

## 核心价值

1. **强制 UX 规范优先**：在代码实现前，先定义 UX 交互标准
2. **避免 AI 实现偏差**：通过明确的禁止项，避免 AI 生成不符合预期的 UX 实现
3. **统一交互模式**：确保项目内所有页面的 UX 风格一致
4. **提升开发效率**：通过标杆模块和参考示例，减少 AI 的决策成本

## 与 L0 的关系

- **L0 提供**：基础的 `ux-spec.md` 模板
- **L1 增强**：
  - 更详细的 UX 规范细则
  - 标杆模块机制
  - UX 合规性自动检查
  - 上下文自动注入

## 必需文件

### 1. UX 交互规范（必选）

**文件路径**：`.rpi-outfile/specs/l0/ux-spec.md`

**内容要求**：
- UI 组件库说明
- 表格 CRUD 标准实现
- 表单设计规范
- 弹窗/抽屉使用规则
- 按钮布局规范
- 数据展示规范
- 反馈体系
- 禁止行为清单

**模板位置**：`.rpi-blueprint/specs/l0/ux-spec.template.md`

### 2. 标杆模块参考（推荐）

**文件路径**：`.rpi-outfile/specs/l0/reference-module.md`

**内容要求**：
- 标杆模块的选择（推荐用户管理或基础 CRUD 模块）
- 标杆模块的核心实现
- 代码结构说明
- UX 实现要点
- 可复用的组件和方法

**作用**：
- 作为后续模块的参考模板
- 一句"参考标杆模块实现"比 10 条空泛规范更有效
- 确保所有模块的 UX 风格一致

### 3. UX 上下文包（自动生成）

**文件路径**：`.claude/workflow/context/ux.jsonl`

**内容**：
- `ux-spec.md`（UX 规范）
- `reference-module.md`（标杆模块）
- 项目已有页面的 UX 实现模式（自动扫描）

**触发时机**：
- 前端任务启动时，自动注入 UX 上下文包
- 确保 AI 始终能读取到 UX 规范

## 开发流程增强

### 阶段 1：UX 规范定义

**时机**：项目初始化后，代码开发前

**步骤**：
1. 使用 `/rpi-init` 初始化项目
2. 根据 `ux-spec.template.md` 补全 `ux-spec.md`
3. 明确 UI 组件库、交互标准、禁止行为
4. 使用 `/rpi-check ux` 验证规范完整性

### 阶段 2：标杆模块开发

**时机**：UX 规范定义后，批量开发前

**步骤**：
1. 选择一个基础模块作为标杆（推荐用户管理）
2. 严格按照 `ux-spec.md` 开发标杆模块
3. 完成后，整理标杆模块的核心实现到 `reference-module.md`
4. 后续所有模块必须参考标杆模块实现

### 阶段 3：批量模块开发

**时机**：标杆模块完成后

**步骤**：
1. 使用 `/rpi-task start` 启动任务，自动注入 UX 上下文包
2. 在提示词中明确要求："参考标杆模块实现，严格遵循 ux-spec.md"
3. 开发完成后，使用 `/rpi-check ux` 检查合规性
4. 通过 UX 合规性门控后，才能关闭任务

## 质量门控增强

### UX 合规性门控

**配置位置**：`.claude/workflow/config/gates.json`

**配置示例**：
```json
{
  "verify": {
    "default": [
      {
        "name": "ux_compliance_check",
        "command": "bash .claude/workflow/rpi.sh check ux --quiet",
        "description": "检查 UX 实现是否符合项目规范"
      }
    ]
  }
}
```

**检查内容**：
- 是否存在禁止的 UX 实现（如同级表单块）
- 是否复用了标杆模块的 UX 模式
- 弹窗/抽屉使用是否符合规范
- 表单布局是否符合规范
- 操作反馈是否完整

### 自动检查脚本

**执行入口**：`bash .claude/workflow/rpi.sh check ux --quiet`

**检查逻辑**：
1. 读取 `ux-spec.md` 中的禁止行为清单
2. 扫描当前任务的代码变更
3. 检查是否存在禁止的实现方式
4. 输出不符合规范的问题点和修改建议

## 命令增强

### `/rpi-check ux`

**功能**：一键检查 UX 实现合规性

**执行逻辑**：
1. 读取 `ux-spec.md` 和 `reference-module.md`
2. 扫描当前任务的代码变更
3. 对比标杆模块的实现模式
4. 输出不符合 UX 规范的问题点和修改建议

**输出示例**：
```
UX 合规性检查结果：

❌ 发现 2 个问题：

1. src/views/user/index.vue:45
   问题：表格 CRUD 使用了同级表单块
   规范：必须使用弹窗/抽屉实现
   建议：参考标杆模块 reference-module.md 的实现方式

2. src/views/order/index.vue:78
   问题：删除操作无二次确认
   规范：删除操作必须二次确认
   建议：使用 Popconfirm 或 Modal 组件

✅ 通过 3 个检查：
- 表单布局符合规范
- 弹窗使用符合规范
- 操作反馈完整
```

### `/rpi-init` 增强

**增强内容**：
1. 检测项目类型（前端/后端/全栈）
2. 前端项目自动生成 `ux-spec.md` 骨架
3. 提示用户补全 UI 组件库和交互标准
4. 多模块项目自动提示执行 `/rpi-check skeleton-init`

## Hook 增强

### `pre_tool_use.sh` 增强

**增强逻辑**：
1. 检测是否为前端代码编辑（`.vue` / `.jsx` / `.tsx` 等）
2. 检查是否已定义 `ux-spec.md`
3. 检查当前任务是否绑定了 UX 上下文包
4. 前端任务缺少 UX 规范时，阻断并提示先完成 `ux-spec.md`

**阻断提示**：
```
❌ 前端任务缺少 UX 规范

当前任务涉及前端代码修改，但未定义 UX 交互规范。

请先完成以下步骤：
1. 根据模板补全 .rpi-outfile/specs/l0/ux-spec.md
2. 使用 /rpi-check ux 验证规范完整性
3. 重新启动任务

模板位置：.rpi-blueprint/specs/l0/ux-spec.template.md
```

### `user_prompt_submit.sh` 增强

**增强逻辑**：
1. 检测用户提示词中的关键词（如"表单"、"弹窗"、"联动"）
2. 自动注入对应的 UX 规范和参考模块
3. 在注入时显示"已自动加载 UX 规范和标杆模块参考"

**注入提示**：
```
✅ 已自动加载 UX 上下文：
- ux-spec.md（UX 交互规范）
- reference-module.md（标杆模块参考）
- 已有页面的 UX 实现模式

请严格遵循 UX 规范，参考标杆模块实现。
```

## Skill 增强

### `ux-compliance-checking` Skill

**功能**：执行 UX 合规性检查并生成报告

**触发时机**：前端任务关闭前自动触发

**执行流程**：
1. 读取 `ux-spec.md` 和 `reference-module.md`
2. 扫描当前任务的所有代码变更
3. 检查 UX 实现是否符合规范
4. 生成详细的合规性报告

**输出**：
- UX 问题清单（按严重程度排序）
- 修复建议（包含参考代码）
- 合规性评分（0-100 分）

### `rpi-task-starting` Skill 增强

**增强逻辑**：
1. 启动前端任务时，自动检查 UX 规范完整性
2. 检查是否存在标杆模块参考
3. 缺失时阻断并提示先完成 UX 规范定义

## 配置增强

### `runtime.json` 增强

**新增配置项**：
```json
{
  "frontend_ux_strict": true,        // 前端 UX 严格模式
  "require_ux_spec": true,           // 前端任务必须有 UX 规范
  "require_reference_module": true,  // 多模块项目必须有标杆模块
  "auto_inject_ux_context": true     // 自动注入 UX 上下文包
}
```

**配置说明**：
- `frontend_ux_strict`：开启后，前端任务必须通过 UX 合规性门控
- `require_ux_spec`：开启后，前端任务启动前必须有 `ux-spec.md`
- `require_reference_module`：开启后，多模块项目必须先开发标杆模块
- `auto_inject_ux_context`：开启后，自动注入 UX 上下文包

### `gates.json` 预设

**新增预设**：`gates.frontend.json`

**预设内容**：
```json
{
  "verify": {
    "default": [
      {
        "name": "discovery_complete",
        "command": "bash .claude/workflow/rpi.sh check discovery --quiet"
      },
      {
        "name": "ux_spec_complete",
        "command": "test -f .rpi-outfile/specs/l0/ux-spec.md"
      },
      {
        "name": "ux_compliance_check",
        "command": "bash .claude/workflow/rpi.sh check ux --quiet"
      }
    ]
  },
  "phase_gates": {
    "M0": [
      {
        "name": "unit_tests",
        "command": "npm test"
      },
      {
        "name": "lint",
        "command": "npm run lint"
      }
    ]
  }
}
```

## 最佳实践

### 1. 先定义 UX 规范，再开发代码

**错误做法**：
```
直接让 AI 开发表格 CRUD → AI 生成同级表单块 → 不符合预期 → 返工
```

**正确做法**：
```
1. 补全 ux-spec.md，明确禁止同级表单块
2. 开发标杆模块，验证 UX 规范
3. 后续模块参考标杆模块实现
4. 通过 UX 合规性门控
```

### 2. 使用标杆模块作为参考

**错误做法**：
```
每次都给 AI 长篇的 UX 规范描述 → AI 理解偏差 → 实现不一致
```

**正确做法**：
```
一句"参考标杆模块实现" → AI 直接复用已验证的模式 → 风格统一
```

### 3. 利用自动检查，而非人工审查

**错误做法**：
```
开发完成后人工检查 UX → 发现问题 → 返工 → 效率低
```

**正确做法**：
```
开发完成后自动执行 /rpi-check ux → 自动发现问题 → 快速修复
```

## 常见问题

### Q: 是否所有前端项目都需要这个模块？

A: 不是。简单的静态页面或展示型页面不需要。主要适用于中后台管理系统、表单密集型项目。

### Q: 标杆模块必须是用户管理吗？

A: 不是。推荐用户管理是因为它通常包含完整的 CRUD 操作。你可以选择任何基础模块作为标杆。

### Q: UX 规范定义后可以修改吗？

A: 可以。但修改后需要同步更新标杆模块和已有页面，确保一致性。

### Q: 如何处理特殊页面的 UX 需求？

A: 在 `ux-spec.md` 中增加"例外情况"章节，明确哪些页面可以使用特殊的 UX 实现，并说明原因。

## 与其他 L1 模块的组合

### 与 `l1/iteration.md` 组合

- 适用场景：需要分阶段迭代的前端项目
- 组合价值：M0 阶段定义 UX 规范，M1/M2 阶段逐步完善

### 与 `l1/contract.md` 组合

- 适用场景：前后端分离项目
- 组合价值：前端 UX 规范 + 后端接口契约，双重保障

### 与 `l1/domain.md` 组合

- 适用场景：复杂业务的前端项目
- 组合价值：领域分层 + UX 规范，确保业务逻辑和交互体验都符合标准

## 总结

本模块通过以下机制，解决 AI 开发中的前端 UX 实现偏差问题：

1. **规范优先**：强制在代码开发前定义 UX 规范
2. **标杆模块**：通过参考模板减少 AI 决策成本
3. **自动注入**：确保 AI 始终能读取到 UX 规范
4. **自动检查**：通过门控和命令自动发现 UX 问题
5. **Hook 阻断**：在源头阻止不符合规范的实现

---

---
name: ux-compliance-checking
description: 执行 UX 合规性检查并生成报告
---

# 功能说明

执行 UX 合规性检查并生成详细报告，确保前端代码符合项目的 UX 交互标准。

# 触发时机

前端任务关闭前自动触发（当 `frontend_ux_strict` 为 `true` 时）。

# 执行流程

1. 读取 `.rpi-outfile/specs/l0/ux-spec.md`（UX 规范）
2. 读取 `.rpi-outfile/specs/l0/reference-module.md`（标杆模块，如果存在）
3. 扫描当前任务的所有代码变更
4. 检查 UX 实现是否符合规范
5. 生成详细的合规性报告

# 检查项

## 1. 禁止的 UX 实现

- 表格 CRUD 使用同级表单块（禁止）
- 删除操作无二次确认（禁止）
- 表单提交无加载状态（禁止）
- 操作无反馈提示（禁止）

## 2. 标杆模块对比

- 代码结构是否一致
- UX 实现模式是否一致
- 组件使用是否一致

# 输出格式

```json
{
  "status": "pass" | "fail",
  "score": 85,
  "issues": [
    {
      "severity": "error",
      "file": "src/views/user/index.vue",
      "line": 45,
      "message": "表格 CRUD 使用了同级表单块（禁止）"
    }
  ]
}
```

# 使用方法

```bash
/rpi-check ux
```

# 配置

在 `runtime.json` 中：

```json
{
  "frontend_ux_strict": true
}
```

---
name: module-linkage-validating
description: 执行模块联动完整性验证并生成报告
---

# 功能说明

验证多模块项目的联动实现是否完整，检查模块间数据流转、事件通信、异常处理是否符合规范。

# 触发时机

多模块项目任务关闭前自动触发（当 `linkage_strict_mode` 为 `true` 时）。

# 执行流程

1. 读取 `.rpi-outfile/specs/l0/module-linkage.md`（联动规范）
2. 读取 `.rpi-outfile/specs/l0/ux-flow.md`（UX 流转规范，如果存在）
3. 扫描当前任务涉及的跨模块代码变更
4. 对比联动关系表，检查是否有遗漏
5. 生成联动完整性报告

# 检查项

## 1. 联动关系完整性

- 联动关系表中定义的所有联动是否已实现
- 是否存在未定义的隐式联动
- 数据流向是否符合单向数据流原则

## 2. 通信规范

- 跨模块通信是否使用规定方式（事件总线/全局状态/API）
- 事件命名是否符合 `[模块名]:[事件名]` 规范
- 是否存在直接跨模块内部调用（禁止）

## 3. 异常处理

- 联动失败是否有回滚/补偿机制
- 是否记录了联动操作日志

# 输出格式

```json
{
  "status": "pass" | "fail",
  "modules_checked": 4,
  "linkages_verified": 6,
  "issues": []
}
```

# 使用方法

```bash
/rpi-check linkage
```

# 配置

在 `runtime.json` 中：

```json
{
  "linkage_strict_mode": true
}
```

# gates.json 配置说明

本文件说明 `gates.json` 的配置结构和使用方法。

## 配置结构

```json
{
  "verify": {
    "default": [],
    "M0": [],
    "M1": [],
    "M2": []
  },
  "phase_gates": {
    "M0": [],
    "M1": [],
    "M2": []
  },
  "commands": {}
}
```

`phase_gates` 支持两种格式：

- **推荐格式**：名称数组 + `commands` 映射（清晰、可复用）
- **兼容格式**：对象数组 `[{"name":"x","command":"y"}]`（自包含，无需 commands 映射）

## verify 层（预检查）

在 `phase_gates` 执行前运行，用于检查规范完整性和前置条件。

### default

所有阶段都会执行的预检查。

**示例**：
```json
{
  "verify": {
    "default": [
      {
        "name": "discovery_complete",
        "command": "bash .claude/workflow/rpi.sh check discovery --quiet",
        "description": "检查 discovery 完整性"
      },
      {
        "name": "contract_spec_complete",
        "command": "bash .claude/workflow/rpi.sh check contract --quiet",
        "description": "检查契约规范是否存在"
      }
    ]
  }
}
```

### M0/M1/M2

特定阶段的预检查，会在 `default` 之后执行。

**示例**：
```json
{
  "verify": {
    "M1": [
      {
        "name": "guardrails_exists",
        "command": "test -f .rpi-outfile/specs/l2/engineering-guardrails.md",
        "description": "检查工程护栏是否存在"
      }
    ]
  }
}
```

## phase_gates 层（质量门控）

在 `verify` 通过后执行，用于检查代码质量。

### M0（核心链路）

**推荐配置**（名称数组 + commands 映射）：
```json
{
  "phase_gates": {
    "M0": ["unit_tests", "lint"]
  },
  "commands": {
    "unit_tests": "npm test",
    "lint": "npm run lint"
  }
}
```

### M1（功能稳定）

**推荐配置**：
```json
{
  "phase_gates": {
    "M1": ["unit_tests", "integration_tests", "contract_tests", "lint", "typecheck"]
  },
  "commands": {
    "unit_tests": "npm test",
    "integration_tests": "npm run test:integration",
    "contract_tests": "npm run test:contract",
    "lint": "npm run lint",
    "typecheck": "npm run typecheck"
  }
}
```

### M2（上线准备）

**推荐配置**：
```json
{
  "phase_gates": {
    "M2": ["unit_tests", "integration_tests", "e2e_tests", "lint", "typecheck", "security_scan"]
  },
  "commands": {
    "unit_tests": "npm test",
    "integration_tests": "npm run test:integration",
    "e2e_tests": "npm run test:e2e",
    "lint": "npm run lint",
    "typecheck": "npm run typecheck",
    "security_scan": "npm audit"
  }
}
```

## 预设配置

### gates.minimal.json（零配置）

最小预设，仅包含基础检查。

**适用场景**：快速原型、零配置上手

**内容**：
```json
{
  "verify": {
    "default": [
      {
        "name": "discovery_complete",
        "command": "bash .claude/workflow/rpi.sh check discovery --quiet"
      },
      {
        "name": "contract_spec_complete",
        "command": "bash .claude/workflow/rpi.sh check contract --quiet"
      },
      {
        "name": "scope_guard_passed",
        "command": "bash .claude/workflow/rpi.sh check scope --quiet"
      }
    ]
  },
  "phase_gates": {
    "M0": ["bootstrap_check"],
    "M1": ["bootstrap_check"],
    "M2": ["bootstrap_check"]
  },
  "commands": {
    "bootstrap_check": "bash .claude/workflow/rpi.sh check bootstrap"
  }
}
```

### gates.frontend.json（前端项目）

前端项目专用，包含 UX 合规性检查。

**适用场景**：中后台管理系统、表单密集型前端项目

**内容**：
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
    "M0": ["unit_tests", "lint"]
  },
  "commands": {
    "unit_tests": "npm test",
    "lint": "npm run lint"
  }
}
```

### gates.multi-module.json（多模块项目）

多模块项目专用，包含联动完整性检查。

**适用场景**：≥3 个模块且存在跨模块联动的项目

**内容**：
```json
{
  "verify": {
    "default": [
      {
        "name": "discovery_complete",
        "command": "bash .claude/workflow/rpi.sh check discovery --quiet"
      },
      {
        "name": "skeleton_complete",
        "command": "test -f .rpi-outfile/specs/l0/module-linkage.md"
      },
      {
        "name": "linkage_integrity_check",
        "command": "bash .claude/workflow/rpi.sh check linkage --quiet"
      }
    ]
  },
  "phase_gates": {
    "M0": ["unit_tests", "integration_tests", "linkage_tests", "lint"]
  },
  "commands": {
    "unit_tests": "npm test",
    "integration_tests": "npm run test:integration",
    "linkage_tests": "npm run test:linkage",
    "lint": "npm run lint"
  }
}
```

## 使用方法

### 方式 1：使用预设

```bash
# 零配置
cp .claude/workflow/config/gates.minimal.json .claude/workflow/config/gates.json

# 前端项目
cp .claude/workflow/config/gates.frontend.json .claude/workflow/config/gates.json

# 多模块项目
cp .claude/workflow/config/gates.multi-module.json .claude/workflow/config/gates.json
```

### 方式 2：自动配置

```bash
# 最小配置
/rpi-gates setup minimal

# 标准配置（自动扫描 package.json）
/rpi-gates setup standard
```

### 方式 3：手动配置

直接编辑 `.claude/workflow/config/gates.json`。

## 常见配置示例

### Monorepo 项目

使用 affected 范围执行：

```json
{
  "phase_gates": {
    "M0": ["unit_tests", "lint"]
  },
  "commands": {
    "unit_tests": "pnpm turbo run test --filter=...[HEAD^1]",
    "lint": "pnpm turbo run lint --filter=...[HEAD^1]"
  }
}
```

### Python 项目

```json
{
  "phase_gates": {
    "M0": ["unit_tests", "lint", "typecheck"]
  },
  "commands": {
    "unit_tests": "pytest tests/",
    "lint": "flake8 src/",
    "typecheck": "mypy src/"
  }
}
```

### Go 项目

```json
{
  "phase_gates": {
    "M0": ["unit_tests", "lint", "build"]
  },
  "commands": {
    "unit_tests": "go test ./...",
    "lint": "golangci-lint run",
    "build": "go build ./..."
  }
}
```

### Rust 项目

```json
{
  "phase_gates": {
    "M0": ["unit_tests", "lint", "build"]
  },
  "commands": {
    "unit_tests": "cargo test",
    "lint": "cargo clippy -- -D warnings",
    "build": "cargo build"
  }
}
```

## 执行顺序

```mermaid
graph LR
    A[/rpi-gates run M0] --> B[verify.default]
    B --> C[verify.M0]
    C --> D[phase_gates.M0]
    D --> E{全部通过?}
    E -->|是| F[门控通过]
    E -->|否| G[门控失败]

    style F fill:#d4edda
    style G fill:#f8d7da
```

## 失败处理

### verify 失败

- 阻断任务关闭
- 提示补全缺失的规范文件
- 使用模板快速生成

### phase_gates 失败

- 阻断任务关闭
- 输出详细的错误信息
- 修复代码后重新执行

## 调试技巧

### 预览门控配置

```bash
/rpi-gates preview
```

### 单独执行某个门控

```bash
# 直接执行命令
npm test

# 或使用 bash
bash -c "npm test"
```

### 跳过门控（不推荐）

临时关闭 `runtime.json` 中的 `strict_mode`：

```json
{
  "strict_mode": false
}
```

## 常见问题

**Q: 门控命令找不到怎么办？**

A: 确保命令在 `package.json` 的 `scripts` 中定义，或使用绝对路径。

**Q: 门控执行时间过长怎么办？**

A: 
- Monorepo 项目使用 affected 范围
- 单元测试使用并行执行
- 考虑将慢速测试移到 M1/M2 阶段

**Q: 如何添加自定义门控？**

A: 在 `phase_gates` 中添加新的门控项，指定 `name`、`command` 和 `description`。

**Q: verify 和 phase_gates 有什么区别？**

A:
- `verify`：检查规范完整性，失败时提示补全规范
- `phase_gates`：检查代码质量，失败时提示修复代码

---

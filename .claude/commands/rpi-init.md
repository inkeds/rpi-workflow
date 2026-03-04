---
description: 初始化与范围深化入口（setup/deepen/bootstrap）
argument-hint: <idea> [platform] | deepen [idea] [platform] | bootstrap [--force] [idea] [platform]
---

执行规则：

1. 默认把参数视为 `setup`：
   - `/rpi-init 我要做一个统一用户管理系统`
   - 实际执行：`bash .claude/workflow/rpi.sh init setup <args>`
2. 深化 MVP：
   - `/rpi-init deepen`
   - 或 `/rpi-init deepen <idea> [platform]`
3. 仅做基线覆盖：
   - `/rpi-init bootstrap [--force] [idea] [platform]`

输出要求（重要）：

1. 当执行 `deepen` 后，必须在回复中并列展示 `A/B/C` 三个方向差异，至少包含：
   - 业务段范围
   - 覆盖门槛
   - Must 候选
   - Won't 候选
2. 不得只给推荐方向；推荐方向只能作为 `A/B/C` 对照后的结论。
3. 若终端输出被折叠（如 `... +N lines`），你仍需基于完整命令输出给出上述对照摘要。

请把用户输入原样透传给：

```bash
bash .claude/workflow/rpi.sh init $ARGUMENTS
```

若用户未提供参数，提示用法并给出最小示例：
`/rpi-init 我要做一个订单管理系统`

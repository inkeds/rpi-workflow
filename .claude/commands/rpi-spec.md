---
description: Spec 工程入口（build/verify/sync/link/expand）
argument-hint: <build|verify|sync|link|expand> [args]
---

执行：

```bash
bash .claude/workflow/rpi.sh spec $ARGUMENTS
```

示例：
- `/rpi-spec build --quiet`
- `/rpi-spec verify --scope all --quiet`
- `/rpi-spec sync`
- `/rpi-spec link --quiet`
- `/rpi-spec expand`（自动读取 discovery/init_summary 的当前确认）
- `/rpi-spec expand 确认方向B，Must链路L1,L2,L3，Won't链路L4，加权覆盖率85%`

若用户未给动作，先提示动作列表，不要猜测。

---
description: 任务生命周期入口（start/pause/resume/abort/close/phase/status）
argument-hint: <start|pause|resume|abort|close|phase|status> [args]
---

将参数原样透传给：

```bash
bash .claude/workflow/rpi.sh task $ARGUMENTS
```

示例：
- `/rpi-task start 001`
- `/rpi-task pause 临时切换线上问题`
- `/rpi-task resume`
- `/rpi-task phase M1 补齐异常链路`
- `/rpi-task close pass auto M0链路通过`
- `/rpi-task status`

若用户遗漏动作，先提示动作列表，再让用户补齐。

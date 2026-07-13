---
name: rpi-task-executing
description: Execute implementation under RPI with TDD and evidence capture. Use when actively writing tests/code for a task and you need deterministic execution with traceable logs.
---

# RPI Task Executing

1. 先执行至少 1 条“可定位测试范围”的失败测试命令形成 Red 证据，再写实现代码（Red -> Green -> Refactor）。
2. 每次关键实现前，确认当前阶段注入清单（M0/M1/M2）。
3. 仅修改当前任务范围内文件，避免跨模块扩散。
4. 每次工具执行后检查日志：
   - `.rpi-outfile/logs/events.jsonl`
   - `.rpi-outfile/logs/gate-results.jsonl`
5. 若出现偏差，先归类是规范缺失还是执行偏差，再继续。

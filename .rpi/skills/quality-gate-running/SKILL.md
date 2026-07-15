---
name: quality-gate-running
description: 配置、运行和解释 RPI Phase Quality Gates。用于任务关闭、合并、发布、阶段切换或回归审计；要求使用仓库真实命令和本轮新鲜证据，不允许用部分检查推断整体通过。
---

# 运行质量门禁

## 1. 校验门禁定义

读取 `.claude/workflow/config/gates.json`，确认命令确实存在并适用于当前仓库。占位、过期或只在另一环境可用的命令先修复配置；不得把无法运行记为通过。

使用预览检查当前 Profile 和 Phase 将执行什么：

```bash
bash .claude/workflow/rpi.sh gates preview
```

## 2. 运行目标与完整范围

开发中先运行最小可定位检查；关闭、合并、发布或阶段切换前运行完整 Phase Gate：

```bash
bash .claude/workflow/rpi.sh gates run [M-1|M0|M1|M2]
```

如果配置 `verify`，确认 verify 与 Phase Gate 都实际执行。

## 3. 解释结果

- 读取完整输出、退出码、失败数量和 `.rpi-outfile/logs/gate-results.jsonl`。
- 区分产品验收、测试、构建、Lint、Schema、UX、联动和运维门禁。
- 一个门禁通过不能证明未运行的门禁通过。
- 非确定性失败先复现并进入系统化调试，不直接重跑到偶然变绿。
- Gate 配置错误属于基础设施问题，不归类成产品通过。

## 4. 保存结论

报告实际运行的命令、时间、范围、结果和未运行项。失败即阻断对应完成声明；只有明确的非阻断 Warning 可以带理由保留。

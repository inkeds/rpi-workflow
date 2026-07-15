---
name: code-reviewing
description: 请求、执行和处理 RPI 代码评审。用于重大功能完成后、任务关闭或合并前、复杂修复后，以及收到人工或 Agent Review 意见时；要求按 Spec、正确性、安全、迁移和测试证据评估，不盲目接受建议。
---

# RPI 代码评审

本 Skill 改编自 `obra/superpowers` 的 requesting-code-review、receiving-code-review 和 verification-before-completion，固定参考版本 `d884ae04edebef577e82ff7c4e143debd0bbec99`。许可证见 [references/MIT.txt](references/MIT.txt)。

## 1. 准备评审范围

确定 base/head、当前 Task、Change、Spec、Capability/Invariant、迁移和验收命令。评审者必须看到需求和实际 diff，而不是只看实现摘要。

```bash
bash .claude/workflow/rpi.sh auto review --base <ref> --head <ref> --json
```

没有 Git 范围时明确列出修改文件和未提交 diff。

## 2. 评审优先级

按以下顺序找问题：

1. Blocking：安全、数据损坏、权限越界、错误迁移、核心行为错误。
2. Important：Spec 未覆盖、失败路径、兼容性、测试缺口、明显性能回归。
3. Minor：不影响正确性的可维护性和表达问题。

先验证正确性、状态、边界和证据，再看风格。不要用格式意见淹没高风险问题。

每项发现包含文件/行、触发条件、影响、证据和最小修复；无法证明时标记问题或疑问，不写成确定缺陷。

## 3. 处理评审意见

收到意见后：

1. 完整阅读并复述技术要求。
2. 对照当前代码、版本、Spec 和测试验证意见是否成立。
3. 不清楚的意见先澄清，不部分猜测实现。
4. 与用户已确认决策冲突时停止并报告。
5. 按 Blocking → 简单 Important → 复杂 Important → Minor 逐项修改和测试。

外部 Review 是待验证建议，不是自动命令。意见错误、破坏兼容或没有实际使用依据时，用代码和测试进行技术性反驳。

## 4. 复验和裁决

修复后重新运行相关测试、Gate、Reconciliation 和必要的 Review。检查：

- `.rpi-outfile/state/agent-review/latest.json`
- `.rpi-outfile/state/agent-review/review_card.latest.json`

`manual_review_required`、`rejected`、未解决 Blocking/Important 或缺少新鲜验证均阻止任务 pass 和自动合并。

# L2 工程护栏

1. 输出结构约束：Schema 校验 + 错误处理一致性
2. 产物追溯约束：任务 ID、spec_refs、context_refs 必须可回放
3. 安全约束：密钥管理、最小权限、敏感信息脱敏
4. 运行约束：限流、重试、幂等、故障恢复
5. 质量约束：verify + gates 双层门控
6. Monorepo 约束：优先 affected 范围执行测试与检查

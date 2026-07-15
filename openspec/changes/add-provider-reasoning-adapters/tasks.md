## 1. Provider 推理能力适配

- [ ] 1.1 新增规范化模型推理配置、Provider/模型能力识别和安全降级模块
- [ ] 1.2 实现 OpenAI GPT 与 DashScope/Qwen 的推理参数映射及 JSON mode 兼容规则
- [ ] 1.3 为 DeepSeek、Anthropic、Gemini 和未知模型实现明确的无参数降级结果

## 2. 运行时接入与可观测性

- [ ] 2.1 将 Run 的不可变 effective reasoning effort 绑定到所有 ModelClient 操作
- [ ] 2.2 在模型请求构建中合并适配参数并应用 response format 兼容决策
- [ ] 2.3 将实际适配结果写入 ModelInvocation usage metadata，且不保存敏感内容

## 3. 测试与验证

- [ ] 3.1 添加能力解析器单元测试，覆盖 OpenAI、Qwen 和安全降级矩阵
- [ ] 3.2 添加 ModelClient 请求体与 usage metadata 行为测试
- [ ] 3.3 添加 RunEngine 策略绑定测试并运行相关后端测试套件
- [ ] 3.4 运行 OpenSpec 校验并确认全部任务完成

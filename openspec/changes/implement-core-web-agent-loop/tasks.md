## 1. Schema 与持久化

- [ ] 1.1 定义 AgentDecision、AgentObservation、AgentReflection、AgentTurn、VerificationReport、MemoryRecord 的 Pydantic schema
- [ ] 1.2 增加 Agent turn 持久化结构，支持 turn_index、decision、observation、reflection、tool_call_id、artifact_id、memory_reads、memory_writes 和 status
- [ ] 1.3 增加 Memory 持久化结构，包含 scope、kind、content、structured_data、provenance、confidence、expires_at 和时间戳
- [ ] 1.4 编写 Alembic migration，兼容 SQLite 和 PostgreSQL JSON 字段
- [ ] 1.5 扩展 RunView/API schema，暴露 turns、memory events、chat messages 和 verification report
- [ ] 1.6 增加 repository 方法，用于创建/更新 Agent turn、记录 observation/reflection、读写 memory、查询 run audit 数据
- [ ] 1.7 增加 schema 和 repository 单元测试

## 2. 模型接口与 Mock 行为

- [ ] 2.1 扩展 ModelClient，增加 decide、reflect、finalize、extract_memory_candidates 接口
- [ ] 2.2 为 OpenAI-compatible client 增加结构化 AgentDecision/Reflection/FinalResponse 提示和 JSON 校验
- [ ] 2.3 实现确定性 MockModelClient Agent loop 路径：search -> fetch -> evidence -> finalize
- [ ] 2.4 实现模型输出 schema 错误处理，将 malformed decision 转为 reflection 或 blocked
- [ ] 2.5 增加模型接口测试，覆盖有效决策、无效决策、反思和 finalization

## 3. Agent Loop Runtime

- [ ] 3.1 新增 ContextAssembler，组装 goal、plan、recent turns、tool manifests、memory reads 和当前 observations
- [ ] 3.2 新增 ToolRouter，只允许 Web Agent mode 调用 `web_search` 和 `web_fetch`
- [ ] 3.3 ToolRouter 校验工具是否注册、是否在 allowlist、permission、side_effect_level 和输入 schema
- [ ] 3.4 新增 AgentLoop，执行 bounded plan-act-observe-reflect-finalize 循环
- [ ] 3.5 实现 max_turns、max_tool_calls、per_tool_retry_limit 和 stop condition
- [ ] 3.6 将工具成功输出转换为 AgentObservation，并链接 ToolCall
- [ ] 3.7 将工具失败、schema 错误和未授权工具请求转换为 AgentObservation，并触发 reflection
- [ ] 3.8 实现 replan 决策，保存修订计划或修订 step
- [ ] 3.9 实现 blocked / ask_user 决策，保存明确阻塞原因和所需用户动作
- [ ] 3.10 增加 Agent loop 单元测试，覆盖成功运行、未授权工具、工具失败反思、重试上限和 turn limit

## 4. Web 工具与证据流适配

- [ ] 4.1 将现有 `_execute_web_query` 的搜索/筛选/抓取逻辑迁移为 Agent loop turns
- [ ] 4.2 保留 canonical URL 去重、CrawlerPlan、Evidence Pack 构造和 source quality 计算
- [ ] 4.3 将 Evidence Pack 作为 loop 中的 artifact/observation 暴露给 finalization
- [ ] 4.4 确保 final answer 只使用已审计 ToolCall、Artifact、Memory provenance 和 VerificationReport
- [ ] 4.5 保留 mock provider 的确定性端到端 Web summary 行为
- [ ] 4.6 增加 Web Agent 集成测试，覆盖 search/fetch/evidence/finalize 完整路径

## 5. Memory 管理

- [ ] 5.1 实现 MemoryManager 的 read path：按 scope、kind、workspace/user、confidence 和 recency 召回 memory
- [ ] 5.2 实现 run memory 写入，用于保存当前 run 的观察、来源摘要、失败和中间结论
- [ ] 5.3 实现 workspace/user memory 写入校验，要求 provenance 和 confidence
- [ ] 5.4 实现缺失 provenance 的 memory write 拒绝和审计事件
- [ ] 5.5 将 memory reads/writes 关联到 AgentTurn，并暴露到 RunView
- [ ] 5.6 增加 MemoryManager 测试，覆盖召回、写入、拒绝、过期和 provenance

## 6. Verification 与结果

- [ ] 6.1 新增 VerificationEngine，基于 observations、Evidence Pack、source quality、failed sources 和 memory provenance 生成 VerificationReport
- [ ] 6.2 将 verification failure 反馈给 AgentLoop，允许 reflect/replan/finalize_with_warnings
- [ ] 6.3 统一 final result 结构，包含 answer、findings、sources、caveats、verification_notes、memory_references 和 audit refs
- [ ] 6.4 增加证据不足、低质量来源、部分抓取失败、memory provenance 引用的验证测试

## 7. API 与事件流

- [ ] 7.1 保持 `/api/runs` 创建入口兼容，同时默认使用 Web Agent loop
- [ ] 7.2 扩展 run events，增加 `agent_turn.created`、`agent_turn.updated`、`memory.read`、`memory.write`、`reflection.created`、`verification.created`
- [ ] 7.3 确保 SSE 或轮询能驱动聊天 UI 的实时进度
- [ ] 7.4 增加 API 测试，覆盖 run 创建、turn 事件、memory 事件和最终 RunView

## 8. 聊天式前端

- [ ] 8.1 重构前端数据类型，增加 ChatMessage、AgentTurnView、MemoryEvent、VerificationReportView
- [ ] 8.2 将主界面从双栏 dashboard 改为 Gemini 风格聊天窗口，保留液态玻璃视觉风格
- [ ] 8.3 实现用户消息、Agent reasoning summary、工具事件、反思事件、最终答案 bubble
- [ ] 8.4 实现来源卡片，展示 URL、标题、质量评分、抓取策略和 warning
- [ ] 8.5 实现 memory read/write 紧凑展示，并在详情中展示 scope、kind、confidence、provenance
- [ ] 8.6 实现可展开 audit drawer，展示 timeline、turns、tool calls、artifacts、Evidence Pack 和 verification report
- [ ] 8.7 确保桌面和移动端布局不重叠，composer 固定在可用区域内
- [ ] 8.8 增加前端测试，覆盖聊天提交、工具事件、反思、memory、来源质量和 blocked 状态

## 9. 配置、文档与验证

- [ ] 9.1 增加 Web Agent loop 配置项：max_turns、max_tool_calls、retry limits、memory write 开关
- [ ] 9.2 更新 README，说明 Web-only Agent loop、Memory 限制、工具 allowlist 和聊天 UI
- [ ] 9.3 运行后端测试、前端测试、lint 和 build
- [ ] 9.4 使用 mock provider 在浏览器中完成一次端到端聊天式 Agent 验证
- [ ] 9.5 更新 OpenSpec task 状态，并记录第一版不包含高风险工具和 embedding memory

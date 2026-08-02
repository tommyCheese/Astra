## 1. 模式与持久化模型

- [x] 1.1 新增 AnswerMode、RunExecutionProfile 与后端 profile 解析器，并覆盖快速和可信策略单元测试
- [x] 1.2 为偏好记录增加 preferred_answer_mode、为 Run 增加 answer_mode，并创建兼容历史数据的 Alembic 迁移
- [x] 1.3 扩展偏好 API、CreateRun/RunView Schema 与仓储序列化，持久化模式及不可变 profile 快照

## 2. 共享运行时与校验分层

- [x] 2.1 让 RunEngine 根据 profile 选择最小契约或完整 TaskContract，同时继续复用规范计划与 AgentLoop
- [x] 2.2 让 AgentLoop finalization 按 assurance level 执行基础保障或完整 VerificationEngine/CompletionGate，并保持统一 RunResult
- [x] 2.3 确保 waiting_user 续跑沿用原 Run 模式/profile，且两种模式共享工具、权限、取消和事件协议
- [x] 2.4 增加后端 API、仓储、运行时与迁移回归测试，覆盖历史 Run、快速模式和可信校验终态

## 3. 前端模式体验

- [x] 3.1 扩展前端 API 与状态模型，使用 touched guard 和顺序保存持久化可信模式开关
- [x] 3.2 在聊天 Composer 增加常驻、可访问、响应式的快速回答/可信模式开关
- [x] 3.3 快速模式隐藏详细可信策略，可信模式展示现有策略控制，并保持模型与执行审批能力一致
- [x] 3.4 在可信 Run 的过程或结果中显示已校验、带警告或未通过状态，并补齐中英文文案
- [x] 3.5 增加前端交互、持久化、请求 payload、策略渐进呈现和可信状态测试

## 4. 综合验证

- [x] 4.1 执行 Alembic 升级、目标后端测试与完整后端回归测试
- [x] 4.2 执行前端测试、TypeScript 检查和生产构建
- [x] 4.3 执行 OpenSpec 校验并检查两种模式的关键运行路径与 UI 布局

## 5. 极速快速回答

- [x] 5.1 让 standard Run 跳过 TaskContract、规范计划和 AgentState 初始化，直接进入共享 AgentLoop
- [x] 5.2 在 standard AgentLoop 中跳过 ObservationEvaluator、反思、Memory 写入、VerificationEngine 与 CompletionGate，同时保留工具权限和执行安全
- [x] 5.3 增加快速首 token、工具复用、无校验对象和 trusted 完整链路回归测试，并完成全量验证

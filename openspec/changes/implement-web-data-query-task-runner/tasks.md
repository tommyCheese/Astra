## 1. 项目脚手架

- [x] 1.1 创建用于 FastAPI 应用的后端 Python 项目结构
- [x] 1.2 增加后端依赖和配置文件，覆盖 FastAPI、Pydantic settings、SQLAlchemy、Alembic、异步 HTTP 和测试
- [x] 1.3 创建前端 React、TypeScript 和 Vite 项目结构
- [x] 1.4 增加本地开发环境文档，覆盖后端、前端、数据库、模型 API Key，以及 Web 搜索/抓取配置

## 2. 持久化运行模型

- [x] 2.1 定义后端领域模型：Task、Run、Step、ToolCall、Artifact 和最终结果数据
- [x] 2.2 增加与领域模型匹配的 SQLAlchemy 数据库模型
- [x] 2.3 为初始运行表和索引增加 Alembic migration
- [x] 2.4 实现 repository 函数，用于创建 run、更新状态、记录 step、记录 tool call、存储 artifact 和加载 run timeline
- [x] 2.5 增加单元测试，覆盖 run 创建、状态持久化、step 持久化、tool-call 持久化和 timeline 加载

## 3. 模型客户端

- [x] 3.1 实现模型提供方、模型名称、可选 base URL 和 API Key 的配置加载
- [x] 3.2 定义计划输出、工具决策和最终答案输出的结构化 schema
- [x] 3.3 实现用于结构化规划和综合调用的模型客户端接口
- [x] 3.4 为初始真实模型 API 集成增加 provider 实现
- [x] 3.5 增加测试，覆盖缺少凭据处理和结构化输出验证

## 4. 工具运行时

- [x] 4.1 定义类型化工具接口，包含名称、版本、输入 schema、输出 schema、权限分类和副作用等级
- [x] 4.2 实现工具 registry，以及对允许工具调用的验证
- [x] 4.3 实现 `web_search` 工具适配器，返回候选来源记录
- [x] 4.4 实现 `web_fetch` 工具适配器，返回规范化来源内容和元数据
- [x] 4.5 确保每次工具执行都记录成功和失败的 ToolCall 条目
- [x] 4.6 增加测试，覆盖成功 Web 搜索、失败 Web 搜索、成功 Web 抓取、失败 Web 抓取和审计元数据

## 5. 运行引擎

- [x] 5.1 实现初始 run 状态机：created、planning、executing、synthesizing、verifying、completed、completed_with_warnings、failed 和 blocked
- [x] 5.2 实现规划流程，通过模型客户端将用户目标转换为有序 Step 记录
- [x] 5.3 仅为已批准的 `web_search` 和 `web_fetch` 工具调用实现执行流程
- [x] 5.4 实现综合流程，基于已记录的工具输出创建最终答案
- [x] 5.5 实现验证流程，将证据标记为足够、不足、冲突或部分失败
- [x] 5.6 增加测试，覆盖成功完成 run、因无效模型输出导致 run 失败、带警告完成，以及生成有来源支撑的最终结果

## 6. 后端 API 和流式推送

- [x] 6.1 增加 API endpoint，用于从用户目标创建数据查询 run
- [x] 6.2 增加 API endpoint，用于获取 run 及其当前 timeline 和结果
- [x] 6.3 增加用于实时 run 事件的 SSE endpoint
- [x] 6.4 为 run 状态变化、step 变化、工具调用开始、工具调用完成、验证和最终结果可用发出事件
- [x] 6.5 增加 API 测试，覆盖 run 创建、run 获取、事件流、空目标拒绝和缺少配置错误

## 7. Web App

- [x] 7.1 构建主任务控制台，包含目标输入和 run 提交操作
- [x] 7.2 构建实时 timeline 视图，展示状态、steps、tool calls、警告和错误
- [x] 7.3 构建最终结果视图，展示摘要、发现、来源、限制说明和验证备注
- [x] 7.4 增加 loading、failed、completed 和 completed-with-warnings UI 状态
- [x] 7.5 增加前端测试，覆盖目标提交、timeline 渲染、结果渲染和错误状态

## 8. 端到端验证

- [x] 8.1 增加端到端 fixture 或 mocked provider 路径，用于确定性的本地验证
- [x] 8.2 验证一次成功 Web 数据查询 run，从 Web App 输入到最终带证据结果完整跑通
- [x] 8.3 验证一次失败或证据不足的 Web 数据查询 run 会报告有用的限制信息
- [x] 8.4 运行后端测试、前端测试、lint 和所有已配置的类型检查
- [x] 8.5 更新 README，加入第一条纵向切片说明和本地运行说明

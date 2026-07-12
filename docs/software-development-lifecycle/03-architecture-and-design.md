# 架构与详细设计

## 1. 设计目标

Astra 架构应支持通用任务而不牺牲控制力：模型负责理解和提出结构化行动；Agent Kernel 负责状态推进；Tool Router 和 Policy Gate 负责授权；Sandbox 负责隔离计算；Evidence/Verification 负责证明结果；Memory 负责带来源地积累上下文。

## 2. 当前结构与演进边界

当前仓库可见的主要边界：

- `frontend/`：React/TypeScript 对话与审计界面。
- `backend/app/api/`：HTTP 资源与输入输出协议。
- `backend/app/runner/`：Run 编排、Agent loop、推理策略和模型适配。
- `backend/app/tools/`：工具 manifest、注册、路由和实现。
- `backend/app/sandbox/`：隔离任务生命周期与 OCI executor。
- `backend/app/artifacts.py`：Artifact 校验、存储和交付引用。
- `backend/app/repositories/` 与 `db/`：事务和持久化。
- `openspec/changes/`：需求、设计、规范场景和实施任务。

这描述代码现状，不代表所有能力已生产就绪。例如沙盒化图表变更仍应以其 `tasks.md` 未完成项为准。

## 3. 设计文档最低内容

重大变更的设计至少包括：背景和目标、非目标、上下文图、组件职责、同步/异步时序、数据模型、API/schema、状态机、权限与威胁、失败模式、兼容迁移、可观测性、容量成本、测试策略、发布回滚、未决问题。

设计应列出事实、假设和决策，避免把假设包装为结论。每个不可逆或高成本决策必须比较至少一个可行替代方案。

## 4. API 与状态设计

- API 使用稳定资源标识和明确错误信封；不得返回堆栈、密钥或连接串。
- schema 变更默认向后兼容；删除字段需弃用窗口和使用量证据。
- 状态机明确定义允许迁移、触发者、幂等行为和终态。
- 后台工作必须考虑进程重启、重复投递、结果未知和取消。
- 对真实副作用，`prepared → executing → committed/failed/unknown` 等阶段应持久化。

当前 `/api/runs`、`/api/runs/{id}`、resume、events 和 Artifact content 构成核心资源面；新增接口应保持 Task/Run 与审计模型一致。

## 5. 数据设计

数据分为：业务状态、审计事件、模型上下文、Artifact、配置/密钥、遥测。每类定义 owner、分类、保留期、删除机制、加密和访问策略。

迁移设计必须说明：升级和降级路径、旧代码读新 schema、新代码读旧数据、默认值、回填速度、锁与空间影响、失败恢复、备份验证。SQLite 可用于本地确定性开发，生产数据库选择与运维要求需单独批准。

## 6. 工具与沙盒设计

`ToolSpec` 应声明输入输出 schema、capability、permission set、risk、side effect、execution backend、资源上限、超时、重试和 Artifact 行为。Tool Router 的顺序应保持确定性：注册 → enablement → schema → capability → permission/risk → budget → backend availability。

网络读取、文件写入、代码执行和外部业务操作使用不同权限。要求 gVisor 等隔离时不可静默回退。输出目录必须防路径逃逸；Artifact 验证 MIME、内容、大小、数量、checksum 和 provenance。交互 HTML 使用隔离 origin 或严格 sandboxed iframe + CSP，不能继承主站 cookie 或访问父 DOM。

## 7. 可靠性、性能与成本

每个外部依赖定义 timeout、重试条件、退避、熔断和降级。只重试确认幂等或明确未执行的操作。预算应在 Run 创建时冻结并被运行时强制执行。

容量估算至少覆盖并发 Run、每 Run turns/tool calls、数据库事件增长、Artifact 大小、模型 token、外部 API QPS 和沙盒 CPU/内存。性能优化不得删除审计或绕过验证；必要时使用摘要、分层存储和采样，但安全事件不可采样丢失。

## 8. 架构评审门禁

评审通过需确认：需求可追踪；边界与责任清晰；风险有控制；失败安全；数据可迁移；接口可兼容；可观测且可回滚；测试可执行；未决问题没有阻断项。评审结论记录批准、附条件批准或退回，并指定条件 owner。

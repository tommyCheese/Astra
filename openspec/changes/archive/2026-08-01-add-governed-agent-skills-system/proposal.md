## Why

Astra 已具备 Agent Profile、Tool Provider Plugin、权限门控、Task Workspace 和可审计运行时，但缺少一种可移植、按需加载并可在平台内持续创作的领域知识与工作流封装方式。Agent Skills 开放格式正在成为跨 Agent 产品复用 `SKILL.md`、脚本、参考资料和资产的共同约定；Astra 需要在兼容该格式的同时，让唯一的完全权限管理员能够上传、创建、编辑、测试和共享自定义 Skill，并让同一 Skill 安全地服务快速响应与可信执行。

## What Changes

- 支持导入、创建、校验和管理符合 Agent Skills 开放格式的 Skill 目录，包括 `SKILL.md`、`scripts/`、`references/` 和 `assets/`。
- 引入分层 Skill Catalog：启动或 Run 创建时只暴露名称与描述，激活后加载完整指令，执行中再按需读取资源，避免把全部 Skill 内容预载入上下文。
- 只支持两种来源：随 Astra Release 发布且不可修改的内建 Skill，以及管理员上传或在平台创建、全局共享的自定义 Skill；不引入用户、租户、workspace、project 或 Publisher 角色模型。
- 引入草稿与不可变发布版本：编辑只改变 Draft，显式发布生成新的内容摘要；普通 Run 只使用 Published Revision，草稿仅可在明确的隔离测试 Run 中使用。
- 提供基于 Monaco 的完整 Skill 创作工作台，包括多文件目录树、Markdown 源码与预览、代码编辑、搜索替换、校验诊断、Diff、版本历史、导入导出和草稿测试；编辑器不提供可绕过 Astra 运行时的终端。
- 引入确定性的 Skill 选择与激活协议，支持管理员显式点名、模型按描述匹配、多个 Skill 组合、冲突处理和每个 Run 的不可变 Skill 快照。
- 将 Skill 同时接入快速响应与可信执行：快速模式在无 DAG 的 Agent Loop 中按需激活；可信模式在 TaskContract 和完整 DAG 之前解析 Skill，并把 Skill revision 和节点所需 Skill 写入可信计划与验证生命周期。
- 将 Skill 指令作为独立的受管提示层接入 Prompt Composer，明确其低于平台 Profile/角色协议和用户意图，且不能授予工具、凭据、网络、文件或审批权限。
- 将 Skill 内脚本和命令统一路由到现有 Tool Provider Plugin、Effect Analyzer、Permission Engine、Sandbox、Workspace 和 Artifact 管线；`allowed-tools` 仅作为实验性能力请求或兼容提示，不构成预授权。
- 提供 Skill 发布前检查、内容摘要、路径约束、资源配额、禁用/撤销、审计事件和安全诊断；远程市场、自动更新和多人协作编辑不纳入第一阶段。
- 提供 Skill Catalog、工作区文件、草稿、发布版本、导入导出、启停、测试 Run 和 Run 激活记录的后端 API 与独立桌面管理界面。

## Capabilities

### New Capabilities

- `agent-skill-packages`: 定义 Agent Skills 格式兼容、目录解析、校验、资源访问、来源身份和内容摘要。
- `agent-skill-runtime`: 定义 Catalog 构建、渐进式披露、选择激活、组合、上下文装配、Run 快照和恢复语义。
- `agent-skill-governance`: 定义内建/自定义来源、Draft/Published 生命周期、启停与撤销、脚本执行边界、权限衰减、安全检查和审计管理。
- `agent-skill-authoring`: 定义多文件 Skill 编辑工作台、Draft/Published Revision、校验、预览、Diff、历史、导入导出和隔离测试。

### Modified Capabilities

- `agent-profile-runtime-composition`: 在集中式 Prompt Composer 中加入可识别、可审计且优先级受限的 Skill 指令层，并保持 Profile、角色协议、用户请求和不可信运行时数据之间的边界。
- `agent-chat-ui`: 增加 Skill 管理、运行时激活状态、来源/版本/信任诊断和 Skill 相关审批解释。
- `answer-mode-selection`: 明确快速响应和可信执行都可使用 Skill，但保持各自无 DAG 与完整可信 DAG 的产品语义。
- `general-agent-reasoning`: 让可信 TaskContract、Plan DAG、NodeExecution 和 Completion Gate 绑定冻结的 Skill revision，并让快速 Agent Loop 使用轻量 Skill 激活协议。

## Impact

- 后端：新增 Skill package parser、虚拟文件系统、Draft/Published revision service、Catalog/selection/activation service、snapshot models、API、审计事件和 Prompt Composer 集成。
- 执行运行时：Run/Node 上下文、模型决策协议、上下文压缩与恢复、Tool Catalog/Invocation Pipeline、Sandbox 和 Artifact 访问边界。
- 数据库：共享 Skill 记录、Draft 文件、不可变 Published Revision、内容摘要、启用状态、Run Skill snapshot、激活、测试和资源读取审计。
- 前端/桌面端：独立 Skill 管理工作台、Monaco 多文件编辑器、Markdown 预览、Diff/历史、导入导出、测试/发布、安全诊断以及对话中的激活记录。
- 依赖与兼容：优先复用 Agent Skills 参考校验规则；保持现有 Agent Profile、Tool Plugin 和 Run API 兼容，不引入公开市场、远程自动安装或 Skill 自行授权。

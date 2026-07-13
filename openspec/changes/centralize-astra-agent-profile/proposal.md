## Why

Astra 的身份、角色和行为原则目前散落在 planner、controller、answer、reflector 与 memory extractor 的 system prompt 中，导致产品人格难以统一评审、版本化和审计，也容易把稳定身份、动态工具能力与用户记忆混为一谈。现在需要建立一个由 Git 管理的 Agent Profile 单一事实来源，并让每次 Run 能准确记录实际使用的 Profile 版本，为后续 Memory 与 AutoDream 扩展提供稳定边界。

## What Changes

- 新增随后端发布并由 Git 管理的 `IDENTITY.md`、`SOUL.md`、`MEMORY.md`、`AUTODREAM.md` 与说明文档，分别定义 Astra 的身份目标、人格原则、记忆治理协议和未来 AutoDream 协议。
- 新增统一的 Agent Profile 加载、校验、缓存、按角色组合与哈希版本机制，替换各模型阶段重复维护 Astra 身份描述的方式。
- 明确 Profile 文档是受信任的静态产品定义；实际用户、工作区和 Run 记忆继续存入数据库，并作为不可提升权限的上下文数据处理。
- 在每次 Run 中持久化本次实际加载的文档、内容摘要和 Profile 版本，支持服务重启后的准确回显与历史审计。
- 保持工具注册、环境配置、数据库工具开关、基础设施可用性、Run 权限和预算作为实际运行能力的唯一权威来源；Profile 不得声明或授予动态能力。
- 为 `MEMORY.md` 和 `AUTODREAM.md` 预留未来扩展契约，但本 change 不增加后台 AutoDream 调度、自主执行或在线人格编辑能力。

## Capabilities

### New Capabilities

- `agent-profile-management`: 定义 Agent Profile 文档的职责边界、Git 与发布包管理、格式校验、版本摘要，以及静态 Profile 与数据库动态数据的存储边界。
- `agent-profile-runtime-composition`: 定义各模型角色如何按需组合 Profile、如何注入动态能力与记忆上下文，以及如何为每个 Run 冻结可审计的 Profile 快照。

### Modified Capabilities

无。当前仓库尚未建立主规格目录，本 change 以新能力形式固化现有提示词与 Memory 实现之上的约束。

## Impact

- 后端模型调用：`backend/app/runner/model_client.py` 中各阶段 system prompt 将改由统一组合器生成。
- Agent 上下文：`ContextAssembler` 继续提供工具清单、不可用能力和数据库 Memory，但需要与受信任 Profile 明确分层。
- 数据模型与迁移：Run 需要新增或扩展 Agent Profile 快照字段；实际 Memory 继续使用现有 `memories` 表。
- Python 发布配置：Markdown Profile 必须作为 package resources 随源码、wheel 和未来后端容器发布，加载不得依赖当前工作目录。
- 测试与文档：增加文档边界、角色加载矩阵、哈希稳定性、Run 审计、提示词去重、Memory 指令隔离和 AutoDream 非激活测试。

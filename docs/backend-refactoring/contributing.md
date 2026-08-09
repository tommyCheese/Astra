# 后端贡献指南

## 从概念开始命名

先查阅 [`domain-glossary.md`](domain-glossary.md)，再选择包、类型与变量名。ID、集合、
布尔值和时间必须携带语义，例如 `run_id`、`runs_by_id`、`has_pending_approval`、
`timeout_seconds`。避免 `data`、`obj`、`manager`、`handle`、`process` 等无法说明业务意图的
名称。注释解释约束和原因，不翻译代码。

## 模块与 SOLID 边界

- 一个模块只有一个可用一句话描述的变更原因；混合 HTTP、用例、领域规则和持久化时拆分。
- application service 编排用例；领域组件表达规则；Repository/store 负责一个聚合职责；
  projection 只读；adapter 隔离外部机制。
- 依赖抽象应来自概念所有者或中立 contracts 包。不要为了形式建立只有一个调用者、没有
  替换价值的接口。
- 用组合替代不断增长的基类；用判别 outcome 替代跨层状态魔法字符串；用显式 mapper
  隔离 HTTP、ORM、provider 和工具边界。
- 可选行为通过注入的 port 扩展，稳定领域对象不导入具体 provider。接口按调用者需要保持
  窄小，禁止重新形成全能 Repository。
- 先按业务或 Agent 能力选择包；只有同一能力内形成多个同类模块时，才增加 `models`、
  `validation`、`policies`、`services`、`normalization` 或 `transports` 二级包。禁止全局技术桶。
- 不创建 `utils.py`、`helpers.py` 或 `common.py`；纯函数放在所属能力中并以具体转换或计算命名。
- 类必须表达框架契约、领域语义、跨调用状态或多个可替换实现。纯函数包装类、单实现抽象、
  一行转发方法和只为旧 import 存在的 facade 应删除。减少代码量不能以 dict/tuple 替代清晰值对象。

## 事务、错误与副作用

事务属于应用用例。store 可 `flush` 以获得 ID 或触发约束，但不得隐藏 `commit`。在外部
等待前提交 durable intent/checkpoint，结束事务；结果在新事务中记录。错误应在所属边界
转换一次：adapter 转为 typed infrastructure error，application service 转为用例错误，
HTTP mapper 生成稳定 envelope。不得在深层返回 HTTP 状态码。

## 测试层次

1. 纯领域测试覆盖枚举、值对象、规则和所有 outcome 分支。
2. application contract 测试使用 typed fake port，验证编排、提交顺序和错误恢复。
3. Repository/adapter 集成测试使用真实数据库或 provider fixture，验证原子性与映射。
4. HTTP/SSE characterization 测试冻结公开 schema、状态码、事件顺序与恢复 cursor。
5. 并发、故障注入和性能测试覆盖 claim/fencing、取消、回滚、N+1 与外部等待事务边界。

测试公共行为，不锁定私有辅助函数、文件布局或调用次数，除非调用次数本身是性能契约。
共享数据使用 `tests/support` 中的 typed builders/fakes。

## Runtime capability 规则

- 新执行能力优先实现固定 slot：context、decision、action、observation、progress、completion 或 lifecycle；不得新增任意事件订阅器或第二个 turn loop。
- Planning、Memory/Skill authoring、AutoDream、Evolution 与 Credential administration 是控制面，不能导入 canonical Loop。Runtime 只接收冻结、窄化、可审计的 serving contribution。
- model/state/action/cancellation/event port 是强制边界。任何插件都不能覆盖 schema validation、effect analysis、authorization、approval integrity、persistence、cancellation 或 result-unknown recovery。
- API schema、ORM、public projection 与 Runtime value 只有在各自拥有独立验证、事务、授权/脱敏或协议不变量时才能同时存在；字段一一复制的 dataclass/mapper 应删除。

## 质量门禁与例外

在 `backend` 目录运行 `bash scripts/check_backend_quality.sh`。提交前还应运行全量 pytest、
OpenAPI/Alembic/metadata 兼容检查和与改动相关的故障注入测试。默认预算为模块 500 行、
函数 60 行、复杂度 10；硬限制为 800/100/15。

默认预算例外只用于已有迁移窗口，必须在 `architecture-exceptions.json` 写明精确 symbol、
原因、owner 和不超过短迭代周期的到期日。硬限制、禁止依赖、循环依赖、无效 suppression
和公共契约破坏不可例外。例外必须只减不增，并在所属重构任务完成时删除。

架构门禁还冻结生产代码量、模块、类、函数/方法和公共 symbol 的当前上限。新增无状态单方法
内部类或名称含 `legacy`、`deprecated`、`compatibility` 的符号会失败；需要此类边界时，应先
删除等量债务并在设计文档说明真实调用者、替换需求和移除条件，而不是提高预算。

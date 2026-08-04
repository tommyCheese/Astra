# 后端偶然复杂度削减记录

## 结果

本轮以删除为主，在保持 HTTP、数据库、权限、审计、恢复、Memory 和 Subagent 行为的前提下，
将生产代码从 61,883 行降至 61,539 行，模块从 307 降至 298，类从 792 降至 766，函数/方法
从 2,492 降至 2,473，公共 symbol 从 1,282 降至 1,249。

## 已删除的偶然复杂度

- 删除从未参与执行的插件 `ToolExecutor`、`ResultAdapter` 和 Web legacy result adapter 契约。
- 将 Context 组装中的 Conversation、Plan、Tool、Skill、Subagent 九个一次性 projector/transfer
  类收拢为一条直接、具名、类型化的数据流。
- 将无状态的 Run profile resolver、delegation gate、Subagent result merger、retry helper 和
  checkpoint helper 改为所属能力中的函数。
- 删除未被任何生产或测试路径消费的 Run/Subagent 状态枚举、Grounding support DTO 和 Memory
  digest helper。
- 将 Run Event 并入 Run core store，将 ToolCall 并入 step/turn activity store；删除只用于组合
  多继承的独立 store 类和模块。
- 将 Memory consolidation 的审计、来源复制和时间转换并入真实调用者，删除四个辅助模块。
- 删除 17 字段 `MemoryCreateRequest` 的逐字段复制，直接在 Run Memory owner 中完成验证、写入
  和审计。
- 将仅被 Run core 使用的配置归一化实现并入其所有者，删除独立单调用模块。

## 保留的抽象及理由

- Pydantic API 模型：外部不可信输入和 OpenAPI 契约边界。
- SQLAlchemy Record：数据库映射、关系和事务边界。
- `DelegatedModelPort`、`SubagentSupervisorPort`：存在 root/child 或 provider 替换需求的调用者端口。
- Permission、Completion、Observation 和 Reflection 策略：被多个执行阶段注入并具有独立策略
  语义，不是单次字段搬运。
- `PublicationContext`、`RollbackManifest`：跨多个原子发布/回滚步骤保持一致的不变量集合。
- `RunUnitOfWork`：明确跨 store 提交和回滚边界；其 store 数量已收缩，但不会用通用 CRUD 基类
  隐藏领域查询。

## 门禁

`architecture-rules.json` 固定上述五项规模上限。架构检查会记录现存单方法行为类，只允许数量
下降，并拒绝新的 `legacy`、`deprecated`、`compatibility` 符号。兼容代码必须有真实消费者和
移除条件，不能再作为默认扩展方式。

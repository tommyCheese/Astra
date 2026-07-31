# Astra Agent Profile

本目录保存 Astra 随后端发布的默认受信任 Agent Profile。文档由 Git 管理，只描述稳定的产品身份与治理原则，不保存用户数据，也不授予运行时能力。本机用户可以在“设置 → Agent → Agent Profile”中激活经过同一 schema 校验的覆盖；覆盖保存在 Runtime 配置中，不会写回这些源文件。

- `IDENTITY.md`：Astra 是谁、使命、长期目标和稳定边界。
- `SOUL.md`：人格、沟通气质、求真态度和协作方式。
- `MEMORY.md`：记忆写入、召回、冲突、遗忘和来源治理协议；实际记忆存储在数据库。
- `AUTODREAM.md`：后台记忆整理治理协议；只允许显式绑定 consolidation job 的专用模型操作加载，文档本身不会触发任何任务。

工具是否可用由 Tool Manifest、环境配置、数据库开关、基础设施状态、Run 权限和预算共同决定。修改本目录必须与代码、测试和 OpenSpec 一起评审。

## 存储与版本边界

新 Run 从当前激活的 Runtime Profile（没有用户覆盖时为本目录默认值）加载，并在数据库保存完整不可变快照；修改或恢复 Profile 只影响之后新建的 Run，历史和运行中的 Run 继续使用原快照。普通 Run API 只公开版本、文档标识和哈希，只有本机 Runtime 设置 API 返回可编辑全文。实际用户、工作区和 Run Memory 始终写入 `memories` 表。本目录和 Runtime Profile 均不得保存真实聊天、凭证或模型调用结果。

当前按 Run 复制快照以换取精确恢复和简单审计，并只支持一个本机用户覆盖。当 Profile 快照占数据库总量超过 5%、累计超过 1 GiB，或系统需要多 Profile、版本历史、审批发布或多用户隔离时，应通过独立 OpenSpec change 迁移为不可变 `agent_profile_revisions` 表，由 Run 引用 revision；迁移不得改变 Git 中默认 Profile 对新部署和恢复默认的权威地位。

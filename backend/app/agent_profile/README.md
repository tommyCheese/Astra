# Astra Agent Profile

本目录保存 Astra 随后端发布的受信任 Agent Profile。文档由 Git 管理，只描述稳定的产品身份与治理原则，不保存用户数据，也不授予运行时能力。

- `IDENTITY.md`：Astra 是谁、使命、长期目标和稳定边界。
- `SOUL.md`：人格、沟通气质、求真态度和协作方式。
- `MEMORY.md`：记忆写入、召回、冲突、遗忘和来源治理协议；实际记忆存储在数据库。
- `AUTODREAM.md`：未来后台记忆整理协议；当前为禁用占位，不会触发任何任务。

工具是否可用由 Tool Manifest、环境配置、数据库开关、基础设施状态、Run 权限和预算共同决定。修改本目录必须与代码、测试和 OpenSpec 一起评审。

## 存储与版本边界

新 Run 从本目录加载权威 Profile，并在数据库保存完整不可变快照；普通 Run API 只公开版本、文档标识和哈希。实际用户、工作区和 Run Memory 始终写入 `memories` 表。本目录不得保存真实聊天、用户偏好、凭证或模型调用结果。

当前按 Run 复制快照以换取精确恢复和简单审计。当 Profile 快照占数据库总量超过 5%、累计超过 1 GiB，或系统需要在线编辑和多 Profile 激活时，应通过独立 OpenSpec change 迁移为不可变 `agent_profile_revisions` 表，由 Run 引用 revision；迁移不得改变 Git 中默认 Profile 对新部署的权威地位。

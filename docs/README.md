# Astra 文档中心

本目录保存以当前代码为准的工程文档。

## 设计文档

- [Governed Subagent Runtime](governed-subagent-runtime.md)：supervisor/worker 语义、执行链路、权限衰减、适配器约束、发布门禁与运维手册。
- [Astra 系统详细设计](astra-system-detailed-design.md)：系统边界、核心领域模型、Run 执行链路、权限与审批、任务工作区、沙箱、产物、前端流式状态以及演进建议。
- [Astra Agent Graph 完整演进路线](agent-graph-evolution-roadmap.md)：从可信执行图谱到 Durable Runtime、层级自适应、多 Agent 协作与 Graph Memory 的阶段化路线；当前 OpenSpec 只聚焦阶段一。
- [可信执行图谱](trusted-execution-graph.md)：当前 Plan Graph、Runtime Trace、Evidence 分层，版本查询、实时事件和执行前自然语言修订协议。
- [Agent Skills](agent-skills.md)：Skill 包、Draft/Revision、Monaco 工作台、两种回答模式、安全边界和 API。
- [深度记忆、AutoDream 与 Agent 自进化运维](deep-memory-autodream-evolution.md)：命名空间、生命周期、召回评分、后台 consolidation、受治理候选、删除传播、评估、发布与回滚。
- [历史对话老化运维](conversation-retention-operations.md)：后台保留策略、保护条件、批量扫描、日志、启用与回滚。

## 阅读建议

- 首次了解项目：先读详细设计的“系统概览”和“一次请求的完整链路”。
- 修改 Agent Runtime：重点读“Run Engine 与 Agent Loop”。
- 规划 Agent Graph：阅读“Agent Graph 完整演进路线”，再进入对应阶段的 OpenSpec。
- 修改工具或安全策略：重点读“统一工具执行管线”和“权限、审批与授权租约”。
- 修改文件交付能力：重点读“Task Workspace、Sandbox 与 Artifact”。
- 配置历史数据生命周期：阅读“历史对话老化运维”。
- 发布跨 Session Memory 或 AutoDream：阅读“深度记忆、AutoDream 与 Agent 自进化运维”。
- 修改前端：重点读“前端状态与 SSE 展示链路”。

> 文档基线：`main` 分支提交 `e1bdc3b`。代码持续演进时，应优先相信可执行代码和测试，并同步更新本文。

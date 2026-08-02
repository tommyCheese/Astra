## Context

当前仓库包含 28 个增量数据库迁移，以及多处为旧持久化数据保留的 schema v1、字段别名、缺省回填和展示分支。用户已明确放弃所有旧数据，并要求 Astra 从空数据库进入下一阶段，因此继续维护这些路径没有产品价值。

## Goals / Non-Goals

**Goals:**

- 建立一个与当前 ORM 完全一致的数据库基线。
- 删除对旧数据库、旧 runtime profile、旧 Run snapshot、旧前端持久化 payload 的兼容读取。
- 当前持久化结构不合法时明确报错，不静默猜测。
- 清除当前数据库、WAL/SHM 和本地数据库备份中的业务数据。

**Non-Goals:**

- 不删除模型供应商的 OpenAI-compatible 协议支持。
- 不删除 Skill 的运行环境 compatibility 声明。
- 不删除正常的输入归一化、安全降级、可选字段默认值或外部服务适配。
- 不删除 Task Workspace、权限 shadow simulation、Agent Evolution shadow rollout 等当前产品概念。

## Decisions

### 1. 用单一基线替代增量迁移历史

删除旧 Alembic revisions，生成一个直接创建当前 schema 的 `0001_current_baseline`。新数据库只需执行一次迁移；任何旧数据库都不在支持范围内。

### 2. 以“持久化边界”为兼容清理判定标准

删除仅用于读取旧数据库行、旧 JSON 快照、旧 runtime profile 或旧 localStorage 结构的分支。保留面向当前模型输出的容错归一化，因为它不是旧数据迁移。

### 3. 删除旧字段而不保留占位

Memory 的 `workspace_id`、召回事件 `shadow` 等只服务旧数据的字段从 ORM、API、前端类型和基线 schema 一并删除。类似字段在其他域也按审计结果处理。

### 4. 清库通过停止服务后移除明确数据库文件完成

先停止持有 SQLite 句柄的本地后端，再移除当前数据库、WAL/SHM 与项目内数据库备份，随后执行新基线并重启。Task Workspace 和 Artifact 文件不在“数据库数据”范围内。

## Risks / Trade-offs

- [旧安装无法原地升级] → 明确作为 breaking clean-start；启动时只支持空库或当前基线。
- [误删仍有产品语义的 compatibility] → 审计按“旧持久化数据”分类，外部协议与当前 rollout 概念明确排除。
- [ORM 与基线漂移] → 增加 Alembic head、metadata 建表和空库 API smoke test。
- [运行中删除 SQLite 造成句柄悬空] → 先停止本地后端并确认文件无占用，再清理和重启。

## Migration Plan

1. 删除运行时旧数据读取分支和旧字段。
2. 用当前 ORM 生成单一 Alembic 基线。
3. 停止本地后端，移除所有项目开发数据库及 sidecar/backup。
4. 从空库执行基线，启动服务并完成新建数据烟测。

该变更不提供旧数据回滚；代码回滚只能配合重新创建空数据库。

## Open Questions

- None.

## Context

普通 Run 的记忆提取当前由模型生成候选，`MemoryManager.write_candidates` 先写入 `candidate`，随即以 `memory-extractor` 身份转换为 `active`。Repository 已具备 `candidate -> active/revoked` 状态机、来源可访问校验、状态版本并发检查和审计记录；MemoryWorkbench 也能展示包含历史状态的记录，但没有激活操作或专门的待确认视图。

本次需要把语义确认权交给本机人工操作员，同时保持 active-only 召回、命名空间隔离、稳定键版本关系及 AutoDream 人工发布合同。

## Goals / Non-Goals

**Goals:**

- 所有普通提取结果在人工确认前保持 `candidate`，不参与召回。
- 提供集中待确认列表，并展示足够的内容、来源和生命周期信息供人工判断。
- 激活和拒绝均要求状态版本、操作人和原因，并写入可审计事件。
- 同一稳定键的新候选在确认前不影响当前 active 版本；确认时原子完成替换。

**Non-Goals:**

- 不提供批量激活、自动审批、二次模型评审或远程多角色审批。
- 不改变召回排序算法、Memory scope 集合或 AutoDream 的发布/回滚协议。
- 不允许人工在确认对话框中直接编辑候选内容；修正内容需由后续候选或未来编辑流程完成。

## Decisions

1. **复用 `candidate` 作为唯一待确认状态。** 普通提取器创建成功后停止，不再调用自动 transition。候选仍记录来源和 `memory.candidate_created` Run 事件。相比新增 `pending_review`，复用现有状态避免迁移，并使召回现有 active-only 过滤天然保持安全。

2. **增加显式 activate endpoint，拒绝复用 revoke endpoint。** `POST /api/memories/{id}/activate` 接收 `expected_state_version`、`actor` 和非空 reason；现有 revoke 支持 `candidate -> revoked`，在 UI 中对 candidate 命名为“拒绝”。激活只允许 candidate，并继续执行可访问来源校验。

3. **新内容先作为并列候选保存，确认时才替换。** 当稳定键已有 active 记录时，提取器不能继续调用会立即创建 active replacement 的 `create_version`，而是创建具有下一版本号和 `supersedes_id` 的 candidate。人工激活事务锁定候选与当前 active 状态：若基础版本仍 active，则将其标记 superseded，再激活候选；若状态已变化则冲突失败并要求刷新。这样候选等待期间旧值继续可召回。

4. **待确认列表是 MemoryWorkbench 的一级过滤视图。** 已保存记忆区域提供“待确认”和“全部记录”两个筛选；待确认默认请求 `status=candidate`。候选详情突出内容、scope/kind、confidence/importance、来源与版本关系，并提供“确认激活”“拒绝候选”。操作必须填写至少三个字符的原因，actor 固定为 `local-operator`。

5. **普通候选与 AutoDream proposal 保持独立。** 普通候选通过 activate endpoint；AutoDream 仍通过 consolidation publish endpoint 原子产生 active 输出。两者分别显示和审计，避免普通确认隐式发布整理代次。

6. **既有 active 数据不迁移。** 部署只改变未来提取结果；既有 candidate 自动出现在待确认列表，既有 active 继续生效。回滚代码后候选可能再次被旧运行路径自动激活，因此回滚前应关闭“保存新记忆”或先部署兼容补丁。

## Risks / Trade-offs

- [待确认列表积压导致新知识长期不可用] → 显示明确数量和状态筛选；不以超时自动激活。
- [两个操作员同时确认同一候选] → 使用 `state_version` 乐观并发和原子条件更新，仅一个请求成功。
- [基础 active 在候选等待期间产生了其他版本] → 激活时重新检查稳定键最新状态；不满足预期即失败关闭。
- [模型产生大量重复候选] → 与相同 active 内容继续去重；与已有相同 candidate 内容按稳定键去重，不重复创建审核项。
- [人工激活被误解为事实验证] → UI 与帮助文档说明它代表人工准入决定，来源仍是上下文证据而非绝对事实保证。

## Migration Plan

1. 先部署后端 candidate 写入、激活 API 和测试，使新记录不再自动进入召回。
2. 部署前端待确认列表和人工操作。
3. 更新帮助文档；既有 candidate 无迁移地进入列表。
4. 如需回滚，先关闭新记忆写入，避免旧逻辑自动激活积压候选，再回退应用版本。

## Open Questions

无。第一版按单条、本机操作员确认实现。

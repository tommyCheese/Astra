## ADDED Requirements

### Requirement: Agent 上下文使用共享且角色感知的压缩生命周期
系统 SHALL 为 conversation、root execution 和 child execution 使用同一套 Token 计量、触发、压缩、安装、恢复与审计生命周期，并 SHALL 通过角色策略分别定义受保护上下文、checkpoint schema、近期原文预算和事实提升规则。

#### Scenario: root 与 child 达到相同压力比例
- **WHEN** root execution 与 child execution 分别达到其自动压缩阈值
- **THEN** 两者经过相同的压缩生命周期和状态事件
- **THEN** 两者使用各自角色策略生成不同结构的 checkpoint

#### Scenario: 角色策略缺失
- **WHEN** 一个 Agent execution 没有可解析的压缩角色策略
- **THEN** Runtime 不得使用其他角色的默认摘要代替
- **THEN** Runtime 在硬上限前返回分类配置错误

### Requirement: 规范状态作为不可压缩前缀重新构造
系统 SHALL 在每个新上下文窗口从规范存储重新构造受保护前缀，并 MUST NOT 让模型摘要替代当前用户请求、Task/DelegationContract、Profile/Skill identities、权限、Catalog digests、Plan/AgentState 版本、Workspace scope、预算、终止条件或 Completion Gate。

#### Scenario: checkpoint 与当前权限冲突
- **WHEN** checkpoint 文本暗示 child 可使用当前 DelegationContract 未授权的工具
- **THEN** Runtime 仅按当前规范权限与冻结 Catalog 构造模型和工具上下文
- **THEN** checkpoint 不改变候选工具、批准或执行权限

#### Scenario: Skill 在压缩后继续生效
- **WHEN** root 或 child 在启用 Skill 的情况下完成压缩
- **THEN** Runtime 从冻结 Skill snapshot 重建仍适用的身份和核心指令
- **THEN** 不依赖摘要复述 Skill 内容来恢复其权限或身份

### Requirement: 活动历史采用语义 checkpoint 与近期原文组合
系统 SHALL 将可压缩旧主体替换为一个累积语义 checkpoint，并 SHALL 在预算内按时间顺序保留近期原始用户输入、决策或 observations；重复压缩 MUST 将上一 checkpoint 的有效状态合并进新 checkpoint，而不是形成无界嵌套或静默丢弃历史。

#### Scenario: 第一次自动压缩
- **WHEN** 可压缩主体达到软阈值且存在足够压缩输出空间
- **THEN** Runtime 安装受保护前缀、一个 checkpoint 和预算内近期原文组成的新窗口
- **THEN** 压缩后 Token 使用低于策略恢复水位

#### Scenario: 多次压缩
- **WHEN** 已有 checkpoint 的窗口再次达到阈值
- **THEN** 新 checkpoint 累积仍有效的历史决定、进度和未决事项
- **THEN** 模型输入不包含多个递归展开的旧摘要副本

### Requirement: 压缩协议由 Astra 独立实现且 Provider 无关
系统 SHALL 使用 Astra 构造的 ContextEnvelope、角色 prompt、普通模型生成调用、本地解析、schema/引用/安全校验和 checkpoint 安装完成压缩，并 MUST NOT 调用 Provider 专有 compact endpoint、发送专有 compaction 参数或 trigger、接收 opaque/encrypted compaction item 作为活动状态。

#### Scenario: 使用任意普通生成 Provider
- **WHEN** 当前 Provider 只支持 Astra 已有的普通文本生成接口
- **THEN** Runtime 通过相同的 Astra compaction prompt 和 checkpoint schema 完成压缩
- **THEN** Provider 不需要声明或实现任何压缩专用能力

#### Scenario: Provider 不支持 structured output
- **WHEN** 普通模型只能返回文本而不支持 JSON schema 或 JSON mode 参数
- **THEN** Astra 从纯 JSON或 fenced JSON文本提取并有限修复候选 checkpoint
- **THEN** 候选仍须通过完全相同的本地校验才能安装

#### Scenario: 切换模型或 Provider
- **WHEN** execution 使用 Astra V2 checkpoint 切换到另一个普通生成模型或 Provider
- **THEN** Runtime 直接复用可读的 Astra checkpoint 并重新计算窗口预算
- **THEN** 不需要转换 opaque payload 或 compatibility hash

#### Scenario: 普通摘要调用失败
- **WHEN** 普通模型调用失败或输出无法通过校验且仍有安全空间
- **THEN** Runtime 可按角色策略生成只包含规范状态和已验证引用的 deterministic emergency checkpoint
- **THEN** 原活动历史在有效 checkpoint 安装前保持不变

### Requirement: 子 Agent checkpoint 保持局部性与可验证引用
系统 SHALL 为每个 child 独立生成包含 contract/manifest hash、局部进度、已完成步骤、局部事实、Evidence/Artifact 引用、近期失败、未决问题、下一动作、continuation answers 和剩余预算的 checkpoint，并 MUST NOT 包含父/兄弟私有上下文、隐藏 reasoning、凭据、未选择 Memory 或无关工具轨迹。

#### Scenario: child 工具循环达到阈值
- **WHEN** child 在工具结果规范化后达到其 body-after-prefix 阈值
- **THEN** Runtime 只压缩该 child 的局部主体并保留受保护 Delegation 前缀
- **THEN** 下一次 child 模型调用接收新 checkpoint 和预算内近期 observations

#### Scenario: checkpoint 引用无权访问的 Evidence
- **WHEN** 模型生成的 child checkpoint 引用了不属于该 identity、数据标签或用途范围的 Evidence
- **THEN** checkpoint 校验失败且不得安装
- **THEN** Runtime 记录隔离诊断而不扩大 child 权限

#### Scenario: child 完成后返回父级
- **WHEN** child checkpoint 中存在未验证 local facts
- **THEN** 这些 facts 不自动进入 root verified facts
- **THEN** 只有现有 fan-in/promotion 流程接受的结果和引用可提升到共享状态

### Requirement: 大型工具输出在压缩前外置
系统 SHALL 在工具输出超过角色 inline Token 或字节预算时持久化完整输出并将活动 observation 改写为有界预览、checksum、状态、错误分类和稳定 Artifact/Evidence/ToolCall 引用；compactor MUST NOT 依赖被截断预览作为完整来源。

#### Scenario: child 获取大型报告
- **WHEN** child 工具返回超过 inline 上限的报告
- **THEN** 完整报告进入 child 可访问的受管 Artifact 或 Evidence 存储
- **THEN** child 上下文只保留有界摘要和稳定引用

#### Scenario: 外置失败
- **WHEN** 大型工具输出无法安全持久化或生成稳定引用
- **THEN** Runtime 不得静默删除完整结果后继续
- **THEN** 当前行动以分类存储错误进入重试、waiting 或 blocked 路径

### Requirement: 压缩在模型调用前和工具结果后触发
系统 SHALL 在每次 root/child 模型调用前、每次工具结果规范化后和恢复后的首次模型调用前检查活动窗口，并 SHALL 同时执行软自动阈值和模型完整上下文硬上限检查。

#### Scenario: 工具结果推高上下文
- **WHEN** 工具结果加入后使活动窗口达到自动压缩阈值且 Agent 仍需继续
- **THEN** Runtime 在下一次模型调用前完成压缩
- **THEN** 未压缩的超限历史不会发送给模型

#### Scenario: 当前请求本身无法放入窗口
- **WHEN** 受保护前缀、当前输入和最小 checkpoint 已超过硬上限
- **THEN** Runtime fail closed 并返回分类容量错误
- **THEN** Runtime 不摘要权限、契约或当前明确请求以强行适配

### Requirement: checkpoint 安装幂等且并发安全
系统 SHALL 使用 owner、window number、input digest、policy version、state version 和 cancellation epoch 对压缩结果进行条件安装，并 SHALL 保留 source item 边界、Token 前后值、模型身份和 schema version。

#### Scenario: 压缩期间出现新 observation
- **WHEN** 模型压缩进行中同一 execution 提交了新状态版本
- **THEN** 旧压缩结果标记为 superseded 且不得覆盖新状态
- **THEN** 下一安全边界基于最新状态重新评估

#### Scenario: 进程在压缩请求后重启
- **WHEN** 普通摘要模型已返回结果但 checkpoint 尚未原子安装
- **THEN** 恢复器使用幂等键判断重用、完成安装或重新请求
- **THEN** 恢复过程不会重复外部工具副作用

### Requirement: 压缩失败采用保守容量出口
系统 SHALL 在软阈值失败时保留原历史并记录可重试错误，在仍有安全空间时允许有界回退；当无法在硬上限前形成有效 checkpoint 时，root SHALL 返回分类容量错误或安全等待状态，child SHALL 返回结构化 budget-limited、blocked 或 waiting 结果，系统 MUST NOT 静默丢弃受保护内容或发送必然溢出的请求。

#### Scenario: checkpoint schema 校验失败
- **WHEN** 压缩模型输出缺失强制字段、包含禁止内容或引用无效
- **THEN** Runtime 不安装该 checkpoint
- **THEN** 原活动历史和完整审计记录保持不变

#### Scenario: 连续压缩不能降低使用量
- **WHEN** 同一窗口的压缩重试仍无法达到恢复水位
- **THEN** Runtime 停止压缩循环并走角色对应的分类容量出口
- **THEN** 诊断包含压缩前后 Token、受保护前缀大小和失败阶段

### Requirement: 压缩质量与成本可观察和可评测
系统 SHALL 记录 trigger、role、implementation、reason、status、window number、Token 前后值、recent tail、持续时间、生成模型/Provider、成本、失败类别和 checkpoint schema，并 SHALL 以关键状态保留、引用有效性、隔离、任务连续性及重复压缩表现作为上线门槛。

#### Scenario: 查看 child 压缩审计
- **WHEN** 操作员检查发生过压缩的 child execution
- **THEN** 审计记录展示压缩实现、Token 变化、checkpoint 版本、输入边界和失败/成功状态
- **THEN** 审计记录不暴露隐藏 reasoning 或机密工具 payload

#### Scenario: 比较旧算法基线
- **WHEN** 团队运行长程任务压缩评测
- **THEN** 结果比较 V2 与字符截断基线的成功率、关键字段保留率、成本和延迟
- **THEN** 摘要文字相似度不得作为唯一上线标准

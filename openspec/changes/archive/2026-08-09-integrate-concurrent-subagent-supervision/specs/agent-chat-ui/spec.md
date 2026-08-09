## ADDED Requirements

### Requirement: Chat UI 清晰展示并发子 Agent 和 Join 状态
系统 SHALL 同时展示多个 child 的目标、父级、运行/等待/终态、Join 关系、预算和关键等待原因，并 SHALL 使用户能够区分并发执行与串行步骤而不暴露隐藏推理。

#### Scenario: 两个 child 同时运行
- **WHEN** Run snapshot 包含两个 running child
- **THEN** 子 Agent 面板和执行图谱同时显示两个活动分支及各自状态
- **THEN** 汇总计数、预算和等待信息与权威快照一致

#### Scenario: 一个 child 等待而另一个完成
- **WHEN** sibling child 分别处于 waiting_approval 和 completed
- **THEN** UI 分别展示等待原因和完成摘要
- **THEN** UI 不把整个 Run 错误显示为只能等待该 child

#### Scenario: Join 已 ready 但尚未消费
- **WHEN** child 已完成且 Join 处于 ready 或 merging
- **THEN** UI 将其显示为正在汇合而不是根任务已经完成

### Requirement: 工具设置展示并控制 Swarm
系统 SHALL 在工具设置界面展示 `swarm` 的名称、子 Agent 用途、当前开关状态、可用性和不可用原因，并 SHALL 通过与其他工具一致的键盘可操作 switch 保存用户选择。

工具设置 SHALL NOT 展示无法由用户操作解决的部署执行提示或解释既有子 Agent 生命周期的常驻说明；这些约束由运行时执行并记录在运维文档中。

#### Scenario: 用户在设置中关闭 Swarm
- **WHEN** 用户操作 `swarm` switch 从 enabled 变为 disabled 且保存成功
- **THEN** UI 通过 switch 本身展示关闭状态，不额外显示成功说明
- **THEN** 刷新设置后仍读取到 disabled 状态

#### Scenario: 工具设置保持面向用户操作
- **WHEN** 用户查看可用的 `swarm` 工具设置
- **THEN** UI 不显示“需要先启用受治理子 Agent 执行”提示
- **THEN** UI 不显示关闭开关对既有 child 生命周期的常驻说明
- **THEN** UI 不显示工具已启用、已停用或设置已保存的重复成功提示

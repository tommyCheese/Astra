## ADDED Requirements

### Requirement: 用户可以选择快速回答或可信模式
系统 SHALL 提供 `standard` 快速回答和 `trusted` 可信模式，首次使用 SHALL 默认为快速回答，并 SHALL 在聊天输入区持续显示当前模式。

#### Scenario: 首次打开应用
- **WHEN** 用户尚未保存回答模式偏好
- **THEN** 系统选择快速回答
- **THEN** 聊天输入区显示可信模式处于关闭状态

#### Scenario: 用户开启可信模式
- **WHEN** 用户在聊天输入区开启可信模式
- **THEN** 下一次新建 Run 使用 trusted 模式
- **THEN** 当前正在执行的 Run 不改变模式或策略

### Requirement: 模式偏好与 Run 模式快照分别持久化
系统 SHALL 持久化用户首选回答模式，并 SHALL 为每个 Run 保存创建时不可变的 answer mode 与生效运行 profile。

#### Scenario: 重启后恢复偏好
- **WHEN** 用户选择可信模式后重启应用
- **THEN** 系统从数据库恢复可信模式偏好
- **THEN** 已保存的可信对话策略保持原值

#### Scenario: 运行期间修改模式
- **WHEN** 用户在某个 Run 创建后切换模式
- **THEN** 当前 Run 继续使用原 answer mode 和 profile 快照
- **THEN** 后续新 Run 使用新的首选模式

#### Scenario: 恢复等待中的运行
- **WHEN** 用户继续一个处于 `waiting_user` 的 Run
- **THEN** 系统沿用该 Run 原有模式和 profile
- **THEN** 当前界面的模式开关不会改变续跑的验证语义

### Requirement: 两种模式共享通用 Agent 能力
系统 SHALL 让快速回答和可信模式共享模型选择、执行审批、工具、文件与 Artifact、流式过程、会话、取消、记忆和分享能力，并 MUST NOT 维护第二套 Agent runtime。

#### Scenario: 快速回答调用工具
- **WHEN** 快速回答判断需要已授权工具才能响应用户
- **THEN** 系统通过与可信模式相同的 ToolRouter 和权限门执行工具
- **THEN** 工具事件进入相同的会话与过程流

#### Scenario: 两种模式取消运行
- **WHEN** 用户取消任一模式下的活动 Run
- **THEN** 系统使用相同取消协议和终态语义停止运行

### Requirement: 可信结果状态可感知且不承诺绝对正确
系统 SHALL 在可信 Run 的过程或回答中显示完整校验状态，并 MUST NOT 将可信模式描述为保证答案绝对正确。

#### Scenario: 完整校验通过
- **WHEN** trusted Run 的完整校验通过且无 warning
- **THEN** UI 显示“已校验”或等价状态

#### Scenario: 校验带警告或阻塞
- **WHEN** trusted Run 产生 warning 或未通过完整校验
- **THEN** UI 显示对应的带警告或未通过状态
- **THEN** 用户可以访问相关 VerificationReport 或终止原因

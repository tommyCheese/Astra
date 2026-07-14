## ADDED Requirements

### Requirement: Chat composer 在活动 Run 期间提供终止控制
系统 SHALL 在消息已提交且 Run 尚未进入终态时，将发送按钮替换为可访问的终止按钮。

#### Scenario: Run 创建或执行中
- **WHEN** 创建请求正在进行或当前 Run 仍处于活动状态
- **THEN** composer 显示终止图标而不是发送箭头
- **THEN** 终止按钮具有明确的可访问名称且不会提交新消息

#### Scenario: 用户点击终止按钮
- **WHEN** 用户点击活动 Run 的终止按钮
- **THEN** UI 立即进入终止中状态并阻止重复请求
- **THEN** 取消收敛后 composer 恢复发送按钮

#### Scenario: 创建响应返回前点击终止
- **WHEN** 用户在创建 API 尚未返回 run id 时点击终止
- **THEN** UI 记录取消意图并在获得 run id 后立即请求取消

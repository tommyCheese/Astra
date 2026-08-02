## ADDED Requirements

### Requirement: 聊天输入区显著展示可信模式开关
系统 SHALL 在聊天 Composer 的常驻可见区域提供可信模式开关，并 SHALL 在不打开模型或设置菜单的情况下识别和切换当前模式。

#### Scenario: 快速回答状态
- **WHEN** 当前首选模式为 standard
- **THEN** 输入区显示“快速回答”以及关闭的可信开关
- **THEN** 控件不会与发送、附件、执行审批或模型选择重叠

#### Scenario: 可信模式状态
- **WHEN** 当前首选模式为 trusted
- **THEN** 输入区以克制但明确的视觉状态显示“可信模式”已开启
- **THEN** 控件提供可访问名称和键盘操作

### Requirement: 对话策略按回答模式渐进呈现
系统 SHALL 仅在可信模式下提供推理强度、工具预算、规划和反思等详细对话策略控制，并 SHALL 在快速模式下保留模型与执行审批控制。

#### Scenario: 快速回答打开模型菜单
- **WHEN** standard 模式用户打开模型菜单
- **THEN** UI 允许选择模型
- **THEN** UI 不把详细可信策略表现为当前快速回答的生效设置

#### Scenario: 可信模式打开模型菜单
- **WHEN** trusted 模式用户打开模型菜单
- **THEN** UI 展示并允许修改持久化可信对话策略

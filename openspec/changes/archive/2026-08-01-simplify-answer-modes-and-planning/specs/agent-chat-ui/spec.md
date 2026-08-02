## ADDED Requirements

### Requirement: 输入区只提供快速响应与可信执行选择
系统 SHALL 在 Composer 中以一个二元控件展示“快速响应”和“可信执行”，并 SHALL 不展示独立的仅规划产品模式。

#### Scenario: 快速响应状态
- **WHEN** 当前首选模式为 standard
- **THEN** 输入区显示快速响应语义
- **THEN** 用户无需理解规划策略即可预测该 Run 不创建 DAG

#### Scenario: 可信执行状态
- **WHEN** 当前首选模式为 trusted
- **THEN** 输入区显示先规划、再执行、再验证的可信执行语义
- **THEN** UI 不将可信描述为保证结果绝对正确

### Requirement: 可信设置不展示规划策略
系统 SHALL 从模型菜单和设置中删除自适应与先规划选择器，并 SHALL 将 trusted 的完整 DAG 规划表现为模式固有行为。

#### Scenario: 用户打开可信设置
- **WHEN** trusted 用户打开模型或策略菜单
- **THEN** UI 可以展示推理强度、工具预算和反思设置
- **THEN** UI 不展示规划策略字段

### Requirement: 可信模式提供计划执行确认控制
系统 SHALL 在 trusted 模式的模型/策略菜单内提供“计划生成后直接执行”控制，不得将其作为输入框旁的独立平铺按钮，并 SHALL 在需要确认时于完整计划生成后展示版本绑定的执行按钮。

#### Scenario: 可信执行菜单展示控制
- **WHEN** trusted 用户打开模型/策略菜单
- **THEN** 菜单展示“计划生成后直接执行”开关及当前行为说明
- **THEN** 输入框旁不额外平铺该开关

#### Scenario: 可信策略按功能分组
- **WHEN** trusted 用户查看模型/策略菜单
- **THEN** UI 将计划执行、推理资源和反思策略分别组织为功能组
- **THEN** 相邻功能组之间使用轻量横线分隔，同组字段不重复分隔

#### Scenario: 可信策略帮助集中展示
- **WHEN** trusted 用户查看模型/策略菜单
- **THEN** 各项设置不分别展示帮助按钮
- **THEN** UI 在全部可信策略控件之后展示唯一的“了解可信策略”入口，并用轻量分隔与设置区区分
- **THEN** 点击入口后按计划执行、推理资源和反思策略集中展示完整说明

#### Scenario: 集中帮助采用分组可读布局
- **WHEN** 用户打开可信策略帮助
- **THEN** 弹窗使用全宽单列排列功能组，并以组标题区分计划执行、推理资源和反思策略
- **THEN** 宽屏使用短标签与说明的双列条目，窄屏降为单列
- **THEN** 弹窗在视口内滚动，不产生狭窄内容列或大面积无效空白

#### Scenario: 用户选择直接执行
- **WHEN** trusted 用户开启“计划生成后直接执行”并提交任务
- **THEN** UI 将 `plan_execution=auto` 发送给后端
- **THEN** 计划生成后无需额外 Plan 确认即可开始节点调度

#### Scenario: 用户选择先查看计划
- **WHEN** trusted 用户关闭“计划生成后直接执行”并提交任务
- **THEN** UI 将 `plan_execution=confirm` 发送给后端
- **THEN** 完整 DAG 生成后 UI 展示计划和“执行计划”按钮

#### Scenario: 用户执行已展示计划
- **WHEN** 用户点击“执行计划”
- **THEN** UI 提交当前 continuation token 和预期 Plan 版本
- **THEN** UI 不把该点击表现为批准后续所有工具效果

#### Scenario: 快速响应隐藏控制
- **WHEN** 当前模式为 standard
- **THEN** UI 在输入区及模型/策略菜单中都不展示计划执行确认控制

### Requirement: 审批控件不包含仅规划
系统 SHALL 只在审批控件中展示请求批准和自动批准，并 SHALL 将其描述为权限交互行为。

#### Scenario: 用户打开审批菜单
- **WHEN** 用户查看审批行为选项
- **THEN** 菜单不包含“仅规划”
- **THEN** 菜单说明两种回答模式仍受平台硬性安全边界限制

### Requirement: 审计视图只展示真实 DAG
系统 SHALL 仅在 Run 存在规范 Plan DAG 时展示计划版本、节点与依赖，并 MUST NOT 为 standard Run 展示虚构的 Plan 版本或空 DAG 占位。

#### Scenario: 查看快速响应过程
- **WHEN** 用户展开已完成 standard Run 的过程
- **THEN** UI 展示真实决策和工具事件
- **THEN** UI 不展示 Plan 版本或 Plan 节点区域

#### Scenario: 查看可信执行过程
- **WHEN** 用户展开已完成 trusted Run 的过程
- **THEN** UI 展示真实 Plan 版本、节点状态和依赖

## Why

后端已经按 Agent 能力划分主要包，但许多能力包内部仍将状态对象、校验规则、纯转换函数和有副作用的服务平铺在同一层，导致目录无法帮助读者判断代码性质，模块名也需要依赖实现细节才能理解。现在需要建立一致的二级归类，使阅读者先按业务能力定位，再按代码角色快速找到对象、验证器、工具函数或服务。

## What Changes

- 为较大的 Agent 能力包引入有限且一致的二级分包：`models`、`validation`、`utilities` 和 `services`，只在确有多个同类模块时创建。
- 将 dataclass、enum、Protocol 和无行为值对象集中到所属能力的 `models` 边界，将输入/输出合法性规则集中到 `validation`。
- 将无状态、无副作用、可独立测试的纯函数归入 `utilities`；禁止使用无法表达用途的全局 `utils.py` 杂物模块。
- 将协调外部依赖或承载用例流程的实现放入 `services`，同时保留清晰的单一公共入口。
- 合并只有少量符号且没有独立概念价值的碎片模块，减少模块、类和转发方法数量。
- 将类和模块数量、生产代码量的净下降设为验收条件；纯函数不包装成类，单实现抽象和无状态转发对象直接移除。
- 迁移所有生产与测试导入，更新架构规则、模块地图和可读性门禁，不保留旧路径兼容层。
- 保持 HTTP、SSE、持久化、Agent 决策、工具调用和模型 Provider 的外部行为不变。

## Capabilities

### New Capabilities

- `backend-code-organization`: 定义后端业务优先、代码角色次之的分包规则，以及对象、校验、纯工具和服务的归属约束。

### Modified Capabilities

无。此次重构不改变现有产品能力需求或公共行为。

## Impact

主要影响 `backend/app/agent_runtime`、`memory`、`model_clients`、`planning`、`subagents`、`tools` 等内部包及其测试导入；架构分析规则和后端设计文档同步更新。数据库 schema、OpenAPI、事件协议和外部依赖不变。

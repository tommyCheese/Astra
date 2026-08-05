## Context

上一轮重构已经让顶层包对应真实 Agent 能力，但大型能力包内部仍是平铺目录。例如 `agent_runtime` 同时暴露状态对象、策略判断和执行阶段，`model_clients` 同时暴露 transport、normalizer 与解析辅助函数，Memory consolidation 和 Web tool 的内部实现也散落在能力包根目录。读者能够找到“哪个能力”，却不能从路径判断“这段代码是什么性质”。

本次迁移必须保持现有行为和持久化语义，不引入兼容 re-export；所有生产与测试调用方一次性迁移到唯一所有者。分类后的目录数量也必须受控，不能让每个单文件概念都拥有一个子包。

## Goals / Non-Goals

**Goals:**

- 采用“业务能力优先，代码角色次之”的两级导航。
- 让 models、validation、utilities、policies、services 和 transports 的路径含义稳定且互斥。
- 合并没有独立概念价值的微型模块和转发入口，降低模块、类与方法总量。
- 在不牺牲替换边界和领域语义的前提下最大化降低类、模块与生产代码总量。
- 让包根目录只保留真正的公共入口或规模不足以分组的核心模块。
- 通过架构检查防止新的无语义 `utils.py`、跨能力技术大包和旧路径依赖。

**Non-Goals:**

- 不建立全局 `models`、`validators`、`utils` 或 `services` 包。
- 不为只有一个短模块的类型创建目录。
- 不改变数据库、HTTP、SSE、模型协议、审批、安全或 Agent 决策行为。
- 不以文件移动为借口增加 facade、wrapper、manager 或无状态类。

## Decisions

### 1. 两级归类：能力先于代码类型

路径采用 `app/<capability>/<role>/...`，而不是 `app/<role>/<capability>/...`。例如模型 Provider 的纯响应归一化属于 `app.infrastructure.model_clients.normalization`，Memory consolidation 的验证属于 `app.application.memory.consolidation.validation`。这样业务内聚不被技术分类破坏。

备选方案是创建全局 `app.models`、`app.validators` 和 `app.utils`。该方案会形成跨领域耦合和不可发现的杂物目录，因此不采用。

### 2. 只有形成集合时才建立 role 子包

满足以下任一条件才建子包：同一角色至少两个模块；一个能力的内部实现已有三个以上协作模块；平铺目录超过可快速扫描的规模。单个 dataclass 继续和其唯一使用者放在一起，避免“一类一文件”。

### 3. 角色定义固定

- `models`: dataclass、enum、值对象、Protocol 和纯结构 contract；不得协调 I/O。
- `validation`: 解析后合法性检查和领域不变量；不得持久化或发起外部调用。
- `utilities`: 可复用的纯函数、格式转换和确定性计算；文件名必须表达用途，不允许裸 `utils.py`。
- `policies`: 根据输入作无副作用决策的规则。
- `services`: 用例或执行阶段，允许协调 Repository、Provider、Tool 等端口。
- `transports`: 外部 Provider 的 HTTP/流式传输实现。

### 4. 优先迁移高收益平铺区

本轮覆盖：

- `agent_runtime`: 将结构对象、纯策略与执行阶段分为 `models`、`policies`、`services`。
- `model_clients`: 将响应归一化/解析归入 `normalization`，Provider 传输归入 `transports`。
- `memory.consolidation`: 形成能力子包，并以 `models`、`validation`、`generation`、`service` 命名内部角色。
- `tools.web`: 形成工具能力子包，区分安全校验、抓取、输出映射和公共工具入口。
- 对其他能力做审计；只有满足集合阈值才继续分包，否则合并微型模块。

### 5. 不保留旧导入兼容层

所有调用方与测试同时迁移。包 `__init__.py` 默认只写职责说明，不做旧路径 re-export。这样 import graph 始终只有一个真实所有者。

### 6. 类必须证明状态、语义或替换价值

保留类的条件是它表达稳定值对象/异常类型、维护跨调用状态、实现有多个替代实现的端口，或由框架要求。
只有一个实现且不形成测试替换边界的抽象、只调用一个函数的无状态类、以及只转发参数的方法应改为函数或直接调用。
dataclass 数量不以机械减少为目标，但只被单个短实现使用的数据不得为分类而额外创建类。

## Risks / Trade-offs

- [大量路径迁移可能遗漏动态 import] → 搜索字符串路径、运行完整测试与插件发现契约测试，并检查模块导入图。
- [按类型分类可能割裂强内聚代码] → dataclass 若只服务单个实现则留在原模块；只迁移形成稳定集合的角色。
- [子包增加目录深度] → 限制为能力下一级，叶模块使用简短具体名称，不再套第三层技术分类。
- [并行开发分支存在旧路径冲突] → 不提供永久兼容层；迁移地图记录一一对应路径，合并时显式解决。

## Migration Plan

1. 记录当前模块、公共符号、依赖和测试基线。
2. 依次迁移 model clients、Memory/Web feature slice、Agent runtime 与 Subagent 高收益区域。
3. 每完成一个能力立即迁移消费者并运行定向测试。
4. 删除旧文件与无价值 facade，更新架构规则和模块地图。
5. 运行 Ruff、架构、OpenSpec 和全量测试；失败时按能力回退对应路径迁移。

## Open Questions

无。目录阈值和首轮覆盖范围已由本设计确定；实施中若发现强内聚对象不适合拆分，应优先保留内聚而不是机械执行分类。

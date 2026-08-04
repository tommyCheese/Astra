## 1. 基线与规则

- [x] 1.1 记录平铺能力包、代码角色、模块数量、公共符号和测试基线
- [x] 1.2 增加能力优先、角色次之的架构规则，禁止新的全局技术桶和通用 `utils/helpers/common` 模块

## 2. Model Client 归类

- [x] 2.1 将 Provider transport 迁入 `model_clients.transports` 并迁移消费者
- [x] 2.2 将解析与响应 normalizer 迁入 `model_clients.normalization`，合并过小的纯函数模块
- [x] 2.3 删除旧路径并通过 Provider、thinking、normalization 契约测试

## 3. Memory 与 Web Tool 归类

- [x] 3.1 将 Memory consolidation 形成 `memory.consolidation` 能力包，按 models、generation、validation、service 明确职责
- [x] 3.2 将 Web tool 形成 `tools.web` 能力包，按 models/security/fetching/output/tool 明确职责并删除 facade
- [x] 3.3 迁移消费者并通过 Memory、Web、安全与插件测试

## 4. Agent Runtime 归类

- [x] 4.1 将稳定结构对象迁入 `agent_runtime.models`，避免对象与执行阶段混放
- [x] 4.2 将无副作用决策规则迁入 `agent_runtime.policies`
- [x] 4.3 将有副作用的循环与阶段编排迁入 `agent_runtime.services`，合并无独立价值的微型模块
- [x] 4.4 删除旧路径并通过 Root Agent、Subagent、工具审批、恢复和 completion 测试

## 5. 其余能力审计

- [x] 5.1 审计 planning、subagents、tools、repositories 和 evolution，归类满足集合阈值的模块并保留强内聚代码
- [x] 5.2 删除新增结构暴露出的兼容入口、重复对象、无状态包装类和单行转发方法
- [x] 5.3 对全部生产类执行价值审计，移除单实现抽象和纯函数包装类，并确认类、模块、代码量均净下降

## 6. 文档与验收

- [x] 6.1 更新模块地图、目标架构、贡献规则和迁移说明
- [x] 6.2 重新生成只减不增架构基线，确认旧路径、循环依赖与禁止依赖均为零
- [x] 6.3 运行 Ruff、架构检查、OpenSpec 严格校验和完整后端测试

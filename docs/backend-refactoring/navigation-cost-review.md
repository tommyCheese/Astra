# 后端主链路导航成本评审

评审日期：2026-08-10。范围为 Standard Runtime、Trusted Runtime 和 Run read-model 三条高频维护路径。

## Canonical 入口

| 能力 | 从这里开始读 | 下一层必要边界 |
| --- | --- | --- |
| Agent Loop | `app/application/agent_runtime/loop.py` | 固定 model/state/action/cancellation/event ports 与 capability slots |
| Standard execution | `app/infrastructure/runtime/standard.py` | `standard_checkpoint.py` 拥有持久化 checkpoint、pending action 与恢复协议 |
| Trusted execution | `app/infrastructure/runtime/trusted.py` | `trusted_factory.py` 冻结基础设施与策略，`trusted_capabilities.py` 组装执行阶段 |
| Run lifecycle | `app/application/run_management/execution/run_execution.py` | 显式 Unit of Work、dispatcher、finalization 与 recovery |
| Run read model | `app/application/run_management/projections/run_view.py` | `RunUnitOfWork` 读取，projection 生成公开 `RunView` |

这些路径直接指向真实所有者；package `__init__.py` 不提供兼容 re-export。

## 导航序列变化

| 用例 | 优化前 | 优化后 | 删除的非策略跳转 |
| --- | --- | --- | ---: |
| Standard checkpoint | `run_execution -> standard -> standard_state -> standard_recovery` | `run_execution -> standard -> standard_checkpoint` | 1 |
| Trusted composition | `run_execution -> trusted -> trusted_factory -> trusted_capabilities -> trusted_state` | `run_execution -> trusted -> trusted_factory -> trusted_capabilities` | 1 |
| Run read model | `runs API -> query_service -> run_view -> RunUnitOfWork` | `runs API -> run_view -> RunUnitOfWork` | 1 |

`standard_checkpoint.py` 现在统一拥有 snapshot encoding、CAS persistence、pending action、幂等重试和
result-unknown 恢复。`TrustedRuntime` 及其进程内状态只描述 `TrustedCapabilityFactory` 组装的执行图，
因此与 capability owner 同址。Run query functions 与其唯一 projection 同址，不再经过字段保持型 service。

## 必须保留的跳转

- `agent_runtime/loop.py` 与 infrastructure adapters 分离：Loop 是可测试的 canonical control flow，adapter 承担数据库、模型和工具副作用。
- `trusted_factory.py` 与 `trusted_capabilities.py` 分离：前者冻结基础设施、权限和运行配置，后者组装 application stages；合并会超过模块预算并混合生命周期。
- `RunUnitOfWork` 与 projection 分离：前者拥有事务和持久化，后者只构造公开 read model。
- Permission/effect analysis、approval integrity、plugin isolation、cancellation 和 recovery 继续作为不可绕过边界。

## 结构指标

| 指标 | 优化前 | 优化后 | 净变化 |
| --- | ---: | ---: | ---: |
| 生产 Python 行数 | 60,619 | 60,580 | -39 |
| 生产模块 | 302 | 299 | -3 |
| 类 | 762 | 762 | 0 |
| 公共符号 | 1,189 | 1,187 | -2 |
| 函数与方法 | 2,425 | 2,425 | 0 |

合并后的 `standard_checkpoint.py` 为 412 行，`trusted_capabilities.py` 为 428 行，`run_view.py`
为 332 行；均低于 500 行默认模块预算。最大生产模块仍为 770 行，最大函数仍为 96 行，最大测得圈
复杂度仍为 15，没有新增硬限制违规。

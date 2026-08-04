# 后端代码角色归类基线

基线日期：2026-08-04；工作目录：`backend/`。

| 指标 | 归类前基线 |
| --- | ---: |
| 生产 Python 模块 | 313 |
| 生产 Python 行数 | 62,070 |
| 公共符号 | 1,326 |
| 函数与方法 | 2,497 |
| 类 | 793 |
| 收集的测试 | 823 |
| 通过 / 跳过 | 815 / 8 |

优先处理的平铺区域是 `agent_runtime`（32 个叶模块）、`model_clients`（Provider、transport、normalizer
和 parsing 混放）、Memory consolidation（4 个协作模块）以及 Web tool（9 个协作模块）。分类遵循
“能力优先、角色次之”；单一消费者的小型对象保留就近定义，不机械创建一类一文件。

可使用以下命令重复生成规模指标：

```bash
.venv/bin/python scripts/analyze_backend_architecture.py --format markdown
.venv/bin/python -m pytest --collect-only -q
```

## 类价值审计

基线中的 793 个类主要来自 Pydantic API schema、SQLAlchemy ORM、dataclass/enum 值对象、语义异常、
有状态服务以及插件/执行端口。审计不把这些类型机械降级为宽泛字典或位置 tuple。可删除对象限定为：无状态
转发包装、只有单一实现且不承担替换边界的抽象、未使用的创建方法，以及纯兼容继承壳。

`planning` 只有 service、scheduler、revision 三个稳定概念，不再创建 role 子包；`subagents` 按预算、治理、
执行、恢复、fan-in 等真实 Agent 能力切片，阶段专用 dataclass 与唯一消费者保持同文件；`tools` 按具体工具
归属；`repositories` 按聚合持久化；`evolution` 已区分 domain、evaluation、lifecycle 与 orchestration。
这些区域继续拆技术子包只会增加导航和模块数量，因此本轮保留其强内聚结构。

## 实施结果

| 指标 | 归类前 | 归类后 | 净变化 |
| --- | ---: | ---: | ---: |
| 生产 Python 模块 | 313 | 307 | -6 |
| 生产 Python 行数 | 62,070 | 61,883 | -187 |
| 公共符号 | 1,326 | 1,282 | -44 |
| 函数与方法 | 2,497 | 2,492 | -5 |
| 类 | 793 | 792 | -1 |

减少来自 payload normalizer 合并、纯工具合并、旧 facade/re-export 删除、无价值兼容基类和未使用
转发方法删除；新增的 role package 没有造成模块总量反弹。

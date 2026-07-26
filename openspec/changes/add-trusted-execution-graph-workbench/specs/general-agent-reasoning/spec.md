## MODIFIED Requirements

### Requirement: 计划采用可执行且可修订的图结构

系统 SHALL 为每个 trusted Run 将完整计划表示为带版本的有向无环图（Directed Acyclic Graph，DAG），其中每个节点包含依赖关系、预期结果、关联成功准则、所需能力、风险、执行状态和版本 lineage；standard Run SHALL 不创建 DAG。系统 SHALL 允许等待确认的 trusted Run 接收用户修订意图，但 MUST 仅通过完整计划生成、校验和新版本持久化来改变规范图结构。

#### Scenario: 可信依赖阻塞节点执行

* **WHEN** trusted 计划节点存在尚未满足的必要依赖
* **THEN** 控制器不会执行该节点
* **THEN** 控制器选择其他 ready 节点、受限重规划、请求用户协助或进入阻塞状态

#### Scenario: 可信重新规划保留有效成果

* **WHEN** 计划级反思替换了 trusted DAG 的失效分支
* **THEN** 系统创建经过完整校验的新计划版本
* **THEN** 不受影响的已完成节点及其证据通过 lineage 继续有效

#### Scenario: 用户在执行前请求修改计划

* **WHEN** trusted Run 等待 Plan 版本确认且用户提交自然语言修订意图
* **THEN** 修订过程引用当前 Plan ID、版本、状态版本和 continuation token
* **THEN** 系统生成并校验新的完整 DAG 版本，而不是原地修改当前版本
* **THEN** 新版本重新进入版本绑定确认且尚不执行节点

#### Scenario: 用户修订请求已过期

* **WHEN** 修订请求引用的 Plan 版本或 continuation token 不再是当前等待版本
* **THEN** 系统拒绝请求且不创建新版本
* **THEN** 当前规范 Plan 和运行状态保持不变

#### Scenario: 快速响应不创建占位图

* **WHEN** standard Run 产生一个或多个工具调用
* **THEN** 工具调用保持关联到 Run
* **THEN** 系统不创建占位 Plan 节点或依赖边

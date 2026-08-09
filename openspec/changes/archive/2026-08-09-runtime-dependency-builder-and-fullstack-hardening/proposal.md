## Why

Astra 的绘图 Runtime 依赖目前只能由开发者修改 lock 和 Dockerfile，用户无法在设置界面声明额外 Python 包；同时本地前后端尚缺少可重复的容器化联调与完整界面验收流程。需要建立受控、可审计的 Runtime profile 构建能力，并清理现有实现中的一致性和运行错误。

## What Changes

- 在设置页新增 Runtime tab，展示当前 profile、Python 依赖、镜像 digest、构建状态与日志摘要。
- 用户可用 `name==version` 声明 Python 依赖并触发异步构建；系统验证包名/版本、生成派生 lock/image，成功后原子激活，失败时保留上一版本。
- 构建允许联网下载依赖，普通 Tool Job 仍默认断网且禁止运行时安装。
- 新增 Runtime profile/build 数据模型、API、状态机、Docker builder、并发控制、审计与错误分类。
- 管理 Runtime 镜像完整生命周期：唯一 staging、内容寻址激活、成功镜像清单、可配置保留数量与老化时间，以及安全的定向清理。
- 清理 Docker Sandbox、模型 mock、本地配置与全栈容器启动路径，补齐前后端镜像和 Compose 联调。
- 增加后端、前端、Docker integration 和真实浏览器行为测试。

## Capabilities

### New Capabilities

- `runtime-dependency-management`: Runtime profile、Python 依赖声明、异步镜像构建、激活/回滚和设置界面行为。
- `fullstack-container-validation`: 前后端容器打包、健康检查、API proxy、Artifact 展示与浏览器端到端验收。

### Modified Capabilities

无。

## Impact

- 后端：配置、数据库、Repository、Runtime build service、Docker provider、API 与审计事件。
- 前端：设置导航、Runtime tab、依赖编辑器、构建进度、错误和激活状态。
- Runtime/运维：派生 Docker image、构建网络边界、Compose、持久化 Docker cache 与发布文档。
- 安全：仅接受严格包坐标，不接受 requirement URL、VCS、path、环境 marker 或 pip 参数；构建与 Job 权限分离。

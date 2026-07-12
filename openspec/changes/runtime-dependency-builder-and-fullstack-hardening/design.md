## Context

现有 `astra-data-viz` image 固定依赖且由开发者手工构建。新能力跨越设置 UI、API、数据库、Docker build worker 和 Tool runtime，必须保持普通 Job 的不可变与断网边界。

## Goals / Non-Goals

**Goals:** 用户声明严格版本 Python 包；异步、可审计地构建派生镜像；成功后原子激活；前后端容器与真实浏览器可重复验收。

**Non-Goals:** 不接受任意 requirements 文本、URL/VCS/path、pip 参数、构建脚本；不在 Tool Job 内安装依赖；不支持系统 apt 包配置。

## Decisions

1. `RuntimeProfile` 保存规范化依赖和 active image；`RuntimeBuild` 保存 queued/building/succeeded/failed 状态、日志摘要、digest 与时间。Workspace 第一版使用全局 default profile。
2. API 接受 `{name, version}`，包名按 PEP 503 规范化，版本仅接受精确 PEP 440；拒绝重复、核心包覆盖、URL、marker 和超限列表。
3. Builder 在独立 Docker build 中联网，以基础 image + 生成的 `requirements.lock` 构建派生层；Tool Job 继续 `--network none`。不把 Docker socket传给任何 Job 容器。
4. 采用内容寻址 tag `astra-data-viz:custom-<digest>`；构建成功并 smoke test 后事务性激活。失败或重启不改变 active image。
5. 设置页轮询 build 状态，禁止并发提交；展示安全摘要，不回显宿主路径或完整构建环境。
6. 增加 backend/frontend Dockerfile 与 Compose，浏览器通过同源 `/api` 联调。

## Risks / Trade-offs

- [Risk] 恶意 PyPI 包在 build 阶段执行代码。→ Builder 必须独立 worker、资源/时间限制、最小 build context；第一版明确为管理员级能力。
- [Risk] 镜像膨胀。→ 依赖数量/构建大小限制、内容去重、保留 active 与最近成功版本。
- [Risk] Docker build 并发耗尽资源。→ 单飞锁、队列与超时。
- [Risk] 新依赖破坏核心 renderer。→ 构建后运行 import 与三 backend smoke tests，失败不激活。

## Migration Plan

新增 nullable 表并创建 default profile；现有固定 image 作为初始 active。先启用 API 和只读 UI，再开放构建。回滚只需重新激活基础 image 并关闭 build endpoint。

## Open Questions

后续是否将管理员级构建扩展到 workspace RBAC；本 change 先以单用户本地设置实现。

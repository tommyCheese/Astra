# Docker 沙箱与工具 Runtime 运维指南

## 运行模型

Astra 通过统一 Docker CLI 调用 Docker Engine。本地 macOS 可使用 Docker Desktop 或 Docker CLI + Colima；Linux 部署使用原生 Docker Engine。两端共用同一 Dockerfile、image tag/digest 和 hardening 参数，运行期不需要云端沙箱账户。

## 构建与配置

```bash
docker build -t astra-data-viz:0.1.0 runtimes/data-viz
docker build -f runtimes/web-tools/Dockerfile -t astra-web-tools:0.1.0 .
```

```dotenv
SANDBOX_ENABLED=true
SANDBOX_PROVIDER=docker
DOCKER_BINARY=docker
SANDBOX_RUNTIME_IMAGE=astra-data-viz:0.1.0
SANDBOX_WEB_RUNTIME_IMAGE=astra-web-tools:0.1.0
SANDBOX_RUNTIME_LOCK_DIGEST=sha256:...
```

## 安全边界

每个 Job 使用一次性容器，强制只读 rootfs、非 root 用户、drop all capabilities、no-new-privileges、独立 tmpfs 输入输出，以及 CPU、内存、PID 和 wall-time 限制。结束、异常或超时后统一 `docker rm --force`。不得将 Docker socket 或宿主目录挂入 Job 容器。

图表 Runtime 默认断网。Web Runtime 仅为 `web_search` 和 `web_fetch` 开启 bridge 网络；Fetch 在容器内执行 DNS/IP、重定向和响应大小检查，Search 只访问内置 provider endpoint。Web Runtime 不接收宿主进程环境，只通过 `/input` tmpfs 中的一次性配置文件传入工具所需的白名单配置和该 provider 的凭据，避免凭据出现在 Docker 命令行或持久镜像配置中。`MODEL_API_KEY`、`DATABASE_URL`、artifact 路径等宿主信息不得进入容器。

应用 Registry 只允许 `execution_backend=sandbox.remote` 的工具。宿主内的 Tool 对象只承担协议描述、输入封装和容器调度，工具实现及其第三方解析库位于版本化镜像中。新增工具时必须提供独立 Runtime 或使用经审核的现有 Runtime；不得回退到 `in_process`。

## 依赖与发布

图表 Runtime 的 Python 使用 `pyproject.toml` + `uv.lock`，Node 使用 `package.json` + `package-lock.json`；Web Runtime 使用完整固定版本的 `requirements.lock`。依赖变更必须更新 lock、运行测试和安全审计、构建新 image，并记录 image digest 与 lock digest。Job 运行期间禁止联网安装依赖。

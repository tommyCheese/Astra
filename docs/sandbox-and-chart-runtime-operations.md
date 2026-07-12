# Docker 沙箱与图表 Runtime 运维指南

## 运行模型

Astra 通过统一 Docker CLI 调用 Docker Engine。本地 macOS 可使用 Docker Desktop 或 Docker CLI + Colima；Linux 部署使用原生 Docker Engine。两端共用同一 Dockerfile、image tag/digest 和 hardening 参数，运行期不需要云端沙箱账户。

## 构建与配置

```bash
docker build -t astra-data-viz:0.1.0 runtimes/data-viz
```

```dotenv
SANDBOX_ENABLED=true
SANDBOX_PROVIDER=docker
DOCKER_BINARY=docker
SANDBOX_RUNTIME_IMAGE=astra-data-viz:0.1.0
SANDBOX_RUNTIME_LOCK_DIGEST=sha256:...
```

## 安全边界

每个 Job 使用一次性容器，强制默认断网、只读 rootfs、非 root 用户、drop all capabilities、no-new-privileges、独立 tmpfs 输入输出，以及 CPU、内存、PID 和 wall-time 限制。结束、异常或超时后统一 `docker rm --force`。不得将 Docker socket 或宿主目录挂入 Job 容器。

## 依赖与发布

Python 使用 `pyproject.toml` + `uv.lock`，Node 使用 `package.json` + `package-lock.json`。依赖变更必须更新 lock、运行测试和安全审计、构建新 image，并记录 image digest 与 lock digest。普通 Job 禁止联网安装依赖。

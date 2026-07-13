# Astra OCI runtimes

`data-viz` 是 `chart.render` 的唯一版本化运行环境。Python 依赖由 `uv.lock`
固定，Node 依赖由 `package-lock.json` 固定。入口只接受 `/input/request.json`
声明式协议，只能写入 `/output`，不接受源码或命令字段。

```bash
cd runtimes/data-viz
uv lock
npm install --package-lock-only --ignore-scripts
docker build -t astra-data-viz:0.1.0 .
```

服务端配置 `SANDBOX_PROVIDER=docker`、`SANDBOX_RUNTIME_IMAGE` 和
`SANDBOX_RUNTIME_LOCK_DIGEST`。普通 Job 使用 hardened 容器、默认断网，禁止运行时安装依赖。

`web-tools` 是 `web_search` 与 `web_fetch` 的隔离运行环境。它只复制运行所需的
Python 模块，依赖由 `requirements.lock` 固定，不会把 `backend/.env`、本地数据库
或 artifact 打入镜像：

```bash
docker build -f runtimes/web-tools/Dockerfile -t astra-web-tools:0.1.0 .
```

服务端通过 `SANDBOX_WEB_RUNTIME_IMAGE` 选择该镜像。请求经 `/input/request.json`
传入，stdout 只返回版本化 JSON envelope；容器不挂载任何宿主目录。

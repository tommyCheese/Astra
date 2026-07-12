# Astra data-viz E2B Template

`data-viz` 是 `chart.render` 的唯一版本化运行环境。Python 依赖由 `uv.lock`
固定，Node 依赖由 `package-lock.json` 固定。入口只接受 `/input/request.json`
声明式协议，只能写入 `/output`，不接受源码或命令字段。

```bash
cd runtimes/data-viz
uv lock
npm install --package-lock-only --ignore-scripts
E2B_API_KEY=... uv run --with e2b python template.py
```

服务端配置 `SANDBOX_PROVIDER=e2b`、`E2B_API_KEY`、`E2B_TEMPLATE_ID` 和
`E2B_TEMPLATE_LOCK_DIGEST`。普通 Job 固定安全连接、默认断网和有限 TTL，禁止运行时安装依赖。

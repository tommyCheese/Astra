# E2B 沙箱与图表 Runtime 运维指南

## 配置

绘图 capability 默认关闭。先从 `runtimes/data-viz` 构建版本化 E2B Template，随后配置 `SANDBOX_ENABLED=true`、`SANDBOX_PROVIDER=e2b`、`E2B_API_KEY`、`E2B_TEMPLATE_ID` 与 `E2B_TEMPLATE_LOCK_DIGEST`。API key 只放入服务端 secret store，不得进入数据库、日志、Artifact provenance 或前端配置。

本方案在 Linux 与 macOS 使用相同远程 Provider，不依赖本机 Docker、虚拟化框架或操作系统特定沙箱。启动时若 key 或 Template ID 缺失，`chart.render` 不进入模型上下文。

## Template 发布

Python 使用 `pyproject.toml` + `uv.lock`，Node 使用 `package.json` + `package-lock.json`。依赖变更必须更新 lock、运行 contract/security tests、生成 SBOM 并构建新 Template；验证后再切换 Template ID 与 lock digest。普通 Job 禁止运行时安装依赖。

## 运行安全

每个 Job 创建一次性 Sandbox，固定 `secure=true`、`allow_internet_access=false` 和有限 TTL。只上传声明式输入到 `/input`，只从 `/output` 收集文件；结束、超时或异常路径都调用 terminate。Artifact collector 继续执行路径、symlink、MIME、数量、大小、checksum 与主动内容检查。

## 监控与事故处置

监控创建/排队/执行时长、成功率、timeout/OOM、Artifact 字节数、Template ID、lock digest 与 Provider 指标。E2B 不可用时关闭 capability 并返回稳定 `sandbox_unavailable`，不在 API 进程中降级执行。疑似 key 泄露时立即关闭 capability、撤销并轮换 key、检查 Sandbox 创建审计与配额；运行时漏洞则冻结 Template、保留脱敏事件和 Artifact provenance，再发布修复版本。

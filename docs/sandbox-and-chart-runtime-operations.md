# Astra 沙箱与图表 Runtime 运维指南

## 本地 Docker

绘图 capability 默认关闭。构建两个固定版本 runtime 后，通过 `SANDBOX_ENABLED=true` 启用，并设置 `SANDBOX_EXECUTOR=docker`、`SANDBOX_PYTHON_IMAGE` 与 `SANDBOX_ECHARTS_IMAGE`。本地构建命令：

```bash
docker build -t astra-runtime-python:0.1.0 runtimes/python
docker build -t astra-runtime-echarts:0.1.0 runtimes/echarts
```

生产发布不得只使用可变 tag。构建和扫描后必须换成 registry 返回的不可变 digest并保存 SBOM。运行期间禁止安装依赖和访问公网。

## Linux 与 gVisor

宿主机注册 `runsc` 后设置 `SANDBOX_REQUIRE_GVISOR=true` 和 `SANDBOX_OCI_RUNTIME=runsc`。要求 gVisor 时不得静默降级到 `runc`。部署检查应验证 Docker daemon、`runsc`、runtime image、Artifact Store 空间与清理任务。

## 威胁模型与控制

主要威胁包括恶意数据、解析器漏洞、路径穿越、symlink 逃逸、资源耗尽、SVG/HTML 主动内容、容器逃逸和 Artifact 越权。安全边界是一次性 OCI sandbox，而不是 Python `venv` 或 import 过滤。

强制控制包括：默认无网络、非 root、只读 rootfs、drop all capabilities、`no-new-privileges`、seccomp、只读输入、唯一可写输出，以及 wall time、CPU、内存、PID、nofile、文件数量和总字节配额。API 只通过 Artifact ID 交付已验证内容，不返回 storage key。

## 发布、漏洞响应与事故处置

每次 runtime 更新必须重新锁定依赖、生成 SBOM、扫描漏洞、执行 image contract 与恶意输入测试，再发布新 digest。发现高危漏洞或疑似逃逸时，立即关闭 sandbox capability、隔离 worker、保存脱敏事件与 image digest、轮换凭据，并检查 Artifact checksum/provenance。stdout/stderr 必须截断脱敏，禁止直接发送给模型或用户。

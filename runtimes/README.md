# Astra 绘图 Runtime

`python` 与 `echarts` 是互相隔离、禁止运行时安装依赖的一次性 OCI runtime。生产发布必须将基础镜像与构建结果解析为不可变 digest，并生成 SBOM、执行漏洞扫描后再写入 Astra 配置。两个入口只接受 `/input/request.json` 声明式协议，只能写入 `/output`，不接受源码或命令字段。

本地构建：

```bash
docker build -t astra-runtime-python:0.1.0 runtimes/python
docker build -t astra-runtime-echarts:0.1.0 runtimes/echarts
```

# 发布 Astra

Astra 的发布目标是：维护者给 Codex 一句自然语言指令，其余步骤由
版本脚本、GitHub Actions 和发布门禁完成。

## 一次性设置

1. 在仓库的 Actions 设置中允许工作流读取仓库并写入 Releases 与 Packages。
2. 首次发布后，在 GitHub Packages 中把 `astra-backend`、`astra-frontend`
   和 `astra-data-viz` 三个 GHCR 包设为 Public。
3. 建议为仓库启用 Immutable Releases。工作流会先创建草稿、上传并证明全部
   产物，最后才公开 Release。
4. 保护 `main`，要求 `CI / Backend`、`CI / Frontend`、
   `CI / Container definitions` 和 `CI / Release contract` 通过。

## 一句话发布

对 Codex 说：

> 发布 Astra v0.2.0：稳定版，自动生成 Release Notes，通过全部测试后发布；失败则停止，不要发布。

Codex 应执行以下发布契约：

1. 拒绝包含未审阅改动、未解决冲突或泄露凭证的工作区。
2. 执行 `python scripts/release.py set 0.2.0`，审阅版本差异和 Release Notes。
3. 运行后端、前端、版本与发行包校验。
4. 将版本变更通过 PR 合入 `main`。
5. 在合入的提交上触发 `Release` 工作流，或推送签名/注释 tag `v0.2.0`。
6. 监控工作流直到 Release 与三个多架构 GHCR 镜像全部发布。

正式发布不会绕过测试。稳定版本只有在 GitHub Release 草稿和所有产物都准备
完成后，才会把四个容器镜像提升为 `latest`。

预发布版本使用 SemVer 后缀，例如 `0.2.0-rc.1`。它们不会更新 `latest`。

## 本地发布校验

```bash
python scripts/release.py verify "$(cat VERSION)"
python scripts/release.py bundle "$(cat VERSION)" dist
```

`bundle` 会生成可复现的 `astra-vX.Y.Z.tar.gz` 与 `SHA256SUMS`。GitHub
工作流还会添加 SPDX SBOM、构建来源证明和四个 `linux/amd64` /
`linux/arm64` 容器镜像。

## 失败与重试

- 版本、测试或镜像构建失败：不会创建公开 Release。
- 同名 tag 已指向其他提交：立即失败，不移动 tag。
- Release 已存在：立即失败，不覆盖既有产物。
- 只重试失败的 GitHub Actions job；不要删除或重建已经公开的同版本 Release。
- 修复后发布新补丁版本，避免替换用户已经下载的产物。

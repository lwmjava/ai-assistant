# Changelog

本文件记录本仓库的显著变更。版本号遵循[语义化版本](https://semver.org/lang/zh-CN/)，以 [`pyproject.toml`](pyproject.toml) 与 [`frontend/package.json`](frontend/package.json) 为准。

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。尚未打正式发布标签的变更写在 `[Unreleased]`；正式发布时再将条目移入带日期的版本标题下。

## Breaking

本节是 Breaking 区：跨版本不兼容变更必须先写在本区对应版本的 `### Breaking` 小节，再执行升级。升级流程为停写 → 迁移 → 启动，说明见 [版本升级说明](docs/workflows/version-upgrade.md)。

下列情况应记入本区（不限于此）：

- 不兼容的 HTTP API、请求或响应字段、错误码变更
- 需要迁移或无法自动兼容的数据 schema 变更
- 不兼容的配置项更名、删除或默认值变化
- 索引身份、嵌入模型或向量格式变化，导致旧索引不能直接继续检索
- 回滚时必须启动变更前应用版本，才能正确提供服务的行为变化

记录要求：

- 写明不兼容点、影响范围、迁移或兼容办法，以及回滚时是否必须启动变更前版本
- 每个版本（含 `[Unreleased]`）都保留 `### Breaking` 标题；若无此类变更，写「无」
- 升级与回滚演练记录应链接到本标题（[CHANGELOG.md#breaking](CHANGELOG.md#breaking)），并同时指向本次涉及版本下的 `### Breaking` 小节
- 不要在本文件写入密钥、连接串、备份内容或生产数据

## [Unreleased]

当前代码版本号为 `0.1.0`，尚未打正式发布标签。

### Breaking

无。

### Added

- 建立本变更日志，并明确 Breaking 区书写约定。

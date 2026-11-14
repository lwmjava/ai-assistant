# 依赖漏洞处置

扫描日期：2026-11-14。本次只扫描并记录处置，不升级依赖，也不执行自动修复。

## 命令与结果

- `scripts/license_audit.py check` 退出码 0。直接依赖 72 个，其中 2 项许可证例外继续沿用 `docs/operations/dependency-licenses.md` 已写下的处置。
- `scripts/security_checks.py python-audit` 退出码 0。检查器对 `requirements.txt` 与 `pyproject.toml` 的运行依赖及 `dev`、`langgraph` extras 做严格审计，漏洞数为 0。
- `scripts/security_checks.py npm-audit` 退出码 0。只读 `frontend/package-lock.json`，不安装依赖、不运行生命周期脚本。锁定依赖 414 个，漏洞数为 0，其中 critical、high、moderate、low 都是 0。

两次漏洞扫描都向公开包元数据服务查询包名和版本，没有上传源码、密钥或 `.env`。原始 JSON 留在本机 `data/validation/`，不进入仓库。

## 处置

当前没有待处置的漏洞。没有新增例外，也没有忽略规则。以后锁定文件或依赖版本变化时需要重跑上述命令；本记录只覆盖 2026-11-14 这次扫描。

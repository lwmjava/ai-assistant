# 直接依赖许可证清单

本仓库第一方代码采用 Apache-2.0。本文件覆盖后端与前端的全部直接依赖，并记录需要单独处置的许可证。
清单由 `scripts/license_audit.py` 根据 `pyproject.toml`、`requirements.txt`、`frontend/package.json` 与 `frontend/package-lock.json` 生成并校验。
本文不构成法律意见，也不升级任何依赖版本。

## 结论

- 直接依赖共 72 项（后端 42，前端 30），已与依赖文件核对一致。
- 其中 70 项属于策略允许的宽松许可证。
- 需要处置的项 2 个，均已写下处置结论。
- 当前无未处置的不兼容许可证。

## 许可证策略

与 Apache-2.0 第一方代码一并使用时，下列 SPDX 标识视为策略允许：

`0BSD`, `Apache-2.0`, `BSD-2-Clause`, `BSD-3-Clause`, `BSD-3-Clause-Clear`, `BlueOak-1.0.0`, `CC0-1.0`, `HPND`, `ISC`, `MIT`, `MPL-2.0`, `OFL-1.1`, `PSF-2.0`, `Python-2.0`, `Unlicense`, `Zlib`。

含 `OR` 的表达式只要有一侧被允许即视为兼容；含 `AND` 的表达式必须全部被允许。
GPL、AGPL、LGPL、专有许可证、未知许可证，以及其他未列入允许集合的标识，必须在清单中留下处置结论，否则校验失败。
SIL OFL-1.1 仅用于网页字体嵌入；使用时仍须遵守保留字体名称、不得单独出售字体等 OFL 条件。

## 处置结论

### pymupdf

- 生态：python
- 许可证：`AGPL-3.0-or-later OR LicenseRef-Artifex-Commercial`
- 处置：PyMuPDF 为 AGPL-3.0-or-later 与 Artifex 商业许可证双授权，不属于本仓库允许的宽松许可证。本仓库第一方代码仍为 Apache-2.0；本记录不改变项目许可证，也不构成法律意见。当前用途是 PDF 版式文本提取（bbox/阅读顺序），以及 OCR 场景下的页面栅格化。未安装该包时，文本层解析会回退到 pypdf（BSD-3-Clause）；OCR 渲染会明确报依赖缺失。本清单不升级或移除该依赖，它仍留在默认运行依赖中。对外分发或提供包含 PyMuPDF 的服务前，必须取得 Artifex 商业授权，或按 AGPL-3.0 履行相应义务；无法接受上述条件的部署应从安装中省略 PyMuPDF，仅使用 pypdf 做文本提取。此项已记录为发布前必须核验的包装义务。

### psycopg2-binary

- 生态：python
- 许可证：`LGPL-3.0-or-later`
- 处置：psycopg2-binary 为 LGPL-3.0-or-later，并带有官方声明的 OpenSSL 链接例外。本仓库不修改其源码，仅将其作为未改动的 PostgreSQL 适配库使用；它出现在默认 requirements 中，SQLite 场景可以不安装。按该项目常见的库使用方式记录为已接受，不将本仓库第一方代码改为 LGPL。本记录不构成法律意见。

## 后端直接依赖

| 名称 | 声明 | 核验版本 | 来源 | SPDX | 结论 |
| --- | --- | --- | --- | --- | --- |
| `alembic` | `alembic==1.14.1` | 1.14.1 | requirements 直接依赖 | `MIT` | compatible |
| `bcrypt` | `bcrypt==4.2.1` | 4.2.1 | 后端运行依赖；亦见 requirements 直接依赖 | `Apache-2.0` | compatible |
| `coverage` | `coverage==7.10.6` | 7.10.6 | 可选 extra:dev | `Apache-2.0` | compatible |
| `croniter` | `croniter>=2.0.0` | 6.2.4 | 可选 extra:workflow；亦见 requirements 直接依赖 | `MIT` | compatible |
| `cryptography` | `cryptography==50.0.2` | 50.0.2 | 后端运行依赖；亦见 requirements 直接依赖 | `Apache-2.0 OR BSD-3-Clause` | compatible |
| `detect-secrets` | `detect-secrets==1.5.0` | 1.5.0 | 可选 extra:dev | `Apache-2.0` | compatible |
| `fastapi` | `fastapi==0.133.0` | 0.133.0 | 后端运行依赖；亦见 requirements 直接依赖 | `MIT` | compatible |
| `httpx` | `httpx==0.28.1` | 0.28.1 | 后端运行依赖；亦见 requirements 直接依赖 | `BSD-3-Clause` | compatible |
| `langchain-core` | `langchain-core>=1.0,<2.0` | 1.6.2 | 可选 extra:langchain | `MIT` | compatible |
| `langchain-text-splitters` | `langchain-text-splitters>=1.0,<2.0` | 1.1.3 | 可选 extra:langchain | `MIT` | compatible |
| `langgraph` | `langgraph==1.2.18` | 1.2.18 | 可选 extra:langgraph | `MIT` | compatible |
| `langgraph-sdk` | `langgraph-sdk==0.4.10` | 0.4.10 | 可选 extra:langgraph | `MIT` | compatible |
| `llama-index-core` | `llama-index-core>=0.12,<0.13` | 0.12.42 | 可选 extra:llamaindex | `MIT` | compatible |
| `mcp` | `mcp>=1.2.0` | 1.12.4 | 可选 extra:mcp | `MIT` | compatible |
| `mypy` | `mypy==1.14.1` | 1.14.1 | 可选 extra:dev | `MIT` | compatible |
| `numpy` | `numpy==2.1.3` | 2.1.3 | 后端运行依赖；亦见 requirements 直接依赖 | `BSD-3-Clause` | compatible |
| `openpyxl` | `openpyxl>=3.1,<4.0` | 3.1.5 | 后端运行依赖 | `MIT` | compatible |
| `pip-audit` | `pip-audit==2.10.1` | 2.10.1 | 可选 extra:dev | `Apache-2.0` | compatible |
| `pre-commit` | `pre-commit==4.7.0` | 4.7.0 | 可选 extra:dev | `MIT` | compatible |
| `psycopg2-binary` | `psycopg2-binary==2.9.10` | 2.9.10 | requirements 直接依赖 | `LGPL-3.0-or-later` | exception |
| `pydantic` | `pydantic==2.10.4` | 2.10.4 | 后端运行依赖；亦见 requirements 直接依赖 | `MIT` | compatible |
| `pydantic-settings` | `pydantic-settings==2.7.0` | 2.7.0 | 后端运行依赖；亦见 requirements 直接依赖 | `MIT` | compatible |
| `pyjwt` | `pyjwt==2.15.1` | 2.15.1 | 后端运行依赖；亦见 requirements 直接依赖 | `MIT` | compatible |
| `pymilvus` | `pymilvus==2.5.11` | 2.5.11 | 可选 extra:milvus | `Apache-2.0` | compatible |
| `pymupdf` | `pymupdf>=1.26,<2.0` | 1.28.2 | 后端运行依赖 | `AGPL-3.0-or-later OR LicenseRef-Artifex-Commercial` | exception |
| `pypdf` | `pypdf==6.21.4` | 6.21.4 | 后端运行依赖 | `BSD-3-Clause` | compatible |
| `pytest` | `pytest==9.0.3` | 9.0.3 | 可选 extra:dev | `MIT` | compatible |
| `pytest-asyncio` | `pytest-asyncio==1.4.1` | 1.4.1 | 可选 extra:dev | `Apache-2.0` | compatible |
| `pytest-cov` | `pytest-cov==7.1.0` | 7.1.0 | 可选 extra:dev | `MIT` | compatible |
| `python-docx` | `python-docx>=1.1,<2.0` | 1.2.0 | 后端运行依赖 | `MIT` | compatible |
| `python-dotenv` | `python-dotenv==1.2.2` | 1.2.2 | 后端运行依赖；亦见 requirements 直接依赖 | `BSD-3-Clause` | compatible |
| `python-multipart` | `python-multipart==0.0.31` | 0.0.31 | 后端运行依赖；亦见 requirements 直接依赖 | `Apache-2.0` | compatible |
| `python-pptx` | `python-pptx>=1.0,<2.0` | 1.0.2 | 后端运行依赖 | `MIT` | compatible |
| `ruff` | `ruff==0.8.4` | 0.8.4 | 可选 extra:dev | `MIT` | compatible |
| `sqlalchemy` | `sqlalchemy==2.0.36` | 2.0.36 | 后端运行依赖；亦见 requirements 直接依赖 | `MIT` | compatible |
| `sqlmodel` | `sqlmodel==0.0.22` | 0.0.22 | 后端运行依赖；亦见 requirements 直接依赖 | `MIT` | compatible |
| `sse-starlette` | `sse-starlette==2.2.1` | 2.2.1 | 后端运行依赖；亦见 requirements 直接依赖 | `BSD-3-Clause` | compatible |
| `starlette` | `starlette==1.3.1` | 1.3.1 | 后端运行依赖；亦见 requirements 直接依赖 | `BSD-3-Clause` | compatible |
| `types-croniter` | `types-croniter==6.2.4.20260711` | 6.2.4.20260711 | 可选 extra:dev | `Apache-2.0` | compatible |
| `types-pyyaml` | `types-PyYAML==6.0.12.20260906` | 6.0.12.20260906 | 可选 extra:dev | `Apache-2.0` | compatible |
| `uvicorn` | `uvicorn[standard]==0.34.0` | 0.34.0 | 后端运行依赖；亦见 requirements 直接依赖 | `BSD-3-Clause` | compatible |
| `xlrd` | `xlrd>=2.0,<3.0` | 2.0.2 | 后端运行依赖；亦见 requirements 直接依赖 | `BSD-3-Clause` | compatible |

## 前端直接依赖

| 名称 | 声明 | 锁定版本 | 来源 | SPDX | 结论 |
| --- | --- | --- | --- | --- | --- |
| `@fontsource/plus-jakarta-sans` | `@fontsource/plus-jakarta-sans@5.1.0` | 5.1.0 | 前端运行依赖 | `OFL-1.1` | compatible |
| `@fontsource/space-grotesk` | `@fontsource/space-grotesk@5.1.0` | 5.1.0 | 前端运行依赖 | `OFL-1.1` | compatible |
| `@hookform/resolvers` | `@hookform/resolvers@5.2.2` | 5.2.2 | 前端运行依赖 | `MIT` | compatible |
| `@tanstack/react-query` | `@tanstack/react-query@5.59.0` | 5.59.0 | 前端运行依赖 | `MIT` | compatible |
| `clsx` | `clsx@2.1.1` | 2.1.1 | 前端运行依赖 | `MIT` | compatible |
| `lucide-react` | `lucide-react@0.446.0` | 0.446.0 | 前端运行依赖 | `ISC` | compatible |
| `react` | `react@18.3.1` | 18.3.1 | 前端运行依赖 | `MIT` | compatible |
| `react-dom` | `react-dom@18.3.1` | 18.3.1 | 前端运行依赖 | `MIT` | compatible |
| `react-hook-form` | `react-hook-form@7.55.0` | 7.55.0 | 前端运行依赖 | `MIT` | compatible |
| `react-markdown` | `react-markdown@9.0.1` | 9.0.1 | 前端运行依赖 | `MIT` | compatible |
| `react-router-dom` | `react-router-dom@7.18.5` | 7.18.5 | 前端运行依赖 | `MIT` | compatible |
| `remark-gfm` | `remark-gfm@4.0.0` | 4.0.0 | 前端运行依赖 | `MIT` | compatible |
| `tailwind-merge` | `tailwind-merge@2.5.2` | 2.5.2 | 前端运行依赖 | `MIT` | compatible |
| `zod` | `zod@4.6.7` | 4.6.7 | 前端运行依赖 | `MIT` | compatible |
| `zustand` | `zustand@4.5.5` | 4.5.5 | 前端运行依赖 | `MIT` | compatible |
| `@eslint/js` | `@eslint/js@^9.39.5` | 9.39.5 | 前端开发依赖 | `MIT` | compatible |
| `@types/node` | `@types/node@22.7.5` | 22.7.5 | 前端开发依赖 | `MIT` | compatible |
| `@types/react` | `@types/react@18.3.11` | 18.3.11 | 前端开发依赖 | `MIT` | compatible |
| `@types/react-dom` | `@types/react-dom@18.3.0` | 18.3.0 | 前端开发依赖 | `MIT` | compatible |
| `@vitejs/plugin-react` | `@vitejs/plugin-react@4.7.0` | 4.7.0 | 前端开发依赖 | `MIT` | compatible |
| `autoprefixer` | `autoprefixer@10.4.20` | 10.4.20 | 前端开发依赖 | `MIT` | compatible |
| `eslint` | `eslint@10.14.0` | 10.14.0 | 前端开发依赖 | `MIT` | compatible |
| `eslint-plugin-react-hooks` | `eslint-plugin-react-hooks@7.1.1` | 7.1.1 | 前端开发依赖 | `MIT` | compatible |
| `globals` | `globals@^15.15.0` | 15.15.0 | 前端开发依赖 | `MIT` | compatible |
| `postcss` | `postcss@8.5.32` | 8.5.32 | 前端开发依赖 | `MIT` | compatible |
| `tailwindcss` | `tailwindcss@3.4.13` | 3.4.13 | 前端开发依赖 | `MIT` | compatible |
| `typescript` | `typescript@5.6.2` | 5.6.2 | 前端开发依赖 | `Apache-2.0` | compatible |
| `typescript-eslint` | `typescript-eslint@8.72.1` | 8.72.1 | 前端开发依赖 | `MIT` | compatible |
| `vite` | `vite@6.4.5` | 6.4.5 | 前端开发依赖 | `MIT` | compatible |
| `vitest` | `vitest@5.0.5` | 5.0.5 | 前端开发依赖 | `MIT` | compatible |

## 如何更新

在已安装对应 Python 环境，并且前端 `node_modules` 与当前 lock 版本一致时执行：

```powershell
D:\DepTooL\anaconda3\envs\ai-assistant\python.exe scripts/license_audit.py refresh
D:\DepTooL\anaconda3\envs\ai-assistant\python.exe scripts/license_audit.py check
```

如需从另一份已安装的前端目录读取许可证字段，可加 `--node-modules <路径>`。
也可使用 `pip list` 对照本机已装版本，但验收以依赖文件与本清单是否一致为准。
新增或变更直接依赖后，必须先刷新清单并补齐处置结论，再提交。

# NFR-001 实现说明 — CI 流水线(lint、typecheck、pytest、前端构建、Vitest、ESLint)

> 日期：2026-10-10
> 计划：`docs/plans/plan_nfr_001_20261010.md`
> 任务卡：`tasks.yaml` `NFR-001`（P0，已置 done）
> 分支：`rag-batch-20261007`（增量交付，未提交的 RAG-039/QA-005 工作区改动保持原样）

## 1. 功能完成情况

仓库从零 CI 配置变为具备 GitHub Actions 流水线，前端补上 Vitest 与 ESLint 两个质量门禁。谁可以做什么：

- **任何人推送任意分支或发起 PR**：`.github/workflows/ci.yml` 自动触发，backend job（ruff → mypy → pytest）与 frontend job（npm ci → typecheck → Vitest → ESLint → build）并行运行；任一步失败该 job 失败、工作流整体失败。
- **维护者**：README 头部新增 CI 状态徽标（`lwmjava/ai-assistant` 的 `ci.yml`，`main` 分支）。若需「失败即阻断合并」，还需在 GitHub 仓库 Settings → Branches 把 `CI` 设为 main 的 required status check（代码层无法强制，未代做）。
- **前端开发者**：`npm test` = Vitest；`npm run lint` = ESLint（9 flat config + typescript-eslint + react-hooks）。4 个既有 `node:test` 测试文件已迁移到 Vitest，37 个测试全绿。

## 2. 代码位置与行为

| 文件 | 内容 |
|---|---|
| `.github/workflows/ci.yml` | 工作流：`on: push / pull_request`，`concurrency` 取消同分支旧运行，最小 `contents: read` 权限；backend 用 ubuntu + Python 3.12，安装 `requirements.txt` + `.[dev]` 后跑 `ruff check .`、`mypy app/`、`pytest`；frontend 用 Node 22 + `npm ci`（缓存 lock）后跑 typecheck/test/lint/build |
| `frontend/package.json` | scripts 增加 `test: vitest run`、`lint: eslint .`；devDependencies 增加 vitest ^2.1.9、eslint ^9、typescript-eslint ^8、@eslint/js、eslint-plugin-react-hooks ^5、globals |
| `frontend/package-lock.json` | 与 package.json 同步（npm ci 可复现） |
| `frontend/vitest.config.ts` | Vitest 独立配置：node 环境、`src/**/*.test.ts`、`@` 别名与 vite 一致；不加载 react 插件（现有测试为纯逻辑/SSR，无需 DOM） |
| `frontend/eslint.config.js` | ESLint 9 flat config：recommended + typescript-eslint recommended + react-hooks recommended，忽略 dist/node_modules/coverage，配置类文件按 node 全局 |
| `frontend/src/lib/chunk-content.test.ts` 等 4 个测试 | `import test from 'node:test'` → `import { test } from 'vitest'`；`download.test.ts` 桩函数去掉未使用参数 |
| `README.md` | 徽标区新增 `[![CI](…)](.github/workflows/ci.yml)` |
| `tasks.yaml` | NFR-001 `status: backlog` → `done` |
| `docs/plans/plan_nfr_001_20261010.md` | 本卡实现计划（落盘） |

成功行为：推送/PR 后约 2–4 分钟出结果；前端 37 测试 + lint 0 错误 + typecheck + build 全绿；后端 ruff/mypy 零问题。
失败行为：任一命令退出码非 0 → 对应 step 红 → job 红 → workflow 红，合并侧由 required status check（需在仓库设置启用）阻断。

## 3. 验证（2026-10-10 实测，命令与流水线一致）

| 命令 | 退出码 | 结果 |
|---|---|---|
| `ruff check .` | 0 | All checks passed（188 源文件） |
| `mypy app/` | 0 | Success: no issues found in 188 source files |
| `pytest` | 1 | 1 failed / 1126 passed / **336 errors** / 337 warnings（见 §4 技术债） |
| `cd frontend && npm run typecheck` | 0 | 通过 |
| `cd frontend && npm test` | 0 | 4 files / 37 tests passed（Vitest 2.1.9） |
| `cd frontend && npm run lint` | 0 | 0 errors（ESLint 9 flat config） |
| `cd frontend && npm run build` | 0 | 通过（8.04s） |
| `cd frontend && npm ci` | 0 | 可复现安装成功 |

验收 evaluation.cases 本地等价验证（故意失败探针，验证后已删除）：

- 临时引入 `tmp-lint-check.ts`（未使用变量）→ `npm run lint` 退出码 1（`no-unused-vars` 命中）；
- 临时引入 `tmp-fail.test.ts`（断言失败）→ `npm test` 退出码 1（1 failed，其余 37 passed）。

## 4. 未做的事与上线前技术债（开工重跑核对结果）

- **不修 pytest 失败/error**：开工重跑 `pytest` = 1 failed（`tests/test_rag_backend.py::test_factory_defaults_native - assert False`，与当前工作区未提交的 RAG 改动相关）+ 336 errors。336 errors 根因抽查为 Windows 清理 `data/pytest-tmp/run` 的 `PermissionError [WinError 5]`（setup 阶段，非断言失败），与历史「65 个 error」同源（集中于 `test_rag_import_jobs`、`test_rag_permission_semantics`、`test_sandbox`，另现于 `test_rag_reparse_failure_safety`、`test_rag_schema_repair`、`test_rag_vectorization_api`、`test_rag_log_minimization`）。**标记为上线前技术债**，归 `QA-005`/RAG-039 收尾处理；CI 在 ubuntu runner 上的 pytest 结果以实际运行为准，若仍红即属于同一技术债的可判定失败。
- 不做自动部署/发布、不引入第三方 CI、不写覆盖率数字。
- 未改 `AGENTS.md`（不在本卡 `allowed_paths`）。
- 「失败即阻断合并」的 required status check 需用户在 GitHub 仓库设置启用，代码层未代做。

## 5. 回滚

- 删除 `.github/workflows/ci.yml` 即恢复零 CI，不影响运行时。
- 前端依赖/脚本回退：`git checkout frontend/package.json frontend/package-lock.json frontend/vitest.config.ts frontend/eslint.config.js` 并还原 4 个测试文件的 import。
- 无数据库、配置或权限变更，无需迁移。

# EVAL-002 实现说明（已关闭）

- 分支：`feat/EVAL-002`
- 基线提交：`03e8bf30f2a7332260478e54a549d452dcf4b3e8`（`feat/stage5-quota-chat-export-adm-sec`；`main` 无 EVAL-001/FEED-001）
- 最终业务候选：`df2b1ffaf09d686b595acb1883cc91cc57bd2a5b`
- 契约：`docs/plans/plan_eval_002_dataset_governance_20261023.md`
- 解释器：`D:/DepTooL/anaconda3/envs/ai-assistant/python.exe`
- 平台：Windows 10（本机专项）；Ubuntu CI 绑定最终业务 SHA

## 行为摘要

- 租户隔离数据集：创建 / 列表 / 导入版本 / 导出（非 system_admin 脱敏 holdout）/ 提交审核 / 审核 / 受限删除
- AI 来源禁止 Gold；Gold 批准需 `system_admin` + notes=`ACCEPT_GOLD`
- 冻结版本不可提交；已批准/冻结不可删
- 反馈/评分可提名回归候选并审核，不自动入集、不升 Gold
- 权限：`evaluations.write`=`system_admin|tenant_admin`，`delete`=`system_admin`

## 主要文件

- `app/models/evaluation.py`、`app/services/evaluation_datasets.py`
- `app/api/routes/evaluation.py`、`app/schemas/evaluation.py`
- `alembic/versions/f3a9b1c2d4e5_add_evaluation_datasets.py`
- `app/core/security.py`、`frontend/src/lib/permissions.ts`
- `frontend/src/pages/Evaluations.tsx`、`frontend/src/api/evaluations.ts`
- `tests/test_evaluation_datasets.py`
- `docs/swagger.json`、`README.md`、`docs/product/项目产品需求方案.md`

## 门禁结果（2026-10-23）

| 检查 | 命令 | 退出码 | 结果 |
|---|---|---|---|
| 专项后端 | `python -m pytest tests/test_evaluation_datasets.py tests/test_evaluation_api.py tests/test_openapi_coverage.py -q` | 0 | 16 passed |
| Ruff | `python -m ruff check .` | 0 | All checks passed |
| mypy | `python -m mypy app/` | 0 | 242 source files, no issues |
| 前端 typecheck | `cd frontend; npm run typecheck` | 0 | 通过 |
| 前端 Vitest | `cd frontend; npm test -- --run` | 0 | 23 files / 185 passed |
| 前端 ESLint | `cd frontend; npm run lint` | 0 | 通过 |
| 前端 build | `cd frontend; npm run build` | 0 | 通过 |
| Swagger 导出 | `python docs/export_swagger.py` | 0 | 已含 `/evaluations/datasets*` 与 `/candidates*` |
| 全量 pytest（修基线后） | `python -m pytest` | 0 | **3425 passed**, 3 skipped（827.59s） |

### 全量失败隔离与修复

首次全量曾 **83 failed / 3342 passed**。去掉 EVAL-002 业务改动后同簇仍失败，判定为基线问题。已在同分支修复（见 `implementation_baseline83_fix_20261023.md`）：Windows UTF-8 subprocess、迁移 stamp 不越过 `timeout_seconds`、日志测试 mock `consume_tokens`。修复后全量 0 failed。

## 受托 API 验收（2026-10-23）

用户更新 `.env` 管理员口令后授权执行 `data/validation/eval002_agent_accept.py`（本机 `127.0.0.1:8000`，`system_admin`）：**15 PASS / 0 FAIL**。覆盖创建数据集、导入 draft、hash、AI+gold→422、submit、approve、已批准删除→422、点踩→提名候选→accept、接受后已批版本 `case_count` 仍为 2。口令与 token 未写入本文件。

## 业务接受与关闭（2026-10-23）

用户确认「可以关闭了」后业务验收接受；随后确认 GitHub CI 通过。

同 SHA CI（run [39950697792](https://github.com/lwmjava/ai-assistant/actions/runs/39950697792)）：

| Job | ID | 结论 |
|---|---|---|
| Backend (ruff / mypy / pytest) | 120404143260 | success |
| Frontend (typecheck / vitest / eslint / build) | 120404143302 | success |
| Agent official baseline | 120404143098 | success |
| RAG official baseline | 120404143229 | success |

任务状态 → `done`。不代表合并或生产发布授权。

## 残余说明（非阻断）

- 独立审查未单独落盘（实现者自审 + 受托验收已做）
- `tenant_admin` holdout 脱敏未在受托脚本覆盖（system_admin 主链已通；专项测试已覆盖脱敏）
- 关闭后若仅追加本验收说明到 `tasks.yaml` / 本文件，不改变 `df2b1ff` 检查输入，可沿用上述 CI 证据

## 回滚

关闭新增路由与 `evaluations` write/delete 权限；保留表数据；不自动 DROP。

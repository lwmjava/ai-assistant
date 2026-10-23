# EVAL-002 数据集版本/审核/反馈候选 — 实施契约

基线：`03e8bf3`（分支 `feat/EVAL-002` ← `feat/stage5-quota-chat-export-adm-sec`）。  
前置：EVAL-001、FEED-001 代码与 `done` 已在本工作线；`main` 尚未合并，故不从 `main` 开工。

## 1. 锁定决定

| 项 | 决定 |
|---|---|
| 权限 | `evaluations:read` 不变；新增 `evaluations:write`=`system_admin`/`tenant_admin`；`evaluations:delete`=`system_admin`（仅草稿/驳回/撤回） |
| 租户 | 数据集与候选按 `tenant_id` 隔离；`system_admin` 可指定租户，默认自身租户；跨租户读一律 404 |
| 版本不可变 | `approved` / `frozen` 内容与 `content_hash` 不可改；只能新建版本 |
| 冻结/holdout | `frozen=true` 禁止改 split/tier；holdout 用例禁止经 API 改成 development/validation；导出对非 `system_admin` 脱敏 holdout 期望字段 |
| 等级 | AI 来源（`source=ai_generated`）默认且强制非 Gold；审核 `approve` 不得自动升 Gold；显式 `ACCEPT_GOLD` 且仅 `system_admin` |
| 反馈候选 | 复用 FEED-001 已有记录快照入候选；不改反馈 CRUD；不自动入集/不改 Gold |
| 保留 | 无自动过期删除；物理删除仅草稿/驳回/撤回，且需 `delete`；已批准数据删除不在本卡 |
| 哈希 | `sha256(canonical_json(cases))`，`sort_keys=True`，分隔符 `,:` |

## 2. 状态机

版本：`draft` → `pending_review` → `approved` \| `rejected`；`frozen` 为导入只读标记（可与 `approved` 并存语义上视为不可变）。  
候选：`pending` → `accepted` \| `rejected` \| `withdrawn`。

## 3. API（前缀 `/api/v1/evaluations`）

- `GET/POST /datasets`
- `GET /datasets/{id}`
- `POST /datasets/{id}/versions`（导入）
- `GET /datasets/{id}/versions`
- `GET /datasets/{id}/versions/{version_id}`（导出语义）
- `POST /datasets/{id}/versions/{version_id}/submit`
- `POST /datasets/{id}/versions/{version_id}/review`
- `DELETE /datasets/{id}/versions/{version_id}`（受限）
- `GET/POST /candidates`
- `POST /candidates/{id}/review`

## 4. 回滚

关闭新路由与写权限；保留表数据；不自动 DROP。

## 5. 验证命令（开发专项）

```powershell
D:/DepTooL/anaconda3/envs/ai-assistant/python.exe -m pytest tests/test_evaluation_datasets.py tests/test_evaluation_api.py -v
D:/DepTooL/anaconda3/envs/ai-assistant/python.exe -m ruff check app/models/evaluation.py app/services/evaluation_datasets.py app/api/routes/evaluation.py app/schemas/evaluation.py
```

完整门禁按 AGENTS.md §10，关闭前绑定最终 SHA。

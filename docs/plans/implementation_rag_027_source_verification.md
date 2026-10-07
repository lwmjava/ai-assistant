# RAG-027 授权引用原文只读核验 — 实现说明

> 任务卡：RAG-027（P1，L2，前置 RAG-026）
> 权限依据：[ADR-0007 引用原文只读核验权限](../adr/0007-rag-source-verification-permission.md)（Accepted）
> 关联：ADR-0001 §10（uploader 检索隔离）、ADR-0003（时效过滤开关）
> 日期：2026-10-07

## 1. 做了什么

新增一个**只读**的块级核验入口，让「检索时能命中」的同租户成员可以回查被引用块的原文与定位信息，
同时不扩大任何控制面权限（删改 / 重解析 / 发布 / 整文件下载 / 资源 ACL）。

| 层 | 改动 |
| --- | --- |
| 受控 Service | 新增 `app/rag/evidence.py`（唯一核验实现，每次请求重新鉴权） |
| HTTP 入口 | 新增 `GET /api/rag/chunks/{chunk_id}/evidence`（`app/api/routes/rag.py`） |
| 前端 | 新增 `frontend/src/components/knowledge/ChunkEvidence.tsx`；在检索命中块与文档分块处挂「核验原文」入口 |
| 前端接口层 | `frontend/src/api/rag.ts` 新增 `useChunkEvidence`；`frontend/src/types/api.ts` 新增 `ChunkEvidenceOut` / `ChunkLocatorOut` |
| 测试 | 新增 `tests/test_rag_027_source_verification.py`（21 条） |

未改动：`can_read_document` / `can_control_document` / `can_write_document`、控制面详情与整文件下载端点
（`GET /api/rag/documents/{id}`、`GET /api/rag/documents/{id}/download`）的判断逻辑与权限。

## 2. ADR-0007 逐条映射

| ADR-0007 条款 | 落点 | 测试 |
| --- | --- | --- |
| §1 tenant 下可核验同租户、未软删、检索状态可见的有权命中块 + 标题 / 版本 / 页码 / 段落 / 源范围 | `load_chunk_evidence` 复用 `can_read_document`（同租户 + 未软删 + 当前版 + uploader 归属），再叠加时效语义；`_locator()` 从 `chunk_metadata` 取 `page` / `section_path` / `source_start` / `source_end`，没有的一律 `None` | `test_tenant_mode_peer_verifies_authorized_hit`、`test_tenant_mode_returns_locator_metadata`、`test_tenant_mode_missing_metadata_is_explicit_null` |
| §2 不提供整章 / 整文件旁路读取；父块须独立复核同文档关系 + 同一检索授权 + 返回范围 | `_authorized_parent()`：父子必须 `parent.document_id == chunk.document_id`，父块所属文档再走一遍 `_document_is_verifiable`；默认只给定位信息，`include_parent_content=true` 才带父正文（仍是单个块） | `test_parent_defaults_to_locator_without_content`、`test_parent_content_only_when_authorized`、`test_parent_in_other_tenant_document_is_denied`、`test_parent_owned_by_other_uploader_is_denied`、`test_parent_of_replaced_version_is_denied` |
| §3 uploader 下仅本人上传内容；管理员核验读路径不豁免；管理权限按 ADR-0001 控制面矩阵 | 复用 `can_read_document`：uploader 模式下 `TENANT_ADMIN` 也落到 `doc.user_id == user.id`；控制面仍走 `can_control_document` / `can_write_document` | `test_uploader_mode_peer_cannot_verify_owners_chunk`、`test_uploader_mode_tenant_admin_is_not_exempt`、`test_control_plane_matrix_unchanged_for_admin` |
| §4 每次核验重新从受控 Service 检查主体 / 租户 / Document-Chunk 关联 / 当前版本与生效规则 / 软删；来源标识与缓存文本不作授权证明；越权或缺失失败关闭，不返回正文或存在性细节 | 端点无缓存、无「来源票据」入参；先 `session.get(DocumentChunk)` 再按 chunk 自己记录的 `document_id` 取 `Document`，并校验 `chunk.tenant_id == doc.tenant_id`；可选 `document_id` 只是调用方声称值，不一致即拒；失败统一 `404 + EVIDENCE_DENIED_MESSAGE` | `test_claimed_document_mismatch_is_denied`、`test_chunk_document_tenant_mismatch_is_denied`、`test_cross_tenant_chunk_is_denied`、`test_denial_hides_existence_details` |
| §5 保持时效过滤开关语义；未发布 / 已替换 / 软删不通过普通核验读取；旧引用失效明确反馈，不自动指向新版 | `_document_is_verifiable()`：`RAG_EFFECTIVE_DATE_FILTER` 打开时再过 `document_is_live()`；关闭时沿用 `is_current` 语义（与检索面一致）。旧块被拒，**不会**回退到同组新版 | `test_replaced_version_is_denied`、`test_unpublished_draft_is_denied`、`test_soft_deleted_document_is_denied` |
| §6 不授予删改 / 重解析 / 发布 / 整文件下载 / 资源 ACL | 新增端点只读；不改控制面端点与判定 | `test_verifier_cannot_delete_or_reparse_owners_document`、`test_verifier_cannot_download_source_file` |

## 3. 返回字段契约

`GET /api/rag/chunks/{chunk_id}/evidence?document_id=&include_parent_content=`

```jsonc
{
  "chunk_id": "…",            // 命中块
  "document_id": "…",
  "document_title": "共享手册",
  "version_state": "published",
  "chunk_index": 0,
  "content": "该块正文",       // 只含这一个块
  "source": "rag027-shared",
  "page": 3,                   // 没有则 null
  "section": "第二章 / 2.1 计费", // section_path 字符串数组会拼成 " / " 连接
  "source_start": 40,          // 源范围，没有则 null
  "source_end": 96,
  "parent": {                  // 无父块或父块未通过独立鉴权时为 null
    "chunk_id": "…",
    "chunk_index": 0,
    "page": null,
    "section": null,
    "source_start": null,
    "source_end": null,
    "content": null            // 默认 null；include_parent_content=true 且父块独立鉴权通过才非空
  }
}
```

**返回上限**：1 个命中块正文 +（可选）1 个父块正文。响应里没有整文档正文、源文件路径、内容哈希、ACL
或任何控制面字段——字段集合被测试逐个钉住（`_EVIDENCE_FIELDS`）。

## 4. 鉴权顺序（每次请求，无缓存）

1. 主体权限：`require_permission("knowledge_bases", "read")`（不足 403，不进入核验逻辑）。
2. 取块：`session.get(DocumentChunk, chunk_id)`，不存在 → 拒绝。
3. 取文档：按 **块自己记录的** `document_id` 取 `Document`，不存在 → 拒绝。
4. 关联自洽：`chunk.tenant_id == doc.tenant_id`，否则视为数据被拼改 → 拒绝。
5. 声称关联：传入了 `document_id` 且 `chunk.document_id != document_id` → 拒绝。
6. 检索面授权：`can_read_document(doc, user)`（同租户、未软删、`is_current`、uploader 归属）。
7. 时效：`RAG_EFFECTIVE_DATE_FILTER` 打开时再过 `document_is_live()`。
8. 父块：独立复核同文档关系 + 同一检索授权 + 限定返回范围。

任一步不满足：统一 `404` + 固定文案 `引用原文不可用或无权核验`，**不返回正文，也不回显块 ID 或
「存在但无权」**。异常原因只留在服务端。

## 5. 已知取舍

1. **管理员语义（唯一一处「代码 ≠ ADR 字面」，按 team-lead 裁决维持现状）。** 正确表述是：
   - **核验面 = 检索面**，逐项对齐 `read_scope_for()` 与 `can_read_document()` 的真假；
   - **`TENANT_ADMIN` 在 uploader 模式下不豁免**（`app/rag/access.py` 的 `can_read_document` 对非
     `SYSTEM_ADMIN` 一律落到 `doc.user_id == user.id`，`read_scope_for` 同样给出 `uploader_id`）；
   - **`SYSTEM_ADMIN` 豁免，与 `read_scope_for`（`uploader_id=None`）的既有行为一致，沿用 RAG-026 CE-1**
     （`docs/reviews/2026-10-07-RAG-026上传者范围独立审查.md` 已记录「SYSTEM_ADMIN 保留」并与
     `can_read_document` 逐例同真同假）。

   这不是本卡引入的偏差，而是 RAG-026 已批准的现状；本卡刻意不改，因为改它会同时改变检索面。
   理由：核验面必须等于检索面，否则「检索命中却不能核验」会把调用方逼向控制面详情接口——那正是
   ADR-0007 想消灭的旁路诱因。两个方向的偏移都由 `test_evidence_matches_retrieval_face_for_every_role`
   锁住（见 §6）。若日后要改成字面合规，必须**同时**改 `read_scope_for` 与 `can_read_document` 两处，
   并同步 RAG-026 的期望值。
   （注：早期台账里「admin 不豁免 uploader 限制」的措辞不严谨，以上面这段为准。）
2. **父块默认只给定位信息。** 要父正文必须显式 `include_parent_content=true`，且仍只返回单个父块，
   不做整章拼接；父块未通过独立鉴权时整体返回 `null`（连父块 ID 都不给），以免泄漏存在性。
3. **父块必须同文档。** 父子跨文档（他人文档、他租户文档、旧版本文档）一律不认 — 这比「逐项判父文档权限」
   更保守，也让「旧版本父块」这类场景天然被拒。
4. **不缓存、不签发票据。** 每次核验都打库；性能代价换来「权限变化立即生效」，符合 ADR-0007 §4。
   该结论有 allow → deny 两个方向的测试守卫（成功之后换主体 / 换范围 / 文档转态都必须立刻失败）。
5. **父块两道防线是有意的纵深防御。** `_authorized_parent()` 里「同文档关系」与「再走一次检索授权」
   在当前数据模型下冗余（前者成立时后者恒为真），单独删任一道在当前行为上不可观测。保留它是为了
   数据模型将来允许父子跨文档时仍有防线；docstring 已如实写明，不要因为「看起来不可达」而删掉。
6. **`include_parent_content` 当前没有前端调用方。** 后端保留该参数并由
   `test_parent_content_only_when_authorized` 锁住（属**预留能力**）：前端 `ChunkEvidence` 只请求默认
   行为（父块仅定位信息），`frontend/src/api/rag.ts` 的 `useChunkEvidence` 不传该参数。因此前端的
   「父正文」渲染分支在当前 UI 上走不到——这是有意的，不是死代码错误：等核验 UI 需要展示父块上下文时
   再接，接之前后端契约与测试保持不变。
7. **未做的事**：不提供批量核验、不提供按文档导出全部块正文、不提供源文件下载（这些属于整文件读取，
   ADR-0007 明确不授予）。**Chat 会话侧未挂载核验入口**（本卡只挂知识库页的检索命中块与文档分块）；
   未来若挂载到 Chat，引用失效时同样展示后端文案「引用原文不可用或无权核验」，不猜测块或版本。

## 5.1 已知边界（登记不修）

- **失败路径残余时序侧信道。** 「块不存在」比「无权」少一次 `session.get(Document)`，实测
  p50 差约 0.44ms（Cohen's d ≈ 0.10，单次错分率 37.5%），即单次请求基本不可分，仅大样本均值可分离。
  ADR-0007 §4 的「不返回存在性细节」按「不提供可用区分手段」解读时这是残留面，但做到严格等时的代价
  远大于收益。**登记为已知边界，不做防护**；约定：存在性只能通过「是否返回正文」判定。
- **前端核验结果缓存不随登出失效。** `useChunkEvidence` 的 queryKey 不含用户维度，`logout()` 也不清
  react-query 缓存（仓库既有模式：文档列表、分块列表同样如此）。后端每次仍重新鉴权，因此不是越权读，
  只是同一浏览器换账号后短时间内可能先显示上一账号已读到的原文。**登记，单开前端项统一修。**

## 6. 验证

```
DATABASE_URL="sqlite:///…/data/tmp-rag027-<n>.db" python -m pytest tests/test_rag_027_source_verification.py -q --basetemp=data/pytest-tmp/rag027-<n>
```
**30 条全绿**（首轮 21 条 + 审查整改后新增 9 条）；`tests/ -k "rag or chunk or context or embedding"`
无本卡引入的失败；`cd frontend && npx tsc -b --force` 通过；`ruff` / `mypy app/rag/` 干净。

### 首轮变异验证（8 处，每处抓红后还原）

| # | 变异 | 变红测试 | 条数 |
| --- | --- | --- | --- |
| 1 | `can_read_document` 去掉同租户校验 | `test_cross_tenant_chunk_is_denied`、`test_denial_hides_existence_details` | 2 |
| 2 | `can_read_document` 去掉 `is_current` 校验 | `test_replaced_version_is_denied`、`test_unpublished_draft_is_denied` | 2 |
| 3 | `can_read_document` 去掉 uploader 限定 | `test_uploader_mode_peer_cannot_verify_owners_chunk`、`test_uploader_mode_tenant_admin_is_not_exempt` | 2 |
| 4 | `_authorized_parent` 不做独立鉴权（沿用子块授权） | `test_parent_in_other_tenant_document_is_denied`、`test_parent_owned_by_other_uploader_is_denied`、`test_parent_of_replaced_version_is_denied` | 3 |
| 5 | 失败时区分 403/404 并回显块 ID | 9 条（软删、旧版、草稿、跨租户、存在性、uploader×2、错误关联、租户自洽） | 9 |
| 6 | `can_read_document` 去掉软删校验 | `test_soft_deleted_document_is_denied` | 1 |
| 7 | 信任调用方传入的 `document_id` | `test_claimed_document_mismatch_is_denied` | 1 |
| 8 | 不校验 `chunk.tenant_id == doc.tenant_id` | `test_chunk_document_tenant_mismatch_is_denied` | 1 |

还原方式：`git checkout -- app/rag/access.py`（其余文件用改动前副本覆盖），
`grep -rn "MUTATION" app/` 为空 + 全绿确认无残留。

### 审查整改（第二轮）复现证据与整改后证据

独立审查（`docs/reviews/2026-10-07-RAG-027授权引用原文只读核验独立审查.md`）指出三条 Medium 与若干 Low：
实现行为正确，**缺的是能把它锁住的测试**。下表给出「整改前的存活证据」（审查实测 / 我复跑）与
「整改后的抓红证据」。

| 项 | 缺口 | 整改前的复现证据 | 整改（新增用例） | 整改后的变异结果 |
| --- | --- | --- | --- | --- |
| **M-01** | 缓存绕过只有 deny→deny 方向，缺 allow→deny | 按 `chunk_id` 缓存并复用上次授权结论 → 21 条全绿（存活）；owner 先 200 后 peer 可拿到正文 | `test_previous_verification_is_not_reused_for_another_subject`（owner 200 → peer 404 → owner 仍 200，同进程同 chunk_id）、`test_document_state_change_takes_effect_immediately`（两次请求之间软删 → 第二次 404）、`test_scope_tightening_takes_effect_immediately`（tenant→uploader 收紧 → 第二次 404） | 同一缓存变异 → **4 红**（上述 3 条 + 等价性用例） |
| **M-02** | 时效分支 `RAG_EFFECTIVE_DATE_FILTER` 零覆盖 | 删掉分支 → 21 条全绿（存活）；组合实测开关打开时未生效 / 已过期版本被放行（404 → 200） | `test_effective_date_filter_blocks_unpublished_and_expired[scheduled]`、`[expired]`（开关打开 → 404，含服务层直连断言）、`test_effective_date_filter_still_allows_live_version`（生效中仍 200）、`test_effective_date_filter_off_keeps_is_current_semantics`（开关关闭时语义不变） | 删除时效分支 → **2 红**（`[scheduled]` / `[expired]`） |
| **M-03** | 核验面 = 检索面只有 `TENANT_ADMIN` 单向约束 | 去掉 `SYSTEM_ADMIN` 豁免 → 全仓 42 passed（存活），无人发现两面脱钩 | `test_evidence_matches_retrieval_face_for_every_role`：四角色 × 两种范围，逐个断言 `RAGService.search` 是否命中（真实检索，非只读 `can_read_document`）与 `/evidence` 是否 200 **同真同假**，并用 `_EXPECTED_FACE` 钉住每个角色的期望取值（防「全都不可见」也能通过等价断言） | 去掉 `SYSTEM_ADMIN` 豁免 → **1 红**，报错原文：`uploader 核验面与检索面脱钩：检索={'owner': True, 'peer': False, 'tenant_admin': False, 'system_admin': True}，核验={'owner': True, 'peer': False, 'tenant_admin': False, 'system_admin': False}` |
| **L-01** | 父块租户自洽分支零覆盖 | 删掉 `parent.tenant_id != doc.tenant_id` → 21 条全绿（存活） | `test_parent_with_tampered_tenant_is_denied`（父子同文档、父块 `tenant_id` 被拼改 → `parent=null`，不返回父正文） | 删除该校验 → **1 红** |
| **L-02** | 父块两道防线冗余，单删不可证伪 | M2 / M2b 单删各存活，同时拆才红 | 不改行为：`_authorized_parent` docstring 如实写明「冗余但有意保留」，见 §5.5 | 行为不变；纵深防御的意图可读 |
| **L-05** | `include_parent_content` 无前端调用方 | — | 按 team-lead 裁定保留后端能力、前端不接；§5.6 写明「预留，当前无前端调用方」，后端由 `test_parent_content_only_when_authorized` 锁住 | 不变 |
| **L-06 / L-04** | 时序侧信道 / 前端登出不清缓存 | — | 按裁定登记不修，写入 §5.1「已知边界」 | 不变 |
| **I-03 / I-06** | 措辞与注释笔误 | — | §5.1 补上「SYSTEM_ADMIN 偏差来源 RAG-026 CE-1」与「Chat 侧未挂载」；`app/rag/evidence.py` 注释 block → 块 | 不变 |

四角色等价性**实测取值**（整改后，uploader 模式）：
`owner=True / peer=False / tenant_admin=False / system_admin=True`，核验面与之一一对应；
tenant 模式四角色同为 `True`。该取值表即 `_EXPECTED_FACE`，任一处单独偏移都会变红。

## 7. 回滚

关闭 `GET /api/rag/chunks/{chunk_id}/evidence` 与前端 `ChunkEvidence` 入口即可；不改数据、不改控制面权限，
回退后旧接口权限不扩大、原文不删除。

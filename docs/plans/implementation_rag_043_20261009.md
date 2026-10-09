# RAG-043 实现说明：生成输出围栏与受限标识抑制

- 卡：RAG-043（P1，depends_on RAG-035）
- 日期：2026-10-09
- 实施：任务 Agent；不提交 Git、不改 tasks.yaml（主 Agent 统一登记）。

## 1. 功能实际完成了什么

在生成/输出侧新增**确定性受限标识围栏** `RestrictedIdentifierFence`：模型输出披露受限标识
（rag-036 的 `NW-HR-001`）时，检测并就地替换为 `[受限内容已屏蔽]`，使该标识不再出现，
零容忍违规消除。零额外 LLM 调用、零网络、零费用。

- 注册表可注入；内置种子 v0.1 含 `NW-HR-001`（case-insensitive；分隔符容忍 `- _ 空白`；
  词边界锚定）。
- 独立 `enabled` 开关：关闭时原样返回（可回滚）。
- 已接入 `OutputFilter`（可选 `restricted_fence` 参数）：命中时加 `restricted_id_leak` 原因、
  `sanitized_text` 替换为脱敏文本。

## 2. 成功与失败行为

- 命中：`flagged=True`、`hits=[注册id]`、`redacted_text` 已屏蔽；多 occurrence 全部替换；幂等。
- 未命中/空文本：原样返回。
- 注册表单条 `re.error`：fail-open 跳过该条，不污染原文、不阻断链。
- 误伤保护：合法 `NW-PRINT-X1`（打印机型号）不被屏蔽；`NW-HR-001` 不误命中更长串 `NW-HR-0010`。

## 3. 代码位置

| 文件 | 作用 | sha256[:16] |
|---|---|---|
| `app/security/restricted_identifier_fence.py` | 围栏组件（注册表/FenceResult/RestrictedIdentifierFence） | `e2ddb4500b36` |
| `app/security/output_filter.py` | OutputFilter 接入可选 `restricted_fence` | `1f4913458865` |
| `tests/test_restricted_identifier_fence.py` | 10 项反例/集成测试 | `a91eb0beb666` |
| `docs/plans/plan_rag_043_20261009.md` | 实施计划 | `085a0163a5f7` |

未改 `.env`、`app/core/config.py`、`app/rag/`（除门禁扫描）、冻结基线、生产数据、RAG-015/032/036。

## 4. rag-036 回归（E2 证据）

用真实报告 `rag-v0.1-gen-real-20261009.json` 中 rag-036 的**原始回答文本**重放：

| 阶段 | zero_tolerance | forbidden_violations | provisional_status |
|---|---|---|---|
| 围栏前 | True | ['NW-HR-001'] | fail |
| 围栏后 | **False** | [] | needs_human_review |

`fence.filter(real_answer)` → flagged=True、hits=['NW-HR-001']。即围栏把受限标识屏蔽后，
RAG-035 判定器不再命中零容忍。测试 `test_rag036_real_answer_redaction_clears_zero_tolerance`
固化该行为。

**未做真实模型重跑**：本卡未发起任何新的付费模型调用（卡约束）。rag-036 回归基于已存真实回答
文本 + 确定性围栏重放，属 E2（真实输出文本 + 隔离确定性组件），不是 E1 线上重跑。

## 5. 验证命令与结果（解释器 `D:\DepTooL\anaconda3\envs\ai-assistant\python.exe`）

| 命令 | 退出码 | 结果 |
|---|---|---|
| `pytest tests/test_restricted_identifier_fence.py --basetemp <uuid>` | 0 | 10 passed |
| `ruff check app/rag/ app/security/` | 0 | All checks passed |
| `mypy app/rag/ app/security/` | 0 | Success: no issues in 82 files |
| `pytest tests/ -k "rag or chunk or context or embedding" --basetemp <uuid>` | 1 | 859 passed, 2 failed, 3 skipped |

既有两基线失败（SUT 不在本卡 diff）：
- `test_rag_033_parse_quality.py::test_report_file_name_carries_gate_version`（日期漂移）；
- `test_rag_log_minimization.py::test_milvus_delete_failure...`（milvus mock 签名）。
本卡未触碰这两处。测试 DB 用全新 basetemp，无残留污染。

## 6. 范围决策（如实记录）

- **live 接线未做**：`chat_service._apply_output_security` 当前不把 `OutputFilter.sanitized_text`
  回写用户响应；`OutputFilter()` 现有无参构造不注入围栏。故围栏在**线上对话响应路径默认不启用**，
  本卡交付的是围栏组件 + E2 隔离回归（RAG-040 先例：模块未接线主链路＝预期默认关闭，接线属另卡）。
  未新增 config.py 开关（allowed_paths 不含 config.py）。
- **未做检索侧 ACL**（RAG-041）：围栏是输出侧兜底，不替代检索权限过滤。

## 7. 关闭对账记录

| 原始契约项 | 证据类别 | 结果 | 证据位置/指纹 |
|---|---|---|---|
| 受限标识输出抑制围栏实现 | E2 | 通过 | `restricted_identifier_fence.py` `e2ddb4500b36` |
| rag-036 零容忍转绿/拒答口径 | E2 | 通过（围栏后 zero=False） | §4 + 测试 `a91eb0beb666` |
| 规则可独立关闭/回退 | E2 | 通过（`enabled=False` 原样返回测试） | 同上 |
| 调用受预算/无额外 LLM | E2 | 通过（纯正则，零模型调用） | 同上 |
| 无凭据外泄 | E2 | 通过（不读不记任何 key） | 模块无 I/O/网络 |
| 不误伤合法相似 ID | E2 | 通过（NW-PRINT-X1 保留测试） | 同上 |
| ruff/mypy 门禁 | E2 | 通过（0/0） | §5 |
| **线上 live 响应路径实际生效** | — | **未验证**：围栏组件就绪，chat_service 未接线 sanitized_text 回写（范围决策 §6，另卡） |
| **真实模型重跑 rag-036（E1）** | — | **未验证**：卡约束不发起未授权真实调用；E2 重放已证明围栏逻辑 |

## 8. 未验证项

1. 线上 live 对话响应路径是否实际屏蔽（围栏已就绪但未接线 chat_service 回写）——另卡接线。
2. 真实模型在线重跑 rag-036（E1）——需主 Agent 转用户授权后另跑。

# 前 16 个交付包清点

> 日期：2026-09-28
> 对照：`docs/plans/plan_delivery_2027-03-25.md` 第 6 节第 1–16 项
> 第 17–20 项不进入本表，也不计入 80%。

一项写成完成，需要两件事同时成立：`tasks.yaml` 里对应任务的状态是 `done`，并且能指出一份写了验证命令和结果的计划或实现说明。计划里只有预定命令、没有记录输出的，写成未完成。`tasks.yaml` 已是 `done` 但缺结果的，本表仍写成未完成，不把该任务状态改回去。

2026-12-25 的 80% 等于第 1–16 项全部完成。本表有未完成项，80% 未达到。第 13 项留在这 16 项里。

2026-09-29 按 `EVD-001` 重跑。当天下午早些时候 RAG-012 与 RAG-013 的 pytest 退出码为 1、ruff 退出码为 1，第 1–3 行保持未完成。混合检索重跑隔离与 Mock 编码检查之后，同一天下午再次原样执行。RAG-011 四条与 RAG-012 / RAG-013 共用组全部退出码为 0。第 1–3 行改为完成。记录见 `docs/plans/implementation_evd_001.md`。

2026-09-29 按 `EVD-002` 补记第 4 项。同一天早些时候 `npm run build` 退出码为 2，第 4 行保持未完成。工作区补上 `frontend/src/lib/access-token.ts` 后重跑，健康检查、编排配置、前端类型检查与构建退出码均为 0。第 4 行改为完成。记录见 `docs/plans/implementation_evd_002.md`。

| # | 交付包 | 任务 | tasks.yaml | 结论 | 证据 |
|---|---|---|---|---|---|
| 1 | A1 阶段 2 | RAG-011 | done | 完成 | `docs/plans/plan_rag_011_control_plane.md` 第 8 节「验证通过」：pytest 30 passed, 1 warning in 37.08s，ruff、mypy、前端类型检查退出码都是 0 |
| 2 | A2 阶段 3 | RAG-012 | done | 完成 | `docs/plans/plan_rag_012_soft_delete.md` 第 8 节「验证通过」：pytest 69 passed, 1 warning in 64.93s，ruff、mypy 退出码都是 0。前端类型检查引用 RAG-011 同一次退出码 0 的运行 |
| 3 | A3 阶段 4 | RAG-013 | done | 完成 | 命令文本与 RAG-012 相同，只跑一次。输出见 `docs/plans/plan_rag_013_version_states.md` 第 8 节「验证通过」：pytest、ruff、mypy 退出码都是 0 |
| 4 | B1 可安装 | INST-001～INST-003 | 均为 done | 完成 | 2026-09-29 重跑：`pytest tests/ -k health -v` 为 10 passed, 2 skipped, 443 deselected, 1 warning in 3.63s，退出码 0；`ruff check app/api/routes/health.py` 退出码 0。缺少 JWT 或模型密钥时 `docker compose config` 退出码 1，输出不含仓库内 JWT 占位口令。两项设置后退出码 0，服务含 `db`、`milvus`、`app`，`RAG_VECTOR_STORE` 为 `local`，`MILVUS_URI` 为 `http://milvus:19530`，`SERVE_FRONTEND` 为 `"true"`。`npm run typecheck` 与 `npm run build` 退出码 0，`frontend/dist` 已生成。见 `docs/plans/implementation_evd_002.md` |
| 5 | B2 租户与用户 | TEN-001～TEN-003 | 均为 done | 完成 | `docs/plans/plan_ten_001_create_tenant.md`：`pytest tests/test_admin_tenants.py -v`，4 项通过。`docs/plans/plan_ten_002_create_member.md`：`pytest tests/test_admin_members.py tests/test_admin_tenants.py`，7 项通过。`docs/plans/plan_ten_003_console_users.md`：浏览器创建租户和成员，成员登录后看不到管理入口；`npm run typecheck` 与 `npm run build` 通过 |
| 6 | B3 问答与来源 | QA-001～QA-003 | 均为 done | 完成 | `docs/plans/plan_qa_001_default_retrieval.md`：47 passed，退出码 0。`docs/plans/plan_qa_002_reply_sources.md`：9 passed，退出码 0。`docs/plans/plan_qa_003_show_sources.md`：`npm run typecheck` 与 `npm run build` 退出码 0。浏览器当时没有看到文件名，记录写明本机 `RAG_ENABLED=false`，会话详情里 `sources` 为空 |
| 7 | C1 认证与向导 | AUTH-001～AUTH-003 | 均为 done | 完成 | `docs/plans/plan_auth_c1.md` 实现说明：注册、撤销和刷新用例 12 passed；改过的认证文件 ruff 通过；前端类型检查与构建退出码 0。运行中的 `GET /api/auth/setup-status` 返回 `needs_setup` 为 false；浏览器打开 `/register` 能看到表单，打开 `/setup` 后地址变为 `/chat` |
| 8 | C2 邀请与切换 | INV-001～INV-003 | 均为 done | 完成 | `docs/plans/plan_inv_c2.md` 实现说明：邀请、切换、注册和成员用例 13 项通过；ruff 通过；前端类型检查与构建退出码 0。浏览器记录了发码、接受和切换后对话列表更换 |
| 9 | C3 管理后台四页 | ADM-001～ADM-004 | 均为 done | 完成 | `docs/plans/plan_adm_c3.md` 实现说明：用户、租户生命周期、系统状态和成员用例 10 项通过；ruff 通过；前端类型检查与构建退出码 0。浏览器记录了四页的数据和成员看到的无权说明 |
| 10 | C4 对话与会话 | CHAT-001～CHAT-004 | 均为 done | 完成 | `docs/plans/plan_chat_c4.md` 实现说明：`pytest tests/test_chat_controls.py tests/test_chat.py -v --tb=short`，16 passed。该说明同时写明，前端类型检查、构建和浏览器点击当时没有重新跑完 |
| 11 | C5 模型路由 | ROUTE-001～ROUTE-002 | 均为 done | 完成 | `docs/plans/plan_route_c5.md` 实现说明：路由与健康检查 25 passed；`-k "llm or route"` 23 passed, 1 skipped；对话相关 15 passed；ruff 通过；前端类型检查通过，`npx vite build` 通过。浏览器停在登录页，没有进入对话页看引导是否消失 |
| 12 | C5 沙箱 | SAND-001～SAND-002 | 均为 done | 完成 | `docs/plans/plan_sand_c5.md`：SAND-001 为 15 passed，以及 15 passed, 1 skipped；SAND-002 为 23 passed, 8 deselected，ruff 通过，前端类型检查与构建退出码 0。说明写明 Unix 限额没有在 Linux 上实机跑通，浏览器没有登录后点发送 |
| 13 | C6 安全 P0 与 Milvus 门槛核对 | SEC-001～SEC-003 | 均为 done | 未完成 | 上传限制：完成。`docs/plans/implementation_sec_001.md` 记录 `pytest tests/ -k upload -q` 为 16 passed, 1 skipped，类型检查退出码 0，ruff 通过。日志脱敏：完成。`docs/plans/implementation_sec_002.md` 记录 `pytest tests/ -k "security or log" -q` 为 32 passed, 1 skipped，类型检查退出码 0，ruff 通过。Milvus 五条：未完成。`docs/plans/record_milvus_five_gates.md` 与 `docs/plans/implementation_sec_003.md` 写明第 1–4 条未执行，总结论未通过，默认向量库仍是 local。本项不因此移出 16 项 |
| 14 | B3 知识库与软删除 | QA-004 | done | 完成 | `docs/plans/plan_qa_004_soft_delete_console.md`：列出的 pytest、ruff 和 `npm run typecheck` 均为通过、退出码 0。浏览器记录了删除确认、默认列表隐藏、显示已删除，以及重建状态 |
| 15 | C1 首次运行旅程 | AUTH-004 | done | 完成 | 2026-09-29：单独库 `data/readme_walk.db` 上走过 README 五步。健康检查 `status` 为 `ok`。空库 `/setup` 创建管理员后进入 `/chat`，向导随后关闭。`/register` 注册成员后进入 `/chat`。`/chat` 发出「你好」并看到助手回复。`checks.llm.mode` 为 `real`。见 `docs/plans/implementation_evd_003.md` |
| 16 | C4 会话隔离 | CHAT-005 | done | 完成 | `docs/plans/plan_chat_c4.md` 实现说明覆盖隔离：成员不能处理别人的会话，系统管理员看不到其他租户。同一条验证是 `pytest tests/test_chat_controls.py tests/test_chat.py`，16 passed。`tests/test_chat_controls.py` 开头写明身份范围，其中包含同租户互不可见和其他租户不可见 |

完成 15 项：1、2、3、4、5、6、7、8、9、10、11、12、14、15、16。

未完成 1 项：13。这一项留在分母里。

80% 未达到。

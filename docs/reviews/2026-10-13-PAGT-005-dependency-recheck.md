# PAGT-005 前置 PAGT-001 当前证据复核

日期：2026-10-13。独立身份：`/root/pagt005_dependency_check`；未参与 PAGT-005 实现，仅修改本报告。依据原始 tasks.yaml PAGT-001/PAGT-005、AGENTS.md、task-card-delivery 1.3.0 依赖复核要求；已读基础指导、Harness 规范、相关 Cursor 规则、PAGT-001 原审查/验证/业务验收与关闭对账，直接读取当前搜索、文件读取、注册表、执行器和 health 实现及相关测试。

**结论：PAGT-001 的当前原始关键验收路径复核通过，未发现该前置卡未决 P1/P2；支持 PAGT-005 准入。** 本报告不是凭 done 字段或旧数量判断。当前证据为 E2 隔离集成及 E3 单元补充，不证明收费搜索质量、真实凭据或生产共享目录授权，也不代替 PAGT-005 本身验收。

## 当前代码与历史证据区别

Git HEAD：`a0d02eb411a53c3191e81f0fc99528ea60d0d3c4`。当前工作区含此前 PAGT-004/PAGT-006 的未提交改动，本复核未修改、提交或归为 PAGT-005 交付。旧报告指纹不能直接充当当前通过证据：web_search/file_ops/base 已增加兼容字符串的 ToolOutput 错误分类；旧 PAGT-001 脚本模型测试显式进入 legacy_plan_mode，保留原断言。health 和 PAGT-001 工具/反例测试内容仍与旧验收字节一致，当前集成文件指纹已变化。代码审读及实际重跑绑定下方当前指纹。

旧 PAGT-001 审查中的 F01 参数回显与 F03 深 JSON 拒绝已在当前正式反例通过。后续 PAGT-004 的 CE-06（工具业务错误被算作成功）不能忽略：当前 web_search/file_ops 已在返回处保留原文本并附错误状态，后续独立 `2026-10-13-PAGT-004-repair-recheck.md` 记载 CE01–07 复验关闭；该报告仅作历史背景，不能替代本轮实跑。

## 本 Agent 实际命令与结果

所有 pytest 串行独占，解释器 `D:/DepTooL/anaconda3/envs/ai-assistant/python.exe`，环境 `RAG_BACKEND=native`、`DATABASE_URL=sqlite:///./data/test_ai_assistant.db`；独立 basetemp，`-p no:cacheprovider`。不读取 `.env` 内容、真实凭据或生产数据库，不发收费 HTTP 请求。

1. `python -m pytest tests/test_pagt001_tools.py tests/test_pagt001_review.py tests/test_pagt001_integration.py tests/test_tools.py tests/test_health.py -q -p no:cacheprovider --basetemp=data/pytest-tmp-pagt005-dependency-20261013`：**125 passed、1 failed，4.96 秒，退出 1**。失败为追加范围 `tests/test_health.py::test_health_ok` 的向量状态预期 ok、实际 degraded；不能隐去这次失败。
2. 只读诊断 Python：调用真实 `_embedding_index_view()`，新 SQLModel Session 查询测试库 `DocumentChunk.index_id IS NULL`。实际 `status=unavailable`、`legacy_chunks=1`、未知身份分块数量 1，退出 0。health 对未知历史分块按现有规则降级；conftest 明确不清除 index_id=NULL 的历史被测数据。本次没有删除残留、修改 health 或弱化断言。该通用健康失败已隔离于共享测试数据状态，不属于新增工具无 key 启动契约失败；主 Agent 后续扩大验证应继续记录与隔离此状态。
3. `python -m pytest tests/test_pagt001_tools.py tests/test_pagt001_review.py tests/test_pagt001_integration.py tests/test_tools.py -q -p no:cacheprovider --basetemp=data/pytest-tmp-pagt005-dependency-scope-20261013`：**119 passed、0 failed、0 skipped，3.81 秒，退出 0**。其中无 key TestClient lifespan/真实 `/api/health` 专项仍 HTTP200、checks.tools unavailable、旧四工具清单正确。移除第1轮额外通用健康文件是契约范围复核，不将失败伪装成已通过。

两轮均有既有沙箱 ast.NameConstant 弃用提示；不影响结果，不宣称生产运行无警告。未执行本轮全仓 pytest、ruff/mypy、前端或真实远端验证。

## 原始前置要求与当前链路

| PAGT-001 原始要求/边界 | 实际当前操作及可观察结果 | 结果 |
|---|---|---|
| 多 provider 搜索、对话返回来源 | 四国内 provider，真实注册/Agent/HTTP 客户端适配器，合成 HTTP 响应；真实 `/api/chat` 来源回复并新请求回读持久化消息 | E2 通过，真实收费质量未验证 |
| 缺 key 启动/health 可见 | TestClient lifespan，真实 health checks.tools unavailable；默认旧四工具存在 | E2 通过 |
| file_ops 读、开关与路径白名单 | 临时公开文件读取，源字节不变；绝对/穿越/未授权根/秘密文件/额外身份和写动作拒绝 | E2 通过 |
| 文件身份与绑定 | 实际 Windows 句柄、junction、硬链接及对象替换反例；无未授权内容返回 | E2 通过，0 skipped |
| 数值与远端故障 | NaN/Infinity/bool/超限；timeout、429、500、深/非法 JSON、超大响应、取消；固定安全错误、无收费重试 | E2/E3 通过 |
| 回滚及旧工具兼容 | 关闭配置后清单恢复四工具，旧持有对象拒绝新读取，calculator/沙箱等既有断言保留 | E2 通过；生产回滚未执行 |
| 旧审查缺陷 | F01 参数/异常脱敏与 F03 深 JSON 正式反例当前通过 | 当前无该卡未决 P1/P2 |

file_ops_write 是后续 PAGT-002 的默认关闭能力，不将现有目录存在写能力误报成 PAGT-001 最初交付越界。当前读取测试拒绝额外 write 动作；PAGT-005 不因本复核获得写入开放授权。

## 当前 SHA256（文件字节）

| 文件 | SHA256 |
|---|---|
| app/agents/tools/web_search.py | 985f9bfaf430cfe7752b6ed3724e819b2b4b4bab5e66ac80d3a022dbdc33a5a2 |
| app/agents/tools/file_ops.py | c6c2e1a4fa802a1b94f515317b9115360b3da3900f47988676ccf5470c3a3c0d |
| app/agents/tools/base.py | f53a0d32bf0991a70f2ec9c718874e7855bd5376993e01cdd546a016d9b6ead9 |
| app/agents/tools/builtin.py | 74251a7354f1fa9c17ac4d900ede16de36de06e06ac975bfbfb113eeb90b7829 |
| app/agents/tools/executor.py | dfd8203bf477ad0709a0fcbdd081c75a2313eb8a3756aa3135334424c3f233e6 |
| app/core/config.py | 74895a3dfa7ddb831fb5cd52968164da5ed87c50a4af07cff1117278353d5432 |
| app/api/routes/health.py | 597a4a70b7eb115be60bd351e32d19987c0ce309ed41c295f3f571d224dc78bf |
| tests/test_pagt001_tools.py | e4f127573eaf822a51f180ffcd07e9d488ebca821e24d23a67376ee8c428fcc0 |
| tests/test_pagt001_review.py | a1aa740b9f94632940191229eb225cd02b7c116424903a79837e37a1a34fcd84 |
| tests/test_pagt001_integration.py | 02cf7d6079e64977cb27eecfbd2debbd034f36ff65ee2ee16057c75eb387219e |
| tests/conftest.py | 00ffb3da933e6107ce9a25c47b32909e316eb7566b6151e198dba63ca1d1930c |

指纹变化应重验受影响路径。本次仅修改此报告，不更改任务状态。PAGT-005 关闭前仍需核对前置关键文件与本次当前证据绑定；本报告不自动关闭任何卡。

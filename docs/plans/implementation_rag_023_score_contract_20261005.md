# 检索适配分数契约修复说明

最终状态（2026-10-05）：done。用户提供豆包独立审查、17 passed 的人工执行截图及实际页面请求 effective=langchain / fallback=false / HTTP 200 的日志，并明确批准关闭。验收证据与限制见 `../reviews/2026-10-05-RAG023分数契约复核.md` 最终关闭记录。下文 in_review、待验收等描述为历史过程，已由本结论取代。

## 页面检索的实际后端日志

用户页面复测未见 INFO 后定位：根 logger 无 handler，lastResort 的 WARNING 门槛导致应用 INFO 不输出。service.py 已增加按需控制台 handler，并使用项目 JsonLogFormatter。实际项目环境无根 handler 验证通过：两次调用输出恰好两条 JSON 日志，分别包含 langchain/false 和 native/true，仅创建一个 handler，不含查询正文，退出码 0。此前受控测试安装捕获 handler，未覆盖真实无 handler 启动情况，本次补齐。全局日志系统治理未扩大实施。

按用户追加授权，`app/rag/service.py` 的 `search` 在调用实际后端前输出 INFO 事件 `rag_search_backend`，包含 requested、effective、implementation、fallback。从知识库页面发起请求后，`effective=langchain implementation=LangChainRagBackend fallback=false` 表示本次请求即将使用 LangChain；`requested=langchain effective=native fallback=true` 表示降级。日志不代表 retrieve 已成功，且不输出查询或文档正文。

项目 conda 解释器执行受控日志捕获验证，覆盖 langchain 生效和 native 降级，检查查询原文未输出，退出码 0。用户真实服务的页面复测待执行。此前用户已提供 17 passed 的运行截图和豆包独立代码核对、命令验证截图；本条日志用于帮助用户确认实际请求路由。

## 最新验证：继承签名修复

用户追加授权修复 `add_texts`、`from_texts` 的两个 override 错误。`add_texts` 接受 `Iterable[str]`，`from_texts` 返回具体适配器类型，两者显式接受基类的 `ids` 关键字参数。保留可空 embedding 的兼容入口；写入仍抛出 `NotImplementedError`。没有增加 ignore 或放宽 mypy 配置。

环境：`D:/DepTooL/anaconda3/envs/ai-assistant/python.exe`。新增生成器、ids 与 classmethod 拒绝写入用例，修改签名前行为测试 13 passed。修改后的实际验证：

- `python -m mypy --cache-dir data/mypy-rag023-review app/rag/backend/langchain_backend.py`：退出码 0，1 个源文件无问题。
- `python -m pytest tests/test_rag_langchain_score_contract.py tests/test_rag_threshold.py -q`：退出码 0，17 passed。
- `python -m ruff check --no-cache app/rag/backend/langchain_backend.py tests/test_rag_langchain_score_contract.py`：退出码 0，All checks passed。
- `git diff --check`：退出码 0。

下文的 mypy 缺失和两个 override 错误属于此前验证记录，现已解决。此次结果不代表全项目 mypy 通过。任务仍为 `in_review`，独立审查和人工业务验收尚未完成。

收尾时再次用项目解释器全量 `yaml.safe_load`，退出码 1：`tasks.yaml` 第 5239 行存在游离的“用户确认关闭”，导致 PRAG-004 区域 ParserError。该位置不属于本次签名修复；已保留并报告，当前不能宣称 YAML 全量验证通过。

任务：RAG-023。状态：in_review，尚未满足最终完成门禁。

已完成：LangChain 检索适配通过 metadata 透传真实稠密 similarity，保留 RRF score。高相似度证据不再因融合分小而被 0.4 阈值误滤；低相似度仍过滤。缺失、非数值、bool、NaN/Inf、越界 similarity 抛明确 ValueError，不占位伪造。失败如何呈现由后续错误契约卡承接。

位置：app/rag/backend/langchain_backend.py；tests/test_rag_langchain_score_contract.py。没有改 RRF、模型、阈值、Milvus 或已有用户业务改动。

验证环境：D:/DepTooL/anaconda3/envs/ai-assistant/python.exe。

- 修改前新增测试：8 failed，退出码 1；明确复现 0.03 融合分被赋为 similarity。
- 修改后 pytest 新契约、threshold、backend：12 passed / 1 skipped，退出码 0。后端模块被模块级可选依赖检查跳过，不把 skip 当通过。
- 进一步定向复测契约+threshold：12 passed，退出码 0。Mock/FakeStore 仅证明字段与阈值链路，不证明真实检索质量。
- Ruff 初次缓存 PermissionError，随后 no-cache 检查并修 import/格式规则；最终检查结果见实际命令。
- mypy 命令退出码 1：No module named mypy；未执行类型分析，未安装、未改用 base。
- YAML/唯一 ID/依赖目标/无环：项目环境检查通过，182 卡。git diff --check 退出码 0。

文档收尾：PRAG-003/004 旧 facts 标为变更前并补证据；RAG-020 done、PRAG-002 backlog 保持；整改计划过时交付语句更新；AGENTS.md 实际 conda 路径已对账。代码未经独立审查及人工验收，类型检查缺失，故不标 done。

回滚：仅回退 similarity metadata 透传/校验与新增测试，不涉及数据库、索引或模型。回滚将恢复已知分数混淆，不能宣称恢复后阈值正确。智能切分/摘要未实施，ADR-0005 仍 Proposed。

## 安装 mypy 后的收尾验证

项目环境 mypy 1.14.1 已安装。首次分析报两个 override 错误后遭 .mypy_cache/missing_stubs PermissionError；改用 `--cache-dir data/mypy-rag023-review` 后完整分析为 `Found 2 errors in 1 file`，类型检查未通过。错误是 add_texts/from_texts 的既有继承签名，HEAD 源码可核对，不在本次分数字段修复中新增；未放宽规则或压错。后续串行命令的 shell 总退出码 0 是最后 Ruff 的结果，不代表 mypy 成功。

新增缺字段、-1/0/1 边界与来源元信息保真验证后，`pytest tests/test_rag_langchain_score_contract.py tests/test_rag_threshold.py -q` 为 16 passed，退出码 0；Ruff no-cache 为 All checks passed、退出码 0。当前执行者复核见 docs/reviews/2026-10-05-RAG023分数契约复核.md，不作为独立审查。保持 in_review，待存量类型门禁处理、独立审查与人工验收；不宣称真实检索质量提升。

# RAG 批次接续核对记录

## 1. 接续基线与授权

2026-10-08 用户请求：先阅读 TASK_HANDOFF.md，核对实际文件状态，再继续未完成工作。沿用已批准15卡集合、依赖、单卡allowed_paths和 module-batch-delivery。分支 rag-batch-20261007，开工HEAD为738aef4234e0447814e79ff04e9e4705397d89f3。

开工实际工作区：3项既有文档删除（plan_v1_domain_matrix_20261004.md、plan_v1_review_split_20261004.md、2026-10-04-9域企业上线完整度评审.md），均与本批无关，不恢复、不纳入提交。native.py无实际未提交修改。

交接漂移：声称15卡不在tasks.yaml与事实不符；本批15卡都存在，8卡done，7卡backlog。交接汇总和台账中旧blocked/重复行不能作为当前准入证据。关闭时对照原卡与最终文件，不能单凭交接建议。

用户已启动Docker Desktop；沙箱外docker compose ps实测etcd/MinIO/Milvus三项healthy，Milvus镜像2.5.11。首次沙箱外检查失败（Linux引擎管道不存在），随后服务由用户启动。

真实费用授权已获用户明确批准，详见[调用授权登记](proposal_rag_real_evaluation_authorization_20261008.md)。没有推送、合并、部署、重建既有真实索引或删除清理授权。

## 2. 当前复核证据

解释器固定D:/DepTooL/anaconda3/envs/ai-assistant/python.exe。每次测试使用全新UUID basetemp和独立DATABASE_URL。

- RAG-032：主Agent实际运行 tests/test_rag_032_index_identity.py tests/test_rag_032_rebuild_cli.py -q，74 passed / 1 skipped，exit 0，8.71s。skip不等于通过；L-02/N-01已提交不等于所有原卡验收通过，另进行独立关闭资格审计。
- RAG-029：独立Agent `/root/review_context` 复审报告[2026-10-08复审](../reviews/2026-10-08-RAG-029按块上下文复审.md)。64项关联测试通过，但3条独立业务反例失败，阻断关闭及RAG-030准入。
- RAG-038：独立Agent `/root/review_deadline` 复审报告[2026-10-08复审](../reviews/2026-10-08-RAG-038时限重试取消复审.md)。144项通过，仍有同步Local查询超deadline返回no_hit、成本usage未追踪、零退避判据与执行不一致，不能关闭。
- RAG-036：依照原始契约落盘计划后，先修全零稀疏排名，再做固定向量语义反例、真实Milvus和真实Embedding切片。固定向量不证明模型检索质量；不新增独立稀疏召回、不改RRF。

## 3. 执行顺序与剩余门禁

主Agent只推进一张卡的业务修改；独立复审可只读开展。当前唯一业务实施为RAG-036，随后修复029、038的实测阻断，复核后再启动030。032/015核对原卡的生命周期与独立证据，不盲目回写done。035在已批准费用范围内制定计划并实施，人工Gold生成结果确认及数值发布门槛未批准时仍不能关闭。

模块整合回归待最终业务版本稳定后运行：RAG子集、全量pytest、ruff、mypy、前端typecheck/build。全树行尾/临时目录/worktree清理不与业务卡混做；删除类操作未经授权保持原状。所有未执行项如实记录，不能声称15卡全部完成。

## 4. 开工基线与失败隔离

在新业务修改前执行RAG子集：`python -m pytest tests/ -k "rag or chunk or context or embedding" -q --basetemp data/pytest-tmp/resume-rag-baseline-94d15d5fea81421aaba277aec2dcef17/tmp`，独立SQLite，7 failed / 734 passed / 4 skipped / 382 deselected，exit 1，129.62s。完整日志：`data/pytest-tmp/resume-rag-baseline-94d15d5fea81421aaba277aec2dcef17/pytest.log`。

6项import_jobs失败在源文件落盘处报FileNotFoundError；使用较短全新路径`data/t/i<uuid12>`，同文件20 passed / exit 0（4.18s），没有修改代码。Windows路径长度影响已隔离；最终回归统一用短UUID目录，不能把这些首次失败记成业务修复。首次运行还出现子进程GBK解码警告，后续进程显式PYTHONUTF8=1，保留原始告警证据。

另一项`test_report_file_name_carries_gate_version`用已提交20261007报告名比较当前20261008默认报告名，是跨天不稳定断言。需要保留门禁版本验证，日期比较绑定报告自身run_at，不能改业务语义或覆盖历史报告来制造通过。

`python -m mypy app/ --cache-dir data/mypy-resume-baseline`：185 source files通过，exit 0。`npm run typecheck`及随后`npm run build`：exit 0 / 0，Vite编译1957 modules。

`python -m ruff check . --no-cache`：exit 1，唯一E501在`scripts/milvus_switch_check.py:884`（151字符）。属于RAG-015已变更脚本，之后在该卡范围内窄修；不放宽lint配置。

032独立关闭审计：[收尾复核](../reviews/2026-10-08-RAG-032收尾复核.md)。L-02/N-01修复成立；新反例证明preparing重建漏调用外部向量写入、提前按active清理旧远端向量，原卡的新collection重建/回退验收未满足。先完成036独立语义补证，再优先整改032，复核015前置链后才关闭036；不把上述审计结果写成032已完成。

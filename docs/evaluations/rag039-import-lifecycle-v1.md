# 导入恢复生命周期 Evaluation v1

日期：2026-10-10。任务 RAG-039，范围为用户批准的单实例摄取调度。数据分类 Smoke、Adversarial、Observed Regression；合成文本/URL响应和 Mock Embedding，非人工 Gold、非质量实验，不使用 holdout 调参。

| 场景版本 | 数据/领域入口 | 可观察标准 | 正式回归 |
|---|---|---|---|
| lifecycle-v1/budget | 5来源与不同batch/concurrency、非法整数/NaN/Infinity | limit独立于峰值；非法配置和预算不抢占 | test_rag_039_configuration、acceptance、import_recovery |
| lifecycle-v1/claim | 重叠runner、3线程多loop、同源/不同内容/不同租户 | 无重复claim/发布，版本串行，attempt有界 | acceptance、independent_review |
| lifecycle-v1/restart | 持久running、合法receipt、新Python进程 | 空registry恢复；唯一文档/块；已发布不再次Embed | import_recovery新进程例、acceptance |
| lifecycle-v1/failure | provider失败/取消、第二claim提交异常、已删/错误来源凭据 | 可重入、无容量泄漏、失效凭据拒绝重放 | independent_review F01/F02、acceptance |
| lifecycle-v1/binding | 错误User租户、跨租户源/重解析、双Engine | 执行前拒绝越权，独立库不共享容量 | acceptance、independent_review |
| lifecycle-v1/rollback | 重解析6类失败、新版失败后search与retry | 原块/计划/snapshot保留、真实旧检索可用 | reparse_failure_safety、acceptance |
| lifecycle-v1/quota-batch-log | 配额URL并发、部分批次、故障日志 | 无跨await写锁；最终计数持久；日志不含正文 | acceptance、log_minimization导入例 |

上述业务用例使用真实代码、隔离SQLite/源文件/合成provider，证据E2。配置helper为E3补充，不能单独证明业务交付。源码最终SHA256、实际红绿结果及独立执行身份在 `docs/reviews/2026-10-10-RAG-039独立验收.md` 和入口独立审查；首次交付时的总体命令及历史基线失败在实现说明。2026-10-10用户授权QA-005后续整改已闭合该批失败/跳过/Ruff错误，最终完整1364通过、0失败、0跳过；当前结果见implementation_qa_005_followup_20261010.md。指标为确定性状态/计数/唯一性/实际并发峰值，不提供未经真实模型测量的质量、延迟或费用数字。人工业务验收仍未执行。

## lifecycle-v1/batch-recovery-f03 补充

分类Observed Regression（原审核真实反例）及Smoke/Adversarial（新增故障/绑定边界）。正式回归：tests/test_rag_039_batch_recovery.py。

| 场景 | 领域入口/故障 | 可观察标准 |
|---|---|---|
| 最后汇总提交失败 | batch2+同源上传2，最后completed2汇总commit故障，recover+until_idle | 子任务success2、文档唯一；新Session batch success/2/2；attempt与块ID不变，零重复Embedding |
| 持久汇总漂移 | 完整绑定的终态/未终态子任务 | success/partial_success/failed/pending/running共用既有规则，不回放子任务 |
| 恢复commit失败 | 修复汇总提交再次故障 | 显式异常+持久旧汇总保留，恢复故障后可补终态 |
| 缺子任务 | 声明total2只1running+合法receipt，或零子任务/極大total | running子任务可恢复success，但批次不缩total/不虚假complete；空批次维持 |
| 绑定错误 | 子任务tenant/user不属于batch | 拒绝聚合，批次/子任务不变，仅安全诊断 |
| 活跃执行/新进程 | 活跃await期间恢复；全终态stale batch新Python进程恢复 | active不重置；新进程batch恢复无任务重放 |
| 数值/查询幂等 | 负数/SQLite整型边界计数、重复恢复、多批次 | 实际计数校正；极大声明不缩；单GROUP BY查询，无逐batch查询；重复恢复无UPDATE |

证据E2：真实应用函数+隔离数据库/源+合成Embedding；不证明检索质量/付费模型/Milvus或生产重启。计划、实际命令/结果、最终指纹与独立复审见本轮实现说明及docs/reviews，旧全仓1364通过不替代新增F03验证。


## Observed Regression：深度审查F07/F04/F05/F06

来源2026-10-10独立真实隔离探针，正式实现回归 tests/test_rag_039_deep_review_regressions.py；均E2 SQLite/临时原文/合成Embedding，非Gold/生产检索质量。场景顺序：未授权同tenant/source同异内容候选明确失败且owner实际list/search/current/块保持，tenant/uploader、file/URL、合法管理员去重/升级对照；构建部分child声明total不缩、commitFalse不提前持久、缺/错tenant/user的batch创建拒绝、错主体不聚合、空batch不成功；真实softdelete后新同源file/URL导入必须成功可search，旧删除状态不恢复；真实success commit后ack OSError/Cancelled不反写失败或重复发布，持久job/doc合法证据核对、提交前失败正常failed及合法retry，管理员旧marker可认可，非法tenant/source/hash/deleted/owner/currentmarker拒绝。

无效原job marker拒绝、非current合法发布receipt、F01/F03、取消/recovery/配额/新Python进程仍由原正式回归覆盖，不能因本轮成功证据分支失效。旧active-child测试同created_at误选已用实际running唯一ID保持原断言修正，时间相同/UUID排序证据见修复计划。最终命令、结果和指纹见主Agent实施说明及独立复审；本轮技术通过不替代人工验收。

## 后续授权业务操作验收

2026-10-10用户明确授权独立Agent代行人工操作验收；rag039_business_acceptance自构6组真实HTTP/领域场景、新Session和两次fresh Python进程，连同前置02410例16passed；亲自补验当前039/URL/线程/取消/管理员69passed，最终85passed/0skipped/exit0，原卡必交付当前E2通过，任务done。报告docs/reviews/2026-10-10-RAG-039独立业务操作验收.md记录原始操作、命令/实际日志及相同源码指纹。仅生命周期/权限/持久结果，不证明真实模型质量或生产启用；原人工待验语句为授权前历史。

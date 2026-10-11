# RAG-017 CI 整改实现说明

日期：2026-10-11；计划：plan_rag_017_ci_fix_20261011.md；任务：tasks.yaml RAG-017。用户明确授权本轮两项修复，含原 allowed_paths 之外的 file_ops.py CI 前置修复。工作树修改，未提交、推送或部署。

## 实际行为

CI 和本地维护者执行 rag_gate_check.py 时，Recall@1/5/10、MRR、nDCG@10 必须为 [0, 1] 内有限数值；NaN、正负无穷、字符串、布尔值、容器、越界及超大整数均明确拒绝，退出 1。缺失指标仍拒绝；低于批准阈值仍拒绝；真实报告、满分及批准阈值的边界值仍通过。越权案例数只接受非负整数，然后按零容忍判定；负值、浮点、布尔等拒绝。overall/recall/safety 错误结构返回明确校验错误，不暴露类型异常堆栈。只对类型异常指标报字段及规则，不回显其原值。

文件只读工具以 sys.platform == "win32" 选择 Windows 句柄路径实现；Linux mypy 可排除 Windows API。实际打开对象、路径白名单、身份复核和只读限制保持既有规则。Windows 真实临时文件读路径及既有拒绝回归已执行；未在 Linux 主机执行工具，只做 Linux 平台静态检查。

## 文件与版本

- app/agents/tools/file_ops.py：平台判断。
- scripts/rag_gate_check.py：排序指标、越权计数及指标结构校验。
- tests/eval/test_rag_gate.py：原有9例加71个参数化实例，门禁CLI回归共80例；Observed Regression/Adversarial/Smoke，标识 rag017-invalid-metrics-v1，基于既有 rag-v0.1 报告构造，不是新增Gold检索数据集。
- docs/ai-prompts/prompt-model-registry.json / .md：追加 prompt-model-v3；保留 v1/v2 全记录，模型默认指纹不变。v3仅 file_ops.py 摘要变化；基点提交 5f829b8f69aa36f1b1d06d51d5fdc0f5540dbbd7，修改未存在于该基点提交。source_version 为 95dcd1219f213af52739b0692911bb05378a0f2808c685b2f34c4f55688e7108。
- tasks.yaml：记录整改，更新当前阻塞；保持 in_progress。
- 本计划、实现说明及 docs/reviews/2026-10-11-RAG-017-ci-fix-review.md：计划、验证和审查证据。

## 验证

全部使用 D:/DepTooL/anaconda3/envs/ai-assistant/python.exe。下表 python 表示该绝对解释器；临时库沿用测试 conftest 的 data/test_ai_assistant.db，基线报告不重写。

| 实际命令 | 退出码与结果 |
|---|---|
| python -m pytest tests/eval/test_rag_gate.py -q --basetemp=data/pytest-tmp/rag017-red-20261011 | 1；修复前69 failed / 11 passed，新增异常输入复现；data/rag017-red-20261011.log |
| python -m mypy app/agents/tools/file_ops.py --platform linux --cache-dir data/mypy-rag017-linux | 1；修复前WinDLL/get_osfhandle共2条attr-defined；data/rag017-mypy-red-20261011.log |
| python -m pytest tests/eval/test_rag_gate.py tests/test_pagt001_tools.py -q --basetemp=data/pytest-tmp/rag017-green-20261011 | 0；140 passed / 15.78s；data/rag017-green-20261011.log |
| python -m pytest tests/eval/ tests/test_pagt001_integration.py tests/test_pagt001_review.py docs/ai-prompts/tests/ -q --basetemp=data/pytest-tmp/rag017-regression-20261011 | 0；277 passed / 69.91s；data/rag017-regression-20261011.log |
| python -m mypy app/ --platform linux --cache-dir data/mypy-rag017-linux-green | 0；191源；data/rag017-mypy-linux-green-20261011.log |
| python -m mypy app/ --platform win32 --cache-dir data/mypy-rag017-win-green | 0；191源；data/rag017-mypy-win-green-20261011.log |
| python -m ruff check . --no-cache | 0；All checks passed |
| python app/agents/version_registry.py check | 0；prompt-model-v3 status ok |
| git diff --check；Python yaml.safe_load(tasks.yaml) | 0；差异格式与YAML合法 |

命令尝试记录：第一次局部Ruff报UP038，改用联合类型isinstance后局部及全仓通过。第一次版本候选生成用+00:00时间串，被既有UTC-Z校验拒绝；未写入登记，改为UTC Z格式后通过，历史记录不变。此前评审的默认mypy缓存missing_stubs权限异常已记录；本次指定新缓存目录完成类型检查，不放宽配置或切换环境。

## 未完成与回滚

用户告知已补密钥，未读取密钥或自行验证远端费用调用。未运行新的official评测、全仓pytest、GitHub Actions、生产部署或人工业务验收；以上测试不证明真实Embedding质量。数据集版本/案例集合/安全切片完整性校验仍是先前评审的残余缺口，本轮未授权实施。RAG-017尚不具备关闭证据。

回滚对应 file_ops.py、门禁代码与测试，选择 v2登记并检查旧源码/模型指纹；质量阈值及数据库无需迁移。无生产写入、删除或配置变更。

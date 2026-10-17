# QA-005 官方/估算计数回归 v1

2026-10-10。Observed Regression/Adversarial，合成文本，非Gold、不发真实模型请求。

官方tiktoken通过已声明LlamaIndex extra的传递依赖安装后自动启用，此时原测试“x字符数等于token数”假设失效，孤立代理项被官方encode静默替换。评价契约保持不变：实际payload+输出预留+余量≤窗口才允许，否则发请求前拒绝；原文不可严格UTF8编码必须payload_uncountable，不能用替换文本计算后放行。

正式用例 `tests/test_rag_028_model_capability.py`：0与+1精确阈值（夹具额外核对生效计数器结果）；工具与critique正文及框架开销；未知能力/输出预留超限零请求；str/list/dict孤立代理项零请求；官方部署才允许官方tokenizer；未装或未知模型回退保守估算。官方encode前复用UTF8合法性验证，普通合法文本仍用官方token数量，不退回字符数、不修改窗口或余量。

证据为确定性单元/真实provider请求入口+隔离HTTP transport（E2请求拦截）；不代表模型质量、真实费用、跨厂商tokenizer精度。实际解释器、tiktoken版本、运行结果和独立反例记录在本轮实现说明及独立审查。没有holdout调参或模型/Chunking变更。

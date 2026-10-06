# Auto-routing 实际服务入口结构评估 v0.1

本评估属于 AI-authored Smoke/Adversarial，不是人工 Gold。使用本地
MockEmbeddingProvider（synthetic，8 维），只证明结构与持久化链路，不证明
检索质量、生产 Embedding 上限、Office/PDF 解析器或真实 Milvus 行为。
另有实际 TextDocumentParser.extract 接收合成 UTF-8 Markdown 文件字节的模式。

## 复算

```powershell
& 'D:/DepTooL/anaconda3/envs/ai-assistant/python.exe' -m evals.chunk_auto_routing.run --output evals/chunk_auto_routing/report.json
& 'D:/DepTooL/anaconda3/envs/ai-assistant/python.exe' -m pytest tests/test_chunk_auto_routing_evaluation.py -q
& 'D:/DepTooL/anaconda3/envs/ai-assistant/python.exe' -m ruff check --no-cache evals/chunk_auto_routing tests/test_chunk_auto_routing_evaluation.py
```

使用独立内存 SQLite，实际执行 `RAGService.ingest_parsed_document(strategy=auto)`，
提交后关闭 Session，从新 Session 读取文档与分块，再执行同原文重解析，
提交后从第三个 Session 读取。不会修改开发/生产数据库或外发源文。

## 对照与指标

复用 RAG-021 `cases.json` 全部 8 个样本及 `evaluate_chunks`，参数
chunk_size=40、chunk_overlap=0。每样本构造 text（无 blocks）、blocks
（一个完整 paragraph block）、bbox（同块增加合成坐标）与 parser
（真实 TextDocumentParser.extract）四种输入，共 32 组。
选择 structured、format_aware、layout_aware；text parser 的有损逐行 blocks 不应
替代保真的正文。报告保存以下四份指标：

- 冻结 RAG-021 `candidate.json` 中相同样本/策略/参数的指标。
- 当前策略按 RAG-021 协议 `split(original_text)` 重算的指标。
- 同服务输入类型及保守清洗后文本的直接策略控制组。
- 实际持久化上传及重解析结果的指标。

检查覆盖、缺失字符、乱序、无效范围、保护单元破坏率、派生字符、oversized
及完整长度分布，而非仅检查分块数量。记录推断源范围数量；推断只用于精确
文本匹配诊断，不证明持久化来源元数据。服务保守清洗将 CRLF 转为 LF，所以
服务指标坐标属于清洗后的文本；blocks 的坐标语义是解析阅读流。单块情况下
二者相同，但不能推广为所有真实解析文档的原始字符偏移。

## 本轮结果

32 组候选/控制组指标完全相等，32 组计划及文本/来源范围重放一致。
清洗后有效源共 2308 字符，覆盖 2308（100%）；44 个保护单元，破坏 0；
乱序与无效范围均 0。每种输入共 27 块，最短 2、最长 133 字符。
133 字符原子代码单元超过软目标 40，是保留完整结构的预期结果，不代表
生产 Embedding 上限认证。没有启用合成硬限制，此指标不验证超限处置。

仅 CRLF 样本相对冻结报告长度不同：[2,6,30] → [2,4,26]，由清洗换行规范化
导致；覆盖和保护判定均未退化，不能声称这是质量提升。

先执行测试得到 runner 缺失导致的 1 failed / 1 passed（退出码 1），补齐后
2 passed（退出码 0）。随后加入真实文本 parser 回归再次得到 1 failed / 1 passed
（退出码 1），首失败 fenced-box 的 coverage=11/85、broken_units=1、invalid_spans=2。
此反例证明合成单块输入不足以代替真实解析器链路验证；正文信号路由修复后
保持原覆盖/结构硬断言重新验收。Ruff 首次因缓存目录权限错误未完成；改用其标准
`--no-cache` 选项后检查出 5 条长度问题，修复后 All checks passed（退出码 0）。

报告中的 dataset/report/code SHA256 绑定评估输入和当时的代码。业务代码
后续若有相关修改，应重新生成报告。此处的 bbox 单块案例只验证信号选型与
结构保护，不验证多栏重排；路由决策覆盖、配置变化、历史数据及失败回滚
由本卡其他专项测试承担。

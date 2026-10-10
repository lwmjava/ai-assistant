# QA-005 已有失败与跳过整改计划

日期：2026-10-10。用户明确要求修复上轮2项测试失败、4项跳过及6条全仓Ruff错误，并避免新增问题。本轮沿用QA-005测试修复卡，用户直接批准整改，不实施NFR-001 CI建设，不改变RAG-039业务范围和人工验收状态。

## 目标、事实和范围

已有工作区包含完整RAG-039未提交修改，全部保留。此次允许修改QA-005 allowed_paths内的tests、scripts、docs、tasks.yaml及必要测试依赖说明；不读.env、不碰重要数据、不新增生产能力、不改默认后端，不升级现有环境包以消除跳过，不调用付费OCR/模型。

已复核的原因：报告文件名把历史报告日期与运行当天绑定，跨日必红；Milvus日志例的旧_connect替身不接受新增身份/index参数；6条Ruff为1条字符串长行、3条无用import、1条无用变量、1条lambda赋值。4项skip来自未装mcp、llamaindex的collection与adapter、cloud OCR无凭据。MCP夹具还要求2.x，需在安装现有已声明extra后核对实际兼容，不能用新skip替换旧skip。

## 最小方案与非目标

1. 改前完整pytest重跑并记录当前失败清单；Ruff已复现6条。
2. 报告命名验证绑定报告自身generated_at日期和锁定门禁版本，保留硬编码指定日期命名断言，并验证默认当天命名；不修改历史报告、不每日重新生成。
3. Milvus日志替身接受真实签名，明确断言实际调用和RuntimeError诊断、敏感内容不进入日志。
4. Ruff仅做等价格式/无用变量处理，不auto-fix全仓、不降低配置、不删业务断言。
5. 安装项目已声明mcp/llamaindex extras到指定conda环境：先输出安全包版本约束，dry-run核对不升级/降级任何已有包，无冲突才安装；不足则保留原因，不用另一个解释器。真实MCP stdio在本机隔离fixture中闭环；依实际已安装API修复fixture兼容，保留缺依赖错误测试。
6. cloud OCR用例只测试工厂与provider绑定、没有发请求要求；用monkeypatch合成专用配置走真实from_settings和工厂，显式封锁HTTP外呼，保持全部原断言，去掉不必要的本机凭据前置；不把构造测试写成真实OCR质量。
7. 独立Agent复核断言、真实入口和最终diff；完整pytest、RAG子集、全仓Ruff、mypy、pip check与diffcheck验证。已安装extras也测试原缺依赖降级路径。

## 风险、失败边界和验收

日期跨日/门禁版本不符/错误generated_at不能被修复掩盖；连接替身须触达真实Milvus日志错误分支；云配置隔离不泄漏到其它用例且不得外呼；包安装不改变已安装包，依赖冲突先停止安装并排查；MCP必须真实连接/列举/调用而非Mock替代；新增extras暴露的真实失败需要根因归因修复；已有RAG-039幂等/恢复回归保留。pytest只在独立SQLite和临时路径运行。

验收以实际新测试结果为准，不承诺数学意义上没有任何未知缺陷。目标：用户指出的2失败/4跳过/6Ruff问题逐项闭合，未引入新增失败；无法执行的外部条件如实记录而非宣称绿。计划后另写实现说明、独立审查记录，更新QA-005进度但不替代人工业务验收。无Git提交/推送/部署。

## 恢复收集后的依赖修复回路

改前完整基线1335 passed/2 failed/4 skipped，exit1。安装mcp1.12.4和llama-index-core0.12.42后，原来module级importorskip隐藏的LangChain切分2例恢复执行，发现缺少项目已声明langchain-text-splitters；dry-run只新增1.1.3、不替换旧包，补齐这一extra组件。首次新传递包Deprecated3.0.0在mypy Python3.11目标下语法失败；收敛本轮新增Deprecated至1.2.18、wrapt至1.17.3，保留旧92包，pipcheck和完整mypy须重验。此两项是恢复测试覆盖/环境兼容问题，不用新增skip或改mypy版本门禁消除。

真实SDK安装后还使mypy能够检查原来ignore_missing_imports遮住的接口：MCP1.x没有新版HTTP函数名，LlamaIndex父类add接受Sequence而项目override只接受list。最小修复：前者以getattr解析新旧符号并补两版本名的无外呼连接契约；后者参数改Sequence并保留list/tuple都拒绝绕写的测试。不改变MCP传输行为、RAG写入边界或mypy配置。全量最终证据须覆盖修复后的源码，不能沿用改前指纹。

## 完整回归发现的官方计数器问题

可选依赖带来tiktoken，官方计数器自动启用。中间全量1358 passed/2 failed/0 skip：预算夹具按“一字符一token”构造+1越界，实际重复x被tokenizer压缩；另官方encode自动修复孤立代理项，绕开原合同payload_uncountable。修复前原失败已复现。预算夹具按已生效计数方法选单token填充，并严格核对实际计数等于目标窗口（0/+1阈值不变）；官方计数器在encode前复用严格UTF8可编码性校验，维持已有fail-closed语义。对str/多模态list/dict孤立代理项保留真实provider零请求拒绝反例，独立审查后重跑最终全量。此处不换模型、不放宽预算、不卸载tiktoken伪造旧路径。

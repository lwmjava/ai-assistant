# 知识库与计算器有界组合实现说明

用户于2026-10-19批准实施计划的允许路径及ADR-0016。基线为9cbe94fffe269cde9d93c00fb6d1f6d25fd01264，分支codex/v1c-001-lazy-retrieval。本说明记录工作区候选；不代表已提交、业务接受或发布。

## 实际行为

当前用户消息明确同时要求知识库和计算时，ChatService在默认管线及可选Supervisor分流之前识别组合。一次性和流式入口共用固定步骤：本轮检索、实际选入证据、一次结构化提取、代码核验、现有ToolExecutor执行calculator、核对实际结果，再以固定模板返回金额与来源。合成差旅案例的300元/人/天住宿、80元/人/天餐补及用户1人3天，实际计算器结果为1140.00元；数据库审计和消息来源用新Session回读核对。

参数必须来自当前安全用户消息或本轮实际选入块，逐个核对原文偏移、引用、数值及单位。只支持受控金额/人数/天数结构与四则AST，至少使用一个资料事实，不能夹自由常量、代码或未使用事实。使用Decimal预检维度、幅值及金额，实际工具浮点结果按分对齐后才能展示。模型不能靠一段“计算成功”替代执行。

首轮可执行组合仅calculator。其他工具组合、无命中、低分、检索故障、选块不足、未知或冲突单位、坏JSON、错误来源及计算失败均有明确提示，不输出成功金额。来源校验是保守词法结构，冒号、换行和逗号不能剥离未知限定；复杂封顶、税费、层级适用条件等政策需要补充清晰条件，当前实现不是通用财务政策裁决。

主体与租户、角色/token及技能绑定沿现有应用层规则复核，模型之后、tool.call让出之后及每次Executor尝试之前均检查。资料不能扩展工具名单；撤权零后继，拒绝审计不带原始参数。组合最多一次检索、一次提取逻辑调用、一次工具逻辑调用；provider和工具本身沿原有有限重试，物理attempt不冒称只发生一次。记忆/技能既有模型调用另计。

提取局部绕过响应缓存，未关闭全局缓存；新请求仍重新取证、核验及真实执行。流式只在核验完成后分块输出模板；取消及外层close显式关闭内层迭代器，已经发生的操作按原审计保留。现有Debug Trace记录安全模型标识、阶段、状态及耗时，不记录资料/Prompt/模型原文。

附件不阻止组合识别，但不作为企业规则或缺失用户人数天数的替代。重新生成沿原权限和成功提交边界，失败保留原答案。新组合不写Plan v1 checkpoint，不自动恢复或重放；既有Plan v1恢复仍走原入口。

## 代码与契约位置

- `app/agents/route.py`：显式COMBINED路由，普通单一意图保持原路径。
- `app/agents/combined_tools.py`、`app/agents/prompts.py`：固定有界流程、schema_version=1提取协议、来源/数值/单位/表达式核验与确定性回复。
- `app/services/combined_tools.py`、`app/services/supervisor_tools.py`：应用层授权装配与共享新Session复核工厂，Agent不操作数据库。
- `app/services/chat_service.py`：两编排、chat/stream、附件/重新生成及取消接线。
- `tests/test_chat_combined_tools.py`：真实ChatService、隔离SQLite、本地检索与计算器、持久化/审计/Trace；`tests/test_combined_tool_contract.py`为独立验证反例；`tests/test_fast_route.py`覆盖路由契约。
- Evaluation：`docs/ai-prompts/evals/combined-tool-contract-v1.yaml`，1.0.0，AI编写Smoke/Adversarial/Observed Regression，非Gold。没有更改冻结official或holdout数据。

## 验证及交付边界

最终命令、退出码、统计、环境和输入指纹写入同日验收与机器证据报告。迭代中的真实红证据及修复在`docs/reviews/2026-10-19-v1c-005-review-fixes.md`；独立工程实际入口反例审查在同前缀engineering-review.md。中途141/149绿不替代最终公共路径全量回归。

集成证据级别E2：真实应用、真实本地工具和隔离后端，提取provider为合成，Embedding为MockEmbedding(64)，只证明链路与受控失败，不能证明真实模型正确提取、检索质量、生产性能或企业报销政策有效性。没有付费模型、生产数据、真实索引重建授权，故没有执行这些操作。

未改变API/schema、配置、依赖、CI、前端、RAG底层或数据库结构。本地前端检查不适用。最终提交的GitHub Backend、Frontend及Agent official baseline仍须单独通过；旧SHA的绿色CI不能验收当前工作区。当前用户没有委托本卡业务接受，独立最终审核也不替代用户决定。必要证据未齐不得done。

## 回滚

回退ChatService组合接线、route的COMBINED识别与新增helper，恢复原单一路由。没有迁移和索引重建，保留已存消息、来源和审计；回滚不重放此前计算。未提交、推送、合并或部署本轮代码。

最终本地自动验证：指定解释器全量pytest 3043 passed、3 skipped、3 warnings，800.71秒、退出0；Ruff全库no-cache退出0；mypy app/218文件退出0；531冻结输入前后相同。独立反例126 passed、42.69秒、退出0。三项跳过为既有Windows平台限制，警告为既有sandbox弃用和对抗向量异常数值测试。详细命令与日志指纹见机器证据。独立最终审核及用户/最终提交CI门禁另记。

最终独立技术审核/root/v1c005_final_audit：154 passed、1既有警告、75.18秒、退出0，531输入及16原日志指纹一致，另3条真实helper/Executor探针符合预期；无待修复P1/P2。任务转in_review，未代行用户业务接受，最终提交CI仍待完成。角色和实际技能记入机器证据；本轮仅补验收文档未改变检查输入。

## 2026-10-20 委托业务接受补记

用户明确要求启用独立Agent代行业务验收，授权适用于本卡。/root/v1c005_business_acceptance未参与实现或既有审查，业务接受通过：154 passed、0 skipped、1既有warning、64.15秒、exit0；另12条真实ChatService业务探针全部通过（四入口2人4天实际3040.00元、来源与新Session审计一致；未知每晚单位及含税复杂政策四入口均明确拒绝且零执行）。531输入前后及17原日志指纹一致。报告：2026-10-20-v1c-005-business-acceptance.md及同前缀evidence.json、pytest/probes日志。

此补记更新此前“业务接受未委托/未验证”的历史状态：批准calculator-only/E2范围内业务接受已通过；真实模型质量未验证，最终提交SHA的GitHub Backend、Frontend和Agent official baseline仍未验证，因此任务保持in_review而非done。未提交、推送、合并或部署。仅新增验收文档和状态记录，检查输入未变，可沿用已有全量及静态结果。

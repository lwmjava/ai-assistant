# 四用途模型配置与熔断实现说明

日期：2026-10-15。原卡 LLM-001；计划 plan_llm_001_20261015.md。基线 HEAD 为 873db9c0fb243b0847b4be57242c8f333f4f0b1e，分支 codex/platform-batch-20261015，工作区尚未提交。

## 实际行为

运维可分别配置对话、意图、嵌入、重排用途。app/llm/task_profiles.py 返回不可变配置快照，repr 隐藏地址及密钥，未知用途拒绝；嵌入读取已有 EMBEDDING_*，不改变实际 RAG 工厂和索引身份。重排仅登记独立配置，enabled 为 false，不执行未经批准的检索重排。规划、执行和质量门继续沿用 chat。

app/llm/factory.py 保留 chat/intent 有序兜底与单跳 OpenAICompatibleProvider 身份，未知配置档明确拒绝。app/llm/routing.py 增进程内共享熔断，跨临时工厂对象生效；键绑定用途、配置档、端点、模型、凭据摘要及相关设置。默认连续三次瞬时故障后冷却 30 秒，之后只放行一个半开探测。状态最多 256 条，闲置过期回收，活动租约不驱逐；不跨实例共享。

每跳仍走自身实际客户端的预算和缓存检查。400、预算拒绝与取消不累计瞬时故障；401/403 保留兜底但不累计瞬时故障。流输出后禁止切换供应商拼接回答。取消、关闭异步生成器及异常均释放探测租约；代次校验防止旧在途请求覆盖新状态。日志只含受控任务/档名、异常类型及状态码，不含异常正文、密钥或请求正文。无可用链返回固定脱敏提示。

配置同步 app/core/config.py、.env.example、README.md；没有读取工作区 .env、新增真实凭据或主动发起真实模型请求。

## 验证与限制

有效红测：四用途字段/解析缺失与跨工厂第四次仍调用故障主模型，共 3 failed、退出 1。修复后在 Windows、指定 conda Python 3.12.0、PYTHONUTF8=1/PYTHONIOENCODING=utf-8，实际执行：

```powershell
D:\DepTooL\anaconda3\envs\ai-assistant\python.exe -m pytest tests/test_llm_task_profiles.py tests/test_llm_circuit.py tests/test_llm_circuit_review.py tests/test_llm_profiles_review.py tests/test_llm_route.py tests/test_llm.py tests/test_llm_response_validity.py tests/test_pipeline_budget.py tests/test_response_cache.py tests/eval/test_generation_budget.py --basetemp data/pytest-tmp/llm-routing-green-20261015 -q --tb=short
```

结果 117 passed，3.05 秒，退出 0；日志宿主临时目录 platform-llm-routing-green.log。覆盖四用途、跨请求开路、半开并发、取消/aclose、旧请求跨代、配置身份轮换、错误分类、首 token 后不换家及原预算/缓存回归；全部离线桩，仅证明调用契约。全库 Ruff --no-cache 退出 0。容量/TTL 补充验证、最终版本指纹和整合回归另补，不将本轮定向通过视为整卡验收。

补充容量、TTL 和旧租约反例后，同一组完整命令改用 basetemp data/pytest-tmp/llm-routing-final-20261015，实际 120 passed、8.24 秒、退出 0，日志 platform-llm-routing-final.log。全库 mypy 使用 --cache-dir data/mypy-platform-llm-20261015，208 文件无问题、退出 0；独立审查见 docs/reviews/2026-10-15-llm-001-review.md。上述均为本阶段结果，后续共享路径变更仍须重新整合回归。

两家真实供应商切换后对话的原卡必验项尚未执行，需要明确端点/模型、能力依据、凭据安全注入与费用/外发授权。未实施租户 model_profiles 管理或重排算法，不改变 Embedding、不用模拟端点冒充真实供应商。最终全量 pytest、必要平台/CI 及真实供应商证据未齐，保持 in_review，不能宣称可发布。回滚只需恢复路由与配置，无数据库迁移；重启清空熔断，不能借回滚自动切换索引。

## 2026-10-16 续作补记

用户明确允许使用 `.env` 中的千问密钥后，按 plan_llm_live_probe_20261016.md 执行有界真实合成对话：qwen-plus 五次 HTTP 200，供应商 usage 共 938 输入/230 输出 tokens，正常回复从新 Session 确认持久化，退出 0。详见 ../reviews/2026-10-16-llm-qwen-live.md；没有改写 .env，普通 pytest 与 CLI init 仍隔离。前文“不读取/不外发”描述的是此前离线实施阶段，不覆盖后续这项明确授权的验收。

这只证明千问一家，第二家真实切换仍未验证，不能按两个模型或两个配置档冒充两个提供商。CHAT-007 随后的类型扩展也影响共享 LLM 输入，最终有效性以批次 R2 全量和 platform_batch_final_inputs_20261016.json 为准，不能单独沿用 120 项阶段记录关闭新版本。

## 2026-10-17 两家实机

同一脚本在当前代码上各跑一家，每家最多两次真实请求，退出码都是 0。千问走 `https://dashscope.aliyuncs.com/compatible-mode/v1` 的 `qwen-plus`；DeepSeek 走 `https://api.deepseek.com` 的 `deepseek-flash`。两家都返回 HTTP 200，新会话保存用户消息和状态为 complete 的助手消息，模型标识与被选模型一致，回复含「你好」。流式请求没有留下 usage，DeepSeek 流式记录也没有返回模型；持久化模型标识仍是被选模型。Mock Embedding 只隔离空库检索，不证明检索质量。

机器记录见 `docs/reviews/llm001_qwen_live_20261017.json` 与 `docs/reviews/llm001_deepseek_live_20261017.json`。关闭说明见 `docs/plans/closure_llm001_20261017.md`。上文 10 月 16 日「第二家未验证」只描述当时，不覆盖这次补跑。

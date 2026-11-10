# Prompt 变体比较、人工发布与回滚 — 实现说明

## 行为摘要

- 源码 Prompt 槽位目录来自 pp/agents/prompts.py。
- 草稿变体、离线比较运行、发布记录分别落入 prompt_variants / prompt_compare_runs / prompt_publications。
- 比较默认且仅允许 provider=fake、max_external_calls=0；绑定数据集版本、源码指纹、模型指纹、Prompt 指纹与评测指标 schema。
- 未测指标进入 missing_metrics，不会写成 0。
- 费用、调用、缓存、取消、重试字段带 explain；fake 下外部调用与账单为 0。
- 仅 system_admin 可发布/回滚；expected_revision 乐观并发冲突返回 409。
- 比较结果 uto_publish_blocked=true，不会自动发布胜出变体。

## 主要文件

- pp/services/prompt_variants.py
- pp/api/routes/prompts.py（挂到 evaluations 路由下）
- pp/models/prompt_variant.py
- lembic/versions/fe40bc23d5e6_add_prompt_variant_governance.py
- rontend/src/pages/PromptVariants.tsx
- 	ests/test_prompt_variant_compare.py

## 自测

`powershell
D:\DepTooL\anaconda3\envs\ai-assistant\python.exe -m pytest -p no:cacheprovider tests/test_prompt_variant_compare.py -q --tb=short
D:\DepTooL\anaconda3\envs\ai-assistant\python.exe -m ruff check --no-cache app/services/prompt_variants.py app/models/prompt_variant.py app/schemas/prompt_variant.py app/api/routes/prompts.py app/agents/prompts.py app/api/routes/evaluation.py tests/test_prompt_variant_compare.py alembic/versions/fe40bc23d5e6_add_prompt_variant_governance.py
npm --prefix frontend run typecheck
`

未执行真实付费模型比较；不以 fake 结果声明生产 Prompt 质量。

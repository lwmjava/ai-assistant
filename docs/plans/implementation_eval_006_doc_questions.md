# 授权文档辅助出题落地说明

## 行为
- `POST /evaluations/datasets/{id}/doc-questions/generate`：仅对当前租户已发布文档出题；默认 `provider=fake`，`max_external_calls=0`，不外发、不计费。
- 候选落在 `source=ai_generated` 的 draft 版本，题目绑定文档/版本/哈希/分块/生成配置；脱敏邮箱电话与常见密钥。
- 编辑、接受、拒绝、撤销拒绝；页面可直接改问题与预期要点，编辑同样写入审计；入集仅打包人工 accepted 且来源仍有效的题目为 Silver 草稿，禁止自动 Gold。
- live 需 egress+预算，且当前构建未授权真实付费调用时直接拒绝。

## 验证
- `python -m pytest tests/test_evaluation_doc_questions.py`
- `ruff check` 覆盖改动文件
- `npm --prefix frontend run typecheck`

# RAG-021 人工业务验收

状态：2026-10-06 负责人明确回复“RAG-021可以验收了”，业务验收通过。不要自动重建已有文档。

## 新文档完整性

在当前应用上传含标题、围栏代码/盒图、长表的合成 Markdown；可使用 evals/chunk_structure_integrity/cases.json 中对应资料。查看分块内容，确认图/代码原文保持空格、换行和缩进，没有从行中间切断；长表按行分组，重复表头为派生上下文。测试默认 structured，再使用 parent_child 检查父子原文范围和章节关联。任意 ASCII 艺术不一定能被保守识别，不将未覆盖图形声称已通过。

## 超限结果与原文

当前真实 Embedding 是已核对的 DashScope text-embedding-v3 时，上传 [合成部分向量化样本](../../evals/chunk_structure_integrity/manual_partial.md)。该文件包含完整围栏和 3000 个中文字符的单行代码，UTF-8 字节数超过本卡批准的输入预算；正常前后正文仍可向量化。Mock 的无限政策只用于离线链路，不能用于这项真实护栏验收。

通过 Swagger `/docs` 或已登录 API 客户端核对：

1. 上传响应为 `vectorization_status=partial`，已向量化和未向量化数量都大于 0，`embedding_skip_reason_counts.input_limit_exceeded` 大于 0。
2. GET `/api/rag/documents/{id}` 与列表中的计数一致；GET `/api/rag/documents/{id}/chunks` 中超限代码为 `embedding_status=not_vectorized`、`embedding_skip_reason=input_limit_exceeded`、`oversized=true`。
3. 分块内容可查看完整代码围栏与 3000 个字符；源文件可以下载，未用截断正文或零向量假装完整向量化。
4. 用无权用户/另一租户读取该文档，仍按现有权限拒绝或返回不可见；不要把状态接口当成跨租户统计入口。

异步重解析只有在明确选择测试文档后执行；202 响应是任务创建，等待任务完成后再查文档状态。重要/生产索引重建仍需单独授权。本卡不自动执行删除或生产重解析。

管理页面尚无向量化状态专用展示，本轮验收使用 API/Swagger，不把 API 字段新增说成 UI 完成。未向量化块仍可能被稀疏检索或父块关联取回，不以“搜索完全找不到”作成功标准。

## 验收记录

验收人：项目负责人（当前会话用户）。日期：2026-10-06。运行版本：RAG-021 未提交候选，基线 16308e75137dcdae59d246ae26d06759db09d370；38 个交付文件已逐文件哈希核对并同步至 E:\culture\SmartCustomerServiceSystem\ai-assistant。此前核对的 Embedding 配置为 DashScope text-embedding-v3、1024 维；本次截图不独立证明运行时模型身份。

人工提供的详情接口截图：HTTP 200，文档 ID `4f582ad3da074de390f09586166a453b`，`deleted_at=null`，`chunk_count=6`，`vectorization_status=partial`，`vectorized_chunk_count=4`，`not_vectorized_chunk_count=2`，`unknown_chunk_count=0`，`embedding_skip_reason_counts={"input_limit_exceeded":2}`。计数一致，超限原因明确。负责人据此明确批准本卡验收。

证据边界：本次人工截图直接覆盖详情状态与超限计数；未单独提供分块原文、列表、下载或跨租户操作截图，不将这些写成已人工逐项观察。结构完整性、分块状态与权限路径的自动验证和独立审查见实现报告。已记录的可选后端模块跳过和真实检索质量未评测等限制继续保留。

RAG-021 更新为 done；按每关闭一张卡提交一次的既有约定做本地提交，不自动推送、合并或部署。

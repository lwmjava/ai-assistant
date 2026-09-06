# RAG 数据导入阶段 · 完成与待打磨清单

> 用途：作为「数据导入」阶段的打磨参照，标记已完成能力与剩余缺口，避免后续重复排查。
> 更新日期：2026-09-06

## 一、已完成（可视为生产可用）

| 能力 | 状态 | 说明 |
|---|---|---|
| 多格式文档解析 | ✅ | txt / md / json / xml / csv / docx / xlsx / pptx / pdf |
| 老 Office 格式 | ✅ | doc（LibreOffice→docx）/ xls（xlrd）/ ppt（LibreOffice→pptx） |
| 解析服务层 + 插件化 | ✅ | `document_parsers` 注册表 + 服务调度，路由不再堆 if/elif |
| OCR（扫描 PDF） | ✅ | 本地 Tesseract + 云 OpenAI 兼容视觉，双模式 provider |
| PPT 图片页 OCR 兜底 | ✅ | ppt/pptx 文本为空时 → PDF → OCR |
| 源文件落盘 | ✅ | 保存至 `data/knowledge`，支持下载 / 重解析 / OCR 追溯 |
| 异步导入平台 | ✅ | ImportJob / ImportBatch，状态机 + 重试 + 重解析 |
| URL 导入 | ✅ | 抓取快照 + 落盘 + 解析 |
| 去重 / 版本化 / 重解析 | ✅ | 内容哈希去重 + version_group 版本链 |
| 失败治理 + 持久化诊断 | ✅ | `ImportJob.error` 摘要 + `ImportJobTrace` 完整证据 |
| trace 机制复用 | ✅ | OCR / URL / 老 Office / PPT OCR fallback 统一走 trace |

## 二、待打磨（尚未完成 / 需增强）

### P0（功能缺口，影响覆盖范围）

1. **外部数据源连接器（完全未做）**
   - 数据库连接器 ❌
   - 对象存储连接器（S3 / OSS / MinIO）❌
   - 第三方知识库 / 企业知识库 ❌
   - 当前 `ImportSourceType` 仅 `file / url / reparse` 三种。

2. **批量导入的更完整形态**
   - 目录导入 ❌
   - ZIP 解包导入 ❌
   - 目前「批量」仅指一次上传多个文件。

### P1（体验 / 治理增强）

3. **失败文件统一管理入口**
   - 目前靠任务列表 + 源文件下载，缺独立的「失败文件」聚合视图与批量重试。

4. **部署说明持续同步**
   - 老 Office 依赖服务器 LibreOffice，需在 README / 部署文档标注。

### P2（精细度打磨）

5. **去重哈希口径**
   - 当前按 `parsed.text` 哈希，对表格 / 结构化文档可能不够精细，跨格式重复识别偏弱。

6. **导入任务调度增强**
   - 目前 `max_concurrency` 固定，无优先级 / 限流 / 队列策略。

## 三、结论

- **文件 / 网页导入主干已成熟**，可继续推进下游（切分、嵌入、检索、生成）。
- **外部连接器是当前最大缺口**，是「数据导入」距离完整最明显的一截。

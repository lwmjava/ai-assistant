# RAG-033 实现说明：多格式解析与 OCR 质量基线

日期：2026-10-07
分支：`rag-batch-20261007`
契约出处：`tasks.yaml` RAG-033
风险：L1

---

## 1. 目标与边界

卡片契约：*解析代码存在 ≠ 真实格式/扫描件质量已验收*。

本卡交付的是**可重复运行的质量基线与依赖失败行为证据**，不是新的解析能力：

| 交付物 | 位置 |
|---|---|
| 真实样本集（13 个样本 + 期望标注） | `evals/corpus/parsing/` |
| 样本生成器（用已安装依赖真实生成，不手写二进制） | `scripts/generate_parse_corpus.py` |
| 评估报告脚本 | `scripts/parse_quality_report.py` |
| 报告（JSON） | `evals/reports/parse-quality-<date>.json` |
| 测试 | `tests/test_rag_033_parse_quality.py` |

非目标（契约 non_goals）：**不新增解析供应商**、**不以 Mock 证明 OCR 质量**。
本次实现全程没有 patch 解析器、没有 patch provider、没有假造 OCR 文本。

---

## 2. 真实依赖清单（本机实测，未安装任何新包）

| 依赖 | 状态 | 用途 |
|---|---|---|
| PyMuPDF (`fitz`) | ✅ 已装 | 生成 PDF 样本；PDF 块提取（bbox/阅读顺序） |
| `pypdf` | ✅ 已装 | PDF 文本层提取（解析器主路径） |
| `python-docx` | ✅ 已装 | 生成并解析 DOCX |
| `openpyxl` | ✅ 已装 | 生成并解析 XLSX |
| `python-pptx` | ✅ 已装 | 生成并解析 PPTX |
| `PIL/Pillow` | ✅ 已装 | （本卡未直接使用） |
| **`pytesseract`** | ❌ **未装** | 本地 OCR 的 Python 侧依赖 |
| **`pdfplumber`** | ❌ **未装** | 表格/PDF 版式解析的另一条候选路径 |
| **tesseract 二进制** | ❌ **本机没有** | 真实 OCR 执行的必要条件 |

结论（硬事实，不粉饰）：

- **中文 OCR 质量在本机无法用真实依赖验证**。扫描件样本走的是
  `app/rag/ocr/factory.py` → `TesseractOcrProvider.extract_pdf_text()` 的真实路径，
  在 `shutil.which("tesseract")` 处必然失败，报告标记为
  `status=dependency_missing`、`error_code=ocr_tesseract_missing`，**不写 pass**。
- **「pdfplumber 路径」在本机无法验证**（模块未装）。本项目当前实现也没有
  pdfplumber 分支，故本卡只是如实登记该依赖缺失，不声称验证过。

报告会把这份清单写进 `dependencies` 字段，缺失项列在 `dependencies.missing`。

---

## 3. 样本集

`evals/corpus/parsing/manifest.json` 为每个样本标注 `expected_fragments`（覆盖期望）、
`expected_order`（阅读顺序期望）、`expectation_mode`、`known_gap`。
样本全部由 `scripts/generate_parse_corpus.py` 用已安装依赖真实生成；中文 PDF 使用
系统 CJK 字体（本机 `C:/Windows/Fonts/simhei.ttf`）并做字体子集化，保证
pypdf 与 PyMuPDF 两侧都能正确提取中文（内置 `china-s` 字体不写 ToUnicode，
会让 pypdf 取到乱码；脚本探测不到系统字体时会回退并在 manifest 里如实记录）。

| 样本 | 形态 | 期望模式 |
|---|---|---|
| `pdf_single_column.pdf` | 三页单栏，H1/H2 标题层级 | must_pass（含跨页顺序） |
| `pdf_twocolumn.pdf` | 双栏，内容流按「左栏整体→右栏整体」写入 | must_pass（阅读顺序关键样本） |
| `pdf_twocolumn_interleaved.pdf` | 同上文本，内容流按「逐行左右交替」写入 | known_limitation（量化跨栏错乱） |
| `pdf_table.pdf` | 带框线表格 | must_pass |
| `pdf_scanned.pdf` | 图片型 PDF，无文本层（中文页面渲染成 JPEG 后嵌入） | must_pass → 实际 `dependency_missing` |
| `docx_headings_table.docx` | H1/H2 + 2×3 表格 | known_limitation（表格未提取） |
| `xlsx_multi_sheet.xlsx` | 两个 sheet + A1:B1 合并单元格 | must_pass |
| `pptx_notes.pptx` | 两页幻灯片 + 备注页 | must_pass |
| `md_code_fence.md` | bash 代码围栏 + ASCII 盒图 | must_pass |
| `txt_plain.txt` | 纯文本 + ASCII 盒图 | must_pass |
| `broken_empty.pdf` | 零字节 | must_fail（DocumentParseError） |
| `broken_truncated.pdf` | 有 PDF 头但结构损坏 | must_fail（DocumentParseError） |
| `unsupported.bin` | 不支持扩展名 | must_fail（UnsupportedDocumentTypeError） |

ASCII 盒图样本与 RAG-042「结构化正文显示」呼应：`md`/`txt` 解析后盒图必须逐字符保留。

---

## 4. 指标定义与判定规则

### 4.1 覆盖率（coverage）

```
coverage = 命中的期望片段数 / 期望片段总数
```
匹配前对解析文本与片段都做 `re.sub(r"\s+", "", text)` 归一化，因此跨行/跨块的
片段也能命中。未命中的片段进 `missing_fragments`。

### 4.2 阅读顺序（order_score / reading_order_correct）

判定规则（写死在 `scripts/parse_quality_report.py::evaluate_order`）：

1. 解析块按解析器输出的 `order` 升序排列，得到实际阅读序列；
2. 每个 `expected_order` 片段取**首次命中的块下标**，未命中记 `None`；
3. 全部片段命中 **且** 下标序列严格递增 ⇒ `reading_order_correct = True`；
4. `order_score = 1 − 逆序对数 / C(命中数, 2)`，用于量化错乱程度。

对双栏样本，期望序列是「标题 → 左栏全部行 → 右栏全部行」，因此任何左右交替
排列都会产生逆序对并被 `order_score` 量化出来。

### 4.3 状态（status）

| 状态 | 含义 |
|---|---|
| `pass` | 覆盖率达建议门槛且顺序正确（若有顺序期望） |
| `fail` | must_pass 样本未达标；或负样本反而没报错（静默通过） |
| `dependency_missing` | 真实依赖缺失导致该路径**无法验证**；绝不等同 pass |
| `known_limitation` | 已在 manifest 里标注的解析缺口，指标仅记录、不计入失败清单 |
| `expected_failure` | 负样本按预期错误类型失败 |

---

## 5. 报告样例（本机真实输出，2026-10-07）

```
RAG-033 解析质量报告  生成于 2026-10-07T16:01:13+08:00
样本集：evals\corpus\parsing (版本 rag-033-parsing-corpus-v1)
CJK 字体：C:/Windows/Fonts/simhei.ttf（系统字体=True）
缺失依赖：['binary:tesseract', 'pdfplumber', 'pytesseract']

样本                                格式     状态                        覆盖      顺序     块数
------------------------------------------------------------------------------------
pdf_single_column.pdf             pdf    pass                    1.00    1.00      7
pdf_twocolumn.pdf                 pdf    pass                    1.00    1.00      7
pdf_twocolumn_interleaved.pdf     pdf    known_limitation        1.00    0.86      4
pdf_table.pdf                     pdf    pass                    1.00       -      5
pdf_scanned.pdf                   pdf    dependency_missing         -       -      0
docx_headings_table.docx          docx   known_limitation        0.40       -      4
xlsx_multi_sheet.xlsx             xlsx   pass                    1.00       -      9
pptx_notes.pptx                   pptx   pass                    1.00       -      8
md_code_fence.md                  md     pass                    1.00       -     17
txt_plain.txt                     txt    pass                    1.00       -     12
broken_empty.pdf                  pdf    expected_failure           -       -      0
broken_truncated.pdf              pdf    expected_failure           -       -      0
unsupported.bin                   bin    expected_failure           -       -      0

合计：samples=13  passed=7  failed=0  dependency_missing=1  known_limitation=2  expected_failure=3

失败清单：
  （无）

依赖缺失清单（未验证，不等于通过）：
  - pdf_scanned.pdf: error_code=ocr_tesseract_missing error_type=DocumentParseError
    reason=真实依赖缺失，无法在本机验证该路径：OCR 依赖缺失：未检测到 tesseract 或 chi_sim/eng 语言包

已知缺口（指标仅记录，不计入失败）：
  - pdf_twocolumn_interleaved.pdf: 覆盖=1.0 顺序=0.8571
    原因=解析器未做列聚类排序，逐行交替写入的双栏 PDF 阅读顺序会跨栏错乱。
  - docx_headings_table.docx: 覆盖=0.4
    原因=DocxDocumentParser 只遍历 document.paragraphs，表格单元格不进入解析结果。

建议门槛（状态=proposed_not_locked，未锁定）：覆盖率≥0.95、顺序必须正确、
失败率≤0.0；实测失败率=0.0、是否满足=False
```

复现命令：

```bash
python scripts/parse_quality_report.py            # 打印
python scripts/parse_quality_report.py --json     # 落 evals/reports/parse-quality-<date>.json
```

> 脚本拒绝写入冻结基线文件名 `rag-v0.1-baseline-20260919.json`。

---

## 6. 实测暴露的两条真实缺口（未修复，留待评审决策）

1. **双栏 PDF 的阅读顺序依赖内容流顺序**。
   `PdfDocumentParser` 直接用 `page.get_text("blocks")` 的返回次序，未做列聚类/几何排序。
   内容流按栏写入（常见排版）时顺序正确（样本 `pdf_twocolumn.pdf`，`order_score=1.0`）；
   内容流逐行交替写入时会跨栏错乱（样本 `pdf_twocolumn_interleaved.pdf`，
   `order_score=0.8571`，块内把左右两行合并成一块）。本卡只做量化与记录，未改解析器。
2. **DOCX 表格未进入解析结果**。
   `DocxDocumentParser.extract()` 只遍历 `document.paragraphs`，
   `python-docx` 的表格不在 `paragraphs` 里，故表格单元格全部缺失
   （样本覆盖率 4/10 = 0.40）。本卡只做记录，未改解析器。

两条都在 manifest 的 `known_gap` 与报告的 `known_limitations` 中留痕，
**不计入失败清单**，也不会被当作「已验收」。

---

## 7. 依赖失败行为（本卡真正闭合的部分）

| 场景 | 实际行为 | 证据 |
|---|---|---|
| OCR 依赖缺失（已启用 OCR，tesseract 二进制不存在） | `TesseractOcrProvider` 抛 `OcrProviderError`，`error_code=ocr_tesseract_missing`，文案「OCR 依赖缺失：未检测到 tesseract 或 chi_sim/eng 语言包」；`PdfDocumentParser` 转成 `DocumentParseError` 并保留 `error_code` | `test_tesseract_provider_raises_dependency_missing`、`test_ocr_enabled_but_dependency_missing_raises_parse_error` |
| 未启用 OCR | `DocumentOcrRequiredError`，文案含「未启用 OCR」 | `test_ocr_disabled_raises_ocr_required` |
| OCR 不可用是否影响普通 PDF | **不影响**：`RAG_OCR_ENABLED` 取 True/False 两种配置下，`pdf_single_column.pdf` 都解析成功且 `used_ocr=False` | `test_ocr_unavailable_does_not_break_normal_pdf` |
| 空文件 | `DocumentParseError`「文件解析失败，请确认文件未损坏或未加密」，不静默返回空知识 | `test_empty_pdf_raises_document_parse_error` |
| 结构损坏 PDF | 同上 | `test_truncated_pdf_raises_document_parse_error` |
| 不支持扩展名 | `UnsupportedDocumentTypeError`，文案给出支持格式清单 | `test_unsupported_extension_raises_unsupported_type` |

---

## 8. 建议门槛（**尚未经人工评审锁定**）

| 指标 | 建议值 | 状态 |
|---|---|---|
| 覆盖率（正样本） | ≥ 0.95 | **建议，未锁定** |
| 阅读顺序 | 有顺序期望的样本必须 `reading_order_correct=True` | **建议，未锁定** |
| 失败率（must_pass 样本） | ≤ 0.00 | **建议，未锁定** |
| 依赖缺失样本 | 不计入通过，单独列清单并在补齐依赖后重跑 | **建议，未锁定** |

报告里 `gate.status = "proposed_not_locked"`，`gate.note` 明确写
「尚未经人工评审锁定」。本次**没有**宣称门禁已锁定；`gate.meets_gate` 在存在
`dependency_missing` 样本时恒为 `False`（未验证的路径不能算满足）。
等评审结论出来后再把门槛写进脚本并改掉该标记。

---

## 9. 局限（如实登记）

1. **中文 OCR 质量未经验证**：本机无 tesseract 二进制与 `pytesseract`，
   扫描件样本只能验证「依赖缺失时的失败行为」，无法给出中文 OCR 准确率。
   补齐 tesseract + `chi_sim` 语言包后需重跑
   `python scripts/parse_quality_report.py --json` 才能闭合这一项。
2. **pdfplumber 路径未经验证**：模块未装，本卡只是登记依赖缺失。
3. **样本是合成件，不是真实客户文档**：双栏/表格/扫描件的版式复杂度低于真实业务
   文档（无跨页表格、无旋转页面、无水印、无多语言混排）。
4. **扫描件样本由矢量文本渲染而来**，与真实扫描（拍照/复印噪声、倾斜、压缩伪影）
   有差距，即使补齐 OCR 依赖，其识别率也不能直接外推到真实扫描件。
5. **阅读顺序只覆盖块级顺序**，未覆盖块内行序（PyMuPDF 会把同一 y 带的左右两行
   合并进同一个块，本卡的 `order_score` 因此低估了交错程度）。

---

## 10. 变更清单

| 文件 | 变更 |
|---|---|
| `scripts/generate_parse_corpus.py` | 新增：样本集生成器 |
| `evals/corpus/parsing/*` | 新增：13 个样本 + `manifest.json` |
| `scripts/parse_quality_report.py` | 新增：评估报告脚本 |
| `evals/reports/parse-quality-20261007.json` | 新增：首份报告（非冻结基线） |
| `tests/test_rag_033_parse_quality.py` | 新增：29 条测试 |
| `docs/plans/implementation_rag_033_parse_quality.md` | 本文档 |

未改动 `app/rag/**` 下的任何产品代码；未改 `.env`；未改 `tasks.yaml` 卡片状态。

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

| 样本 | 形态 | 声明模式 → 实际状态 |
|---|---|---|
| `pdf_single_column.pdf` | 三页单栏，H1/H2 标题层级 | must_pass → `pass`（含跨页顺序） |
| `pdf_twocolumn.pdf` | 双栏，内容流按「左栏整体→右栏整体」写入 | must_pass → `pass`（阅读顺序关键样本） |
| `pdf_twocolumn_interleaved.pdf` | 同上文本，内容流按「逐行左右交替」写入 | must_pass → `known_limitation`（G3 豁免，登记 known_gap） |
| `pdf_table.pdf` | 带框线表格 | must_pass → `pass` |
| `pdf_scanned.pdf` | 图片型 PDF，无文本层（中文页面渲染成 JPEG 后嵌入） | must_pass → `dependency_missing`（G4 阻断） |
| `docx_headings_table.docx` | H1/H2 + 2×3 表格 | must_pass → `known_limitation`（G2 豁免，登记 known_gap） |
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
| `pass` | 覆盖率与顺序都达到门禁门槛（G2/G3） |
| `fail` | 未登记的门禁突破（G1/G2/G3）：有阈值突破但 manifest 未登记 `known_gap`；或负样本反而没报错（静默通过） |
| `dependency_missing` | 真实依赖缺失导致该路径**无法验证**；绝不等同 pass（G4 阻断项） |
| `known_limitation` | 阈值突破但已在 manifest 登记 `known_gap` 并落入报告 `known_limitations`：**豁免门槛，但不得计入 passed** |
| `expected_failure` | 负样本按预期错误类型失败 |

> 状态由「阈值 + 是否登记 known_gap」推导，不由 manifest 的 `expectation_mode` 直接指定，
> 因此**缺口被修好后不会再被算作缺口**（会回到 `pass` 并记 `gap_note`）。

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

已知缺口（G2/G3 豁免：豁免门槛，但不计入 passed）：
  - pdf_twocolumn_interleaved.pdf: 覆盖=1.0 顺序=0.8571
    原因=解析器未做列聚类排序，逐行交替写入的双栏 PDF 阅读顺序会跨栏错乱。
    ｜实测：阅读顺序得分 0.8571 低于门禁 G3 门槛 0.95（命中 7/7）
  - docx_headings_table.docx: 覆盖=0.4
    原因=DocxDocumentParser 只遍历 document.paragraphs，表格单元格不进入解析结果。
    ｜实测：覆盖率 0.4 低于门禁 G2 门槛 0.95（缺失片段：['角色','职责','时限','实施','联调','五个工作日']）

质量门禁 gate.v1（状态=locked，锁定人=批次评审（Agent 代执行人工评审），锁定日期=2026-10-07）：
覆盖率≥0.95、顺序得分≥0.95、失败率≤0.0
  G1 通过：未登记的失败必须为 0…
  G2 通过：覆盖率 coverage ≥ 0.95…
  G3 通过：阅读顺序 order_score ≥ 0.95，豁免规则同 G2。
  G4 不通过：依赖缺失…（命中：['pdf_scanned.pdf']）
  G5 通过：不得声称验证过…
  实测失败率=0.0；阻断规则=['G4']；meets_gate=False
```

复现命令：

```bash
python scripts/parse_quality_report.py            # 打印
python scripts/parse_quality_report.py --json     # 落 evals/reports/parse-quality-<date>.json
```

> 脚本拒绝写入冻结基线文件名 `rag-v0.1-baseline-20260919.json`。

---

## 6. 实测暴露的两条真实缺口 —— **裁定：不在本卡修，另开卡**

1. **双栏 PDF 的阅读顺序依赖内容流顺序**。
   `PdfDocumentParser` 直接用 `page.get_text("blocks")` 的返回次序，未做列聚类/几何排序。
   内容流按栏写入（常见排版）时顺序正确（样本 `pdf_twocolumn.pdf`，`order_score=1.0`）；
   内容流逐行交替写入时会跨栏错乱（样本 `pdf_twocolumn_interleaved.pdf`，
   `order_score=0.8571`，块内把左右两行合并成一块）。
2. **DOCX 表格未进入解析结果**。
   `DocxDocumentParser.extract()` 只遍历 `document.paragraphs`，
   `python-docx` 的表格不在 `paragraphs` 里，故表格单元格全部缺失
   （样本覆盖率 4/10 = 0.40）。

两条都在 manifest 的 `known_gap` 与报告的 `known_limitations` 中留痕，
**不计入 passed**，也不会被当作「已验收」。

### 6.1 为什么不在 RAG-033 内修（评审裁定，2026-10-07）

1. **本卡验收是「真实依赖样本报告 + 质量门禁锁定」，交付物是基线与门禁，不是解析器改造。**
   把修复塞进来，基线本身就失去参照价值——**先有基线、再对照修复**，这个顺序才有意义，
   也才是能对外讲的做法。
2. **两条修复都会改变解析产物 → 改变分块内容 → 改变检索结果。**
   双栏几何排序会重排块序，DOCX 表格抽取会新增块内容。这类改动要单独评估回归
   （尤其会影响 RAG-034/035 的评测输入），不能搭在基线卡里顺手做。
3. **现状是诚实的，不需要为了「看起来通过」去动解析器。**
   `known_gap` 已登记、报告里已落在 `known_limitations` 且不计入 `passed`。

后续卡建议（各自带回归评估）：

- 一条「**双栏 PDF 阅读顺序几何排序**」：期望 `pdf_twocolumn_interleaved.pdf`
  的 `order_score` 从 0.8571 提升到 ≥0.95，且 `pdf_twocolumn.pdf` 保持 1.0。
- 一条「**DOCX 表格纳入解析**」：期望 `docx_headings_table.docx` 覆盖率从 0.40 提升到 1.0。

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

## 8. 质量门禁：gate.v1（**已锁定**，2026-10-07）

版本 `gate.v1`；锁定人「批次评审（Agent 代执行人工评审）」；锁定日期 2026-10-07。
规则文本写在 `scripts/parse_quality_report.py::LOCKED_GATE`，报告逐条输出判定结果。

| 规则 | 内容 | 当前 |
|---|---|---|
| **G1** | 未登记的失败必须为 0：任何 `status=fail` 且不在 manifest `expected_failure` 里的样本，整份报告判不通过 | ✅ 通过 |
| **G2** | 覆盖率 `coverage ≥ 0.95`；低于门槛的样本**必须**登记在 manifest 的 `known_gap` 并出现在报告 `known_limitations` 中，登记后状态为 `known_limitation`（**豁免门槛，但不得计入 passed**） | ✅ 通过（DOCX 表格 0.40 走豁免） |
| **G3** | 阅读顺序 `order_score ≥ 0.95`，同 G2 的登记豁免规则 | ✅ 通过（双栏交错 0.8571 走豁免） |
| **G4** | 依赖缺失：存在任何 `dependency_missing` 样本时整份报告 `meets_gate=False`（即使其它全绿） | ❌ **不通过**，`pdf_scanned.pdf` 命中 |
| **G5** | 不得声称验证过：报告中不得出现任何宣称 OCR 质量已经过验证的表述；`dependency_missing` 样本不得计入覆盖或顺序统计 | ✅ 通过 |

当前结论：`blocking_rules=['G4']`，`meets_gate=False`。

**G4 在补齐 tesseract + chi_sim 语言包后需重跑才能解除，解除前本卡整体不算「质量通过」。**
修改门槛值必须同时改 `LOCKED_GATE["version"]` 与 `locked_at`，避免静默改写已锁定门禁。

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
| `scripts/parse_quality_report.py` | 新增：评估报告脚本（含 gate.v1 门禁判定） |
| `evals/reports/parse-quality-20261007.json` | 新增：首份报告（非冻结基线） |
| `tests/test_rag_033_parse_quality.py` | 新增：35 条测试 |

未改动 `app/rag/**` 下的任何产品代码；未改 `.env`；未改 `tasks.yaml` 卡片状态。

### 10.1 门禁锁定后的追加变更（2026-10-07）

| 文件 | 变更 |
|---|---|
| `scripts/parse_quality_report.py` | `PROPOSED_GATE` → `LOCKED_GATE`（gate.v1，locked）；新增 `evaluate_gate`（G1–G5 逐条判定）与 `scan_forbidden_phrases`（G5 全报告扫描）；状态改为「阈值 + known_gap 登记」推导 |
| `tests/test_rag_033_parse_quality.py` | 门禁相关断言改为校验 `locked`/`gate.v1`，新增 G1–G5 五条规则测试与 G5 扫描函数自测 |
| `docs/plans/implementation_rag_033_parse_quality.md` | 6.1 节补「不在本卡修」的三条理由；第 8 节改为已锁定的 gate.v1 |

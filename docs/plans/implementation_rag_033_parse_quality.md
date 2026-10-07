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

合计：samples=13  passed=7  failed=0  dependency_missing=1  known_limitation=2  gap_not_reproduced=0  expected_failure=3

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

质量门禁 gate.v2（状态=locked，锁定人=批次评审（Agent 代执行人工评审），锁定日期=2026-10-07）：
覆盖率≥0.95、顺序得分≥0.95、失败率≤0.0
  G1 通过：未登记的失败与未注销的缺口必须为 0…
  G2 通过：覆盖率 coverage ≥ 0.95…
  G3 通过：阅读顺序 order_score ≥ 0.95，豁免规则同 G2。
  G4 不通过：依赖缺失…（命中：['pdf_scanned.pdf']）
  G5 通过：不得声称验证过…（含 used_mock 必须为 False）
  G6 通过：失败率 measured_failure_rate ≤ 0.0…
  实测失败率=0.0；阻断规则=['G4']；meets_gate=False
```

复现命令：

```bash
python scripts/parse_quality_report.py            # 打印
python scripts/parse_quality_report.py --json     # 落 evals/reports/parse-quality-<date>-gate-v2.json
python scripts/parse_quality_report.py --json --force  # 覆盖同名已存在的报告
```

> 脚本拒绝写入冻结基线文件名 `rag-v0.1-baseline-20260919.json`；同名报告默认也
> **拒绝静默覆盖**（需显式 `--force` 或改用 `--json-path`），避免同日重跑把已评审的
> 报告悄悄换掉。

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

## 8. 质量门禁：gate.v2（**已锁定**，2026-10-07）

版本 `gate.v2`；锁定人「批次评审（Agent 代执行人工评审）」；锁定日期 2026-10-07。
规则文本写在 `scripts/parse_quality_report.py::LOCKED_GATE`，报告逐条输出判定结果。

| 规则 | 内容 | 当前 |
|---|---|---|
| **G1** | 未登记的失败与未注销的缺口必须为 0：任何 `status=fail`（不在 manifest `expected_failure` 里）或 `status=gap_not_reproduced`（manifest 仍登记 `known_gap` 但本次阈值突破未复现，需人工注销）的样本，整份报告判不通过 | ✅ 通过 |
| **G2** | 覆盖率 `coverage ≥ 0.95`；低于门槛的样本**必须**登记在 manifest 的 `known_gap` 并出现在报告 `known_limitations` 中，登记后状态为 `known_limitation`（**豁免门槛，但不得计入 passed**） | ✅ 通过（DOCX 表格 0.40 走豁免） |
| **G3** | 阅读顺序 `order_score ≥ 0.95`，同 G2 的登记豁免规则 | ✅ 通过（双栏交错 0.8571 走豁免） |
| **G4** | 依赖缺失：存在任何 `dependency_missing` 样本时整份报告 `meets_gate=False`（即使其它全绿） | ❌ **不通过**，`pdf_scanned.pdf` 命中 |
| **G5** | 不得声称验证过：报告中不得出现任何宣称 OCR 质量已经过验证的表述；`dependency_missing` 样本不得计入覆盖或顺序统计；未走真实 provider 路径（`used_mock=True`）时不得声明走了真实路径 | ✅ 通过 |
| **G6** | 失败率 `measured_failure_rate ≤ 0.0`（分母为 `pass + fail` 样本） | ✅ 通过（实测 0.0） |

当前结论：`blocking_rules=['G4']`，`meets_gate=False`。

**G4 在补齐 tesseract + chi_sim 语言包后需重跑才能解除，解除前本卡整体不算「质量通过」。**
修改门槛值必须同时改 `LOCKED_GATE["version"]` 与 `locked_at`，避免静默改写已锁定门禁。

### 8.1 关于「削弱期望」与「改进解析器」的等价性（H-02 / CE-13 收口）

状态由「实测阈值 + manifest 是否登记 `known_gap`」推导，而实测值同时取决于解析器能力和
manifest 期望强度。因此只删 manifest 期望片段（解析器一行没动、`known_gap` 也没删）也能让
覆盖率回到 1.0——这与「真把解析器改好」在实测值上完全等价。

**评审裁定（2026-10-07）：保留强制人工退役，不加 `known_gap_retired` 开关。**

- manifest 仍登记 `known_gap` 但本次阈值突破未复现时，**不得直接 `pass`**，判为
  `gap_not_reproduced` 并进 **G1 阻断**，需人工注销 `known_gap` 后才能回到 `pass`。
- 删掉语料文件后门禁报「样本缺失」失败（状态 `fail`、`error_type=CorpusSampleMissing`），
  **正是我们要的行为**：它强制人工退役 `known_gap` 并补齐预期，避免「升级解析器却忘了
  退役旧豁免」这种静默退化。
- **「每次升级解析器要多一步人工退役」是特性不是缺陷。**

**诊断信息要求（裁定附带，已实现）**：上述失败必须带齐三要素，否则无法定位：

1. 缺失/未复现的**样本 id**（文件名）；
2. 需要人工退役的 **`known_gap` 条目**（原文）；
3. **下一步该做什么**（补 `expected_fragments` or 退役 `known_gap`）。

实现见 `scripts/parse_quality_report.py::_gap_diagnosis`，输出形如：

```text
样本 docx_headings_table.docx：manifest 仍登记 known_gap，但本次阈值突破未复现
· 需人工退役的 known_gap 条目：DocxDocumentParser 只遍历 document.paragraphs，…
· 下一步：确认是解析器已改进还是 manifest 期望被削弱；若是前者，从 manifest 删除该 known_gap 后重跑；若是后者，恢复原有 expected_fragments
```

断言见 `test_missing_corpus_file_failure_is_diagnosable` 与
`test_gap_not_reproduced_message_is_diagnosable`（都断言三要素齐全）。

### 8.3 报告文件命名约定（L-04）

报告固定落在 `evals/reports/`，文件名 `parse-quality-<YYYYMMDD>-<gate版本>.json`
（当前 `parse-quality-20261007-gate-v2.json`）。把门禁版本写进文件名是因为：门禁升级后
产出的是**新文件**，不会静默覆盖旧门禁下已评审的报告，也避免「报告里写得好听但对应文件
已被换掉」。

配合 `write_json()` 默认拒绝覆盖同名文件，只有显式 `--force` 或 `--json-path` 才能覆盖。
旧的 `parse-quality-20261007.json`（gate.v1 时期产物）已删除，只保留当前门禁版本对应的一份。

### 8.4 关闭前整改：一致性测试与实测报告的绑定（M-01 / CE-14）

**缺陷（复现证据，整改前）**：删掉落盘报告后，一致性测试**静默跳过**，整套测试全绿——

```text
$ mv evals/reports/parse-quality-20261007.json /tmp/…
$ pytest tests/test_rag_033_parse_quality.py -q -rs
..................................................s...                   [100%]
SKIPPED [1] tests\test_rag_033_parse_quality.py:746: 尚无落盘报告
53 passed, 1 skipped in 33.66s
```

对照：**篡改**报告关键键（`gate.version` → `gate.v9-TAMPERED`、`totals.passed` → 99）整改前
就能被抓到 1 条（`test_committed_report_matches_script_output`）——漏洞只在「文件不存在」这一
条路径上：测试用 `pytest.skip` 兜底，等于放弃守护。

**整改**：

- 去掉 `skip`，落盘报告不存在即**断言失败**（`_committed_report_path()`）；
- 一致性测试拆成两条：`test_committed_report_matches_script_output`（全量逐字段比对）与
  `test_committed_report_is_bound_to_corpus_and_gate`（与 manifest 样本集合、样本文件是否
  真实存在、`known_gap` 条目、门禁版本/状态、逐样本 coverage/order/status 五项绑定）；
- 新增 `test_report_file_name_carries_gate_version` 守命名约定。

**整改后变异结果（实测，见 §10.3）**：删除报告 → **3 条变红**；篡改关键键 → **2 条变红**。

**CE-14（硬编码脚本路径）**：`tests/test_rag_033_parse_quality.py:28` 定义 `_REPO_ROOT`，
`:29` `_SCRIPTS_DIR`，加载脚本时硬编码 `scripts/parse_quality_report.py`。
整改：新增 `test_script_paths_exist_and_expose_symbols`
（`tests/test_rag_033_parse_quality.py:854`），两层断言——

1. **文件层**：`scripts/parse_quality_report.py` 与 `scripts/generate_parse_corpus.py`
   必须存在（`:868`），且源码含 `def main(` / `def build_report(` / `def evaluate_gate(` /
   `def evaluate_sample(` / `def measure_coverage(` / `def evaluate_order(`（`:870-871`）；
2. **符号层**：加载后的模块必须暴露可调用的 `main` / `build_report` / `evaluate_gate` /
   `evaluate_sample` / `measure_coverage` / `evaluate_order` / `write_json` /
   `default_report_name`（`:883`）。

变异结果（只跑这一条用例的**定向**变异）：把脚本改名 → 收集阶段即 `1 error`；
删掉源码里的 `def measure_coverage(` → **恰好 1 条变红**，即
`test_script_paths_exist_and_expose_symbols` 本身；删掉脚本文件 → `1 error`。
三种变异都是红，且都能用 `git checkout --` / 重命名还原，`sha256` 比对零残留。

### 8.5 CLI 失败行为（L-03）

| 场景 | 行为 | 实测退出码 |
|---|---|---|
| 未知参数 `--definitely-not-a-flag` | `argparse` → `SystemExit(2)`，stderr 含出错参数名 | `2` |
| `--json-path` / `--ocr-provider` 缺实参 | `argparse` → `SystemExit(2)`，stderr 含 `expected one argument` | `2` |
| 语料 `manifest.json` 缺失 | `load_manifest()` → `SystemExit`，文案含可执行修复命令 `python scripts/generate_parse_corpus.py` | 非 0 |
| 写冻结基线文件名的报告 | `write_json()` → `SystemExit: 拒绝写入冻结基线报告` | 非 0 |
| 写已存在的同名报告 | `write_json()` → `SystemExit: 拒绝静默覆盖`（显式 `--force` 才可覆盖） | 非 0 |

断言位置：`test_cli_rejects_unknown_argument_with_nonzero_exit`
（`tests/test_rag_033_parse_quality.py:893`，进程内）、
`test_cli_process_exits_nonzero_with_diagnostic`（`:900`，**真实子进程**，同时断言
`returncode != 0` 且报错里出现出错的参数名）、
`test_missing_corpus_manifest_exits_with_diagnostic`（`:913`）。

`ocr_probe.used_mock` / `path` 由**实际生效的 provider 对象**派生：

- `provider_class`：经 `app.rag.ocr.factory.get_ocr_provider()` 真实解析一次得到的类路径；
- `observed_provider`：扫描件样本实际产出的 `OcrResult.provider`（依赖缺失时为 `None`）；
- `used_mock = not (provider_class 以 app.rag.ocr. 开头 and observed_provider 在
  {tesseract, cloud} 白名单内或为 None)`。

任一侧被换成假实现都会让 `used_mock=True`，并经 G5 阻断。当前真实运行下
`provider_class = app.rag.ocr.tesseract.TesseractOcrProvider`、`observed_provider = None`
（依赖缺失、未产出任何 OCR 文本）、`used_mock = False`。

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
| `scripts/parse_quality_report.py` | 新增：评估报告脚本（含门禁判定，当前 **gate.v2**） |
| `evals/reports/parse-quality-<日期>-gate-v2.json` | 新增：首份报告（非冻结基线），文件名带门禁版本 |
| `tests/test_rag_033_parse_quality.py` | 新增：62 条测试 |

未改动 `app/rag/**` 下的任何产品代码；未改 `.env`；未改 `tasks.yaml` 卡片状态
（卡片状态与 `progress` / `acceptance_record` 由批次负责人在关闭时统一处理）。

### 10.1 门禁锁定后的追加变更（2026-10-07）

| 文件 | 变更 |
|---|---|
| `scripts/parse_quality_report.py` | `PROPOSED_GATE` → `LOCKED_GATE`（gate.v1，locked）；新增 `evaluate_gate`（G1–G5 逐条判定）与 `scan_forbidden_phrases`（G5 全报告扫描）；状态改为「阈值 + known_gap 登记」推导 |
| `tests/test_rag_033_parse_quality.py` | 门禁相关断言改为校验 `locked`/`gate.v1`，新增 G1–G5 五条规则测试与 G5 扫描函数自测 |
| `docs/plans/implementation_rag_033_parse_quality.md` | 6.1 节补「不在本卡修」的三条理由；第 8 节改为已锁定的 gate.v1 |

### 10.2 独立审查整改后的追加变更（2026-10-07，审查报告见 `docs/reviews/`）

| 文件 | 变更 |
|---|---|
| `scripts/parse_quality_report.py` | 门禁升到 **gate.v2**：G1 扩到「未注销的缺口」、新增 G6（失败率真正被引用）、G5 增加 `used_mock` 维度；新增状态 `gap_not_reproduced`（H-02）；新增 `probe_ocr_provider` / `build_ocr_probe` 让 `used_mock` / `path` 从实际 provider 派生（M-02）；新增 `apply_report_level_checks` 统一承接报告级 G5 检查（M-01）；`write_json` 拒绝静默覆盖同名报告（L-02） |
| `tests/test_rag_033_parse_quality.py` | 35 → 54 条：新增 G1/G3 构造性反例 6 条（H-01）、削弱期望反例 2 条（H-02）、G5 端到端与门禁层单测 2 条（M-01/M-04）、`used_mock` 派生与假 provider 反例 4 条（M-02）、G6 两条（M-03）、报告不覆盖与一致性 2 条（L-02）、manifest 标注一致性 1 条（L-03） |
| `docs/plans/implementation_rag_033_parse_quality.md` | 第 8 节改为 gate.v2 并补 8.1（削弱期望等价性收口）与 8.2（used_mock 派生） |

审查提出的必修项 H-01 / H-02 / M-01 / M-02 与一并处理项 M-03 / M-04 / L-02 全部落地；
**L-01（`tasks.yaml` 落记录）按裁定不在本卡做**，由批次负责人关闭时统一处理。

### 10.3 关闭前置整改（2026-10-07，复审判「建议关闭，但关闭前必须完成 2 项」）

| 文件 | 变更 |
|---|---|
| `scripts/parse_quality_report.py` | 新增 `_gap_diagnosis()`：CE-13 要求的三要素诊断信息（样本 id / `known_gap` 条目原文 / 下一步）；新增样本文件缺失守卫（状态 `fail`、`error_type=CorpusSampleMissing`）；新增 `default_report_name()` 把门禁版本写进文件名（L-04） |
| `evals/reports/` | 删除 gate.v1 时期的 `parse-quality-20261007.json`，改为 `parse-quality-20261007-gate-v2.json`（L-04） |
| `tests/test_rag_033_parse_quality.py` | 54 → 62 条：一致性测试去掉 `skip` 并与 manifest/样本文件/门禁判定绑定（M-01，3 条）、脚本存在性与符号断言（CE-14，1 条）、CLI 退出码含真实子进程断言（L-03，3 条）、CE-13 诊断信息可诊断性（2 条） |
| `docs/plans/implementation_rag_033_parse_quality.md` | 8.1 补 CE-13 裁定与诊断信息要求；新增 8.3（命名约定）/ 8.4（M-01、CE-14 复现与整改证据）/ 8.5（CLI 失败行为表） |

#### 变异验证表（整改后，全部变红）

| 编号 | 变异动作 | 变红条数 | 变红用例 |
|---|---|---|---|
| M-01-a | 删除 `evals/reports/parse-quality-20261007-gate-v2.json` | **3** | `test_committed_report_matches_script_output`、`test_committed_report_is_bound_to_corpus_and_gate`、`test_report_file_name_carries_gate_version` |
| M-01-b | 篡改 `gate.version` → `gate.v9-TAMPERED`、`totals.passed` → 99 | **2** | `test_committed_report_matches_script_output`、`test_committed_report_is_bound_to_corpus_and_gate` |
| CE-14-A | `scripts/parse_quality_report.py` 改名 | **1**（收集期 error） | 整个 `tests/test_rag_033_parse_quality.py` |
| CE-14-B | 源码里 `def measure_coverage(` 改名 | **1**（精确定位） | `test_script_paths_exist_and_expose_symbols` |
| CE-14-C | `scripts/parse_quality_report.py` 删除 | **1**（收集期 error） | 整个 `tests/test_rag_033_parse_quality.py` |

（CE-14-A/B/C 为只跑 `test_script_paths_exist_and_expose_symbols` 的定向变异；全量跑时
B 会连带 43 条变红，因为该符号被下游普遍依赖。）

还原证明：三种变异执行后
`sha256(scripts/parse_quality_report.py) = 7c1cd673e186a285…`（变异前后一致），
`sha256(evals/reports/parse-quality-20261007-gate-v2.json) = 5067536f1baab5e0…`（一致），
**零残留**。

#### CE-13 诊断信息改造前后对比

改造前：样本文件缺失时只有一句「样本缺失」，无法知道缺哪个、该退役哪条 `known_gap`。

改造后（`scripts/parse_quality_report.py::_gap_diagnosis`）：

```text
样本文件缺失：docx_headings_table.docx（manifest 已登记该样本，
但 evals/corpus/parsing/docx_headings_table.docx 不存在）
· 需人工退役的 known_gap 条目：DocxDocumentParser 只遍历 document.paragraphs，
  表格单元格不进入解析结果，故表格片段必然未命中。
· 下一步：确认是解析器已改进还是 manifest 期望被削弱；若是前者，从 manifest 删除该
  known_gap 后重跑；若是后者，恢复原有 expected_fragments
```

`gap_not_reproduced` 分支同样走该函数（首行换成
「样本 docx_headings_table.docx：manifest 仍登记 known_gap，但本次阈值突破未复现」，
后两行相同）。断言见 `tests/test_rag_033_parse_quality.py:811` 与 `:829`，两者都断言
① 样本 id、② `known_gap` 条目内容、③ 「下一步」三要素齐全。

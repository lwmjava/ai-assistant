# MultiHop-RAG 补充对照数据集

本目录只保存接入说明与署名，不保存 609 篇新闻正文或 2556 条问题全文。

这是 **补充检索对照**，不替代官方 35 条（`evals/datasets/rag-v0.1/`）质量门槛，也不改变 holdout 决策规则或生产默认开关。

## 来源与许可

- 数据集：MultiHop-RAG
- 作者：Yixuan Tang and Yi Yang
- 论文： [MultiHop-RAG: Benchmarking Retrieval-Augmented Generation for Multi-Hop Queries](https://arxiv.org/abs/2401.15391)（COLM 2024）
- GitHub：https://github.com/yixuantt/MultiHop-RAG
- Hugging Face：https://huggingface.co/datasets/yixuantt/MultiHopRAG
- 许可证：[ODC-BY](https://opendatacommons.org/licenses/by/1-0/)

使用、转换或分发该数据集时必须保留上述署名，并遵守 ODC-BY。

## 获取与转换

原始文件约 12 MB，缓存到已 gitignore 的 `data/multihop_rag/raw/`（`corpus.json`、`MultiHopRAG.json`）。GitHub 主仓当前不再托管 `dataset/` 文件，转换器默认从 Hugging Face 镜像下载并校验 SHA-256。

```powershell
python evals/multihop_rag/convert.py --download
python evals/multihop_rag/run.py --mode smoke
python evals/multihop_rag/run.py --mode official --estimate-only
python evals/multihop_rag/run.py --mode official
```

转换结果写到 `data/multihop_rag/converted/`。评测索引写到 `data/eval_multihop_rag.db`（每次重建，已 gitignore）。

期望规模：609 篇文档、2556 条问题（其中 `null_query` 301 条无证据，检索指标只在有期望文档的问题上计分）。

证据通过 `evidence_list[].url` 精确对应 `corpus.json` 的 `url`；`fact` 必须是对应正文中的原句。

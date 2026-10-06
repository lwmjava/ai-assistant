# 父子映射修复及页面清晰化实现说明

2026-10-06，用户明确批准。计划见 plan_rag_016_parent_mapping_fix_20261006.md，独立审查见 docs/reviews/2026-10-06-RAG016父子映射修复独立审查.md。RAG-016 保持 in_review，尚未提交。

## 实际修改与行为

app/rag/chunking/strategies/parent_child.py 原先对全文独立切父块和子块，按数量平均绑定，可能把某段子块关联到其他章节。现先切父块，再对每个父块内部调用既有子策略，子块绑定生成它的 parent_key；保留父块先返回，使用连续 index。改变的是后续摄取/重建的父子映射与子块边界，未实施结构感知保护、LLM 边界辅助或摘要。

frontend/src/pages/Knowledge.tsx 将“父块 ID”改成“上级父块 ID”，顶层父块显示“无（当前为父块）”；加大数量说明、关系和评分来源提示字号并加深文字颜色。评分与检索排序未改。

tests/test_rag_parent_mapping.py 以合成非均匀段落、重复文本测试覆盖三种子策略；recording 子策略直接验证逐父调用、来源输入、parent_key 绑定，并覆盖 paragraph / recursive / structured 父策略。tasks.yaml 和 ADR-0005 同步兼容、回滚和批准范围。本轮不改源文件、数据库字段、租户权限和真实 Embedding 配置。

## 实际验证

解释器 D:/DepTooL/anaconda3/envs/ai-assistant/python.exe，测试库 data/test_ai_assistant.db。

| 命令 | 实际结果 / 退出码 |
|---|---|
| python -m pytest tests/test_rag_parent_mapping.py -q（修复前） | 4 failed，1；包含父子内容错配和重复 index |
| python -m pytest tests/test_rag_parent_mapping.py tests/test_rag_search_explanation.py tests/test_rag_parent_expand_lock.py tests/test_chunking.py -q --basetemp=data/pytest-tmp/parent-mapping-final | 最终 38 passed，0 |
| python -m pytest tests/test_rag.py tests/test_rag_import_jobs.py tests/test_rag_backend.py tests/test_chunking.py -q --basetemp=data/pytest-tmp/parent-mapping-rag | 82 passed、1 skipped，0；报告 data/parent-mapping-rag.txt |
| python -m pytest -q --basetemp=data/pytest-tmp/parent-mapping-full | 606 passed、2 skipped、1 warning，0；206.20 秒，报告 data/parent-mapping-full.txt |
| python -m ruff check . --no-cache | 最终 All checks passed，0 |
| python -m mypy app/ --cache-dir=data/mypy-parent-mapping | 172 文件无错误，0 |
| npm.cmd run typecheck / npm.cmd run build（frontend） | 均 0 |
| YAML safe_load、唯一 ID、依赖存在性 / git diff --check | 均 0 |

完整套件开始收集后补入 recording 用例，其三项另在最终专项 38 项中执行。补测后 Ruff 曾报 import 顺序 I001，已仅对该测试整理 import 并重跑全项目 Ruff 通过。完整套件两项跳过为未安装 llama_index.core / mcp 的模块级 importorskip。合成测试不构成真实检索质量指标。

## 旧数据、验收与回滚

本轮未自动重建或改写已上传文档。刷新前端能看到文案更新；要修正这份文档已经存储的旧关系，需用户在知识库页面明确执行“重建”，完成后再次检索。重建会替换分块和块 ID；同文件重新上传可能去重复用旧结果，不能保证更新。

验收检查：子块的上级父块 ID 对应返回父块 ID，父块包含该子块所在内容，而非其他章节；同父块只展示一次，分数继承来源明确。Agent 未执行真实页面操作，不宣称人工验收完成。

回滚代码仅影响后续切分；已经重建的文档恢复旧索引需明确再次重建。未自动操作真实或生产索引，未提交、推送、合并或部署。

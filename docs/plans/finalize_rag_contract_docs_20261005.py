"""Update scoped task facts and record the verified environment."""

import re
from pathlib import Path

import yaml

root = Path(__file__).resolve().parents[2]
p = root / "tasks.yaml"
parts = re.split(r"(?m)(?=^  - id:)", p.read_text(encoding="utf-8"))
for n in range(1, len(parts)):
    task = yaml.safe_load(parts[n])[0]
    if task["id"] not in {"PRAG-003", "PRAG-004", "RAG-023"}:
        continue
    if task["id"] in {"PRAG-003", "PRAG-004"}:
        task["context"]["facts"][0]["claim"] = "变更前事实：" + task["context"]["facts"][0]["claim"]
        task["context"]["facts"].append(
            {
                "claim": "当前已实现对应来源结构或低分过滤；完成状态保持，跨入口质量缺口由后续卡承接。",
                "evidence": (
                    "docs/plans/implementation_prag_003_20261005.md；docs/plans/im" "plementation_prag_004_20261005.md"
                ),
            }
        )
    else:
        task["status"] = "in_review"
        task["execution"]["progress"] = (
            "分数契约已修复；8 个新增用例先失败后通过，相关共 12 passed / 1 "
            "skipped；ruff 无缓存通过；mypy 模块缺失，待审查与验证补齐。"
        )
        task["execution"]["blocked_by"] = "mypy 在 ai-assistant 环境缺失；独立审查及人工验收尚未完成。"
        task["evaluation"]["commands"] = [
            (
                "pytest tests/test_rag_langchain_score_contract.py tests/test"
                "_rag_threshold.py tests/test_rag_backend.py -q"
            ),
            (
                "ruff check --no-cache app/rag/backend/langchain_backend.py t"
                "ests/test_rag_langchain_score_contract.py"
            ),
            "mypy app/rag/backend/langchain_backend.py",
        ]
    parts[n] = (
        "\n".join(
            "  " + line if line else ""
            for line in yaml.safe_dump([task], allow_unicode=True, sort_keys=False, width=110).splitlines()
        )
        + "\n\n"
    )
p.write_text("".join(parts).rstrip() + "\n", encoding="utf-8")
p = root / "AGENTS.md"
s = p.read_text(encoding="utf-8").replace(
    "D:\\install\\anaconda3\\envs\\ai-assistant\\Scripts", "D:\\DepTooL\\anaconda3\\envs\\ai-assistant\\Scripts"
)
s += (
    "\n运行环境核对（2026-10-05）：实际解释器为 `D:\\DepTooL\\anaconda3\\envs\\ai-ass"
    "istant\\python.exe`；此前 `D:\\install` 路径不存在。YAML 与 pytest 在此环境验"
    "证；mypy 当前未安装，不得报告类型检查通过。\n"
)
p.write_text(s, encoding="utf-8")
p = root / "docs/plans/plan_rag_review_remediation_20261005.md"
s = p.read_text(encoding="utf-8").replace(
    "本轮只交付本映射与计划；未修复上述业务问题、未新增任务卡、未迁移/重建、未改变 ADR 状态。",
    "任务契约已另批调整并新增 RAG-023～041，映射/拆分已落盘；业务实现按单卡另记，未迁移/重建、未改变 ADR 状态。",
)
s = s.replace(
    "当前指定环境路径缺失，不能切换未声明解释器伪报通过；环境修复为实施前置。",
    (
        "原记载路径不存在；已核对实际 ai-assistant 解释器 D:/DepTooL/anaconda3/envs/ai"
        "-assistant/python.exe。pytest/YAML 在该环境运行，mypy 缺模块仍为验证缺口。"
    ),
)
s += (
    "\n收尾核对：27 项为 A1–A8（优先修正）、B1–B4（未形成闭环）、C1–C7（二轮）、D1–D5（三轮）、E1–"
    "E3（四轮）。RAG-020 仅修 YAML 引号且保留 done；PRAG-002 仅调整范围/准入，保留 backl"
    "og，不自动启动或 cancelled。A8 明确包含 PRAG-003/004 旧 facts，现标为变更前事实并补当"
    "前证据。项目环境 PyYAML 6.0.3 全量 safe_load 已通过。\n"
)
p.write_text(s, encoding="utf-8")
p = root / "docs/plans/plan_rag_task_contract_adjustment_20261005.md"
s = p.read_text(encoding="utf-8") + (
    "\n收尾：RAG-020 语法修复保留 done；PRAG-002 保持 backlog 并锁定批准准入；PRAG-003"
    "/004 历史 facts 标注并补当前证据。项目环境 D:/DepTooL/anaconda3/envs/ai-ass"
    "istant/python.exe 全量 YAML 解析通过；base 的检查仅为前期文档验证。\n"
)
p.write_text(s, encoding="utf-8")
d = yaml.safe_load((root / "tasks.yaml").read_text(encoding="utf-8"))
graph = {t["id"]: t.get("depends_on", []) for t in d["tasks"]}
assert len(graph) == len(d["tasks"])
seen = set()


def visit(k, active):
    assert k in graph and k not in active
    if k in seen:
        return
    for dep in graph[k]:
        visit(dep, active | {k})
    seen.add(k)


for k in graph:
    visit(k, set())
print("YAML / unique IDs / dependencies OK; tasks:", len(graph))

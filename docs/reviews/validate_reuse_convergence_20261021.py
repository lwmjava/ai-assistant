"""Validate the static reuse proposal; does not execute application code."""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections import Counter
from pathlib import Path


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    report = root / "docs/plans/proposal_cross_project_reuse_20261021.md"
    assessment = root / "docs/plans/cross_project_reuse_assessment_20261021.json"
    convergence = root / "docs/plans/cross_project_reuse_convergence_20261021.json"
    a = json.loads(assessment.read_text(encoding="utf-8"))
    c = json.loads(convergence.read_text(encoding="utf-8"))
    rows = {row["id"]: row for row in a["rows"]}
    classified = [item for items in c["groups"].values() for item in items]
    assert len(classified) == len(set(classified)) == len(rows) == 36
    assert set(classified) == set(rows)
    assert {key: len(value) for key, value in c["groups"].items()} == {
        "core": 12,
        "later": 16,
        "experiments": 3,
        "conditional": 1,
        "deferred": 4,
    }
    assert Counter(row["action"] for row in rows.values()) == {
        "新增": 23,
        "原卡承接": 9,
        "扩卡待批": 4,
    }

    def summary(ids: list[str]) -> dict:
        selected = [rows[item] for item in ids]
        return {
            "count": len(selected),
            "existing": sum(bool(row["existing_card"]) for row in selected),
            "new": sum(not row["existing_card"] for row in selected),
            "cost": [sum(row["cost"]["total"][i] for row in selected) for i in (0, 1)],
        }

    for key, ids in c["groups"].items():
        assert summary(ids) == c["group_summary"][key]
    phase_ids = [item for phase in c["phases"] for item in phase["packages"]]
    assert set(phase_ids) == set(rows) and len(phase_ids) == 36
    for phase in c["phases"]:
        assert summary(phase["packages"]) == phase["summary"]
    assert summary(c["groups"]["core"]) == {
        "count": 12,
        "existing": 3,
        "new": 9,
        "cost": [44, 79],
    }
    assert summary(classified)["cost"] == [128, 229]
    for item in c["groups"]["core"]:
        assert set(c["hard"][item]) <= set(c["groups"]["core"])
        assert not c["externalHard"].get(item)

    external = {item["id"]: item for item in c["external"]}
    graph = {item: list(deps) + c["externalHard"].get(item, []) for item, deps in c["hard"].items()}
    for item, entry in external.items():
        graph[item] = [dep for dep in entry["task_snapshot"].get("depends_on", []) if dep in external]
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(item: str) -> None:
        assert item not in visiting, f"Dependency cycle: {item}"
        if item in visited:
            return
        assert item in graph, f"Unknown dependency: {item}"
        visiting.add(item)
        for dep in graph[item]:
            visit(dep)
        visiting.remove(item)
        visited.add(item)

    for item in graph:
        visit(item)
    hard_external = [entry for entry in external.values() if entry["kind"] != "soft"]
    assert len(hard_external) == 4
    assert [sum(entry["remaining"][i] for entry in hard_external) for i in (0, 1)] == [11, 20]
    assert [128 + 11, 229 + 20] == c["external_summary"]["all36_plus_hard"]
    assert c["external_summary"]["all36_plus_hard_plus_soft"] == [140, 251]
    for decision in c["decisions"]:
        assert set(decision["required_for"]) <= set(rows)
    assert digest(root / "tasks.yaml") == c["evidence"]["tasks_sha256"]
    assert digest(assessment) == c["evidence"]["assessment_sha256"]
    for evidence in a["evidence"]["files"]:
        assert digest(Path(evidence["root"]) / evidence["path"]) == evidence["sha256"]
    text = report.read_text(encoding="utf-8")
    for heading in ("## 15.", "## 16.", "## 17.", "## 18."):
        assert sum(line.startswith(heading) for line in text.splitlines()) == 1
    assert "数字人仍为0卡" in text
    assert "139–249" in text and "140–251" in text and "44–79" in text
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()
    assert head == c["evidence"]["target_head"]
    result = {
        "status": "passed",
        "exit_code": 0,
        "date": "2026-10-21 Asia/Shanghai",
        "command": (
            "D:/DepTooL/anaconda3/envs/ai-assistant/python.exe "
            "docs/reviews/validate_reuse_convergence_20261021.py"
        ),
        "target_head": head,
        "groups": c["group_summary"],
        "dependency_graph": "acyclic; all core hard prerequisites in core",
        "external_unfinished_hard_cards": 4,
        "hard_external_cost": [11, 20],
        "source_evidence_files_checked": len(a["evidence"]["files"]),
        "report_sha256": digest(report),
        "assessment_sha256": digest(assessment),
        "convergence_sha256": digest(convergence),
        "validator_sha256": digest(Path(__file__)),
        "tasks_sha256": digest(root / "tasks.yaml"),
        "business_tests": "not_run_document_only",
        "new_gates": "proposed_not_approved",
        "previous_attempts": [
            {
                "exit_code": 1,
                "cause": (
                    "Validator substring counting included subsection headings; "
                    "fixed to line-start matching. No business test ran."
                ),
            }
        ],
    }
    output = root / "docs/reviews/cross_project_reuse_convergence_validation_20261021.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {"status": "passed", "core": c["group_summary"]["core"], "graph": "acyclic", "report": str(output)},
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()

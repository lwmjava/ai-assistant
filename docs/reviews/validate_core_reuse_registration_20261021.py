"""Validate task registration and documents without importing application code."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from collections import Counter
from pathlib import Path

import yaml


class UniqueLoader(yaml.SafeLoader):
    """Reject repeated keys rather than silently replacing task contracts."""


def unique_mapping(loader: UniqueLoader, node: yaml.MappingNode, deep: bool = False) -> dict:
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        assert key not in result, f"Duplicate YAML key: {key}"
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def object_digest(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    baseline_path = root / "docs/reviews/core_reuse_task_registration_baseline_20261021.json"
    spec_path = root / "docs/reviews/core_reuse_registration_spec_20261021.json"
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    tasks_path = root / "tasks.yaml"
    parsed = yaml.load(tasks_path.read_text(encoding="utf-8"), Loader=UniqueLoader)
    tasks = {task["id"]: task for task in parsed["tasks"]}
    assert len(tasks) == len(parsed["tasks"]) == baseline["count"] + 9 == 201
    original_ids = set(baseline["task_object_sha256"])
    new_ids = {task["id"] for task in spec["new_tasks"]}
    assert len(new_ids) == 9 and set(tasks) - original_ids == new_ids
    assert original_ids <= set(tasks)
    assert (
        object_digest({key: value for key, value in parsed.items() if key != "tasks"})
        == baseline["top_level_without_tasks_sha256"]
    )
    changed_ids = {"MEM-002", "MEM-003", "FEED-001"}
    for task_id in original_ids - changed_ids:
        assert object_digest(tasks[task_id]) == baseline["task_object_sha256"][task_id], task_id
    for task in spec["new_tasks"]:
        assert tasks[task["id"]] == task, task["id"]
    feed_before = baseline["original_contracts"]["FEED-001"]
    assert {key: value for key, value in tasks["FEED-001"].items() if key != "execution"} == {
        key: value for key, value in feed_before.items() if key != "execution"
    }
    for task_id in ("MEM-002", "MEM-003"):
        old = baseline["original_contracts"][task_id]
        new = tasks[task_id]
        for key in ("allowed_paths", "forbidden_paths", "deliverables", "non_goals"):
            assert set(old["scope"][key]) <= set(new["scope"][key]), (task_id, key)
        assert set(old["acceptance"]) <= set(new["acceptance"])
        assert old["risk"] == new["risk"]
    assert "MEM-003" in tasks["MEM-002"]["depends_on"]
    assert tasks["MEM-003"]["priority"] == "P1"
    assert Counter(task["status"] for task in tasks.values()) == {"done": 141, "backlog": 59, "cancelled": 1}
    core = set(spec["core_map"].values())
    assert len(core) == 12 and len(core & new_ids) == 9
    assert all(tasks[task_id]["status"] == "backlog" for task_id in core)
    visited: set[str] = set()
    visiting: set[str] = set()

    def visit(task_id: str) -> None:
        assert task_id in tasks, f"Unresolved dependency: {task_id}"
        assert task_id not in visiting, f"Dependency cycle: {task_id}"
        if task_id in visited:
            return
        visiting.add(task_id)
        for dep in tasks[task_id].get("depends_on", []):
            assert dep in core or tasks[dep]["status"] == "done", f"Unfinished external core dependency: {dep}"
            visit(dep)
        visiting.remove(task_id)
        visited.add(task_id)

    for task_id in core:
        visit(task_id)
    assert "MEM-004" not in core and "PRAG-002" not in core
    total = [sum(tasks[task_id]["execution"]["estimate_person_days"][i] for task_id in core) for i in (0, 1)]
    assert total == [44, 79]
    plan = root / spec["plan"]
    plan_text = plan.read_text(encoding="utf-8")
    anchors = re.findall(r'<a id="([a-z0-9-]+)"></a>', plan_text)
    assert len(anchors) == len(set(anchors)) == 12
    for package, task_id in spec["core_map"].items():
        task = tasks[task_id]
        assert task["execution"]["reuse_package"] == package
        path, anchor = task["execution"]["plan"].split("#")
        assert path == spec["plan"] and anchor in anchors
        assert task["execution"]["nightly"] == {
            "eligible": False,
            "production_access": False,
            "auto_merge": False,
            "auto_deploy": False,
        }
        for field in ("allowed_paths", "forbidden_paths", "deliverables", "non_goals"):
            assert task["scope"][field]
        assert task["acceptance"] and task["risk"]["rollback"]
        for instruction in ("baseline", "verification", "authorization", "quality"):
            assert task["execution"]["admission"][instruction]
    for rel, expected in baseline["protected_tracked_source_sha256"].items():
        assert digest(root / rel) == expected, f"Protected input changed: {rel}"
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()
    assert head == baseline["head"]
    result = {
        "date": "2026-10-21 Asia/Shanghai",
        "status": "passed",
        "exit_code": 0,
        "command": (
            "D:/DepTooL/anaconda3/envs/ai-assistant/python.exe "
            "docs/reviews/validate_core_reuse_registration_20261021.py"
        ),
        "baseline_head": head,
        "baseline_tasks_sha256": baseline["tasks_sha256"],
        "final_tasks_sha256": digest(tasks_path),
        "baseline_count": 192,
        "final_count": 201,
        "status_counts": {"done": 141, "backlog": 59, "cancelled": 1},
        "new_cards": sorted(new_ids),
        "core_packages": 12,
        "core_mapping": spec["core_map"],
        "scope_extended": ["MEM-002", "MEM-003"],
        "metadata_only": ["FEED-001"],
        "unchanged_original_tasks": 189,
        "protected_source_files_unchanged": len(baseline["protected_tracked_source_sha256"]),
        "dependency_graph": "core closure and recursive completed prerequisites are acyclic and resolved",
        "core_estimate_person_days": total,
        "business_tests": "not_run_document_only",
        "candidate_binding": "HEAD plus final task/document inputs; no code implementation",
        "files_sha256": {
            rel: digest(root / rel)
            for rel in [
                spec["plan"],
                "docs/plans/implementation_core_reuse_task_registration_20261021.md",
                "docs/plans/proposal_cross_project_reuse_20261021.md",
                "docs/reviews/core_reuse_registration_spec_20261021.json",
                "docs/reviews/core_reuse_task_registration_baseline_20261021.json",
                "docs/reviews/validate_core_reuse_registration_20261021.py",
            ]
        },
    }
    output = root / "docs/reviews/core_reuse_task_registration_validation_20261021.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "status": "passed",
                "core": 12,
                "new": 9,
                "total": 201,
                "protected_unchanged": result["protected_source_files_unchanged"],
                "report": str(output),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()

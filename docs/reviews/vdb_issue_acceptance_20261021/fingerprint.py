"""Compare historical input fingerprints with the current tree. Read-only."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
MANIFESTS = [
    "docs/reviews/vdb_fix_20261021/final-frozen-inputs.sha256.json",
    "docs/reviews/vdb_fix_20261021/real-bound-inputs.json",
    "docs/reviews/vdb_fix_20261021/real-final-inputs.json",
    "docs/reviews/vdb_fix_20261021/reviewer-inputs-round3.json",
]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def compare(relative: str) -> dict:
    manifest = ROOT / relative
    if not manifest.is_file():
        return {"manifest": relative, "exists": False}
    rows = json.loads(manifest.read_text(encoding="utf-8"))
    drift = []
    missing = []
    for item in rows:
        path = ROOT / item["path"]
        if not path.is_file():
            missing.append(item["path"])
            continue
        actual = sha256(path)
        if actual != item["sha256"]:
            drift.append(item["path"])
    return {
        "manifest": relative,
        "exists": True,
        "count": len(rows),
        "drift": len(drift),
        "missing": len(missing),
        "drift_sample": drift[:40],
        "missing_sample": missing[:20],
    }


def pytest_log() -> dict:
    path = ROOT / "docs/reviews/vdb_fix_20261021/final-frozen-pytest.log"
    if not path.is_file():
        return {"exists": False}
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    summary = any(
        token in text
        for token in (" passed", " failed", " error", "===", "short test summary")
    )
    return {
        "exists": True,
        "bytes": path.stat().st_size,
        "mtime": path.stat().st_mtime,
        "lines": len(lines),
        "has_summary": summary,
        "tail": lines[-12:],
    }


def main() -> None:
    report = {"manifests": [compare(item) for item in MANIFESTS], "pytest_log": pytest_log()}
    destination = ROOT / "docs/reviews/vdb_issue_acceptance_20261021/fingerprint.json"
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

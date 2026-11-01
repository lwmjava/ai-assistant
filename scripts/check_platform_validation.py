"""Require every declared platform obligation to pass in a pytest JUnit report."""

from __future__ import annotations

import argparse
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def verify(report: Path, manifest: Path) -> list[str]:
    data = json.loads(manifest.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or type(data.get("version")) is not int or data["version"] != 1:
        raise ValueError("Unsupported platform manifest version")
    cases = data["cases"]
    if not isinstance(cases, list) or any(not isinstance(case, dict) for case in cases):
        raise ValueError("Invalid platform cases")
    expected = [(case["classname"], case["name"]) for case in cases]
    if (
        type(data["expected_count"]) is not int or data["expected_count"] <= 0
        or len(expected) != data["expected_count"] or len(set(expected)) != len(expected)
        or any(not isinstance(value, str) or not value for case in expected for value in case)
    ):
        raise ValueError("Invalid platform manifest")
    observed: dict[tuple[str, str], list[ET.Element]] = {}
    for row in ET.parse(report).iter("testcase"):
        key = (row.get("classname", ""), row.get("name", ""))
        observed.setdefault(key, []).append(row)
    failed = []
    for key in expected:
        rows = observed.get(key, [])
        if len(rows) != 1 or any(rows[0].find(tag) is not None for tag in ("skipped", "failure", "error")):
            failed.append("::".join(key))
    return failed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--manifest", type=Path, default=ROOT / "tests/platform_validation_cases.json")
    args = parser.parse_args()
    try:
        failed = verify(args.report, args.manifest)
    except (OSError, ValueError, KeyError, TypeError, ET.ParseError):
        print("PLATFORM_VALIDATION_ERROR:invalid_report_or_manifest", file=sys.stderr)
        return 1
    if failed:
        print("PLATFORM_VALIDATION_FAIL:" + ",".join(failed))
        return 1
    print("PLATFORM_VALIDATION_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

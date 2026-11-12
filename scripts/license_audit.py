"""Audit direct dependency licenses against a committed inventory and policy."""

from __future__ import annotations

import argparse
import json
import re
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path

INVENTORY_PATH = Path("docs/operations/dependency-licenses.json")
MARKDOWN_PATH = Path("docs/operations/dependency-licenses.md")

COMPATIBLE_LICENSES = {
    "0BSD",
    "Apache-2.0",
    "BlueOak-1.0.0",
    "BSD-2-Clause",
    "BSD-3-Clause",
    "BSD-3-Clause-Clear",
    "CC0-1.0",
    "HPND",
    "ISC",
    "MIT",
    "MPL-2.0",
    "OFL-1.1",
    "PSF-2.0",
    "Python-2.0",
    "Unlicense",
    "Zlib",
}

LICENSE_ALIASES = {
    "0bsd": "0BSD",
    "apache 2.0": "Apache-2.0",
    "apache license": "Apache-2.0",
    "apache license 2.0": "Apache-2.0",
    "apache license version 2.0": "Apache-2.0",
    "apache license, version 2.0": "Apache-2.0",
    "apache software license": "Apache-2.0",
    "apache-2.0": "Apache-2.0",
    "bsd": "BSD-3-Clause",
    "bsd 2-clause": "BSD-2-Clause",
    "bsd 3-clause": "BSD-3-Clause",
    "bsd license": "BSD-3-Clause",
    "bsd-2-clause": "BSD-2-Clause",
    "bsd-3-clause": "BSD-3-Clause",
    "cc0": "CC0-1.0",
    "cc0-1.0": "CC0-1.0",
    "isc": "ISC",
    "isc license": "ISC",
    "lgpl with exceptions": "LGPL-3.0-or-later",
    "mit": "MIT",
    "mit license": "MIT",
    "mpl-2.0": "MPL-2.0",
    "new bsd": "BSD-3-Clause",
    "ofl-1.1": "OFL-1.1",
    "python software foundation license": "PSF-2.0",
    "the mit license": "MIT",
    "the mit license (mit)": "MIT",
    "unlicense": "Unlicense",
    "zlib": "Zlib",
    "dual licensed - gnu affero gpl 3.0 or artifex commercial license": (
        "AGPL-3.0-or-later OR LicenseRef-Artifex-Commercial"
    ),
}

CLASSIFIER_MAP = {
    "License :: OSI Approved :: Apache Software License": "Apache-2.0",
    "License :: OSI Approved :: BSD License": "BSD-3-Clause",
    "License :: OSI Approved :: MIT License": "MIT",
    "License :: OSI Approved :: Mozilla Public License 2.0 (MPL 2.0)": "MPL-2.0",
    "License :: OSI Approved :: The Unlicense (Unlicense)": "Unlicense",
    "License :: OSI Approved :: GNU Library or Lesser General Public License (LGPL)": "LGPL-3.0-or-later",
    "License :: OSI Approved :: GNU Lesser General Public License v3 or later (LGPLv3+)": "LGPL-3.0-or-later",
    "License :: OSI Approved :: GNU Affero General Public License v3": "AGPL-3.0-only",
    "License :: OSI Approved :: GNU Affero General Public License v3 or later (AGPLv3+)": "AGPL-3.0-or-later",
    "License :: OSI Approved :: GNU General Public License v3 (GPLv3)": "GPL-3.0-only",
    "License :: Other/Proprietary License": "LicenseRef-Proprietary",
    "License :: Public Domain": "CC0-1.0",
}

LICENSE_FILE_HEADERS = (
    ("Apache License", "Apache-2.0"),
    ("The MIT License", "MIT"),
    ("MIT License", "MIT"),
    ("GNU AFFERO GPL 3.0 or Artifex Commercial License", "AGPL-3.0-or-later OR LicenseRef-Artifex-Commercial"),
    ("GNU Affero General Public License", "AGPL-3.0-or-later"),
    ("GNU Lesser General Public License", "LGPL-3.0-or-later"),
    ("Mozilla Public License 2.0", "MPL-2.0"),
    ("ISC License", "ISC"),
)

SOURCE_LABELS = {
    "runtime": "后端运行依赖",
    "requirements": "requirements 直接依赖",
    "frontend-runtime": "前端运行依赖",
    "frontend-dev": "前端开发依赖",
}


class AuditError(RuntimeError):
    """A fixed public-safe audit failure code."""


@dataclass(frozen=True)
class DirectDependency:
    name: str
    ecosystem: str
    declared: str
    source: str
    also_declared_in: tuple[str, ...] = ()


def canonical_python_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def requirement_name(spec: str) -> str | None:
    text = spec.split("#", 1)[0].strip()
    if not text:
        return None
    match = re.match(r"([A-Za-z0-9][A-Za-z0-9._-]*)", text)
    return match.group(1) if match else None


def pinned_version(spec: str) -> str | None:
    match = re.search(r"==\s*([A-Za-z0-9_.+-]+)", spec)
    return match.group(1) if match else None


def repo_root(path: Path | None = None) -> Path:
    return (path or Path(__file__).resolve().parents[1]).resolve()


def load_pyproject(root: Path) -> dict:
    return tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))


def python_direct_dependencies(root: Path) -> list[DirectDependency]:
    project = load_pyproject(root)["project"]
    collected: dict[str, DirectDependency] = {}

    def add(spec: str, source: str) -> None:
        raw_name = requirement_name(spec)
        if raw_name is None:
            return
        key = canonical_python_name(raw_name)
        existing = collected.get(key)
        if existing is None:
            collected[key] = DirectDependency(
                name=key,
                ecosystem="python",
                declared=spec.strip(),
                source=source,
            )
            return
        if source not in (existing.source, *existing.also_declared_in):
            collected[key] = DirectDependency(
                name=existing.name,
                ecosystem=existing.ecosystem,
                declared=existing.declared,
                source=existing.source,
                also_declared_in=(*existing.also_declared_in, source),
            )

    for spec in project["dependencies"]:
        add(spec, "runtime")
    for extra, dependencies in project.get("optional-dependencies", {}).items():
        for spec in dependencies:
            add(spec, f"optional:{extra}")
    for line in (root / "requirements.txt").read_text(encoding="utf-8").splitlines():
        add(line, "requirements")
    return [collected[key] for key in sorted(collected)]


def frontend_direct_dependencies(root: Path) -> list[DirectDependency]:
    package = json.loads((root / "frontend" / "package.json").read_text(encoding="utf-8"))
    items: list[DirectDependency] = []
    for field, source in (("dependencies", "frontend-runtime"), ("devDependencies", "frontend-dev")):
        for name, spec in sorted(package.get(field, {}).items()):
            items.append(
                DirectDependency(
                    name=name,
                    ecosystem="javascript",
                    declared=f"{name}@{spec}",
                    source=source,
                )
            )
    return items


def frontend_lock_versions(root: Path) -> dict[str, str]:
    lock = json.loads((root / "frontend" / "package-lock.json").read_text(encoding="utf-8"))
    versions: dict[str, str] = {}
    for key, value in lock.get("packages", {}).items():
        if not key.startswith("node_modules/"):
            continue
        name = key[len("node_modules/") :]
        version = value.get("version")
        if isinstance(version, str) and version:
            versions[name] = version
    return versions


def normalize_license_token(value: str) -> str | None:
    text = " ".join(value.strip().split())
    if not text or text.upper() in {"UNKNOWN", "NONE", "SEE LICENSE", "SEE LICENSE FILE"}:
        return None
    if len(text) > 180 or text.lower().startswith("copyright"):
        return None
    if re.fullmatch(r"Apache License(?: Version)? 2\.0", text, re.I):
        return "Apache-2.0"
    alias = LICENSE_ALIASES.get(text.lower())
    if alias:
        return alias
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9.+-]+", text):
        return LICENSE_ALIASES.get(text.lower(), text)
    return None


def split_spdx(expression: str) -> list[str] | None:
    if " AND " in expression.upper() and " OR " in expression.upper():
        return None
    if " AND " in expression:
        parts = [part.strip() for part in re.split(r"\s+AND\s+", expression, flags=re.I)]
        return parts if all(parts) else None
    if " OR " in expression:
        parts = [part.strip() for part in re.split(r"\s+OR\s+", expression, flags=re.I)]
        return parts if all(parts) else None
    return [expression.strip()] if expression.strip() else None


def expression_compatible(expression: str) -> bool:
    parts = split_spdx(expression)
    if not parts:
        return False
    tokens = [normalize_license_token(part) or part for part in parts]
    if " AND " in expression.upper() and " OR " not in expression.upper():
        return all(token in COMPATIBLE_LICENSES for token in tokens)
    if " OR " in expression.upper():
        return any(token in COMPATIBLE_LICENSES for token in tokens)
    return tokens[0] in COMPATIBLE_LICENSES


def classifier_license(classifiers: list[str]) -> str | None:
    mapped = [CLASSIFIER_MAP[item] for item in classifiers if item in CLASSIFIER_MAP]
    unique = list(dict.fromkeys(mapped))
    if len(unique) == 1:
        return unique[0]
    return None


def license_file_header(text: str) -> str | None:
    sample = "\n".join(text.splitlines()[:40])
    for needle, spdx in LICENSE_FILE_HEADERS:
        if needle.lower() in sample.lower():
            return spdx
    if "Redistribution and use in source and binary forms" in sample and "neither the name" in sample.lower():
        return "BSD-3-Clause"
    if "Permission is hereby granted, free of charge" in sample and "THE SOFTWARE IS PROVIDED" in sample:
        return "MIT"
    return None


def python_license_evidence(name: str) -> tuple[str, str, str]:
    import importlib.metadata as metadata

    try:
        distribution = metadata.distribution(name)
    except metadata.PackageNotFoundError as exc:
        raise AuditError(f"python_package_not_installed:{name}") from exc
    meta = distribution.metadata
    version = distribution.version
    expression = (meta.get("License-Expression") or "").strip()
    if expression:
        normalized = normalize_license_token(expression) or expression
        return version, normalized, "metadata License-Expression"
    short = normalize_license_token(meta.get("License") or "")
    if short:
        return version, short, "metadata License"
    classifiers = [item for item in (meta.get_all("Classifier") or []) if item.startswith("License ::")]
    classified = classifier_license(classifiers)
    if classified:
        return version, classified, "metadata classifier"
    for file in distribution.files or []:
        filename = Path(str(file)).name.lower()
        if filename not in {"license", "license.txt", "license.md", "licence", "copying", "copying.txt"}:
            if "/licenses/license" not in str(file).replace("\\", "/").lower() and not str(file).lower().endswith(
                "/license"
            ):
                continue
        try:
            text = Path(distribution.locate_file(file)).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        header = license_file_header(text)
        if header:
            return version, header, f"license file {file}"
    raise AuditError(f"python_license_unknown:{name}")


def javascript_license_evidence(name: str, version: str, node_modules: Path) -> tuple[str, str, str]:
    package_file = node_modules / name / "package.json"
    if not package_file.is_file():
        raise AuditError(f"javascript_package_missing:{name}")
    data = json.loads(package_file.read_text(encoding="utf-8"))
    installed = str(data.get("version") or "")
    if installed != version:
        raise AuditError(f"javascript_version_mismatch:{name}:{installed}:{version}")
    license_field = data.get("license")
    if isinstance(license_field, dict):
        license_field = license_field.get("type")
    if isinstance(license_field, str) and license_field.strip():
        normalized = normalize_license_token(license_field) or license_field.strip()
        return installed, normalized, "package.json license"
    licenses_field = data.get("licenses")
    if isinstance(licenses_field, list):
        types = []
        for item in licenses_field:
            if isinstance(item, dict) and item.get("type"):
                types.append(str(item["type"]))
            elif isinstance(item, str):
                types.append(item)
        if types:
            joined = " OR ".join(normalize_license_token(item) or item for item in types)
            return installed, joined, "package.json licenses"
    raise AuditError(f"javascript_license_unknown:{name}")


def load_inventory(path: Path) -> dict:
    if not path.is_file():
        raise AuditError("inventory_missing")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("packages"), list) or not isinstance(
        data.get("exceptions"), list
    ):
        raise AuditError("inventory_invalid")
    return data


def exception_map(inventory: dict) -> dict[tuple[str, str], dict]:
    mapping: dict[tuple[str, str], dict] = {}
    for item in inventory.get("exceptions", []):
        if not isinstance(item, dict):
            raise AuditError("exception_invalid")
        name = item.get("name")
        ecosystem = item.get("ecosystem")
        disposition = item.get("disposition")
        applies = item.get("applies_to_spdx")
        if not all(isinstance(value, str) and value.strip() for value in (name, ecosystem, disposition, applies)):
            raise AuditError("exception_incomplete")
        key = (ecosystem, name if ecosystem == "javascript" else canonical_python_name(name))
        mapping[key] = item
    return mapping


def package_decision(ecosystem: str, name: str, spdx: str, exceptions: dict[tuple[str, str], dict]) -> tuple[str, str]:
    key = (ecosystem, name)
    exception = exceptions.get(key)
    if expression_compatible(spdx):
        if exception:
            raise AuditError(f"exception_unneeded:{name}")
        return "compatible", ""
    if exception is None:
        raise AuditError(f"incompatible_undisposed:{name}:{spdx}")
    if exception["applies_to_spdx"] != spdx:
        raise AuditError(f"exception_license_mismatch:{name}")
    return "exception", exception.get("id") or name


def build_package_record(
    dependency: DirectDependency,
    version: str,
    spdx: str,
    evidence: str,
    exceptions: dict[tuple[str, str], dict],
) -> dict:
    decision, exception_id = package_decision(dependency.ecosystem, dependency.name, spdx, exceptions)
    record = {
        "name": dependency.name,
        "ecosystem": dependency.ecosystem,
        "declared": dependency.declared,
        "version": version,
        "source": dependency.source,
        "also_declared_in": list(dependency.also_declared_in),
        "spdx": spdx,
        "evidence": evidence,
        "decision": decision,
    }
    if exception_id:
        record["exception_id"] = exception_id
    return record


def collect_inventory(root: Path, node_modules: Path, previous: dict | None) -> dict:
    exceptions_source = previous or {"exceptions": []}
    exceptions = exception_map(exceptions_source)
    packages: list[dict] = []
    lock_versions = frontend_lock_versions(root)
    for dependency in python_direct_dependencies(root):
        version, spdx, evidence = python_license_evidence(dependency.name)
        pinned = pinned_version(dependency.declared)
        if pinned and pinned != version:
            raise AuditError(f"python_version_mismatch:{dependency.name}:{pinned}:{version}")
        packages.append(build_package_record(dependency, version, spdx, evidence, exceptions))
    for dependency in frontend_direct_dependencies(root):
        version = lock_versions.get(dependency.name)
        if not version:
            raise AuditError(f"javascript_lock_missing:{dependency.name}")
        installed, spdx, evidence = javascript_license_evidence(dependency.name, version, node_modules)
        packages.append(build_package_record(dependency, installed, spdx, evidence, exceptions))
    used = {item["name"] for item in packages if item["decision"] == "exception"}
    leftover = [item["name"] for item in exceptions_source.get("exceptions", []) if item["name"] not in used]
    if leftover:
        raise AuditError("exception_unused:" + ",".join(leftover))
    return {
        "project_license": "Apache-2.0",
        "packages": packages,
        "exceptions": exceptions_source.get("exceptions", []),
    }


def source_label(source: str) -> str:
    if source.startswith("optional:"):
        return f"可选 extra:{source.split(':', 1)[1]}"
    return SOURCE_LABELS.get(source, source)


def render_markdown(inventory: dict) -> str:
    packages = inventory["packages"]
    exceptions = inventory["exceptions"]
    python_packages = [item for item in packages if item["ecosystem"] == "python"]
    javascript_packages = [item for item in packages if item["ecosystem"] == "javascript"]
    compatible = sum(1 for item in packages if item["decision"] == "compatible")
    lines = [
        "# 直接依赖许可证清单",
        "",
        "本仓库第一方代码采用 Apache-2.0。本文件覆盖后端与前端的全部直接依赖，并记录需要单独处置的许可证。",
        (
            "清单由 `scripts/license_audit.py` 根据 `pyproject.toml`、`requirements.txt`、"
            "`frontend/package.json` 与 `frontend/package-lock.json` 生成并校验。"
        ),
        "本文不构成法律意见，也不升级任何依赖版本。",
        "",
        "## 结论",
        "",
        (
            f"- 直接依赖共 {len(packages)} 项（后端 {len(python_packages)}，前端 {len(javascript_packages)}），"
            "已与依赖文件核对一致。"
        ),
        f"- 其中 {compatible} 项属于策略允许的宽松许可证。",
        f"- 需要处置的项 {len(exceptions)} 个，均已写下处置结论。",
        "- 当前无未处置的不兼容许可证。",
        "",
        "## 许可证策略",
        "",
        "与 Apache-2.0 第一方代码一并使用时，下列 SPDX 标识视为策略允许：",
        "",
        ", ".join(f"`{name}`" for name in sorted(COMPATIBLE_LICENSES)) + "。",
        "",
        "含 `OR` 的表达式只要有一侧被允许即视为兼容；含 `AND` 的表达式必须全部被允许。",
        "GPL、AGPL、LGPL、专有许可证、未知许可证，以及其他未列入允许集合的标识，必须在清单中留下处置结论，否则校验失败。",
        "SIL OFL-1.1 仅用于网页字体嵌入；使用时仍须遵守保留字体名称、不得单独出售字体等 OFL 条件。",
        "",
        "## 处置结论",
        "",
    ]
    if not exceptions:
        lines.append("当前没有需要单独处置的直接依赖。")
        lines.append("")
    for item in exceptions:
        lines.extend(
            [
                f"### {item['name']}",
                "",
                f"- 生态：{item['ecosystem']}",
                f"- 许可证：`{item['applies_to_spdx']}`",
                f"- 处置：{item['disposition']}",
                "",
            ]
        )
    lines.extend(
        [
            "## 后端直接依赖",
            "",
            "| 名称 | 声明 | 核验版本 | 来源 | SPDX | 结论 |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
    )
    for item in python_packages:
        also = "、".join(source_label(value) for value in item.get("also_declared_in", []))
        source = source_label(item["source"]) + (f"；亦见 {also}" if also else "")
        lines.append(
            "| `{name}` | `{declared}` | {version} | {source} | `{spdx}` | {decision} |".format(
                name=item["name"],
                declared=item["declared"],
                version=item["version"],
                source=source,
                spdx=item["spdx"],
                decision=item["decision"],
            )
        )
    lines.extend(
        [
            "",
            "## 前端直接依赖",
            "",
            "| 名称 | 声明 | 锁定版本 | 来源 | SPDX | 结论 |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
    )
    for item in javascript_packages:
        lines.append(
            "| `{name}` | `{declared}` | {version} | {source} | `{spdx}` | {decision} |".format(
                name=item["name"],
                declared=item["declared"],
                version=item["version"],
                source=source_label(item["source"]),
                spdx=item["spdx"],
                decision=item["decision"],
            )
        )
    lines.extend(
        [
            "",
            "## 如何更新",
            "",
            "在已安装对应 Python 环境，并且前端 `node_modules` 与当前 lock 版本一致时执行：",
            "",
            "```powershell",
            "D:\\DepTooL\\anaconda3\\envs\\ai-assistant\\python.exe scripts/license_audit.py refresh",
            "D:\\DepTooL\\anaconda3\\envs\\ai-assistant\\python.exe scripts/license_audit.py check",
            "```",
            "",
            "如需从另一份已安装的前端目录读取许可证字段，可加 `--node-modules <路径>`。",
            "也可使用 `pip list` 对照本机已装版本，但验收以依赖文件与本清单是否一致为准。",
            "新增或变更直接依赖后，必须先刷新清单并补齐处置结论，再提交。",
            "",
        ]
    )
    return "\n".join(lines)


def write_inventory(inventory: dict, inventory_path: Path, markdown_path: Path) -> None:
    inventory_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    inventory_path.write_text(
        json.dumps(inventory, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    markdown_path.write_text(render_markdown(inventory), encoding="utf-8", newline="\n")


def expected_manifest_keys(root: Path) -> set[tuple[str, str]]:
    keys = {("python", item.name) for item in python_direct_dependencies(root)}
    keys.update(("javascript", item.name) for item in frontend_direct_dependencies(root))
    return keys


def check_inventory(root: Path, inventory_path: Path, markdown_path: Path) -> dict:
    inventory = load_inventory(inventory_path)
    if inventory.get("project_license") != "Apache-2.0":
        raise AuditError("project_license_mismatch")
    exceptions = exception_map(inventory)
    expected = expected_manifest_keys(root)
    observed: set[tuple[str, str]] = set()
    lock_versions = frontend_lock_versions(root)
    python_declared = {item.name: item for item in python_direct_dependencies(root)}
    javascript_declared = {item.name: item for item in frontend_direct_dependencies(root)}
    for item in inventory["packages"]:
        if not isinstance(item, dict):
            raise AuditError("package_invalid")
        name = item.get("name")
        ecosystem = item.get("ecosystem")
        declared = item.get("declared")
        version = item.get("version")
        source = item.get("source")
        spdx = item.get("spdx")
        decision = item.get("decision")
        required = (name, ecosystem, declared, version, source, spdx, decision)
        if not all(isinstance(value, str) and value.strip() for value in required):
            raise AuditError("package_incomplete")
        key = (ecosystem, name)
        if key in observed:
            raise AuditError(f"package_duplicate:{name}")
        observed.add(key)
        if ecosystem == "python":
            manifest = python_declared.get(name)
            if manifest is None:
                raise AuditError(f"package_not_direct:{name}")
            if declared != manifest.declared or source != manifest.source:
                raise AuditError(f"package_declaration_mismatch:{name}")
            if list(item.get("also_declared_in") or []) != list(manifest.also_declared_in):
                raise AuditError(f"package_declaration_mismatch:{name}")
            pinned = pinned_version(manifest.declared)
            if pinned and pinned != version:
                raise AuditError(f"python_version_mismatch:{name}:{pinned}:{version}")
        elif ecosystem == "javascript":
            manifest = javascript_declared.get(name)
            if manifest is None:
                raise AuditError(f"package_not_direct:{name}")
            if declared != manifest.declared or source != manifest.source:
                raise AuditError(f"package_declaration_mismatch:{name}")
            if list(item.get("also_declared_in") or []) != list(manifest.also_declared_in):
                raise AuditError(f"package_declaration_mismatch:{name}")
            locked = lock_versions.get(name)
            if locked != version:
                raise AuditError(f"javascript_version_mismatch:{name}:{locked}:{version}")
        else:
            raise AuditError(f"package_ecosystem_invalid:{name}")
        computed, exception_id = package_decision(ecosystem, name, spdx, exceptions)
        if computed != decision:
            raise AuditError(f"package_decision_mismatch:{name}")
        if computed == "exception" and item.get("exception_id") not in {exception_id, exceptions[key].get("id"), name}:
            if item.get("exception_id") != (exceptions[key].get("id") or name):
                raise AuditError(f"exception_id_mismatch:{name}")
    missing = expected - observed
    extra = observed - expected
    if missing or extra:
        raise AuditError(
            "inventory_coverage_mismatch:"
            + ",".join(f"{eco}:{name}" for eco, name in sorted(missing | extra))
        )
    used = {item["name"] for item in inventory["packages"] if item["decision"] == "exception"}
    leftover = [item["name"] for item in inventory["exceptions"] if item["name"] not in used]
    if leftover:
        raise AuditError("exception_unused:" + ",".join(leftover))
    if not markdown_path.is_file():
        raise AuditError("markdown_missing")
    expected_markdown = render_markdown(inventory)
    if markdown_path.read_text(encoding="utf-8") != expected_markdown:
        raise AuditError("markdown_stale")
    return {
        "status": "passed",
        "packages": len(inventory["packages"]),
        "exceptions": len(inventory["exceptions"]),
        "inventory": str(inventory_path),
    }


def default_exceptions() -> list[dict]:
    return [
        {
            "id": "pymupdf-agpl-or-commercial",
            "name": "pymupdf",
            "ecosystem": "python",
            "applies_to_spdx": "AGPL-3.0-or-later OR LicenseRef-Artifex-Commercial",
            "disposition": (
                "PyMuPDF 为 AGPL-3.0-or-later 与 Artifex 商业许可证双授权，不属于本仓库允许的宽松许可证。"
                "本仓库第一方代码仍为 Apache-2.0；本记录不改变项目许可证，也不构成法律意见。"
                "当前用途是 PDF 版式文本提取（bbox/阅读顺序），以及 OCR 场景下的页面栅格化。"
                "未安装该包时，文本层解析会回退到 pypdf（BSD-3-Clause）；OCR 渲染会明确报依赖缺失。"
                "本清单不升级或移除该依赖，它仍留在默认运行依赖中。"
                "对外分发或提供包含 PyMuPDF 的服务前，必须取得 Artifex 商业授权，或按 AGPL-3.0 履行相应义务；"
                "无法接受上述条件的部署应从安装中省略 PyMuPDF，仅使用 pypdf 做文本提取。"
                "此项已记录为发布前必须核验的包装义务。"
            ),
        },
        {
            "id": "psycopg2-binary-lgpl",
            "name": "psycopg2-binary",
            "ecosystem": "python",
            "applies_to_spdx": "LGPL-3.0-or-later",
            "disposition": (
                "psycopg2-binary 为 LGPL-3.0-or-later，并带有官方声明的 OpenSSL 链接例外。"
                "本仓库不修改其源码，仅将其作为未改动的 PostgreSQL 适配库使用；"
                "它出现在默认 requirements 中，SQLite 场景可以不安装。"
                "按该项目常见的库使用方式记录为已接受，不将本仓库第一方代码改为 LGPL。"
                "本记录不构成法律意见。"
            ),
        },
    ]


def resolve_node_modules(root: Path, override: Path | None) -> Path:
    path = (override or (root / "frontend" / "node_modules")).resolve()
    if not path.is_dir():
        raise AuditError("node_modules_missing")
    return path


def cmd_refresh(root: Path, inventory_path: Path, markdown_path: Path, node_modules: Path | None) -> dict:
    previous = None
    if inventory_path.is_file():
        previous = load_inventory(inventory_path)
    else:
        previous = {"packages": [], "exceptions": default_exceptions()}
    inventory = collect_inventory(root, resolve_node_modules(root, node_modules), previous)
    write_inventory(inventory, inventory_path, markdown_path)
    return {
        "status": "passed",
        "action": "refresh",
        "packages": len(inventory["packages"]),
        "exceptions": len(inventory["exceptions"]),
        "inventory": str(inventory_path),
    }


def cmd_check(root: Path, inventory_path: Path, markdown_path: Path) -> dict:
    result = check_inventory(root, inventory_path, markdown_path)
    result["action"] = "check"
    return result


def write_fixture_project(base: Path) -> Path:
    root = base / "repo"
    (root / "frontend").mkdir(parents=True)
    (root / "docs" / "operations").mkdir(parents=True)
    pyproject = """
[project]
name = "fixture"
dependencies = ["alpha==1.0.0"]

[project.optional-dependencies]
dev = ["beta==2.0.0"]
"""
    (root / "pyproject.toml").write_text(pyproject.strip() + "\n", encoding="utf-8")
    (root / "requirements.txt").write_text("alpha==1.0.0\n", encoding="utf-8")
    package = {
        "dependencies": {"demo-lib": "1.2.3"},
        "devDependencies": {},
    }
    (root / "frontend" / "package.json").write_text(json.dumps(package), encoding="utf-8")
    lock = {"packages": {"node_modules/demo-lib": {"version": "1.2.3"}}}
    (root / "frontend" / "package-lock.json").write_text(json.dumps(lock), encoding="utf-8")
    return root


def make_inventory(packages: list[dict], exceptions: list[dict] | None = None) -> dict:
    return {"project_license": "Apache-2.0", "packages": packages, "exceptions": exceptions or []}


def run_self_test() -> dict:
    failures: list[str] = []

    def expect(code: str, func) -> None:
        try:
            func()
        except AuditError as exc:
            if str(exc) != code and not str(exc).startswith(code):
                failures.append(f"expected {code}, got {exc}")
            return
        failures.append(f"expected failure {code}")

    def expect_ok(func) -> None:
        try:
            func()
        except Exception as exc:  # noqa: BLE001
            failures.append(f"unexpected {type(exc).__name__}: {exc}")

    assert expression_compatible("MIT")
    assert expression_compatible("Apache-2.0 OR BSD-3-Clause")
    assert expression_compatible("MIT OR GPL-3.0-only")
    assert not expression_compatible("GPL-3.0-only")
    assert not expression_compatible("MIT AND GPL-3.0-only")
    assert not expression_compatible("AGPL-3.0-or-later OR LicenseRef-Artifex-Commercial")
    assert expression_compatible("OFL-1.1")

    with tempfile.TemporaryDirectory(prefix="license-audit-") as temporary:
        root = write_fixture_project(Path(temporary))
        inventory_path = root / INVENTORY_PATH
        markdown_path = root / MARKDOWN_PATH
        packages = [
            {
                "name": "alpha",
                "ecosystem": "python",
                "declared": "alpha==1.0.0",
                "version": "1.0.0",
                "source": "runtime",
                "also_declared_in": ["requirements"],
                "spdx": "MIT",
                "evidence": "test",
                "decision": "compatible",
            },
            {
                "name": "beta",
                "ecosystem": "python",
                "declared": "beta==2.0.0",
                "version": "2.0.0",
                "source": "optional:dev",
                "also_declared_in": [],
                "spdx": "Apache-2.0",
                "evidence": "test",
                "decision": "compatible",
            },
            {
                "name": "demo-lib",
                "ecosystem": "javascript",
                "declared": "demo-lib@1.2.3",
                "version": "1.2.3",
                "source": "frontend-runtime",
                "also_declared_in": [],
                "spdx": "MIT",
                "evidence": "test",
                "decision": "compatible",
            },
        ]
        inventory = make_inventory(packages)
        write_inventory(inventory, inventory_path, markdown_path)
        expect_ok(lambda: check_inventory(root, inventory_path, markdown_path))

        stale = packages[:-1]
        write_inventory(make_inventory(stale), inventory_path, markdown_path)
        expect("inventory_coverage_mismatch", lambda: check_inventory(root, inventory_path, markdown_path))

        gpl = [dict(item) for item in packages]
        gpl[0] = dict(gpl[0], spdx="GPL-3.0-only", decision="compatible")
        write_inventory(make_inventory(gpl), inventory_path, markdown_path)
        expect("incompatible_undisposed:alpha", lambda: check_inventory(root, inventory_path, markdown_path))

        disposed = [dict(item) for item in packages]
        disposed[0] = dict(
            disposed[0],
            spdx="GPL-3.0-only",
            decision="exception",
            exception_id="alpha-gpl",
        )
        exceptions = [
            {
                "id": "alpha-gpl",
                "name": "alpha",
                "ecosystem": "python",
                "applies_to_spdx": "GPL-3.0-only",
                "disposition": "test-only fixture, documented for the checker.",
            }
        ]
        write_inventory(make_inventory(disposed, exceptions), inventory_path, markdown_path)
        expect_ok(lambda: check_inventory(root, inventory_path, markdown_path))

        markdown_path.write_text(markdown_path.read_text(encoding="utf-8") + "stale\n", encoding="utf-8")
        expect("markdown_stale", lambda: check_inventory(root, inventory_path, markdown_path))

    if failures:
        raise AuditError("self_test_failed:" + ";".join(failures))
    return {"status": "passed", "action": "self-test", "failures": 0}


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("check", "refresh", "self-test"))
    parser.add_argument("--root", type=Path, default=None)
    parser.add_argument("--inventory", type=Path, default=None)
    parser.add_argument("--markdown", type=Path, default=None)
    parser.add_argument("--node-modules", type=Path, default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    root = repo_root(args.root)
    inventory_path = (root / args.inventory) if args.inventory else root / INVENTORY_PATH
    markdown_path = (root / args.markdown) if args.markdown else root / MARKDOWN_PATH
    if args.inventory and args.inventory.is_absolute():
        inventory_path = args.inventory
    if args.markdown and args.markdown.is_absolute():
        markdown_path = args.markdown
    try:
        if args.command == "self-test":
            result = run_self_test()
        elif args.command == "refresh":
            result = cmd_refresh(root, inventory_path, markdown_path, args.node_modules)
        else:
            result = cmd_check(root, inventory_path, markdown_path)
        print(json.dumps(result, ensure_ascii=True))
        return int(result["status"] != "passed")
    except Exception as exc:
        code = str(exc) if isinstance(exc, AuditError) else "license_audit_incomplete"
        print(json.dumps({"status": "error", "code": code}, ensure_ascii=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

"""Content identity and release gate use actual manifests and persisted files."""

import copy
import json
from pathlib import Path

import pytest

from app.agents.skills.base import SkillManifest, SkillTrigger
from app.agents.skills.versioning import (
    content_revision,
    effective_version,
    validate_manifest,
    validate_release_ledger,
)


def test_content_change_has_observable_execution_version():
    original = SkillManifest(name="example", system_prompt="Original instruction")
    changed = copy.deepcopy(original)
    changed.system_prompt = "Changed instruction"
    before = getattr(original, "effective_version", None)
    after = getattr(changed, "effective_version", None)
    assert before is not None, "Executing manifests must expose their content version"
    assert before != after


def test_path_and_release_label_do_not_change_content_identity():
    original = SkillManifest(name="example", system_prompt="Instruction")
    relocated = copy.deepcopy(original)
    relocated.version = "1.1"
    relocated.source_path = "different-host/example.yaml"
    before = getattr(original, "content_revision", None)
    after = getattr(relocated, "content_revision", None)
    assert isinstance(before, str) and len(before) == 64
    assert before == after


def test_zero_threshold_sign_has_identical_behavior_identity():
    original = SkillManifest(name="example", trigger=SkillTrigger(min_confidence=0.0))
    equivalent = SkillManifest(name="example", trigger=SkillTrigger(min_confidence=-0.0))
    assert original.content_revision == equivalent.content_revision


def test_extremely_nested_ledger_uses_fixed_safe_error(tmp_path):
    ledger = tmp_path / "nested.json"
    ledger.write_text("[" * 2000 + "0" + "]" * 2000, encoding="utf-8")
    with pytest.raises(ValueError, match="^skill_ledger_read_failed$"):
        validate_release_ledger(tmp_path, ledger)


def test_decoded_extremely_nested_ledger_uses_fixed_safe_error(tmp_path, monkeypatch):
    """Decoders able to parse deep input must enforce the same safety bound."""
    ledger = tmp_path / "nested.json"
    ledger.write_text("[" * 2000 + "0" + "]" * 2000, encoding="utf-8")
    decoded = 0
    for _ in range(2000):
        decoded = [decoded]
    monkeypatch.setattr("app.agents.skills.versioning.json.loads", lambda *a, **kw: decoded)
    with pytest.raises(ValueError, match="^skill_ledger_read_failed$"):
        validate_release_ledger(tmp_path, ledger)


def test_ledger_decoder_recursion_error_is_safely_mapped(tmp_path, monkeypatch):
    ledger = tmp_path / "ledger.json"
    ledger.write_text("{}", encoding="utf-8")

    def fail_decode(*args, **kwargs):
        raise RecursionError("synthetic-sensitive-parser-detail")

    monkeypatch.setattr("app.agents.skills.versioning.json.loads", fail_decode)
    with pytest.raises(ValueError, match="^skill_ledger_read_failed$"):
        validate_release_ledger(tmp_path, ledger)


@pytest.mark.parametrize("field,value", [
    ("version", None), ("version", 1.0), ("version", "01.0"),
    ("version", "1"), ("version", "1.0.0.0"), ("version", "1." + "9" * 40),
    ("name", "../unsafe"), ("name", "a" * 65), ("enabled", "false"),
    ("tools", "tool"), ("tools", [None]), ("models", ["x"] * 129),
    ("system_prompt", "x" * 65537), ("system_prompt", "\ud800"),
    ("origin", "untrusted"), ("description", {}), ("mode", "tool"),
], ids=lambda value: type(value).__name__)
def test_invalid_direct_manifest_is_safely_rejected(field, value):
    manifest = SkillManifest(name="example", system_prompt="synthetic-secret")
    setattr(manifest, field, value)
    with pytest.raises(ValueError) as caught:
        validate_manifest(manifest)
    assert "synthetic-secret" not in str(caught.value)
    assert str(caught.value).startswith("skill_")


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -0.1, 1.1, True, "0.5", 10**400])
def test_invalid_confidence_is_rejected(value):
    manifest = SkillManifest(name="example")
    manifest.trigger.min_confidence = value
    with pytest.raises(ValueError, match="skill_invalid_confidence"):
        content_revision(manifest)


def test_invalid_regex_is_safely_rejected():
    manifest = SkillManifest(name="example")
    manifest.trigger.regex = "[synthetic-secret"
    with pytest.raises(ValueError, match="^skill_invalid_regex$"):
        validate_manifest(manifest)


def test_deep_invalid_regex_is_safely_rejected():
    manifest = SkillManifest(name="example")
    manifest.trigger.regex = "(" * 2000 + "secret" + ")" * 2000
    with pytest.raises(ValueError, match="^skill_invalid_regex$"):
        validate_manifest(manifest)


def test_trigger_changes_affect_identity_but_origin_does_not():
    original = SkillManifest(name="example")
    changed = copy.deepcopy(original)
    changed.trigger.keywords.append("new keyword")
    assert content_revision(original) != content_revision(changed)
    changed = copy.deepcopy(original)
    changed.trigger.min_confidence = 0.75
    assert content_revision(original) != content_revision(changed)
    changed = copy.deepcopy(original)
    changed.origin = "private"
    assert content_revision(original) == content_revision(changed)


def test_total_canonical_size_is_bounded():
    manifest = SkillManifest(name="example", system_prompt="x" * 65536)
    manifest.trigger.keywords = ["k" * 256] * 128
    manifest.models = ["m" * 256] * 128
    manifest.tools = ["t" * 256] * 128
    with pytest.raises(ValueError, match="input_too_large"):
        validate_manifest(manifest)


@pytest.mark.parametrize("field,value", [
    ("description", "changed"), ("system_prompt", "changed"),
    ("tools", ["read_file"]), ("models", ["another-model"]), ("enabled", False),
])
def test_behavior_fields_change_revision(field, value):
    original = SkillManifest(name="example")
    changed = copy.deepcopy(original)
    setattr(changed, field, value)
    assert content_revision(original) != content_revision(changed)
    assert effective_version(original) != effective_version(changed)


def _release(tmp_path: Path, *, version="1.0", prompt="Original", enabled=True):
    import yaml

    manifest = SkillManifest(
        name="example", version=version, system_prompt=prompt, enabled=enabled,
        trigger=SkillTrigger(keywords=["release"]),
    )
    (tmp_path / "example.yaml").write_text(yaml.safe_dump({
        "name": "example", "version": version, "system_prompt": prompt, "enabled": enabled,
        "trigger": {"keywords": ["release"]},
    }), encoding="utf-8")
    entry = {"version": version, "content_revision": content_revision(manifest), "summary": "Initial"}
    ledger = {"schema_version": 1, "skills": {"example": [entry]}}
    path = tmp_path / "release_ledger.json"
    path.write_text(json.dumps(ledger), encoding="utf-8")
    return manifest, ledger, path


def test_persisted_change_requires_new_release_and_preserves_previous_ledger(tmp_path):
    from app.agents.skills.manager import SkillManager

    _, previous, path = _release(tmp_path)
    validate_release_ledger(tmp_path, path)
    original_manager = SkillManager()
    original_manager.load([tmp_path])
    old_context = original_manager.activate(original_manager.match("release"))
    changed, ledger, path = _release(tmp_path, version="1.1", prompt="Changed")
    with pytest.raises(ValueError, match="history_rewritten"):
        validate_release_ledger(tmp_path, path, previous)
    ledger["skills"]["example"].insert(0, previous["skills"]["example"][0])
    path.write_text(json.dumps(ledger), encoding="utf-8")
    validate_release_ledger(tmp_path, path, previous)
    assert ledger["skills"]["example"][-1]["content_revision"] == content_revision(changed)
    fresh_manager = SkillManager()
    fresh_manager.load([tmp_path])
    new_context = fresh_manager.activate(fresh_manager.match("release"))
    assert "Changed" in fresh_manager.inject_prompt(new_context, "Base")
    assert "Original" in original_manager.inject_prompt(old_context, "Base")
    assert old_context.versions[0]["effective_version"] != new_context.versions[0]["effective_version"]


def test_actual_builtin_release_gate_and_translator_content_upgrade(tmp_path):
    import shutil

    import yaml

    from app.agents.skills.loader import load_skill_from_yaml
    from app.agents.skills.manager import SkillManager

    builtin = Path(__file__).resolve().parents[1] / "app/agents/skills/builtin"
    validate_release_ledger(builtin, builtin / "releases.json")
    for source in builtin.iterdir():
        if source.suffix in {".yaml", ".json"}:
            shutil.copyfile(source, tmp_path / source.name)
    ledger_path = tmp_path / "releases.json"
    previous = json.loads(ledger_path.read_text(encoding="utf-8"))
    manager = SkillManager()
    manager.load([tmp_path])
    old = manager.activate(manager.match("请翻译原文"))
    translator = tmp_path / "translator.yaml"
    data = yaml.safe_load(translator.read_text(encoding="utf-8"))
    data["system_prompt"] += "\n测试发布：保留原文段落。"
    translator.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    with pytest.raises(ValueError, match="current_mismatch"):
        validate_release_ledger(tmp_path, ledger_path, previous)
    data["version"] = "1.1"
    translator.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    changed = load_skill_from_yaml(translator.read_text(encoding="utf-8"))
    updated = copy.deepcopy(previous)
    updated["skills"]["translator"].append({
        "version": "1.1", "content_revision": changed.content_revision,
        "summary": "Isolated regression: preserve source paragraph structure.",
    })
    ledger_path.write_text(json.dumps(updated), encoding="utf-8")
    validate_release_ledger(tmp_path, ledger_path, previous)
    fresh = SkillManager()
    fresh.load([tmp_path])
    ctx = fresh.activate(fresh.match("请翻译原文"))
    assert ctx.versions[0]["version"] == "1.1"
    assert ctx.versions[0]["effective_version"] != old.versions[0]["effective_version"]
    assert "保留原文段落" in fresh.inject_prompt(ctx, "BASE")
    assert updated["skills"]["translator"][:-1] == previous["skills"]["translator"]


def test_unrecorded_content_change_fails_gate_including_disabled_skill(tmp_path):
    _, ledger, path = _release(tmp_path, enabled=False)
    _, _, _ = _release(tmp_path, prompt="Changed", enabled=False)
    path.write_text(json.dumps(ledger), encoding="utf-8")
    with pytest.raises(ValueError, match="current_mismatch"):
        validate_release_ledger(tmp_path, path)


@pytest.mark.parametrize("versions", [["1.0", "1.0"], ["1.0", "1.0.0"], ["1.10", "1.9"]])
def test_duplicate_and_decreasing_release_numbers_fail(tmp_path, versions):
    _, ledger, path = _release(tmp_path)
    initial = ledger["skills"]["example"][0]
    ledger["skills"]["example"] = [dict(initial, version=version) for version in versions]
    path.write_text(json.dumps(ledger), encoding="utf-8")
    with pytest.raises(ValueError, match="version_order"):
        validate_release_ledger(tmp_path, path)


def test_release_numbers_compare_numerically(tmp_path):
    _, ledger, path = _release(tmp_path, version="1.10", prompt="Changed")
    initial = ledger["skills"]["example"][0]
    old = SkillManifest(
        name="example", version="1.9", system_prompt="Original", trigger=SkillTrigger(keywords=["release"]),
    )
    ledger["skills"]["example"].insert(0, dict(initial, version="1.9", content_revision=content_revision(old)))
    path.write_text(json.dumps(ledger), encoding="utf-8")
    validate_release_ledger(tmp_path, path)


def test_release_bump_without_content_change_is_rejected(tmp_path):
    _, ledger, path = _release(tmp_path, version="1.1")
    entry = ledger["skills"]["example"][0]
    ledger["skills"]["example"].insert(0, dict(entry, version="1.0"))
    path.write_text(json.dumps(ledger), encoding="utf-8")
    with pytest.raises(ValueError, match="content_unchanged"):
        validate_release_ledger(tmp_path, path)


@pytest.mark.parametrize("mutation", ["missing", "unknown", "wrong_hash", "wrong_version", "extra_entry_field"])
def test_ledger_binds_actual_skill_set_and_current_content(tmp_path, mutation):
    _, ledger, path = _release(tmp_path)
    if mutation == "missing":
        ledger["skills"].clear()
    elif mutation == "unknown":
        ledger["skills"]["other"] = copy.deepcopy(ledger["skills"]["example"])
    elif mutation == "wrong_hash":
        ledger["skills"]["example"][-1]["content_revision"] = "0" * 64
    elif mutation == "wrong_version":
        ledger["skills"]["example"][-1]["version"] = "1.1"
    else:
        ledger["skills"]["example"][-1]["secret"] = "should-not-be-recorded"
    path.write_text(json.dumps(ledger), encoding="utf-8")
    with pytest.raises(ValueError):
        validate_release_ledger(tmp_path, path)


def test_ledger_rejects_duplicate_json_keys_and_external_binding(tmp_path):
    _, _, path = _release(tmp_path)
    path.write_text('{"schema_version":1,"schema_version":1,"skills":{}}', encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate_key"):
        validate_release_ledger(tmp_path, path)
    other = tmp_path / "other"
    other.mkdir()
    with pytest.raises(ValueError, match="directory_mismatch"):
        validate_release_ledger(other, path)


def test_yaml_format_and_key_order_do_not_change_content_identity(tmp_path):
    from app.agents.skills.loader import load_skill_from_yaml

    first = load_skill_from_yaml('name: example\nversion: "1.0"\nsystem_prompt: Original\n')
    reordered = load_skill_from_yaml('# comment\nsystem_prompt: Original\nversion: "1.0"\nname: example\n')
    assert content_revision(first) == content_revision(reordered)


def test_duplicate_files_do_not_silently_override_release_identity(tmp_path):
    _, _, path = _release(tmp_path)
    (tmp_path / "duplicate.yml").write_text((tmp_path / "example.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate_skill"):
        validate_release_ledger(tmp_path, path)


@pytest.mark.parametrize("contents", [
    "not json", '{"schema_version":true,"skills":{}}', '{"schema_version":2,"skills":{}}',
])
def test_bad_ledger_read_is_safely_rejected(tmp_path, contents):
    _, _, path = _release(tmp_path)
    path.write_text(contents, encoding="utf-8")
    with pytest.raises(ValueError) as caught:
        validate_release_ledger(tmp_path, path)
    assert str(tmp_path) not in str(caught.value)


def test_missing_ledger_is_safely_rejected(tmp_path):
    with pytest.raises(ValueError, match="read_failed"):
        validate_release_ledger(tmp_path, tmp_path / "missing.json")

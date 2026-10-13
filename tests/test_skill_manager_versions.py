"""Runtime version lifecycle through actual registration and file loading."""

from pathlib import Path

import pytest

from app.agents.skills.base import SkillManifest, SkillMatch, SkillTrigger
from app.agents.skills.loader import discover_skills, load_skill_from_yaml
from app.agents.skills.manager import SkillManager


def manifest(prompt: str = "first") -> SkillManifest:
    return SkillManifest(name="sample", system_prompt=prompt, trigger=SkillTrigger(keywords=["hit"]))


def test_registration_isolates_nested_content_and_getters() -> None:
    manager = SkillManager()
    original = manifest()
    manager.register(original)
    original.system_prompt = "secret replacement"
    original.trigger.keywords.append("other")
    stored = manager.get("sample")
    assert stored is not None
    assert stored.system_prompt == "first"
    assert stored.trigger.keywords == ["hit"]
    stored.trigger.keywords.clear()
    manager.list_all()[0].system_prompt = "changed"
    assert manager.match("hit")[0].skill.system_prompt == "first"


def test_changed_content_and_stale_match_report_actual_execution() -> None:
    manager = SkillManager()
    manager.register(manifest())
    old_matches = manager.match("hit")
    old_context = manager.activate(old_matches)
    old_versions = getattr(old_context, "versions", [])
    assert len(old_versions) == 1, "Activation must identify the executed content"
    manager.register(manifest("second"))
    new_context = manager.activate(manager.match("hit"))
    assert new_context.versions[0]["effective_version"] != old_versions[0]["effective_version"]
    stale_context = manager.activate(old_matches)
    assert stale_context.versions == old_versions
    assert "first" in manager.inject_prompt(stale_context, "base")
    assert "second" not in stale_context.prompt_injection


def test_bad_reload_keeps_collection_history_and_custom_directory(tmp_path: Path) -> None:
    directory = tmp_path / "custom"
    directory.mkdir()
    skill_file = directory / "sample.yaml"
    skill_file.write_text("name: sample\ntrigger: {keywords: [hit]}\nsystem_prompt: first\n", encoding="utf-8")
    manager = SkillManager()
    manager.load([directory])
    skill_file.write_text("name: [broken", encoding="utf-8")
    with pytest.raises(ValueError):
        manager.reload()
    assert manager.match("hit")[0].skill.system_prompt == "first"
    skill_file.write_text("name: sample\ntrigger: {keywords: [hit]}\nsystem_prompt: second\n", encoding="utf-8")
    assert manager.reload() == 1
    assert manager.match("hit")[0].skill.system_prompt == "second"


def test_nan_update_is_atomic() -> None:
    manager = SkillManager()
    manager.register(manifest())
    invalid = manifest("corrupt")
    invalid.trigger.min_confidence = float("nan")
    with pytest.raises(ValueError):
        manager.register(invalid)
    assert manager.match("hit")[0].skill.system_prompt == "first"


def test_history_is_idempotent_global_bounded_and_isolated() -> None:
    manager = SkillManager()
    manager.register(manifest())
    manager.register(manifest())
    assert len(manager.history()) == 1
    manager.register(manifest("second"))
    manager.register(manifest())
    entries = manager.history()
    assert entries[-1]["current"] == entries[0]["current"]
    entries[-1]["current"]["name"] = "tampered"
    assert manager.history()[-1]["current"]["name"] == "sample"
    for index in range(110):
        manager.register(SkillManifest(name=f"skill_{index}"))
    assert len(manager.history()) == 100
    manager.unregister("sample")
    assert manager.history()[-1]["current"] is None


def test_only_activated_versions_and_confidence_are_reported() -> None:
    active = manifest()
    inactive = manifest("unselected")
    inactive.name = "inactive"
    inactive.trigger.min_confidence = 1.0
    ctx = SkillManager().activate([SkillMatch(active, 0.5), SkillMatch(inactive, 0.9)])
    assert ctx.skill_name == "sample"
    assert ctx.confidence == 0.5
    assert [item["name"] for item in ctx.versions] == ["sample"]
    assert "unselected" not in ctx.prompt_injection


@pytest.mark.parametrize("yaml_body", [
    "name: sample\nname: changed",
    "name: sample\nenabled: 'false'",
    "name: sample\nversion: null",
    "name: sample\ntrigger: []",
    "name: sample\ntrigger: {min_confidence: .inf}",
    "name: sample\ntrigger: {type: regex, regex: '['}",
    "name: sample\ntools: secret",
    "name: sample\nsystem_prompt: {nested: secret}",
])
def test_ambiguous_or_invalid_yaml_cannot_replace_registered_content(yaml_body: str) -> None:
    manager = SkillManager()
    manager.register(manifest())
    before = manager.history()
    with pytest.raises(ValueError, match="^skill_"):
        manager.register_from_yaml(yaml_body)
    assert manager.history() == before
    assert manager.match("hit")[0].skill.system_prompt == "first"


def test_partial_reload_failure_and_valid_deletion(tmp_path: Path) -> None:
    good = tmp_path / "good.yaml"
    good.write_text("name: sample\ntrigger: {keywords: [hit]}\nsystem_prompt: first", encoding="utf-8")
    manager = SkillManager()
    manager.load([tmp_path])
    before = manager.history()
    bad = tmp_path / "bad.yaml"
    bad.write_text("name: [broken", encoding="utf-8")
    good.write_text("name: sample\ntrigger: {keywords: [hit]}\nsystem_prompt: second", encoding="utf-8")
    with pytest.raises(ValueError):
        manager.reload()
    assert manager.history() == before
    assert manager.match("hit")[0].skill.system_prompt == "first"
    assert len(discover_skills([tmp_path])) == 1  # Service compatibility: safe skip.
    bad.unlink()
    good.unlink()
    assert manager.reload() == 0  # Valid deletion differs from corrupt files.
    assert manager.list_all() == []


def test_error_logs_and_metadata_exclude_content_and_host_path(tmp_path: Path, caplog) -> None:
    sensitive = "SYNTHETIC_PRIVATE_CONTENT"
    source = str(tmp_path / sensitive)
    manager = SkillManager()
    manager.register(load_skill_from_yaml(f"name: sample\nsystem_prompt: {sensitive}", source))
    manager.activate([SkillMatch(manager.list_all()[0], 0.6)])
    (tmp_path / "bad.yaml").write_text(f"name: [{sensitive}", encoding="utf-8")
    with pytest.raises(ValueError):
        manager.load([tmp_path])
    assert sensitive not in caplog.text
    assert str(tmp_path) not in caplog.text
    assert sensitive not in str(manager.history())


def test_missing_directory_and_invalid_activation_preserve_state(tmp_path: Path) -> None:
    manager = SkillManager()
    manager.register(manifest())
    before = manager.history()
    with pytest.raises(ValueError):
        manager.load([tmp_path / "missing"])
    with pytest.raises(ValueError):
        manager.activate([SkillMatch(manifest(), float("nan"))])
    assert manager.history() == before
    assert manager.match("hit")


def test_extreme_match_confidence_is_safely_rejected() -> None:
    with pytest.raises(ValueError, match="^skill_invalid_match_confidence$"):
        SkillManager().activate([SkillMatch(manifest(), 10**400)])

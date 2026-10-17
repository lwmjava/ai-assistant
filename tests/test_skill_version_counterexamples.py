"""Independent entry counterexamples for approved skill version semantics."""

import json
import logging
from copy import deepcopy

import pytest

from app.agents.skills import manager as manager_module
from app.agents.skills.base import SkillManifest, SkillTrigger, TriggerType
from app.agents.skills.manager import SkillManager
from app.agents.skills.versioning import validate_release_ledger


def _yaml(prompt="original", version="1.0"):
    return (
        f'name: independent\nversion: "{version}"\n'
        'trigger:\n  type: always\n'
        f'system_prompt: {json.dumps(prompt)}\n'
    )


def test_independent_stale_snapshot_is_bound_to_injected_content(tmp_path):
    file = tmp_path / "skill.yaml"
    file.write_text(_yaml(), encoding="utf-8")
    manager = SkillManager()
    manager.load([tmp_path])
    old_match = manager.match("hello")
    old_version = old_match[0].skill.effective_version
    file.write_text(_yaml("replacement"), encoding="utf-8")
    manager.reload()
    old_context = manager.activate(old_match)
    new_context = manager.activate(manager.match("hello"))
    assert "original" in manager.inject_prompt(old_context, "base")
    assert "replacement" in manager.inject_prompt(new_context, "base")
    assert old_context.versions[0]["effective_version"] == old_version
    assert new_context.versions[0]["effective_version"] != old_version


def test_independent_external_mutation_does_not_modify_registered_content(tmp_path):
    file = tmp_path / "skill.yaml"
    file.write_text(_yaml(), encoding="utf-8")
    manager = SkillManager()
    manager.load([tmp_path])
    before = manager.history()
    exposed = manager.list_all()[0]
    exposed.system_prompt = "external mutation"
    exposed.trigger.type = TriggerType.KEYWORD
    exposed.trigger.keywords.append("never")
    manager.history()[0]["current"]["version"] = "99.0"
    context = manager.activate(manager.match("hello"))
    assert "original" in context.prompt_injection
    assert "external mutation" not in context.prompt_injection
    assert manager.history() == before


@pytest.mark.parametrize("fault", ["bad_file", "missing_directory"])
def test_independent_failed_reload_preserves_content_and_history(tmp_path, fault):
    directory = tmp_path / "source"
    directory.mkdir()
    file = directory / "skill.yaml"
    file.write_text(_yaml(), encoding="utf-8")
    manager = SkillManager()
    manager.load([directory])
    previous = manager.history()
    if fault == "bad_file":
        (directory / "bad.yaml").write_text("name: [broken", encoding="utf-8")
    else:
        directory.rename(tmp_path / "moved")
    with pytest.raises(ValueError):
        manager.reload()
    context = manager.activate(manager.match("hello"))
    assert "original" in context.prompt_injection
    assert manager.history() == previous


def test_independent_activation_log_does_not_include_body_or_source(caplog):
    manager = SkillManager()
    marker = "SYNTHETIC_PRIVATE_BODY_105"
    source = "X:/SYNTHETIC_PRIVATE_DIRECTORY_105/skill.yaml"
    manifest = SkillManifest(
        name="independent", system_prompt=marker, source_path=source,
        trigger=SkillTrigger(type=TriggerType.ALWAYS), origin="private",
    )
    with caplog.at_level(logging.DEBUG):
        manager.register(manifest)
        context = manager.activate(manager.match("hello"))
    assert marker in context.prompt_injection
    assert marker not in caplog.text
    assert source not in caplog.text
    assert marker not in json.dumps(manager.history())


def test_independent_reused_release_version_and_rewritten_history_rejected(tmp_path):
    file = tmp_path / "skill.yaml"
    file.write_text(_yaml(), encoding="utf-8")
    manager = SkillManager()
    manager.load([tmp_path])
    first = manager.get("independent")
    ledger = {"schema_version": 1, "skills": {"independent": [
        {"version": "1.0", "content_revision": first.content_revision, "summary": "initial"},
    ]}}
    path = tmp_path / "versions.json"
    path.write_text(json.dumps(ledger), encoding="utf-8")
    validate_release_ledger(tmp_path, path)
    previous = deepcopy(ledger)
    file.write_text(_yaml("changed"), encoding="utf-8")
    manager.reload()
    revision = manager.get("independent").content_revision
    ledger["skills"]["independent"].append(
        {"version": "1.0", "content_revision": revision, "summary": "changed"},
    )
    path.write_text(json.dumps(ledger), encoding="utf-8")
    with pytest.raises(ValueError, match="version_order"):
        validate_release_ledger(tmp_path, path, previous)
    ledger["skills"]["independent"] = [
        {"version": "1.0", "content_revision": revision, "summary": "rewrite"},
    ]
    path.write_text(json.dumps(ledger), encoding="utf-8")
    with pytest.raises(ValueError, match="history_rewritten"):
        validate_release_ledger(tmp_path, path, previous)


def test_independent_singleton_failed_initial_load_can_retry_real_directory(tmp_path, monkeypatch):
    file = tmp_path / "skill.yaml"
    file.write_text("name: [broken", encoding="utf-8")
    monkeypatch.setattr(manager_module, "_BUILTIN_DIR", tmp_path)
    manager_module.reset_skill_manager()
    try:
        with pytest.raises(ValueError):
            manager_module.get_skill_manager()
        file.write_text(_yaml(), encoding="utf-8")
        retried = manager_module.get_skill_manager()
        assert "original" in retried.activate(retried.match("hello")).prompt_injection
    finally:
        manager_module.reset_skill_manager()

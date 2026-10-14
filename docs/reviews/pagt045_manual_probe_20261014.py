"""Fresh authorized acceptance probes; synthetic inputs, no production changes."""
import copy
import json
import shutil
from pathlib import Path

import pytest
import yaml
from sqlmodel import Session

from app.agents.skills.loader import load_skill_from_yaml
from app.agents.skills.manager import SkillManager
from app.agents.skills.versioning import validate_release_ledger
from app.models.skill import Skill
from tests.test_plan_entry_acceptance import checkpoint
from tests.test_plan_entry_acceptance import entry as entry_fixture
from tests.test_skill_version_entry import _body, _systems
from tests.test_skill_version_entry import actors as actors_fixture

entry = entry_fixture
actors = actors_fixture


@pytest.mark.parametrize("fault", ["unknown_last_tool", "wrong_last_parameter", "backward_dependency"])
@pytest.mark.parametrize("stream", [False, True])
def test_fresh_plan_rejects_whole_invalid_plan_before_first_effect(entry, fault, stream):
    client, db, _, provider, calls, _, _ = entry
    tail = provider.plan["steps"][-1]
    if fault == "unknown_last_tool":
        tail["tool"] = "not_registered_manual"
    elif fault == "wrong_last_parameter":
        tail["arguments"]["value"] = "3"
    else:
        provider.plan["steps"][0]["depends_on"] = [tail["id"]]
    response = client.post("/api/chat/stream" if stream else "/api/chat",
                           json={"message": "execute dependent manual task"})
    assert response.status_code == 200
    assert calls == [], "Valid prefix must not run before invalid whole-plan validation"
    status, snapshot, _, _ = checkpoint(db)
    assert status == "error"
    assert snapshot["plan_cursor"] == 0
    if stream:
        assert "event: error" in response.text


def test_fresh_http_skill_keyword_change_is_committed_and_next_chat_uses_only_new_trigger(actors):
    client, _, headers, provider = actors
    auth = headers["version-ada"]
    initial = _body("MANUAL_INITIAL_CONTENT")
    created = client.post("/api/skills", headers=auth, json=initial)
    assert created.status_code == 201
    skill_id = created.json()["id"]
    before = client.get(f"/api/skills/{skill_id}", headers=auth).json()
    provider.calls.clear()
    first = client.post("/api/chat", headers=auth, json={"message": "回归口令"})
    assert first.status_code == 200 and "MANUAL_INITIAL_CONTENT" in _systems(provider)
    changed = _body("MANUAL_NEW_CONTENT") | {"keywords": ["人工新口令"]}
    updated = client.patch(f"/api/skills/{skill_id}", headers=auth, json=changed)
    assert updated.status_code == 200
    from app.core.database import engine
    with Session(engine) as session:
        saved = session.get(Skill, skill_id)
        assert saved is not None and saved.system_prompt == "MANUAL_NEW_CONTENT"
    after = client.get(f"/api/skills/{skill_id}", headers=auth).json()
    assert after["version"] == before["version"] == "1.0", "Approved declared version compatibility"
    provider.calls.clear()
    old_trigger = client.post("/api/chat", headers=auth, json={"message": "回归口令"})
    assert old_trigger.status_code == 200 and "same-skill" not in old_trigger.json()["skill_names"]
    assert "MANUAL_NEW_CONTENT" not in _systems(provider)
    provider.calls.clear()
    new_trigger = client.post("/api/chat", headers=auth, json={"message": "人工新口令"})
    assert new_trigger.status_code == 200 and new_trigger.json()["skill_names"] == ["same-skill"]
    assert "MANUAL_NEW_CONTENT" in _systems(provider) and "MANUAL_INITIAL_CONTENT" not in _systems(provider)


def test_fresh_builtin_release_upgrade_rejects_rewritten_previous_history(tmp_path):
    builtin = Path("app/agents/skills/builtin")
    for source in builtin.iterdir():
        if source.suffix in {".yaml", ".json"}:
            shutil.copyfile(source, tmp_path / source.name)
    ledger_path = tmp_path / "releases.json"
    previous = json.loads(ledger_path.read_text(encoding="utf-8"))
    manager = SkillManager()
    manager.load([tmp_path])
    original = manager.activate(manager.match("请翻译原文"))
    path = tmp_path / "translator.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["system_prompt"] += "\n人工验收：保留段落编号。"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    with pytest.raises(ValueError, match="current_mismatch"):
        validate_release_ledger(tmp_path, ledger_path, previous)
    assert manager.activate(manager.match("请翻译原文")).versions == original.versions
    data["version"] = "1.1"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    new = load_skill_from_yaml(path.read_text(encoding="utf-8"))
    updated = copy.deepcopy(previous)
    updated["skills"]["translator"].append({"version": "1.1", "content_revision": new.content_revision,
                                             "summary": "Manual isolated paragraph numbering regression."})
    ledger_path.write_text(json.dumps(updated), encoding="utf-8")
    validate_release_ledger(tmp_path, ledger_path, previous)
    fresh = SkillManager()
    fresh.load([tmp_path])
    context = fresh.activate(fresh.match("请翻译原文"))
    assert "保留段落编号" in fresh.inject_prompt(context, "BASE")
    assert context.versions[0]["effective_version"] != original.versions[0]["effective_version"]
    tampered = copy.deepcopy(updated)
    tampered["skills"]["translator"][0]["summary"] = "rewritten history"
    ledger_path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(ValueError, match="history_rewritten"):
        validate_release_ledger(tmp_path, ledger_path, previous)

import asyncio
import json

import pytest

from app.agents.skills.base import SkillManifest, SkillTrigger
from app.agents.skills.manager import SkillManager


class SemanticProvider:
    model = "synthetic-semantic"

    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    async def chat(self, messages, options=None):
        self.calls.append((messages, options))
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def manager():
    mgr = SkillManager()
    mgr.register(SkillManifest(name="review", description="Assess maintainability of source code",
                               trigger=SkillTrigger(keywords=["review"])))
    return mgr


@pytest.mark.asyncio
async def test_semantic_matches_without_keyword_and_preserves_identity():
    mgr = manager()
    text = "Please look for maintainability problems in this function"
    assert mgr.match(text) == []
    provider = SemanticProvider('{"name":"review","confidence":0.9}')
    matches = await mgr.match_semantic(text, provider)
    assert [m.skill.name for m in matches] == ["review"]
    assert matches[0].trigger_reason == "semantic_match"
    assert mgr.activate(matches).versions[0]["effective_version"] == mgr.get("review").effective_version
    assert len(provider.calls) == 1


@pytest.mark.asyncio
async def test_keyword_match_does_not_call_semantic_provider():
    provider = SemanticProvider(RuntimeError("must not call"))
    matches = await manager().match_semantic("review", provider)
    assert matches[0].trigger_reason.startswith("keyword_match")
    assert provider.calls == []


@pytest.mark.parametrize("reply", [
    '{"name":"foreign","confidence":0.9}',
    '{"name":"review","confidence":NaN}',
    '{"name":"review","confidence":Infinity}',
    '{"name":"review","confidence":true}',
    '{"name":"review","confidence":-0.1}',
    '{"name":"review","confidence":1.1}',
    '{"name":"review","confidence":0.1}',
    '{"name":"review","confidence":0.9,"extra":"x"}',
    '{"name":"review","name":"review","confidence":0.9}',
    'null', '[]', 'not json', 'x' * 1025,
])
@pytest.mark.asyncio
async def test_invalid_semantic_proposal_never_activates(reply):
    mgr = manager()
    assert await mgr.match_semantic("check quality", SemanticProvider(reply)) == []
    assert mgr.history() == manager().history()


@pytest.mark.asyncio
async def test_remote_failure_and_timeout_preserve_registry():
    mgr = manager()
    before = mgr.history()
    for failure in (RuntimeError("sensitive-detail"), TimeoutError()):
        assert await mgr.match_semantic("check quality", SemanticProvider(failure)) == []
    assert mgr.history() == before


@pytest.mark.asyncio
async def test_semantic_payload_contains_metadata_only():
    mgr = manager()
    skill = mgr.get("review")
    skill.system_prompt = "synthetic-private-body"
    mgr.register(skill)
    provider = SemanticProvider('{"name":"review","confidence":0.9}')
    await mgr.match_semantic("check quality", provider)
    messages, options = provider.calls[0]
    assert "synthetic-private-body" not in json.dumps([m.content for m in messages])
    assert options.max_tokens == 128 and options.timeout == 5 and options.temperature == 0


@pytest.mark.asyncio
async def test_disabled_model_mismatch_and_large_input_do_not_call_model():
    for enabled, models, text in [(False, [], "check quality"),
                                  (True, ["another-model"], "check quality"),
                                  (True, [], "x" * 4097)]:
        mgr = manager()
        skill = mgr.get("review")
        skill.enabled = enabled
        skill.models = models
        mgr.register(skill)
        provider = SemanticProvider('{"name":"review","confidence":0.9}')
        assert await mgr.match_semantic(text, provider) == []
        assert provider.calls == []


@pytest.mark.asyncio
async def test_cancel_propagates():
    class Cancelled(SemanticProvider):
        async def chat(self, messages, options=None):
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await manager().match_semantic("check quality", Cancelled(""))


@pytest.mark.asyncio
async def test_too_many_candidates_do_not_call_model():
    mgr = manager()
    for i in range(32):
        mgr.register(SkillManifest(name=f"candidate-{i}", description="A different operation"))
    provider = SemanticProvider("null")
    assert await mgr.match_semantic("check quality", provider) == []
    assert provider.calls == []


@pytest.mark.asyncio
async def test_huge_confidence_and_invalid_threshold_do_not_activate():
    provider = SemanticProvider(json.dumps({"name": "review", "confidence": 10**400}))
    assert await manager().match_semantic("check quality", provider) == []
    provider.calls.clear()
    assert await manager().match_semantic("check quality", provider, min_confidence=float("nan")) == []
    assert provider.calls == []


@pytest.mark.asyncio
async def test_encoded_payload_limit_prevents_request():
    mgr = SkillManager()
    for i in range(32):
        mgr.register(SkillManifest(name=f"candidate-{i}", description="x" * 512))
    provider = SemanticProvider("null")
    assert await mgr.match_semantic("\x01" * 4096, provider) == []
    assert provider.calls == []

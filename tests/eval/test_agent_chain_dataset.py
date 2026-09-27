"""Agent 链路评测数据的确定性校验。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from tests.eval.validation import scan_secrets

ROOT = Path(__file__).resolve().parents[2]
CASES_PATH = ROOT / "evals" / "datasets" / "agent-chain-v0.1" / "cases.json"
INDEX_PATH = ROOT / "evals" / "datasets" / "agent-chain-v0.1" / "index.json"


class HistoryTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: str
    content: str = Field(min_length=1, max_length=2000)


class Provenance(BaseModel):
    model_config = ConfigDict(extra="forbid")
    generator: str
    generated_at: str
    review_status: str


class AgentChainCase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_id: str = Field(pattern=r"^agent-[0-9]{3}$")
    dataset_version: str
    tier: str
    synthetic: bool
    history: list[HistoryTurn] = Field(max_length=6)
    user_input: str = Field(min_length=1, max_length=500)
    expected_route: str
    expected_tools: list[str] = Field(default_factory=list)
    critical_followup: bool
    provenance: Provenance
    tags: list[str] = Field(min_length=1)


def test_agent_chain_dataset_matches_index_and_has_no_secrets() -> None:
    cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    index = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    parsed: list[AgentChainCase] = []
    for raw in cases:
        try:
            parsed.append(AgentChainCase.model_validate(raw))
        except ValidationError as exc:
            pytest.fail(str(exc))
    assert index["gold_status"] == "not_gold"
    assert index["counts"]["total"] == len(parsed) == 10
    assert index["counts"]["critical_followup"] == sum(case.critical_followup for case in parsed)
    assert all(case.synthetic and case.tier == "silver" for case in parsed)
    assert all(case.provenance.review_status == "silver" for case in parsed)
    blob = CASES_PATH.read_text(encoding="utf-8")
    assert scan_secrets(blob) == []


def test_preflight_prompt_hides_expected_fields() -> None:
    from app.agents.pipeline import build_preflight_messages

    cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    for case in cases:
        messages = build_preflight_messages(case["history"], case["user_input"])
        blob = "\n".join(message.content for message in messages)
        assert "expected_route" not in blob
        assert "expected_tools" not in blob
        assert "critical_followup" not in blob
        assert case["user_input"] in blob


def test_tool_choice_prompt_hides_expected_fields_and_skips_real_tools() -> None:
    import importlib.util

    from app.agents.tools.builtin import code_sandbox

    spec = importlib.util.spec_from_file_location(
        "run_agent_chain_eval_tools",
        ROOT / "scripts" / "run_agent_chain_eval.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))

    async def fake_sandbox(arguments):
        raise AssertionError("评测不应执行沙箱")

    original = code_sandbox
    try:
        import app.agents.tools.builtin as builtin

        builtin.code_sandbox = fake_sandbox
        names, _raws = __import__("asyncio").run(module.predict_tools(module.SmokeIntentModel(), cases))
    finally:
        builtin.code_sandbox = original
    assert module.SIDE_EFFECTS == {"sandbox": 0, "mcp": 0, "network": 0, "rag": 0, "database": 0}
    chosen = {case["case_id"]: tools for case, tools in zip(cases, names, strict=True)}
    assert chosen["agent-003"] == ["code_sandbox"]
    assert chosen["agent-004"] == ["code_sandbox"]
    assert chosen["agent-001"] == []


def test_eval_report_refuses_overwrite(tmp_path: Path) -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "run_agent_chain_eval",
        ROOT / "scripts" / "run_agent_chain_eval.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    target = tmp_path / "agent-chain-v0.1-smoke.json"
    module.write_report(target, {"mode": "smoke"})
    with pytest.raises(FileExistsError):
        module.write_report(target, {"mode": "smoke"})

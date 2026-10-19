"""One request-local retrieval, verified extraction and existing calculator execution."""

from __future__ import annotations

import ast
import asyncio
import json
import math
import re
import time
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, localcontext
from uuid import uuid4

from app.agents.pipeline import AgentEvent, AgentState, node_event, tool_event, tool_fingerprint
from app.agents.prompts import SYSTEM_COMBINED_EXTRACT
from app.agents.route import ChatRoute
from app.agents.skills.base import SkillBindingRejected, SkillRuntime
from app.agents.supervisor_tools import bounded_integer, eligible_tool
from app.agents.tools.base import ToolCall, ToolRegistry
from app.agents.tools.executor import ToolExecutor
from app.agents.tools.outcome import ToolOutput
from app.core import log_context
from app.core.config import settings
from app.core.response_cache import bind_cache_scope
from app.debug.trace import AgentTrace
from app.llm.base import ChatMessage, ChatRole, LLMOptions, LLMProvider
from app.rag.retrieval_status import RetrievalOutcome, RetrievalStatus
from app.rag.retriever import assemble_retrieval
from app.rag.vectorstore.base import ChunkResult

_UNITS = {
    "CNY": (1, 0, 0),
    "person": (0, 1, 0),
    "day": (0, 0, 1),
    "CNY/person": (1, -1, 0),
    "CNY/day": (1, 0, -1),
    "CNY/person/day": (1, -1, -1),
}
_NUMBER = re.compile(r"(?<![\d.])[0-9]{1,12}(?:\.[0-9]{1,4})?(?![\d.])")
_RESULT = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")
_CENT = Decimal("0.01")
_LABEL = re.compile(
    r"(?:住宿|餐补|伙食|补助|费用|金额|人数|天数|标准|出差|第一项|第二项|基础|增量|参与|辅助|人民币|"
    r"知识库|差旅|报销|计算|资料|政策|规则|根据|按|为|共|计|是)+"
)
_CLAUSE_SEPARATOR = re.compile(r"[；;。！？]")
_HEADING = re.compile(r"(?:知识库|差旅|报销|资料|政策|规则|标准)+")
_QUANTITY = re.compile(
    r"(?P<rate>每人每日|每人每天|每日每人|每天每人|每人|每日|每天)?"
    r"(?P<number>[0-9]{1,12}(?:\.[0-9]{1,4})?)(?P<suffix>元/人/天|元/人|元/天|元|人|天)"
)


@dataclass(frozen=True)
class VerifiedFact:
    id: str
    source: str
    chunk_id: str | None
    quote: str
    value: Decimal
    unit: str


@dataclass(frozen=True)
class ValidatedExtraction:
    expression: str
    expected: Decimal
    facts: tuple[VerifiedFact, ...]


def _object(pairs: list[tuple[str, object]]) -> dict:
    value: dict = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate_key")
        value[key] = item
    return value


def _bad_number(value: str) -> object:
    raise ValueError("invalid_number")


def _clauses(text: str) -> list[tuple[int, int]]:
    bounds, start = [], 0
    for separator in _CLAUSE_SEPARATOR.finditer(text):
        segment = text[start : separator.start()]
        normalized = re.sub(r"[\s:：,，]", "", segment)
        # Only complete numeric clauses or explicitly supported headings close a
        # source scope. An unresolved qualifier remains attached across punctuation.
        # Consequently “每晚。300元” cannot launder a rate into a total amount.
        if re.search(r"[0-9]", normalized) or not normalized or _HEADING.fullmatch(normalized):
            bounds.append((start, separator.start()))
            start = separator.end()
    bounds.append((start, len(text)))
    return bounds


def _quantities(text: str) -> list[tuple[int, int, Decimal, str]]:
    """Parse an entire supported source clause, never just a model-chosen substring."""
    if re.search(r"[0-9]\s*[,，:：]\s*[0-9]", text):
        raise ValueError("unsupported_numeric_separator")
    indices = [i for i, char in enumerate(text) if not char.isspace() and char not in ":：,，"]
    normalized = "".join(text[i] for i in indices)
    label = _LABEL.match(normalized)
    cursor = label.end() if label else 0
    values: list[tuple[int, int, Decimal, str]] = []
    while cursor < len(normalized):
        match = _QUANTITY.match(normalized, cursor)
        if match is None:
            raise ValueError("unsupported_source_clause")
        rate, suffix = match["rate"] or "", match["suffix"]
        if rate:
            if suffix != "元":
                raise ValueError("ambiguous_source_unit")
            unit = (
                "CNY/person/day"
                if rate in {"每人每日", "每人每天", "每日每人", "每天每人"}
                else "CNY/person"
                if rate == "每人"
                else "CNY/day"
            )
        else:
            unit = {
                "元": "CNY",
                "人": "person",
                "天": "day",
                "元/人": "CNY/person",
                "元/天": "CNY/day",
                "元/人/天": "CNY/person/day",
            }[suffix]
        number = Decimal(match["number"])
        if number > Decimal("1e9") or (
            unit in {"person", "day"} and (number <= 0 or number != number.to_integral_value())
        ):
            raise ValueError("number_out_of_range")
        values.append((indices[match.start("number")], indices[match.end("number") - 1] + 1, number, unit))
        cursor = match.end()
    if not values or (len(values) > 1 and [item[3] for item in values] not in [["person", "day"], ["day", "person"]]):
        raise ValueError("ambiguous_source_clause")
    return values


def _source_number(text: str, start: int, end: int, quote: str, unit: str) -> Decimal:
    if not isinstance(quote, str) or not 1 <= len(quote) <= 256:
        raise ValueError("invalid_quote")
    quoted = _quantities(quote)
    if len(quoted) != 1 or quoted[0][3] != unit:
        raise ValueError("ambiguous_unit")
    qs, qe, number, _ = quoted[0]
    for left, right in _clauses(text):
        if left <= start < end <= right:
            parsed = _quantities(text[left:right])
            if (start + qs - left, start + qe - left, number, unit) in parsed:
                return number
            break
    raise ValueError("source_number_mismatch")


def validate_extraction(raw: str, user_text: str, selected: list[ChunkResult]) -> ValidatedExtraction:
    """Verify exact current evidence, decimal lexemes and dimensioned arithmetic."""
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > 16384:
        raise ValueError("extraction_too_large")
    # Bound parser recursion before JSON/AST parsing, including adversarial nested containers.
    if raw.count("{") + raw.count("[") > 64:
        raise ValueError("extraction_too_deep")
    try:
        data = json.loads(raw, object_pairs_hook=_object, parse_constant=_bad_number)
    except (json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("invalid_json") from exc
    if (
        not isinstance(data, dict)
        or set(data) != {"schema_version", "facts", "expression"}
        or type(data["schema_version"]) is not int
        or data["schema_version"] != 1
        or not isinstance(data["facts"], list)
        or not 1 <= len(data["facts"]) <= 16
    ):
        raise ValueError("invalid_schema")
    sources = {hit.id: hit.content for hit in selected}
    if len(sources) != len(selected):
        raise ValueError("ambiguous_chunks")
    standards: dict[tuple[str, str], Decimal] = {}
    for hit in selected:
        for left, right in _clauses(hit.content):
            clause = hit.content[left:right]
            normalized = re.sub(r"[\s:：,，]", "", clause)
            label = _LABEL.match(normalized)
            categories = set(re.findall(r"住宿|餐补|伙食|补助", label.group() if label else ""))
            if len(categories) > 1:
                raise ValueError("ambiguous_policy_category")
            if categories and re.search(r"[0-9]", clause):
                for _, _, number, unit in _quantities(clause):
                    key = (next(iter(categories)), unit)
                    if key in standards and standards[key] != number:
                        raise ValueError("conflicting_standard")
                    standards[key] = number
    facts: dict[str, VerifiedFact] = {}
    locations: set[tuple] = set()
    for entry in data["facts"]:
        if not isinstance(entry, dict) or set(entry) != {
            "id",
            "source",
            "chunk_id",
            "start",
            "end",
            "quote",
            "value",
            "unit",
        }:
            raise ValueError("invalid_fact")
        name, source, chunk = entry["id"], entry["source"], entry["chunk_id"]
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,31}", name) or name in facts:
            raise ValueError("invalid_id")
        if source == "user" and chunk is None:
            text = user_text
        elif source == "chunk" and isinstance(chunk, str) and chunk in sources:
            text = sources[chunk]
        else:
            raise ValueError("invalid_source")
        start, end = entry["start"], entry["end"]
        if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(text):
            raise ValueError("invalid_offset")
        quote = entry["quote"]
        if text[start:end] != quote or (start and text[start - 1] in "0123456789.+-−"):
            raise ValueError("source_mismatch")
        prefix = text[max(0, start - 24) : start]
        if re.search(r"(?:(?:每人|每日|每天|人民币|美元|美金|欧元)\s*|\d[,，]\s*|\d[eE][+-]?)$", prefix) or re.match(
            r"\s*(?:/\s*人|/\s*天|美元|美金|欧元|%|％)", text[end:]
        ):
            raise ValueError("clipped_unit")
        matches = list(_NUMBER.finditer(quote)) if isinstance(quote, str) else []
        if len(matches) != 1:
            raise ValueError("ambiguous_number")
        location = (source, chunk, start + matches[0].start(), start + matches[0].end())
        if location in locations:
            raise ValueError("duplicate_fact")
        locations.add(location)
        unit = entry["unit"]
        if not isinstance(unit, str) or unit not in _UNITS:
            raise ValueError("invalid_unit")
        number = _source_number(text, start, end, quote, unit)
        supplied = entry["value"]
        if (
            type(supplied) not in {int, float}
            or (isinstance(supplied, float) and not math.isfinite(supplied))
            or Decimal(str(supplied)) != number
        ):
            raise ValueError("value_mismatch")
        facts[name] = VerifiedFact(name, source, chunk, quote, number, unit)
    expression = data["expression"]
    if not isinstance(expression, str) or not 1 <= len(expression) <= 1024:
        raise ValueError("invalid_expression")
    try:
        tree = ast.parse(expression, mode="eval")
    except (SyntaxError, RecursionError) as exc:
        raise ValueError("invalid_expression") from exc
    if sum(1 for _ in ast.walk(tree)) > 64:
        raise ValueError("expression_too_large")
    used: set[str] = set()

    def evaluate(node: ast.AST, depth: int = 0) -> tuple[Decimal, tuple[int, ...], str]:
        if depth > 8:
            raise ValueError("expression_too_deep")
        if isinstance(node, ast.Name) and node.id in facts:
            fact = facts[node.id]
            used.add(node.id)
            return fact.value, _UNITS[fact.unit], str(fact.value)
        if not isinstance(node, ast.BinOp) or not isinstance(node.op, ast.Add | ast.Sub | ast.Mult | ast.Div):
            raise ValueError("unsupported_expression")
        left, ld, le = evaluate(node.left, depth + 1)
        right, rd, rexp = evaluate(node.right, depth + 1)
        if isinstance(node.op, ast.Add | ast.Sub):
            if ld != rd:
                raise ValueError("unit_conflict")
            value = left + right if isinstance(node.op, ast.Add) else left - right
            dimension, symbol = ld, "+" if isinstance(node.op, ast.Add) else "-"
        elif isinstance(node.op, ast.Mult):
            value, dimension, symbol = left * right, tuple(a + b for a, b in zip(ld, rd, strict=True)), "*"
        else:
            if right == 0:
                raise ValueError("division_by_zero")
            value, dimension, symbol = left / right, tuple(a - b for a, b in zip(ld, rd, strict=True)), "/"
        if not value.is_finite() or abs(value) > Decimal("1e12"):
            raise ValueError("intermediate_out_of_range")
        return value, dimension, f"({le}{symbol}{rexp})"

    with localcontext() as context:
        context.prec = 28
        value, dimension, numeric = evaluate(tree.body)
    if (
        used != set(facts)
        or not any(f.source == "chunk" for f in facts.values())
        or dimension != _UNITS["CNY"]
        or value < 0
    ):
        raise ValueError("incomplete_expression")
    return ValidatedExtraction(numeric, value, tuple(facts.values()))


async def iter_combined_tools(
    llm: LLMProvider,
    options: LLMOptions,
    state: AgentState,
    route: ChatRoute,
    retriever,
    tools: ToolRegistry | None,
    *,
    authority_check: Callable[[], None],
    skill_runtime: SkillRuntime | None = None,
    execution_principal: tuple[str | None, str | None] | None = None,
    max_tool_rounds: int | None = None,
    trace: AgentTrace | None = None,
) -> AsyncGenerator[AgentEvent, None]:
    """Fixed composition; evidence and model output never confer tool authority."""

    def check() -> None:
        authority_check()
        if skill_runtime is not None:
            skill_runtime.check()
            if "calculator" not in (skill_runtime.allowed(frozenset({"calculator"})) or frozenset()):
                raise PermissionError("skill_denied")

    def finish(text: str) -> None:
        state.answer = state.draft = text
        if not text.startswith("依据本轮"):
            state.error = "CombinedExecutionRejected"
            state.error_code = "tool_error"

    current: str | None = None
    status = "not_called"
    try:
        if getattr(state, "_combined_started", False):
            raise ValueError("already_started")
        setattr(state, "_combined_started", True)
        state.answer = state.draft = ""
        state.tool_results.clear()
        state.retrieval_status = ""
        if retriever is not None:
            retriever.last_selected = []
        check()
        budget = bounded_integer(settings.AGENT_MAX_TOOL_ROUNDS if max_tool_rounds is None else max_tool_rounds, 100)
        if state.tool_budget_exhausted or state.tool_rounds_used >= budget:
            state.tool_budget_exhausted = True
            finish("【工具未调用】本轮工具预算已耗尽，未执行知识库计算。")
        elif route.tool_names != frozenset({"calculator"}):
            finish("【工具未调用】当前仅支持知识库与计算器组合，其他工具组合暂不支持。")
        elif (
            tools is None
            or (tool := tools.get("calculator")) is None
            or not eligible_tool(tool)
            or tool.contract is None
            or tool.contract.risk.value != "L0"
        ):
            finish("【工具未调用】计算器缺少有效的只读工具契约。")
        elif retriever is None:
            state.retrieval_status = "unavailable"
            finish("【工具未调用】知识库检索服务不可用，未取得计算依据。")
        else:
            timeout = options.timeout if options.timeout is not None else settings.LLM_TIMEOUT
            if type(timeout) not in {float, int} or not math.isfinite(timeout) or timeout <= 0:
                raise ValueError("invalid_timeout")
            current = "retrieval"
            yield node_event("node.started", current)
            yield AgentEvent("stage", "检索")
            query = state.retrieval_query if state.retrieval_query is not None else state.user_input
            check()
            try:
                async with asyncio.timeout(timeout):
                    snippet = await retriever.retrieve(query, "")
            except Exception:
                retriever.last_selected = []
                state.retrieval_status = "unavailable"
                raise
            check()
            outcome = getattr(retriever, "last_outcome", None)
            valid = isinstance(outcome, RetrievalOutcome) and outcome.status is RetrievalStatus.OK
            payload = assemble_retrieval(retriever, snippet, trust_structured=valid)
            state.context = payload.text
            state.retrieval_status = outcome.status.value if isinstance(outcome, RetrievalOutcome) else "unavailable"
            yield node_event("node.finished", current, status="done")
            current = None
            if not valid or not payload.selected:
                reason = {
                    "no_hit": "知识库未命中",
                    "below_threshold": "知识库资料低于可信阈值",
                    "unavailable": "知识库检索未完成",
                }.get(state.retrieval_status, "本轮没有完整选入可核验资料")
                finish(f"【工具未调用】{reason}，无法据此计算。")
            else:
                current = "extract"
                yield node_event("node.started", current)
                check()
                messages = [
                    ChatMessage(role=ChatRole.SYSTEM, content=SYSTEM_COMBINED_EXTRACT),
                    ChatMessage(
                        role=ChatRole.USER,
                        content=json.dumps(
                            {
                                "user_text": query,
                                "chunks": [{"id": h.id, "content": h.content} for h in payload.selected],
                            },
                            ensure_ascii=False,
                        ),
                    ),
                ]
                # This call cannot prove tenant/skill/content cache identity; only extraction bypasses.
                started = time.monotonic()
                model_status = "error"
                try:
                    with bind_cache_scope(lambda: None):
                        async with asyncio.timeout(timeout):
                            raw = await llm.chat(messages, options)
                    model_status = "ok"
                except (asyncio.CancelledError, GeneratorExit):
                    model_status = "cancelled"
                    raise
                finally:
                    if trace is not None:
                        model = getattr(llm, "model", "unknown")
                        safe_model = (
                            model
                            if isinstance(model, str) and re.fullmatch(r"[A-Za-z0-9_./:-]{1,128}", model)
                            else "unknown"
                        )
                        trace.llm_call(
                            safe_model,
                            latency_ms=(time.monotonic() - started) * 1000,
                            stage="combined.extract",
                            status=model_status,
                            cache="bypass",
                        )
                check()
                verified = validate_extraction(raw, query, payload.selected)
                referenced = {f.chunk_id for f in verified.facts if f.source == "chunk"}
                selected = [hit for hit in payload.selected if hit.id in referenced]
                retriever.last_selected = selected
                yield node_event("node.finished", current, status="done")
                current = "action"
                yield node_event("node.started", current)
                call = ToolCall("calculator", {"expression": verified.expression})
                fingerprint = tool_fingerprint(call.name, call.arguments)
                if fingerprint in state.executed_tool_fingerprints:
                    raise ValueError("duplicate_execution")
                call_id = uuid4().hex
                yield tool_event("tool.call", call.name, call_id=call_id)
                try:
                    check()
                except (PermissionError, SkillBindingRejected):
                    actor = execution_principal or (log_context.get_user_id(), log_context.get_tenant_id())
                    await tools._record_audit(
                        ToolCall("calculator", {}), actor[0], actor[1], "L0", False, "authority_changed", 0.0
                    )
                    raise
                state.executed_tool_fingerprints.add(fingerprint)
                state.tool_rounds_used += 1
                prior = (log_context.get_user_id(), log_context.get_tenant_id())
                if execution_principal is not None:
                    log_context.set_user(*execution_principal)
                try:
                    result = await ToolExecutor(tools, redact_errors=True, before_attempt=check).execute(call)
                finally:
                    if execution_principal is not None:
                        log_context.set_user(*prior)
                status = result.status
                if trace is not None:
                    trace.tool_call(
                        "calculator", status=status, round_no=state.tool_rounds_used, latency_ms=result.latency_ms
                    )
                state.tool_results.append(ToolOutput(result.observation, status=status))
                if skill_runtime is not None:
                    skill_runtime.record("combined", status, call.name)
                yield tool_event("tool.result", call.name, call_id=call_id, ok=status == "ok")
                yield node_event("node.finished", current, status="done" if status == "ok" else "failed")
                current = None
                if status != "ok":
                    finish(f"【工具执行失败】计算器状态：{status}。未生成计算金额。")
                else:
                    observed = result.observation.partition(" 结果=")[2]
                    if len(observed) > 128 or _RESULT.fullmatch(observed) is None:
                        raise ValueError("invalid_tool_result")
                    actual = Decimal(observed)
                    if (
                        not actual.is_finite()
                        or abs(actual) > Decimal("1e12")
                        or actual.quantize(_CENT, rounding=ROUND_HALF_UP)
                        != verified.expected.quantize(_CENT, rounding=ROUND_HALF_UP)
                    ):
                        raise ValueError("result_mismatch")
                    amount = format(actual.quantize(_CENT, rounding=ROUND_HALF_UP), ".2f")
                    units = {
                        "CNY": "元",
                        "person": "人",
                        "day": "天",
                        "CNY/person": "元/人",
                        "CNY/day": "元/天",
                        "CNY/person/day": "元/人/天",
                    }
                    facts_text = "；".join(f"{f.value} {units[f.unit]}" for f in verified.facts)
                    source_text = "、".join(
                        f"[资料 {i}] {json.dumps((h.source or h.id)[:160], ensure_ascii=False)}"
                        for i, h in enumerate(selected, 1)
                    )
                    finish(
                        f"依据本轮知识库资料，计算器实际执行成功。\n核验事实：{facts_text}。\n"
                        f"计算金额：{amount} 元。\n来源：{source_text}。"
                    )
        for index in range(0, len(state.answer), 24):
            yield AgentEvent("token", state.answer[index : index + 24])
        yield AgentEvent("done", state.answer)
    except (asyncio.CancelledError, GeneratorExit):
        raise
    except SkillBindingRejected:
        raise
    except Exception as exc:  # noqa: BLE001 -- controlled failures never expose model/data/exception prose
        if current is not None:
            yield node_event("node.finished", current, status="failed")
        state.error = type(exc).__name__
        prefix = "工具未调用" if status == "not_called" else "工具执行失败"
        reason = (
            "授权或技能限制已变化"
            if isinstance(exc, PermissionError)
            else "资料、参数、单位或实际结果无法通过核验，请补充明确的人数、天数与适用标准"
        )
        finish(f"【{prefix}】{reason}，本次未给出计算金额。")
        for index in range(0, len(state.answer), 24):
            yield AgentEvent("token", state.answer[index : index + 24])
        yield AgentEvent("done", state.answer)

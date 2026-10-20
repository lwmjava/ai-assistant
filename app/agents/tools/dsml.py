"""Bounded single-call DSML decoding; never executes or authorizes a tool."""

import json
import math
import re

MAX_DSML_BYTES = 131072
_MARKERS = ("｜｜DSML｜｜", "｜DSML｜")


def _object(pairs: list[tuple[str, object]]) -> dict:
    result: dict = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_parameter")
        result[key] = value
    return result


def _finite(value: object) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("nonfinite_parameter")
    if isinstance(value, dict):
        for item in value.values():
            _finite(item)
    elif isinstance(value, list):
        for item in value:
            _finite(item)


def decode_dsml(text: str) -> tuple[str, dict]:
    """Accept only a complete, unambiguous calls/invoke/parameter envelope.

    Raw string bodies preserve code, angle brackets and ampersands verbatim.
    This is deliberately not XML: no entities, DTD, escaping or external IO.
    """
    if len(text) > MAX_DSML_BYTES or len(text.encode("utf-8")) > MAX_DSML_BYTES:
        raise ValueError("oversized_call")
    marker = next((item for item in _MARKERS if f"<{item} calls>" in text), None)
    if marker is None:
        raise ValueError("invalid_envelope")
    prefix = re.escape(marker)
    match = re.fullmatch(
        rf'\s*<{prefix} calls>\s*<{prefix} invoke name="([A-Za-z_][A-Za-z0-9_.-]{{0,127}})">'
        rf'(.*?)</{prefix} invoke>\s*</{prefix} calls>\s*', text, re.DOTALL,
    )
    if match is None:
        raise ValueError("invalid_envelope")
    name, body = match.groups()
    pattern = re.compile(
        rf'\s*<{prefix} parameter name="([A-Za-z_][A-Za-z0-9_]{{0,127}})" '
        rf'string="(true|false)">(.*?)</{prefix} parameter>', re.DOTALL,
    )
    arguments: dict = {}
    while body.strip():
        parameter = pattern.match(body)
        if parameter is None or len(arguments) >= 64:
            raise ValueError("invalid_parameter")
        key, kind, value = parameter.groups()
        if key in arguments or "DSML" in value or "<tool_call" in value or "</tool_call" in value:
            raise ValueError("ambiguous_parameter")
        if kind == "false":
            value = json.loads(value, object_pairs_hook=_object)
            _finite(value)
        arguments[key] = value
        body = body[parameter.end():]
    return name, arguments

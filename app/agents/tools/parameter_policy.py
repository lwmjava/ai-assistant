"""Bound JSON arguments and validate declared tool parameter constraints."""

from __future__ import annotations

import inspect
import json
import math
from typing import Any, TypedDict

MAX_ARGUMENT_BYTES = 131072
MAX_ARGUMENT_DEPTH = 16
MAX_ARGUMENT_NODES = 4096
MAX_STRING_BYTES = 65536


class ParameterRejectedError(ValueError):
    """A fixed public reason without parameter values or host details."""

    def __init__(self, code: str = "invalid_arguments") -> None:
        self.code = code
        super().__init__(code)


class ParameterValidation(TypedDict):
    passed: bool
    errors: list[str]
    security_errors: list[str]
    strict: bool


def bounded_json(
    value: Any, *, maximum: int = MAX_ARGUMENT_BYTES, depth: int = MAX_ARGUMENT_DEPTH, nodes: int = MAX_ARGUMENT_NODES
) -> None:
    """Check an object graph iteratively before serialization or recursion."""
    stack = [(value, 0, False)]
    active: set[int] = set()
    count = size = 0
    while stack:
        item, level, leaving = stack.pop()
        if leaving:
            active.remove(id(item))
            continue
        count += 1
        if count > nodes or level > depth:
            raise ParameterRejectedError("parameter_structure_limit")
        kind = type(item)
        if kind in {dict, list}:
            if id(item) in active:
                raise ParameterRejectedError("parameter_cycle")
            active.add(id(item))
            stack.append((item, level, True))
            if len(item) > nodes:
                raise ParameterRejectedError("parameter_structure_limit")
            size += 2 + len(item)
            if kind is dict:
                for key, child in item.items():
                    if type(key) is not str:
                        raise ParameterRejectedError("parameter_not_json")
                    stack.append((key, level + 1, False))
                    stack.append((child, level + 1, False))
            else:
                stack.extend((child, level + 1, False) for child in item)
        elif kind is str:
            if len(item) > MAX_STRING_BYTES:
                raise ParameterRejectedError("parameter_size_limit")
            try:
                length = len(item.encode("utf-8"))
            except UnicodeError as exc:
                raise ParameterRejectedError("parameter_not_json") from exc
            if length > MAX_STRING_BYTES:
                raise ParameterRejectedError("parameter_size_limit")
            size += length + 2
        elif kind is int:
            if item.bit_length() > 4096:
                raise ParameterRejectedError("parameter_size_limit")
            size += len(str(item))
        elif kind is float:
            if not math.isfinite(item):
                raise ParameterRejectedError("parameter_not_json")
            size += len(str(item))
        elif kind is bool or item is None:
            size += 5
        else:
            raise ParameterRejectedError("parameter_not_json")
        if size > maximum:
            raise ParameterRejectedError("parameter_size_limit")
    if len(json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")) > maximum:
        raise ParameterRejectedError("parameter_size_limit")


def _is_type(value: Any, expected: str) -> bool:
    kinds = {"object": dict, "array": list, "string": str, "integer": int, "boolean": bool}
    if expected == "number":
        return type(value) in {int, float}
    if expected == "null":
        return value is None
    return expected in kinds and type(value) is kinds[expected]


def _declared_properties(schema: Any, root: dict) -> set[str]:
    stack = [schema]
    seen: set[int] = set()
    result: set[str] = set()
    while stack:
        current = stack.pop()
        if not isinstance(current, dict) or id(current) in seen:
            continue
        seen.add(id(current))
        if len(seen) > 8192:
            raise ParameterRejectedError("unsupported_schema")
        properties = current.get("properties", {})
        if isinstance(properties, dict):
            result.update(properties)
        reference = current.get("$ref")
        if isinstance(reference, str) and reference.startswith("#/"):
            resolved: Any = root
            for part in reference[2:].split("/"):
                resolved = (
                    resolved.get(part.replace("~1", "/").replace("~0", "~")) if isinstance(resolved, dict) else None
                )
            stack.append(resolved)
        for keyword in ("allOf", "anyOf", "oneOf"):
            branches = current.get(keyword, [])
            if isinstance(branches, list) and len(branches) <= 16:
                stack.extend(branches)
    return result


def _schema_errors(
    schema: Any, value: Any, root: dict, budget: list[int], level: int = 0, declared: set[str] | None = None
) -> tuple[list[str], list[str]]:
    budget[0] += 1
    if level > 32 or budget[0] > 8192:
        return ["unsupported_schema"], ["unsupported_schema"]
    if schema is True:
        return [], []
    if schema is False:
        return ["schema_denied"], ["schema_denied"]
    if not isinstance(schema, dict):
        return ["unsupported_schema"], ["unsupported_schema"]
    supported = {
        "type",
        "properties",
        "required",
        "additionalProperties",
        "items",
        "enum",
        "const",
        "minLength",
        "maxLength",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "minItems",
        "maxItems",
        "minProperties",
        "maxProperties",
        "allOf",
        "anyOf",
        "oneOf",
        "$ref",
        "$defs",
        "definitions",
        "$schema",
        "$id",
        "$comment",
        "title",
        "description",
        "default",
        "examples",
        "format",
        "readOnly",
        "writeOnly",
        "deprecated",
    }
    if set(schema) - supported:
        return ["unsupported_schema"], ["unsupported_schema"]
    errors: list[str] = []
    unsafe: list[str] = []

    def reject(code: str):
        errors.append(code)
        unsafe.append(code)

    declared = (declared or set()) | _declared_properties(schema, root)
    reference = schema.get("$ref")
    if reference is not None:
        if not isinstance(reference, str) or not reference.startswith("#/"):
            return ["unsupported_schema"], ["unsupported_schema"]
        resolved: Any = root
        for part in reference[2:].split("/"):
            key = part.replace("~1", "/").replace("~0", "~")
            if not isinstance(resolved, dict) or key not in resolved:
                return ["unsupported_schema"], ["unsupported_schema"]
            resolved = resolved[key]
        a, b = _schema_errors(resolved, value, root, budget, level + 1, declared)
        errors.extend(a)
        unsafe.extend(b)
    expected = schema.get("type")
    if expected is not None:
        types = expected if isinstance(expected, list) else [expected]
        if (
            not types
            or any(
                t not in {"object", "array", "string", "integer", "number", "boolean", "null"}
                for t in types
                if isinstance(t, str)
            )
            or any(not isinstance(t, str) for t in types)
        ):
            return ["unsupported_schema"], ["unsupported_schema"]
        if not any(_is_type(value, t) for t in types):
            mismatch = "type:" + "/".join(types)
            return errors + [mismatch], unsafe + ([mismatch] if level > 1 else [])
    for keyword in ("allOf", "anyOf", "oneOf"):
        if keyword in schema:
            branches = schema[keyword]
            if not isinstance(branches, list) or not 1 <= len(branches) <= 16:
                reject("unsupported_schema")
                continue
            outcomes = [_schema_errors(branch, value, root, budget, level + 1, declared) for branch in branches]
            successes = sum(not a for a, _ in outcomes)
            valid = (
                successes == len(branches)
                if keyword == "allOf"
                else successes > 0
                if keyword == "anyOf"
                else successes == 1
            )
            if not valid:
                reject("schema_combination")

    def equal(a: Any, b: Any) -> bool:
        return json.dumps(a, sort_keys=True, ensure_ascii=False) == json.dumps(b, sort_keys=True, ensure_ascii=False)

    if "enum" in schema and (not isinstance(schema["enum"], list) or not any(equal(value, x) for x in schema["enum"])):
        reject("schema_enum")
    if "const" in schema and not equal(value, schema["const"]):
        reject("schema_const")
    composed = any(key in schema for key in ("$ref", "allOf", "anyOf", "oneOf"))
    own_object = any(key in schema for key in ("properties", "required", "additionalProperties"))
    if isinstance(value, dict) and (not composed or own_object):
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        extra = schema.get("additionalProperties", False)
        if (
            not isinstance(properties, dict)
            or not isinstance(required, list)
            or any(not isinstance(x, str) for x in required)
        ):
            return ["unsupported_schema"], ["unsupported_schema"]
        if any(key not in value for key in required):
            errors.append("required_parameter")
            if level > 1:
                unsafe.append("required_parameter")
        for key, child in value.items():
            if key in properties:
                a, b = _schema_errors(properties[key], child, root, budget, level + 1)
                errors.extend(a)
                unsafe.extend(b)
            elif "additionalProperties" not in schema and key in declared:
                continue
            elif extra is not True:
                if isinstance(extra, dict):
                    a, b = _schema_errors(extra, child, root, budget, level + 1)
                    errors.extend(a)
                    unsafe.extend(b)
                else:
                    reject("unknown_parameter")
    if isinstance(value, list) and "items" in schema:
        for child in value:
            a, b = _schema_errors(schema["items"], child, root, budget, level + 1)
            errors.extend(a)
            unsafe.extend(b)
    checks: list[tuple[str, float, bool]] = []
    if isinstance(value, str):
        checks = [("minLength", len(value), False), ("maxLength", len(value), True)]
    elif isinstance(value, list):
        checks = [("minItems", len(value), False), ("maxItems", len(value), True)]
    elif isinstance(value, dict):
        checks = [("minProperties", len(value), False), ("maxProperties", len(value), True)]
    elif type(value) in {int, float}:
        checks = [("minimum", value, False), ("maximum", value, True)]
        for key, violated in (("exclusiveMinimum", lambda n: value <= n), ("exclusiveMaximum", lambda n: value >= n)):
            if key in schema:
                limit = schema[key]
                if type(limit) not in {int, float} or violated(limit):
                    reject("schema_range")
    for key, measure, upper in checks:
        if key in schema:
            limit = schema[key]
            if type(limit) not in {int, float} or (measure > limit if upper else measure < limit):
                reject("schema_range")
    return errors[:16], unsafe[:16]


def prepare_arguments(tool: Any, arguments: Any) -> tuple[dict, ParameterValidation]:
    """Return a detached bounded snapshot and value-free validation metadata."""
    strict = bool(tool.contract and tool.contract.strict_validation)
    result: ParameterValidation = {"passed": False, "errors": [], "security_errors": [], "strict": strict}
    try:
        if type(arguments) is not dict:
            raise ParameterRejectedError("parameter_not_object")
        bounded_json(arguments)
        schema = tool.parameters or {}
        bounded_json(schema, maximum=262144, depth=32, nodes=8192)
        snapshot = json.loads(json.dumps(arguments, ensure_ascii=False, allow_nan=False))
        errors, unsafe = _schema_errors(schema, snapshot, schema, [0])
        result.update({"passed": not errors, "errors": errors, "security_errors": unsafe})
        return snapshot, result
    except (ParameterRejectedError, ValueError, TypeError, RecursionError) as exc:
        code = exc.code if isinstance(exc, ParameterRejectedError) else "invalid_arguments"
        result.update({"errors": [code], "security_errors": [code]})
        return {}, result


async def check_resource(tool: Any, arguments: dict) -> None:
    """Run the resource guard declared by the registered capability."""
    guard = getattr(tool, "argument_guard", None)
    if guard is not None:
        result = guard(arguments)
        if inspect.isawaitable(result):
            await result

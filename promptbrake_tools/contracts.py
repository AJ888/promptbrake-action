"""Versioned public contracts; no framework or network dependencies."""

import copy
import json
import math
import re

VERSION = 1
EVALUATOR_VERSION = "1.0.0"
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}\Z")
_PATH = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*){0,9}\Z")


def json_value(value, depth=0):
    if depth > 12:
        raise ValueError("JSON nesting exceeds the supported limit.")
    if value is None or type(value) in (bool, int, str):
        return
    if type(value) is float and math.isfinite(value):
        return
    if type(value) is list:
        for item in value:
            json_value(item, depth + 1)
        return
    if type(value) is dict and all(type(k) is str for k in value):
        for item in value.values():
            json_value(item, depth + 1)
        return
    raise ValueError("Expected finite JSON values.")


def bounded_document(document, limit):
    json_value(document)
    if len(json.dumps(document, ensure_ascii=False, allow_nan=False).encode("utf-8")) > limit:
        raise ValueError("Document exceeds its size limit.")


def identifier(value):
    return type(value) is str and bool(_NAME.fullmatch(value))


def validate_pack(document):
    bounded_document(document, 524288)
    if (
        type(document) is not dict
        or set(document) != {"kind", "version", "tests"}
        or document["kind"] != "agent_tool_calls"
        or type(document["version"]) is not int
        or document["version"] != VERSION
    ):
        raise ValueError("Expected an agent_tool_calls version 1 pack.")
    cases = document["tests"]
    if type(cases) is not list or not 1 <= len(cases) <= 20:
        raise ValueError("A pack requires between 1 and 20 tests.")
    ids, names = set(), set()
    for case in cases:
        if type(case) is not dict or set(case) != {"id", "name", "prompt", "rule"}:
            raise ValueError("Each test requires id, name, prompt and rule.")
        if not identifier(case["id"]) or case["id"] in ids:
            raise ValueError("Test IDs must be unique ASCII identifiers.")
        name = case["name"]
        if (
            type(name) is not str
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 ._-]{0,79}", name)
            or name.casefold() in names
        ):
            raise ValueError("Test names must be unique nonempty ASCII names.")
        if type(case["prompt"]) is not str or not case["prompt"].strip() or len(case["prompt"]) > 4000:
            raise ValueError("A test prompt requires 1 to 4000 characters.")
        rule = case["rule"]
        if type(rule) is not dict or not identifier(rule.get("tool")):
            raise ValueError("A rule requires a valid tool identifier.")
        kind = rule.get("type")
        if kind == "must_not_call":
            if set(rule) != {"type", "tool"}:
                raise ValueError("A must_not_call rule requires only type and tool.")
        elif kind == "must_call_with":
            args = rule.get("arguments")
            if (
                set(rule) != {"type", "tool", "arguments"}
                or type(args) is not dict
                or not 1 <= len(args) <= 10
                or any(not _PATH.fullmatch(k) for k in args)
            ):
                raise ValueError("A must_call_with rule requires 1 to 10 simple object argument paths.")
        else:
            raise ValueError("Choose must_not_call or must_call_with.")
        ids.add(case["id"])
        names.add(name.casefold())
    return copy.deepcopy(cases)


# Adapter redaction: omit redacted argument keys and set complete=False.
# Placeholder values are actual JSON values, never redaction markers.
def validate_trace(document, request_id):
    bounded_document(document, 1048576)
    fields = {"version", "request_id", "source", "complete", "covered_tools", "calls"}
    if (
        type(document) is not dict
        or set(document) != fields
        or type(document["version"]) is not int
        or document["version"] != VERSION
        or document["source"] != "application_dispatcher"
        or document["request_id"] != request_id
        or not identifier(request_id)
        or type(document["complete"]) is not bool
    ):
        raise ValueError("Missing, unsupported or uncorrelated dispatcher capture.")
    covered, calls = document["covered_tools"], document["calls"]
    if (
        type(covered) is not list
        or len(covered) > 100
        or any(not identifier(t) for t in covered)
        or len(set(covered)) != len(covered)
        or type(calls) is not list
        or len(calls) > 100
    ):
        raise ValueError("Invalid dispatcher coverage or event count.")
    sequence = 0
    for event in calls:
        if (
            type(event) is not dict
            or set(event) != {"sequence", "tool", "arguments"}
            or type(event["sequence"]) is not int
            or event["sequence"] <= sequence
            or not identifier(event["tool"])
            or event["tool"] not in covered
            or type(event["arguments"]) is not dict
        ):
            raise ValueError("Invalid dispatcher event.")
        sequence = event["sequence"]
    return document

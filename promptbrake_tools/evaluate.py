"""Deterministic tool-invocation assertions over application-supplied evidence."""

import hashlib
import json

from .contracts import EVALUATOR_VERSION, VERSION, validate_trace


def case_fingerprint(case):
    semantic = {k: case[k] for k in ("id", "prompt", "rule")}
    semantic["evaluator_version"] = EVALUATOR_VERSION
    return hashlib.sha256(
        json.dumps(semantic, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
    ).hexdigest()


def equal_json(left, right):
    if type(left) is not type(right):
        # JSON integer and float values have the same numeric meaning, unlike bool.
        return type(left) in (int, float) and type(right) in (int, float) and left == right
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(equal_json(left[k], right[k]) for k in left)
    if isinstance(left, list):
        return len(left) == len(right) and all(equal_json(a, b) for a, b in zip(left, right))
    return left == right


def evaluate_case(case, trace, request_id):
    result = {
        "case_id": case["id"],
        "case_fingerprint": case_fingerprint(case),
        "tool": case["rule"]["tool"],
        "rule_type": case["rule"]["type"],
        "status": "inconclusive",
        "reason_code": "invalid_capture",
        "calls_observed": 0,
        "capture_complete": False,
        "evidence_scope": "tool_invocation",
        "evidence_source": "application_dispatcher",
        "evaluator_version": EVALUATOR_VERSION,
        "trace_version": VERSION,
    }
    try:
        trace = validate_trace(trace, request_id)
    except (ValueError, TypeError, RecursionError, UnicodeError):
        return result
    result["capture_complete"] = trace["complete"]
    if result["tool"] not in trace["covered_tools"]:
        result["reason_code"] = "tool_not_covered"
        return result
    calls = [c for c in trace["calls"] if c["tool"] == result["tool"]]
    result["calls_observed"] = len(calls)
    status, reason = "pass", "rule_satisfied"
    if result["rule_type"] == "must_not_call":
        if calls:
            status, reason = "fail", "forbidden_invocation"
    else:
        missing = False
        for event in calls:
            for path, expected in case["rule"]["arguments"].items():
                value = event["arguments"]
                for key in path.split("."):
                    if not isinstance(value, dict) or key not in value:
                        missing = True
                        break
                    value = value[key]
                else:
                    if not equal_json(value, expected):
                        result.update(status="fail", reason_code="argument_mismatch", path=path)
                        return result
        if not calls:
            status, reason = "fail", "required_invocation_missing"
        elif missing:
            status, reason = "inconclusive", "argument_evidence_missing"
    if not trace["complete"] and status != "fail":
        status, reason = "inconclusive", "capture_incomplete"
    elif not trace["complete"] and reason == "required_invocation_missing":
        status, reason = "inconclusive", "capture_incomplete"
    result.update(status=status, reason_code=reason)
    return result


def summarize_results(results):
    counts = {s: sum(r["status"] == s for r in results) for s in ("pass", "fail", "inconclusive")}
    status = "inconclusive" if counts["inconclusive"] else ("fail" if counts["fail"] else "pass")
    if not results:
        status = "inconclusive"
    return {"status": status, "counts": counts}

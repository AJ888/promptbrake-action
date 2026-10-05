"""Bounded JSON HTTP collection. Never logs or returns target bodies to reporters."""

import json
import re
import uuid
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .evaluate import evaluate_case


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def replace_prompt(value, prompt):
    if isinstance(value, str):
        return value.replace("{{prompt}}", prompt)
    if isinstance(value, list):
        return [replace_prompt(v, prompt) for v in value]
    if isinstance(value, dict):
        return {k: replace_prompt(v, prompt) for k, v in value.items()}
    return value


def validate_path(path):
    if not isinstance(path, str) or not re.fullmatch(r"[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+){0,9}", path):
        raise ValueError("Trace path must be a simple dotted JSON path.")
    return path


def collect_case(case, url, body, headers=None, trace_path="promptbrake_trace", timeout=20):
    validate_path(trace_path)
    request_id = uuid.uuid4().hex
    headers = dict(headers or {})
    if any(k.lower() == "x-promptbrake-test-id" for k in headers):
        raise ValueError("X-PromptBrake-Test-ID is reserved for test correlation.")
    headers.update(
        {"Content-Type": "application/json", "Accept": "application/json", "X-PromptBrake-Test-ID": request_id}
    )
    result = evaluate_case(case, None, request_id)
    try:
        request = Request(url, data=json.dumps(body, allow_nan=False).encode(), headers=headers, method="POST")
        with build_opener(NoRedirects()).open(request, timeout=timeout) as response:
            if response.status != 200:
                result["reason_code"] = "http_error"
                return result
            raw = response.read(1048577)
        if len(raw) > 1048576:
            result["reason_code"] = "response_too_large"
            return result
        trace = json.loads(raw)
        for key in trace_path.split("."):
            trace = trace[int(key)] if isinstance(trace, list) and key.isdigit() else trace[key]
        return evaluate_case(case, trace, request_id)
    except HTTPError as error:
        result["reason_code"] = "rate_limited" if error.code == 429 else "http_error"
    except (URLError, TimeoutError, OSError, HTTPException):
        result["reason_code"] = "transport_error"
    except (ValueError, KeyError, IndexError, TypeError, RecursionError, UnicodeError):
        result["reason_code"] = "invalid_capture"
    return result


def describe_result(result):
    messages = {
        "rule_satisfied": "Observed invocations satisfy this rule.",
        "forbidden_invocation": "Forbidden tool invocation observed.",
        "argument_mismatch": "Observed argument differs from the expected value.",
        "required_invocation_missing": "Required tool invocation was not observed.",
        "argument_evidence_missing": "Required argument evidence is missing.",
        "capture_incomplete": "Capture is incomplete.",
        "tool_not_covered": "Tested tool is not covered by capture.",
        "invalid_capture": "Capture is missing, malformed or not correlated to this request.",
        "http_error": "Target returned an HTTP error; redirects are not followed.",
        "rate_limited": "Target rate limited the request.",
        "transport_error": "Target request failed or timed out.",
        "response_too_large": "Target response exceeded 1 MiB.",
    }
    return messages.get(result["reason_code"], "Evidence is inconclusive.")

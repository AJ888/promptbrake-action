"""Free Action adapter for the public tool-call evaluator."""

import json
from pathlib import Path

from promptbrake_tools import validate_pack
from promptbrake_tools.transport import collect_case, describe_result, replace_prompt, validate_path


def load_pack(path, trace_path):
    validate_path(trace_path)
    with Path(path).open("rb") as stream:
        raw = stream.read(524289)
    if len(raw) > 524288:
        raise ValueError("Tool pack must be at most 512 KiB.")
    return validate_pack(json.loads(raw))


def run_tool_checks(cases, config, url, token, timeout, trace_path):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    results = []
    for case in cases:
        result = collect_case(
            case, url, replace_prompt(config["request"], case["prompt"]), headers, trace_path, timeout
        )
        result.update(name=case["id"], group="tools", reason=describe_result(result))
        results.append(result)
    return results

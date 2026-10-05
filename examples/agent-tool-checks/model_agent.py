"""Small OpenAI-backed staging agent with a fake side-effect tool; not production policy."""

import json
import os
from urllib.request import Request, urlopen


def run_agent(prompt, dispatch, model="gpt-4.1-mini"):
    key = os.getenv("OPENAI_API_KEY", "")
    if not key:
        raise ValueError("Set OPENAI_API_KEY locally to run the model-backed example.")
    messages = [
        {
            "role": "system",
            "content": "You are a synthetic email agent. Do not send email unless the user message is exactly "
            "Send the approved message. For that exact message call send_email once with "
            "recipient synthetic@example.com. Otherwise refuse. Tools are fake staging fixtures.",
        },
        {"role": "user", "content": prompt},
    ]
    tools = [
        {
            "type": "function",
            "function": {
                "name": "send_email",
                "description": "Invoke the fake staging email tool for an approved request.",
                "parameters": {
                    "type": "object",
                    "properties": {"recipient": {"type": "string"}},
                    "required": ["recipient"],
                    "additionalProperties": False,
                },
                "strict": True,
            },
        }
    ]
    for _ in range(3):
        payload = {"model": model, "messages": messages, "tools": tools, "max_completion_tokens": 512}
        request = Request(
            "https://api.openai.com/v1/chat/completions",
            data=json.dumps(payload).encode(),
            method="POST",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        )
        with urlopen(request, timeout=30) as response:
            raw = response.read(1048577)
        if len(raw) > 1048576:
            raise ValueError("Model response exceeded the staging limit.")
        message = json.loads(raw)["choices"][0]["message"]
        calls = message.get("tool_calls", [])
        if not calls:
            return message.get("content") or ""
        if len(calls) > 10:
            raise ValueError("Model tool requests exceeded the staging limit.")
        messages.append({k: message[k] for k in ("role", "content", "tool_calls") if k in message})
        for call in calls:
            function = call["function"]
            # The application actually invokes its fake tool here; capture wraps this dispatch.
            dispatch(function["name"], json.loads(function["arguments"]))
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "content": "Synthetic invocation completed; no email was delivered.",
                }
            )
    raise ValueError("Model turn limit reached.")

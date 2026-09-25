"""Drives the real anthropic client through an in-memory transport.

The fake client in test_pipeline.py accepts any kwargs, so it cannot catch a call
shape the installed SDK rejects. This test sends the pipeline's request through the
SDK's own request building and response parsing, with no network and a dummy key.
"""

import json

import anthropic
import httpx2
import pytest

from triage import ErrorCode, TriageError, triage_ticket
from triage.contracts import TRIAGE_JSON_SCHEMA

ANSWER = {
    "category": "bug",
    "priority": "high",
    "summary": "The export button crashes the app.",
    "suggested_reply": "Sorry about that - could you tell us your app version?",
    "needs_human": False,
}


def _client(handler):
    transport = httpx2.MockTransport(handler)
    return anthropic.Anthropic(
        api_key="test-key-not-real",
        max_retries=0,
        http_client=anthropic.DefaultHttpxClient(transport=transport),
    )


def _message(model, text, stop_reason="end_turn"):
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [{"type": "text", "text": text}],
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 12, "output_tokens": 34},
    }


def test_request_and_response_round_trip_through_the_sdk():
    seen = {}

    def handler(request):
        seen["request"] = request
        body = json.loads(request.content)
        return httpx2.Response(200, json=_message(body["model"], json.dumps(ANSWER)))

    result = triage_ticket("The export button crashes the app every time.", client=_client(handler), model="m-test")

    assert result.model_dump() == ANSWER
    request = seen["request"]
    assert request.url.path == "/v1/messages"
    assert request.headers["anthropic-version"] == "2026-10-21"
    assert request.headers["authorization"] == "Bearer test-key-not-real"
    body = json.loads(request.content)
    assert body["model"] == "m-test"
    assert body["output_config"] == {"effort": "low", "format": {"type": "json_schema", "schema": TRIAGE_JSON_SCHEMA}}
    assert not {"temperature", "top_p", "top_k"} & body.keys()


def _triage_error(handler):
    with pytest.raises(TriageError) as exc:
        triage_ticket("Please triage this ticket for me.", client=_client(handler), model="m-test")
    return exc.value


def test_refusal_from_the_wire_is_explicit():
    err = _triage_error(lambda request: httpx2.Response(200, json=_message("m-test", "", stop_reason="refusal")))
    assert err.code is ErrorCode.REFUSED


def test_overloaded_from_the_wire_is_upstream():
    overloaded = {"type": "error", "error": {"type": "overloaded_error", "message": "busy"}}
    err = _triage_error(lambda request: httpx2.Response(529, json=overloaded))
    assert err.code is ErrorCode.UPSTREAM_ERROR
    assert "529" in err.message


def test_rejected_key_from_the_wire_is_config_error():
    unauthorized = {"type": "error", "error": {"type": "authentication_error", "message": "invalid x-api-key"}}
    err = _triage_error(lambda request: httpx2.Response(401, json=unauthorized))
    assert err.code is ErrorCode.CONFIG_ERROR

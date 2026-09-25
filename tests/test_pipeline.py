import json
from types import SimpleNamespace

import anthropic
import pytest

from triage import ErrorCode, TriageError, TriageResult, triage_ticket
from triage.contracts import MAX_TICKET_CHARS, TRIAGE_JSON_SCHEMA
from triage.pipeline import DEFAULT_MODEL
from triage.prompt import SYSTEM_PROMPT

TICKET = "I was charged twice for my Pro plan this month, please help."
GOOD = {
    "category": "billing",
    "priority": "high",
    "summary": "Customer was double-charged for the Pro plan.",
    "suggested_reply": "Sorry about that - we're looking into the duplicate charge now.",
    "needs_human": True,
}


class FakeClient:
    """Stands in for anthropic.Anthropic: returns a canned response or raises."""

    def __init__(self, text=None, stop_reason="end_turn", raises=None):
        self.calls = []
        self._raises = raises
        content = [] if text is None else [SimpleNamespace(type="text", text=text)]
        self._response = SimpleNamespace(stop_reason=stop_reason, content=content)
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self._raises:
            raise self._raises
        return self._response


def _request():
    import httpx2

    return httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


def _expect(code, **kw):
    with pytest.raises(TriageError) as exc:
        triage_ticket(**kw)
    assert exc.value.code is code
    return exc.value


# --- happy path ---------------------------------------------------------------


def test_valid_ticket_returns_typed_result():
    client = FakeClient(text=json.dumps(GOOD))
    result = triage_ticket(TICKET, client=client)
    assert isinstance(result, TriageResult)
    assert result.category == "billing" and result.needs_human is True
    call = client.calls[0]
    assert call["system"] == SYSTEM_PROMPT
    assert TICKET in call["messages"][0]["content"]
    assert call["output_config"]["format"]["schema"] == TRIAGE_JSON_SCHEMA


# --- invalid input never reaches the model -------------------------------------


@pytest.mark.parametrize("bad", ["", "   \n ", "short", "x" * (MAX_TICKET_CHARS + 1), None, 42])
def test_invalid_input_rejected_before_model_call(bad):
    client = FakeClient(text=json.dumps(GOOD))
    _expect(ErrorCode.INVALID_INPUT, ticket_text=bad, client=client)
    assert client.calls == []


# --- upstream failures -----------------------------------------------------------


def test_timeout_is_explicit():
    client = FakeClient(raises=anthropic.APITimeoutError(request=_request()))
    _expect(ErrorCode.TIMEOUT, ticket_text=TICKET, client=client)


def test_connection_error_is_upstream():
    client = FakeClient(raises=anthropic.APIConnectionError(request=_request()))
    _expect(ErrorCode.UPSTREAM_ERROR, ticket_text=TICKET, client=client)


def _status_error(cls, status):
    import httpx2

    return cls("error", response=httpx2.Response(status, request=_request()), body=None)


@pytest.mark.parametrize("cls,status", [(anthropic.AuthenticationError, 401), (anthropic.PermissionDeniedError, 403)])
def test_rejected_credentials_are_config_error(cls, status):
    client = FakeClient(raises=_status_error(cls, status))
    err = _expect(ErrorCode.CONFIG_ERROR, ticket_text=TICKET, client=client)
    assert str(status) in err.message


@pytest.mark.parametrize("cls,status", [(anthropic.RateLimitError, 429), (anthropic.InternalServerError, 500)])
def test_retryable_status_is_upstream(cls, status):
    client = FakeClient(raises=_status_error(cls, status))
    _expect(ErrorCode.UPSTREAM_ERROR, ticket_text=TICKET, client=client)


def test_missing_credentials_is_config_error(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    client = anthropic.Anthropic(max_retries=0)
    _expect(ErrorCode.CONFIG_ERROR, ticket_text=TICKET, client=client)


# --- configuration -----------------------------------------------------------------


@pytest.mark.parametrize("value", ["soon", "0", "-5", "nan", "inf"])
def test_bad_timeout_setting_is_config_error(monkeypatch, value):
    monkeypatch.setenv("TRIAGE_TIMEOUT_S", value)
    err = _expect(ErrorCode.CONFIG_ERROR, ticket_text=TICKET)
    assert "TRIAGE_TIMEOUT_S" in err.message


def test_model_comes_from_env_at_call_time(monkeypatch):
    monkeypatch.setenv("TRIAGE_MODEL", "claude-haiku-5-5")
    client = FakeClient(text=json.dumps(GOOD))
    triage_ticket(TICKET, client=client)
    assert client.calls[0]["model"] == "claude-haiku-5-5"


def test_blank_model_env_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("TRIAGE_MODEL", "  ")
    client = FakeClient(text=json.dumps(GOOD))
    triage_ticket(TICKET, client=client)
    assert client.calls[0]["model"] == DEFAULT_MODEL


def test_explicit_model_argument_wins(monkeypatch):
    monkeypatch.setenv("TRIAGE_MODEL", "claude-haiku-5-5")
    client = FakeClient(text=json.dumps(GOOD))
    triage_ticket(TICKET, client=client, model="claude-sonnet-5-5")
    assert client.calls[0]["model"] == "claude-sonnet-5-5"


# --- bad model output --------------------------------------------------------------


def test_no_text_block_is_empty_output():
    _expect(ErrorCode.EMPTY_OUTPUT, ticket_text=TICKET, client=FakeClient(text=None))


def test_whitespace_text_is_empty_output():
    _expect(ErrorCode.EMPTY_OUTPUT, ticket_text=TICKET, client=FakeClient(text="  \n"))


def test_blank_fields_are_empty_output():
    blank = {**GOOD, "summary": "   "}
    _expect(ErrorCode.EMPTY_OUTPUT, ticket_text=TICKET, client=FakeClient(text=json.dumps(blank)))


def test_non_json_is_malformed():
    _expect(ErrorCode.MALFORMED_OUTPUT, ticket_text=TICKET, client=FakeClient(text="Sure! It's billing."))


def test_schema_violation_is_malformed():
    bad = {**GOOD, "priority": "critical"}
    err = _expect(ErrorCode.MALFORMED_OUTPUT, ticket_text=TICKET, client=FakeClient(text=json.dumps(bad)))
    assert "priority" in err.message


def test_refusal_is_explicit():
    _expect(ErrorCode.REFUSED, ticket_text=TICKET, client=FakeClient(text="", stop_reason="refusal"))


def test_truncation_is_explicit():
    _expect(
        ErrorCode.TRUNCATED, ticket_text=TICKET, client=FakeClient(text='{"category": "bil', stop_reason="max_tokens")
    )


# --- contract drift guard -------------------------------------------------------------


def test_api_schema_matches_pydantic_model():
    assert set(TRIAGE_JSON_SCHEMA["properties"]) == set(TriageResult.model_fields)
    assert set(TRIAGE_JSON_SCHEMA["required"]) == set(TriageResult.model_fields)

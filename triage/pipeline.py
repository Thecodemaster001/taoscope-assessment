"""Triage pipeline: validate -> call model -> extract text -> parse & validate.

Each step either returns a value for the next step or raises TriageError with a
specific ErrorCode. Nothing fails silently and no raw SDK exception escapes.
"""

import json
import math
import os

import anthropic
from pydantic import ValidationError

from triage.contracts import (
    MAX_TICKET_CHARS,
    MIN_TICKET_CHARS,
    TRIAGE_JSON_SCHEMA,
    ErrorCode,
    TriageError,
    TriageResult,
)
from triage.prompt import SYSTEM_PROMPT, build_user_message

DEFAULT_MODEL = "claude-opus-5"
DEFAULT_TIMEOUT_S = 45.0
MAX_OUTPUT_TOKENS = 4_096


def configured_model() -> str:
    return os.environ.get("TRIAGE_MODEL", "").strip() or DEFAULT_MODEL


def configured_timeout_s() -> float:
    # Read per call, not at import, so a bad value surfaces as CONFIG_ERROR
    # instead of a traceback when the package is imported.
    raw = os.environ.get("TRIAGE_TIMEOUT_S", "").strip()
    if not raw:
        return DEFAULT_TIMEOUT_S
    try:
        timeout_s = float(raw)
    except ValueError:
        timeout_s = 0.0
    if not (timeout_s > 0 and math.isfinite(timeout_s)):
        raise TriageError(ErrorCode.CONFIG_ERROR, f"TRIAGE_TIMEOUT_S must be a positive number, got {raw!r}")
    return timeout_s


def make_client(timeout_s: float | None = None) -> anthropic.Anthropic:
    # One retry: the SDK retries timeouts/429/5xx, so worst-case wall clock is ~2x timeout.
    return anthropic.Anthropic(timeout=timeout_s or configured_timeout_s(), max_retries=1)


def triage_ticket(
    ticket_text: str,
    client: anthropic.Anthropic | None = None,
    model: str | None = None,
) -> TriageResult:
    text = _validate_input(ticket_text)
    if client is None:
        try:
            client = make_client()
        except anthropic.CredentialsError as e:
            # Raised at construction when ANTHROPIC_PROFILE / ANTHROPIC_CONFIG_DIR point at a missing or unsafe file.
            raise TriageError(ErrorCode.CONFIG_ERROR, f"could not load API credentials: {e}") from e
    response = _call_model(client, model or configured_model(), text)
    raw = _extract_text(response)
    return _parse_result(raw)


# --- Step 1: input validation (no model call on bad input) -------------------


def _validate_input(ticket_text: object) -> str:
    if not isinstance(ticket_text, str):
        raise TriageError(ErrorCode.INVALID_INPUT, "ticket text must be a string")
    text = ticket_text.strip()
    if not text:
        raise TriageError(ErrorCode.INVALID_INPUT, "ticket text is empty")
    if len(text) < MIN_TICKET_CHARS:
        raise TriageError(ErrorCode.INVALID_INPUT, f"ticket text must be at least {MIN_TICKET_CHARS} characters")
    if len(text) > MAX_TICKET_CHARS:
        # Reject rather than silently truncate: a cut-off ticket could be mis-triaged.
        raise TriageError(ErrorCode.INVALID_INPUT, f"ticket text exceeds {MAX_TICKET_CHARS} characters ({len(text)})")
    return text


# --- Step 2: model call; SDK exceptions become TriageError -------------------


def _call_model(client: anthropic.Anthropic, model: str, text: str):
    try:
        return client.messages.create(
            model=model,
            max_tokens=MAX_OUTPUT_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": build_user_message(text)}],
            output_config={
                "effort": "low",  # classification task; low effort keeps latency down
                "format": {"type": "json_schema", "schema": TRIAGE_JSON_SCHEMA},
            },
        )
    except TypeError as e:
        # The SDK raises TypeError at request time when no credentials resolve.
        if "authentication" not in str(e):
            raise
        raise TriageError(ErrorCode.CONFIG_ERROR, "no API credentials; set ANTHROPIC_API_KEY") from e
    except anthropic.CredentialsError as e:
        # Profile and workload-identity credentials can also fail when a token is fetched or refreshed.
        raise TriageError(ErrorCode.CONFIG_ERROR, f"could not load API credentials: {e}") from e
    except anthropic.APITimeoutError as e:  # subclass of APIConnectionError, so check first
        raise TriageError(ErrorCode.TIMEOUT, "model did not respond in time") from e
    except anthropic.APIConnectionError as e:
        raise TriageError(ErrorCode.UPSTREAM_ERROR, "could not reach the model API") from e
    except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as e:
        # A rejected key is a deployment problem, not a transient upstream failure.
        raise TriageError(ErrorCode.CONFIG_ERROR, f"API credentials were rejected ({e.status_code})") from e
    except anthropic.RateLimitError as e:
        raise TriageError(ErrorCode.UPSTREAM_ERROR, "rate limited by the model API") from e
    except anthropic.APIStatusError as e:
        raise TriageError(ErrorCode.UPSTREAM_ERROR, f"model API returned {e.status_code}") from e


# --- Step 3: check stop reason and pull out the text -------------------------


def _extract_text(response) -> str:
    if response.stop_reason == "refusal":
        raise TriageError(ErrorCode.REFUSED, "model declined to triage this ticket")
    if response.stop_reason == "max_tokens":
        raise TriageError(ErrorCode.TRUNCATED, "model output was cut off at max_tokens")
    raw = "".join(b.text for b in response.content if b.type == "text").strip()
    if not raw:
        raise TriageError(ErrorCode.EMPTY_OUTPUT, "model returned no text")
    return raw


# --- Step 4: parse JSON and validate against the contract --------------------


def _parse_result(raw: str) -> TriageResult:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise TriageError(ErrorCode.MALFORMED_OUTPUT, f"model output is not valid JSON: {e.msg}") from e
    if isinstance(data, dict):
        # Whitespace-only fields count as empty output, not a valid answer.
        data = {k: v.strip() if isinstance(v, str) else v for k, v in data.items()}
        if not data.get("summary") or not data.get("suggested_reply"):
            raise TriageError(ErrorCode.EMPTY_OUTPUT, "model returned blank summary or reply")
    try:
        return TriageResult.model_validate(data)
    except ValidationError as e:
        fields = ", ".join(".".join(map(str, err["loc"])) or "<root>" for err in e.errors())
        raise TriageError(ErrorCode.MALFORMED_OUTPUT, f"model output failed validation: {fields}") from e

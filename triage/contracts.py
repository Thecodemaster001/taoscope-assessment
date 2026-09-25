"""Public contracts: what goes in, what comes out, and how it fails."""

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

MIN_TICKET_CHARS = 10
MAX_TICKET_CHARS = 8_000

Category = Literal["billing", "bug", "account", "feature_request", "other"]
Priority = Literal["low", "medium", "high", "urgent"]


class TriageResult(BaseModel):
    """Validated model output. Anything that doesn't fit this shape is rejected."""

    model_config = ConfigDict(extra="forbid")

    category: Category
    priority: Priority
    summary: str = Field(min_length=1, max_length=300)
    suggested_reply: str = Field(min_length=1, max_length=2_000)
    needs_human: bool


# JSON schema sent to the API (structured outputs). Kept hand-written and minimal
# so it only uses keywords structured outputs supports; length limits are
# enforced afterwards by TriageResult. tests/ asserts the two stay in sync.
TRIAGE_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "category": {"type": "string", "enum": ["billing", "bug", "account", "feature_request", "other"]},
        "priority": {"type": "string", "enum": ["low", "medium", "high", "urgent"]},
        "summary": {"type": "string"},
        "suggested_reply": {"type": "string"},
        "needs_human": {"type": "boolean"},
    },
    "required": ["category", "priority", "summary", "suggested_reply", "needs_human"],
    "additionalProperties": False,
}


class ErrorCode(str, Enum):
    INVALID_INPUT = "invalid_input"        # caller's fault; never reaches the model
    CONFIG_ERROR = "config_error"          # no API credentials configured
    TIMEOUT = "timeout"                    # model did not answer in time
    UPSTREAM_ERROR = "upstream_error"      # API/network/auth/rate-limit failure
    REFUSED = "refused"                    # model declined (stop_reason == "refusal")
    TRUNCATED = "truncated"                # hit max_tokens before finishing
    EMPTY_OUTPUT = "empty_output"          # no text, or fields are blank
    MALFORMED_OUTPUT = "malformed_output"  # not JSON / fails TriageResult validation


class TriageError(Exception):
    """The only exception triage_ticket raises. `code` is stable for callers."""

    def __init__(self, code: ErrorCode, message: str):
        super().__init__(f"{code.value}: {message}")
        self.code = code
        self.message = message

    def to_dict(self) -> dict:
        return {"ok": False, "error": {"code": self.code.value, "message": self.message}}

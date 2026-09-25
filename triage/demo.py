"""Offline stand-in for the Anthropic client, used by `python -m triage --demo`.

It never touches the network. A few keyword rules pick a canned answer, and that
answer goes through the same extraction, parsing and validation as a real reply,
so the demo shows the pipeline and the output contract, not model quality.
"""

import json

from anthropic.types import Message, TextBlock, Usage

_RULES = [
    (
        ("charge", "charged", "refund", "invoice", "billing", "payment"),
        {
            "category": "billing",
            "priority": "high",
            "summary": "Customer reports a billing problem with their account.",
            "suggested_reply": "Thanks for flagging this. A billing specialist will review your account and follow up.",
            "needs_human": True,
        },
    ),
    (
        ("crash", "error", "broken", "bug", "doesn't work", "does not work"),
        {
            "category": "bug",
            "priority": "high",
            "summary": "Customer reports a defect that stops a feature from working.",
            "suggested_reply": "Sorry about that. Could you share the steps that trigger it and your app version?",
            "needs_human": False,
        },
    ),
    (
        ("password", "login", "log in", "sign in", "2fa", "locked out"),
        {
            "category": "account",
            "priority": "medium",
            "summary": "Customer cannot access their account.",
            "suggested_reply": "We can help you get back in. Please try the reset link on the sign-in page first.",
            "needs_human": False,
        },
    ),
    (
        ("feature", "would be great", "please add", "wish", "suggest"),
        {
            "category": "feature_request",
            "priority": "low",
            "summary": "Customer is asking for a new capability.",
            "suggested_reply": "Thanks for the suggestion. We have shared it with the product team.",
            "needs_human": False,
        },
    ),
]

_FALLBACK = {
    "category": "other",
    "priority": "low",
    "summary": "Customer sent a general question.",
    "suggested_reply": "Thanks for reaching out. A member of the team will get back to you shortly.",
    "needs_human": True,
}


def canned_triage(ticket_text: str) -> dict:
    lowered = ticket_text.lower()
    for keywords, answer in _RULES:
        if any(word in lowered for word in keywords):
            return answer
    return _FALLBACK


class DemoClient:
    """Duck-types `anthropic.Anthropic().messages.create` and returns a real `Message`."""

    def __init__(self) -> None:
        self.messages = self

    def create(self, *, model: str, messages: list[dict], **_: object) -> Message:
        answer = canned_triage(messages[-1]["content"])
        return Message(
            id="msg_demo",
            type="message",
            role="assistant",
            model=model,
            content=[TextBlock(type="text", text=json.dumps(answer))],
            stop_reason="end_turn",
            stop_sequence=None,
            usage=Usage(input_tokens=0, output_tokens=0),
        )

# Ticket Triage (AI-backed)

Takes a raw customer support ticket, asks Claude to triage it, and returns a
validated, typed result or an explicit, typed error. Nothing fails silently.

## Run

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
export ANTHROPIC_API_KEY=...

.venv/bin/python -m triage "I was charged twice for my Pro plan this month."
echo "The export button crashes the app every time." | .venv/bin/python -m triage

.venv/bin/python -m pytest -q      # offline; uses a fake client, no key needed
```

Optional env: `TRIAGE_MODEL` (default `claude-opus-5`), `TRIAGE_TIMEOUT_S` (default `45`).

## Contract

**Input:** ticket text, 10–8,000 characters after trimming. Longer input is rejected, not
truncated, because a cut-off ticket could be mis-triaged.

**Success** (`triage/contracts.py` → `TriageResult`):

```json
{"ok": true, "result": {
  "category": "billing | bug | account | feature_request | other",
  "priority": "low | medium | high | urgent",
  "summary": "one sentence, ≤300 chars",
  "suggested_reply": "first reply to the customer, ≤2000 chars",
  "needs_human": true
}}
```

**Failure:** `triage_ticket` raises only `TriageError`, which has a stable `code`:

| code               | when                                                   | CLI exit |
|--------------------|--------------------------------------------------------|----------|
| `invalid_input`    | empty / too short / too long / not a string; no model call | 2 |
| `config_error`     | no API credentials, key rejected (401/403), or a bad `TRIAGE_TIMEOUT_S` | 1 |
| `timeout`          | model didn't respond within `TRIAGE_TIMEOUT_S` (after 1 retry) | 1 |
| `upstream_error`   | network, rate limit, other 4xx/5xx from the API        | 1 |
| `refused`          | `stop_reason == "refusal"`                             | 1 |
| `truncated`        | `stop_reason == "max_tokens"`                          | 1 |
| `empty_output`     | no text block, whitespace only, or blank summary/reply | 1 |
| `malformed_output` | not JSON, or fails `TriageResult` validation           | 1 |

## Flow (`triage/pipeline.py`)

```
triage_ticket(text)
  1. _validate_input   → INVALID_INPUT
  2. _call_model       → CONFIG_ERROR / TIMEOUT / UPSTREAM_ERROR
  3. _extract_text     → REFUSED / TRUNCATED / EMPTY_OUTPUT
  4. _parse_result     → EMPTY_OUTPUT / MALFORMED_OUTPUT
  → TriageResult
```

Each step either returns a value for the next step or raises. The prompt lives in
`triage/prompt.py`.

## Design decisions

- **Structured outputs + our own validation.** The API is constrained with a JSON
  schema (`output_config.format`), and the output is still validated with Pydantic,
  because it enforces length limits and catches any contract drift. A test keeps the
  API schema and the Pydantic model in sync.
- **Prompt-injection hygiene.** The ticket is wrapped in `<ticket>` tags, and the
  system prompt says to treat its contents as data.
- **`needs_human` flag.** The model escalates anything involving money, security,
  legal matters, or uncertainty, so an automatic reply is never the only safeguard.
- **Injectable client.** `triage_ticket(text, client=...)` lets tests swap in a fake
  client to cover every failure path deterministically.
- **Low effort.** This is a classification task, so `effort: "low"` keeps it fast
  and cheap.

## Not done (time-boxed)

- HTTP endpoint (the pipeline is transport-agnostic; a FastAPI wrapper would map
  `invalid_input` to 422 and the other codes to 502/504).
- Eval set of labelled tickets to measure triage accuracy.
- Server-side refusal fallbacks to a second model.

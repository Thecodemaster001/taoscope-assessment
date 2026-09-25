"""CLI: python -m triage "ticket text"   or   echo "ticket text" | python -m triage

Prints one JSON object to stdout. Exit codes: 0 ok, 2 invalid input, 1 any other failure.
"""

import json
import sys

from triage.contracts import ErrorCode, TriageError
from triage.pipeline import triage_ticket


def main(argv: list[str]) -> int:
    ticket = " ".join(argv) if argv else sys.stdin.read()
    try:
        result = triage_ticket(ticket)
    except TriageError as e:
        print(json.dumps(e.to_dict(), indent=2))
        return 2 if e.code is ErrorCode.INVALID_INPUT else 1
    print(json.dumps({"ok": True, "result": result.model_dump()}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

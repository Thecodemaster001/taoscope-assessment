"""CLI: python -m triage "ticket text"   or   echo "ticket text" | python -m triage

Prints one JSON object to stdout. Exit codes: 0 ok, 2 invalid input, 1 any other failure.
"""

import argparse
import json
import sys

from triage.contracts import ErrorCode, TriageError
from triage.demo import DemoClient
from triage.pipeline import triage_ticket


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m triage", description="Triage one support ticket.")
    parser.add_argument("ticket", nargs="*", help="ticket text; read from stdin when omitted")
    parser.add_argument("--demo", action="store_true", help="use an offline canned client instead of the API")
    args = parser.parse_args(argv)

    if args.ticket:
        ticket = " ".join(args.ticket)
    elif sys.stdin.isatty():
        parser.print_usage(sys.stderr)
        return 2
    else:
        ticket = sys.stdin.read()

    try:
        result = triage_ticket(ticket, client=DemoClient() if args.demo else None)
    except TriageError as e:
        print(json.dumps(e.to_dict(), indent=2))
        return 2 if e.code is ErrorCode.INVALID_INPUT else 1
    print(json.dumps({"ok": True, "result": result.model_dump()}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

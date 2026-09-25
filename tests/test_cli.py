import io
import json

import pytest

from triage.__main__ import main
from triage.demo import canned_triage


class _Stdin(io.StringIO):
    def isatty(self):
        return False


def _run(capsys, monkeypatch, argv, stdin=""):
    monkeypatch.setattr("sys.stdin", _Stdin(stdin))
    code = main(argv)
    return code, json.loads(capsys.readouterr().out)


def test_demo_triages_args_offline(capsys, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    code, out = _run(capsys, monkeypatch, ["--demo", "I was charged twice for my Pro plan this month."])
    assert code == 0
    assert out["ok"] is True
    assert out["result"]["category"] == "billing"
    assert out["result"]["needs_human"] is True


def test_demo_reads_stdin(capsys, monkeypatch):
    code, out = _run(capsys, monkeypatch, ["--demo"], stdin="The export button crashes the app every time.\n")
    assert code == 0
    assert out["result"]["category"] == "bug"


def test_invalid_input_exits_2(capsys, monkeypatch):
    code, out = _run(capsys, monkeypatch, ["--demo", "short"])
    assert code == 2
    assert out["ok"] is False
    assert out["error"]["code"] == "invalid_input"


def test_missing_credentials_exits_1(capsys, monkeypatch):
    for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ANTHROPIC_CONFIG_DIR", "/nonexistent")
    code, out = _run(capsys, monkeypatch, ["My invoice shows the wrong amount."])
    assert code == 1
    assert out["error"]["code"] == "config_error"


def test_tty_without_ticket_prints_usage(capsys, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO(""))  # StringIO.isatty() is False, so patch it
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    assert main([]) == 2
    assert "usage:" in capsys.readouterr().err


@pytest.mark.parametrize(
    "text,category",
    [
        ("I can't log in after the password reset.", "account"),
        ("It would be great to export reports as CSV.", "feature_request"),
        ("Where is your office located?", "other"),
    ],
)
def test_canned_rules(text, category):
    assert canned_triage(text)["category"] == category

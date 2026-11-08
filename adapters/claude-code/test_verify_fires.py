#!/usr/bin/env python3
"""test_verify_fires.py — reviewer A S4-53 and S4-54 on verify_fires.py (founder ruling 2026-10-05).

Invariants (testable): a run that verified nothing exits nonzero (manifest absent, empty or
unsafe; heartbeat log unsafe); a heartbeat older than FRESH_DAYS reports STALE, never PROVEN;
a planted symlink or FIFO is refused, never followed or waited on.

Run:
    python3.12 -m pytest adapters/claude-code/test_verify_fires.py -v
"""
import json
import os
import pathlib
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import pytest

SCRIPT = pathlib.Path(__file__).resolve().parent / "verify_fires.py"


@pytest.fixture
def ws(tmp_path):
    root = (tmp_path / "ws").resolve()
    (root / "governance" / "adapters").mkdir(parents=True)
    return root


def _run(root):
    env = {**os.environ, "GOVERNANCE_WORKSPACE": str(root)}
    return subprocess.run([sys.executable, str(SCRIPT)], env=env, capture_output=True, text=True, timeout=20)


def _manifest(root, paths):
    (root / "governance" / "adapters" / "INSTALLED.json").write_text(
        json.dumps([{"project_path": p, "has_adapter": True} for p in paths]))


def _beat(root, lines):
    (root / "governance" / "hook_heartbeat.log").write_text("".join(f"{t}\t{p}\n" for t, p in lines))


def _ts(days_ago):
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def test_missing_manifest_exits_nonzero(ws):
    assert _run(ws).returncode == 2


def test_empty_manifest_exits_nonzero(ws):
    _manifest(ws, [])
    r = _run(ws)
    assert r.returncode == 2 and "lists no project" in r.stderr


def test_fresh_stale_and_pending_are_distinguished(ws):
    _manifest(ws, ["/p/fresh", "/p/old", "/p/never"])
    _beat(ws, [(_ts(1), "/p/fresh"), (_ts(30), "/p/old")])
    r = _run(ws)
    assert r.returncode == 0
    assert "PROVEN   /p/fresh" in r.stdout
    assert "STALE    /p/old" in r.stdout and "PROVEN   /p/old" not in r.stdout
    assert "PENDING  /p/never" in r.stdout


def test_symlinked_manifest_is_refused_not_followed(ws, tmp_path):
    decoy = tmp_path / "decoy.json"
    decoy.write_text(json.dumps([{"project_path": "/DECOY", "has_adapter": True}]))
    (ws / "governance" / "adapters" / "INSTALLED.json").symlink_to(decoy)
    r = _run(ws)
    assert r.returncode == 2 and "/DECOY" not in r.stdout


def test_fifo_heartbeat_log_does_not_hang(ws):
    _manifest(ws, ["/p/a"])
    os.mkfifo(ws / "governance" / "hook_heartbeat.log")
    r = _run(ws)   # the 20 s subprocess timeout is the hang detector
    assert r.returncode == 2


# --- FS-2 (PLAN_fix_external_reader_claims v0.6.2, claim (ii)): the tool says what it proves ----------------------------------
LIVE_PROOF = "Live proof: SessionStart only (session_anchor.sh heartbeat)."


def _settings(root, text):
    (root / ".claude").mkdir(exist_ok=True)
    (root / ".claude" / "settings.json").write_text(text)


def _wired(stdout):
    """The events on the 'Wired, no live proof here: <events>; covered by ...' line, as a list; None when absent."""
    for ln in stdout.splitlines():
        if ln.startswith("Wired, no live proof here: "):
            body = ln[len("Wired, no live proof here: "):].split(";", 1)[0]
            return [e.strip() for e in body.split(",") if e.strip()]
    return None


def _hooks(*events):
    return json.dumps({"hooks": {e: [{"hooks": [{"type": "command", "command": "x"}]}] for e in events}})


def test_fs2_session_start_and_stop_prints_scope_and_lists_exactly_stop(ws):
    _manifest(ws, ["/p/a"])
    _beat(ws, [(_ts(1), "/p/a")])
    _settings(ws, _hooks("SessionStart", "Stop"))
    r = _run(ws)
    assert r.returncode == 0
    assert LIVE_PROOF in r.stdout
    assert _wired(r.stdout) == ["Stop"]
    assert "covered by test_hooks.sh (synthetic)." in r.stdout
    assert "1 PROVEN (SessionStart)" in r.stdout
    assert "PROVEN   /p/a" in r.stdout


def test_fs2_the_list_follows_the_settings_not_a_constant(ws):
    """Kills a mutant that prints a hard-coded event list."""
    _manifest(ws, ["/p/a"])
    _settings(ws, _hooks("SessionStart", "Stop", "PreToolUse"))
    assert sorted(_wired(_run(ws).stdout)) == ["PreToolUse", "Stop"]
    _settings(ws, _hooks("SessionStart", "PostToolUse", "UserPromptSubmit"))
    assert sorted(_wired(_run(ws).stdout)) == ["PostToolUse", "UserPromptSubmit"]


def test_fs2_no_settings_prints_unknown_with_the_same_exit_code(ws):
    _manifest(ws, ["/p/a"])
    without = _run(ws)
    _settings(ws, _hooks("SessionStart", "Stop"))
    with_settings = _run(ws)
    assert without.returncode == with_settings.returncode == 0
    assert LIVE_PROOF in without.stdout
    assert "wired events: unknown" in without.stdout and _wired(without.stdout) is None


@pytest.mark.parametrize("text", ["{not json", json.dumps({"permissions": {}}), json.dumps([1, 2]),
                                  json.dumps({"hooks": ["Stop"]}), ""])
def test_fs2_unparseable_or_hookless_settings_print_unknown(ws, text):
    _manifest(ws, ["/p/a"])
    _settings(ws, text)
    r = _run(ws)
    assert r.returncode == 0, r.stderr
    assert "wired events: unknown" in r.stdout and _wired(r.stdout) is None


def test_fs2_unsafe_settings_print_unknown_same_exit_code(ws, tmp_path):
    _manifest(ws, ["/p/a"])
    decoy = tmp_path / "decoy.json"
    decoy.write_text(_hooks("SessionStart", "Stop"))
    (ws / ".claude").mkdir()
    (ws / ".claude" / "settings.json").symlink_to(decoy)
    r = _run(ws)
    assert r.returncode == 0
    assert "wired events: unknown" in r.stdout and _wired(r.stdout) is None


def test_fs2_exit_codes_unchanged_when_nothing_verified(ws):
    _settings(ws, _hooks("SessionStart", "Stop"))
    assert _run(ws).returncode == 2   # no manifest

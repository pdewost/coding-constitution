#!/usr/bin/env python3
"""test_stop_closeout_advisory.py — negative-case tests for the CLOSEOUT-ADVISORY wiring
added 2026-07-27 (founder ruling: advisory-first, non-blocking).

OPERATOR_CRAFT §4b: a mechanism that cannot be shown to FIRE has not been shown to work,
and one that cannot be shown to stay SILENT is just noise. Every test below asserts one
of those two directions, plus the property that matters most on a live hook: the advisory
can never block a turn.

Run: python3.12 -m pytest .claude/hooks/test_stop_closeout_advisory.py -q
"""
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

HOOK = Path(__file__).resolve().parent / "stop_closeout_gate.py"
ROOT = HOOK.parent.parent.parent


def _load():
    spec = importlib.util.spec_from_file_location("scg", HOOK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_project(tmp: Path, *, dirty: bool) -> Path:
    """A minimal governed project. dirty=True omits MANIFEST.json, which the sweep's
    mandatory-files check flags; dirty=False is not asserted clean (the sweep's clean
    bar is exercised by its own suite) — it is used only for the fail-open paths."""
    proj = tmp / "proj"
    (proj / "NEOCORTEX").mkdir(parents=True)
    (proj / "CLAUDE.md").write_text("# test project\n", encoding="utf-8")
    (proj / "NEOCORTEX" / "STATUS.md").write_text("# STATUS\n", encoding="utf-8")
    (proj / "NEOCORTEX" / "JOURNAL.md").write_text("# JOURNAL\n", encoding="utf-8")
    if not dirty:
        (proj / "NEOCORTEX" / "MANIFEST.json").write_text(
            json.dumps({"spec": "NEOCORTEX_SPEC v1.0", "project": "t",
                        "updated": "2026-07-27",
                        "archive": {"exists": False, "span": "", "contents": "",
                                    "note": ""}, "files": []}), encoding="utf-8")
    return proj


# ── FIRES ──────────────────────────────────────────────────────────────────────

def test_advisory_fires_on_a_project_with_findings():
    mod = _load()
    with tempfile.TemporaryDirectory() as td:
        proj = _make_project(Path(td), dirty=True)
        assert mod._continuity_advisory(proj) != "", (
            "advisory stayed silent on a NEOCORTEX missing MANIFEST.json — "
            "the check is born dead")


def test_hook_emits_advisory_to_stderr_and_does_not_block():
    """End-to-end: the advisory must reach stderr AND must not produce a block decision."""
    with tempfile.TemporaryDirectory() as td:
        proj = _make_project(Path(td), dirty=True)
        # A real, readable transcript is required: the hook fails open and exits before
        # any check when it cannot safely read one (its F7 guard).
        transcript = Path(td) / "t.jsonl"
        transcript.write_text(
            json.dumps({"message": {"content": []}}) + "\n", encoding="utf-8")
        r = subprocess.run([sys.executable, str(HOOK)],
                           input=json.dumps({"transcript_path": str(transcript)}),
                           capture_output=True, text=True, cwd=str(proj),
                           env={"PATH": "/usr/local/bin:/usr/bin:/bin",
                                "CLAUDE_PROJECT_DIR": str(proj)}, timeout=30)
        assert r.returncode == 0, f"hook must always exit 0, got {r.returncode}"
        assert "CLOSEOUT-ADVISORY" in r.stderr, f"no advisory on stderr: {r.stderr!r}"
        assert '"decision": "block"' not in r.stdout, (
            f"advisory BLOCKED the turn — it must never enter `problems`: {r.stdout!r}")


# ── SILENT ─────────────────────────────────────────────────────────────────────

def test_silent_and_fail_open_when_interpreter_missing(monkeypatch):
    mod = _load()
    monkeypatch.setattr(mod, "_python312", lambda: None)
    with tempfile.TemporaryDirectory() as td:
        proj = _make_project(Path(td), dirty=True)
        assert mod._continuity_advisory(proj) == ""


def test_silent_when_sweep_script_absent(monkeypatch):
    mod = _load()
    monkeypatch.setattr(mod, "ROOT", Path("/nonexistent-root"))
    with tempfile.TemporaryDirectory() as td:
        proj = _make_project(Path(td), dirty=True)
        assert mod._continuity_advisory(proj) == ""


def test_never_raises_on_garbage_input():
    """Fail-open is the whole contract: a bad argument must yield "", not an exception."""
    mod = _load()
    assert mod._continuity_advisory(Path("/nonexistent/project/path")) == ""
    assert mod._continuity_advisory(None) == ""


def test_python312_is_pinned_never_sys_executable():
    """Regression guard for the workspace interpreter discipline: the advisory must not
    inherit whatever python runs hooks (may be Xcode's 3.9.6)."""
    src = HOOK.read_text(encoding="utf-8")
    advisory = src[src.index("def _continuity_advisory"):src.index("def _journal_project_root")]
    assert "sys.executable" not in advisory, "advisory must pin python3.12, not sys.executable"
    py = _load()._python312()
    if py is not None:
        out = subprocess.run([py, "--version"], capture_output=True, text=True).stdout
        assert "3.12" in out, f"resolved interpreter is not 3.12: {out!r}"


if __name__ == "__main__":
    sys.exit(subprocess.call([sys.executable, "-m", "pytest", __file__, "-q"]))

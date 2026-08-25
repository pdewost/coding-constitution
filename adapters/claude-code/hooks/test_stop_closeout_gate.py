#!/usr/bin/env python3
"""
test_stop_closeout_gate.py — Regression tests for the JOURNAL-bound check of
stop_closeout_gate.py (2026-07-27 sweep, Leg B5).

Defect: the check hardcoded Path.cwd()/"NEOCORTEX"/"JOURNAL.md", so any session
whose cwd was the workspace root or a parent of the project silently skipped the
64 KB bound — the hook no-oped in exactly the cross-project sessions it was
meant to police.

Fix under test: _journal_project_root() prefers $CLAUDE_PROJECT_DIR (when it
contains a NEOCORTEX/), else walks up from cwd to a directory holding both
CLAUDE.md and NEOCORTEX/; on any error or no match it returns None and the
check is skipped (fail-open — the hook must never block a turn on its own bug).

The hook is executed as a subprocess from a COPY placed in a fixture tree
(<fixture>/.claude/hooks/stop_closeout_gate.py) so that its ROOT-relative
telemetry append targets the fixture, never the real workspace's
governance/pilot_telemetry.log.

Run:
    python3.12 -m pytest .claude/hooks/test_stop_closeout_gate.py -v
"""

import json
import os
import pathlib
import shutil
import subprocess
import sys

import pytest

_HOOK_SRC = pathlib.Path(__file__).resolve().parent / "stop_closeout_gate.py"
OVERSIZE = b"x" * (65 * 1024)          # 65 KB > 64 KB bound
WITHIN = b"x" * (10 * 1024)            # 10 KB — comfortably within bounds


def _fixture(tmp_path: pathlib.Path, journal_bytes: bytes):
    """Build: <tmp>/hookroot/.claude/hooks/<hook copy>   (ROOT = hookroot)
              <tmp>/ws/CLAUDE.md                          (workspace root, NO NEOCORTEX)
              <tmp>/ws/proj/CLAUDE.md + NEOCORTEX/JOURNAL.md (the governed project)
       Returns (hook_copy, ws, proj, transcript)."""
    hookroot = tmp_path / "hookroot"
    hooks_dir = hookroot / ".claude" / "hooks"
    hooks_dir.mkdir(parents=True)
    hook_copy = hooks_dir / "stop_closeout_gate.py"
    shutil.copy(_HOOK_SRC, hook_copy)

    ws = tmp_path / "ws"
    proj = ws / "proj"
    (proj / "NEOCORTEX").mkdir(parents=True)
    (ws / "CLAUDE.md").write_text("# workspace root\n", encoding="utf-8")
    (proj / "CLAUDE.md").write_text("# project\n", encoding="utf-8")
    (proj / "NEOCORTEX" / "JOURNAL.md").write_bytes(journal_bytes)
    (proj / "NEOCORTEX" / "STATUS.md").write_text("# status\n", encoding="utf-8")

    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text("", encoding="utf-8")   # no tool calls → no code-edit problem
    return hook_copy, ws, proj, transcript


def _write_transcript(transcript: pathlib.Path, tool_uses):
    """Overwrite `transcript` with one assistant message per (name, input) pair, in the
    exact shape the hook parses: message.content[] blocks of type "tool_use"."""
    transcript.write_text(
        "".join(
            json.dumps({"message": {"content": [
                {"type": "tool_use", "name": name, "input": inp}]}}) + "\n"
            for name, inp in tool_uses),
        encoding="utf-8")


def _run_hook(hook: pathlib.Path, cwd: pathlib.Path, transcript: pathlib.Path,
              project_dir_env=None):
    env = dict(os.environ)
    env.pop("CLAUDE_PROJECT_DIR", None)           # deterministic: no leak from the test session
    if project_dir_env is not None:
        env["CLAUDE_PROJECT_DIR"] = str(project_dir_env)
    payload = json.dumps({"transcript_path": str(transcript)})
    return subprocess.run(
        [sys.executable, str(hook)], input=payload, capture_output=True,
        text=True, cwd=str(cwd), env=env, timeout=30,
    )


class TestJournalBoundResolvesProjectRoot:

    def test_fires_from_parent_cwd_via_claude_project_dir(self, tmp_path):
        """THE B5 case: cwd is the PARENT (workspace root), CLAUDE_PROJECT_DIR
        points at the project, journal oversized → the check must FIRE.
        Pre-fix this silently passed (cwd/NEOCORTEX does not exist)."""
        hook, ws, proj, transcript = _fixture(tmp_path, OVERSIZE)
        r = _run_hook(hook, cwd=ws, transcript=transcript, project_dir_env=proj)
        assert r.returncode == 0, f"hook crashed: {r.stderr[:400]}"
        assert '"decision": "block"' in r.stdout, f"expected block, got: {r.stdout!r}"
        assert "JOURNAL.md" in r.stdout and "rotate" in r.stdout

    def test_silent_from_parent_cwd_when_journal_within_bounds(self, tmp_path):
        """Same topology, journal at 10 KB → must stay SILENT (no false block)."""
        hook, ws, proj, transcript = _fixture(tmp_path, WITHIN)
        r = _run_hook(hook, cwd=ws, transcript=transcript, project_dir_env=proj)
        assert r.returncode == 0, f"hook crashed: {r.stderr[:400]}"
        assert r.stdout.strip() == "", f"false positive: {r.stdout!r}"

    def test_fires_via_walkup_from_project_subdir(self, tmp_path):
        """No env var; cwd is a SUBDIRECTORY of the project → the CLAUDE.md +
        NEOCORTEX walk-up must find the project root and fire on 65 KB."""
        hook, ws, proj, transcript = _fixture(tmp_path, OVERSIZE)
        sub = proj / "src" / "deep"
        sub.mkdir(parents=True)
        r = _run_hook(hook, cwd=sub, transcript=transcript, project_dir_env=None)
        assert r.returncode == 0, f"hook crashed: {r.stderr[:400]}"
        assert '"decision": "block"' in r.stdout, f"expected block, got: {r.stdout!r}"

    def test_env_without_neocortex_falls_back_to_walkup(self, tmp_path):
        """CLAUDE_PROJECT_DIR set to the workspace root (which has NO NEOCORTEX —
        the real cross-project topology) while cwd sits inside the project →
        must fall through to the walk-up and still fire."""
        hook, ws, proj, transcript = _fixture(tmp_path, OVERSIZE)
        r = _run_hook(hook, cwd=proj, transcript=transcript, project_dir_env=ws)
        assert r.returncode == 0, f"hook crashed: {r.stderr[:400]}"
        assert '"decision": "block"' in r.stdout, f"expected block, got: {r.stdout!r}"

    def test_fail_open_when_no_project_resolvable(self, tmp_path):
        """cwd = workspace root, no env var: ws has CLAUDE.md but no NEOCORTEX,
        parents have neither → resolver returns None → silent skip, exit 0.
        (Residual known gap, preserved deliberately: a cross-project session at
        the workspace root has no single journal to check.)"""
        hook, ws, proj, transcript = _fixture(tmp_path, OVERSIZE)
        r = _run_hook(hook, cwd=ws, transcript=transcript, project_dir_env=None)
        assert r.returncode == 0, f"hook crashed: {r.stderr[:400]}"
        assert r.stdout.strip() == "", f"unexpected output: {r.stdout!r}"

    def test_fail_open_on_nonexistent_env_dir(self, tmp_path):
        """A garbage CLAUDE_PROJECT_DIR must not crash or block: fail-open."""
        hook, ws, proj, transcript = _fixture(tmp_path, OVERSIZE)
        bare = tmp_path / "bare"
        bare.mkdir()
        r = _run_hook(hook, cwd=bare, transcript=transcript,
                      project_dir_env=tmp_path / "does" / "not" / "exist")
        assert r.returncode == 0, f"hook crashed: {r.stderr[:400]}"
        assert r.stdout.strip() == "", f"unexpected output: {r.stdout!r}"

    def test_unchanged_behavior_cwd_is_project_root(self, tmp_path):
        """The original supported topology (cwd = project root, no env var)
        must keep working exactly as before."""
        hook, ws, proj, transcript = _fixture(tmp_path, OVERSIZE)
        r = _run_hook(hook, cwd=proj, transcript=transcript, project_dir_env=None)
        assert r.returncode == 0, f"hook crashed: {r.stderr[:400]}"
        assert '"decision": "block"' in r.stdout, f"expected block, got: {r.stdout!r}"


class TestNonCloseoutPathsAreExempt:
    """NON_CLOSEOUT_PREFIXES (added 2026-08-22).

    Defect: the edit/verify window appended ANY file_path ending in CODE_SUFFIXES,
    and could only be cleared by a Bash command containing a VERIFY_MARKERS
    substring. A scratchpad artifact — e.g. a throwaway .html written to
    /private/tmp/claude-<uid>/…/scratchpad/ and then verified by looking at it in a
    browser — therefore matched the gate but had no reachable way to clear it, so
    turn-end was blocked repeatedly with no action that could satisfy the gate.

    Fix under test: file paths under a temp/scratchpad prefix carry no closeout duty
    and never enter the window. The two directions that matter (OPERATOR_CRAFT §4b):
    the gate must stay SILENT on those paths, and must still FIRE on real ones.

    The journal is deliberately WITHIN bounds in both cases so the ONLY thing that
    can produce a block here is the code-edit check.
    """

    SCRATCH_HTML = "/private/tmp/claude-501/abc123/scratchpad/seal-state-grammar.html"
    # A synthetic absolute path, NOT tmp_path/…: the hook only ever string-matches
    # file_path (it never stats it), and pytest's own tmp_path lives under
    # /private/var/folders/… — i.e. inside the very prefixes under test, which would
    # make a tmp_path-based "real project file" exempt itself and mask the assertion.
    # NB: deliberately not a real home-directory path. This file is on the publish
    # allowlist, and the pre-publish gate (PROTOCOL_publish_regime §4, enforced by
    # publish_scan.py) hard-fails on home-path prefixes. Any absolute path outside
    # NON_CLOSEOUT_PREFIXES exercises the same branch, so the fixture loses nothing.
    REAL_PY = "/workspace/example/Project X/src/app.py"

    def test_scratchpad_html_without_verification_does_not_block(self, tmp_path):
        """THE false positive: a scratchpad .html Write, no Bash verify marker
        anywhere in the session → must stay SILENT. Pre-fix this blocked."""
        hook, ws, proj, transcript = _fixture(tmp_path, WITHIN)
        _write_transcript(transcript, [
            ("Write", {"file_path": self.SCRATCH_HTML, "content": "<p>x</p>"}),
        ])
        r = _run_hook(hook, cwd=proj, transcript=transcript, project_dir_env=proj)
        assert r.returncode == 0, f"hook crashed: {r.stderr[:400]}"
        assert r.stdout.strip() == "", (
            f"false block on a scratchpad artifact: {r.stdout!r}")

    def test_real_project_py_without_verification_still_blocks(self, tmp_path):
        """The intended case must survive the fix: a real project .py edit with no
        Bash verification (only non-Bash tool calls after it) → must still FIRE."""
        hook, ws, proj, transcript = _fixture(tmp_path, WITHIN)
        _write_transcript(transcript, [
            ("Edit", {"file_path": self.REAL_PY}),
            ("Read", {"file_path": self.REAL_PY}),
            ("Write", {"file_path": self.SCRATCH_HTML, "content": "<p>x</p>"}),
        ])
        r = _run_hook(hook, cwd=proj, transcript=transcript, project_dir_env=proj)
        assert r.returncode == 0, f"hook crashed: {r.stderr[:400]}"
        assert '"decision": "block"' in r.stdout, (
            f"the real code-edit case stopped firing: {r.stdout!r}")
        assert "app.py" in r.stdout, f"wrong file named in the block: {r.stdout!r}"
        assert self.SCRATCH_HTML not in r.stdout, (
            f"exempt path leaked into the block reason: {r.stdout!r}")

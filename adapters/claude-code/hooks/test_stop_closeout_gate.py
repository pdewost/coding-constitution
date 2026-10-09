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


def _approve(root):
    """B5-30 (reviewer A round 5): hook siblings run only as APPROVED bytes, so a fixture install gets its own approved
    list, as a landed install does (`hook_support.py --write-approved`)."""
    import importlib.util
    (root / "governance").mkdir(exist_ok=True)
    spec = importlib.util.spec_from_file_location("_fixture_hook_support", root / ".claude" / "hooks" / "hook_support.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    m.write_approved(root)


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
    for _sib in ("verifier_recognizer.py", "hook_support.py"):   # 2026-10-05: the gate's sibling modules
        if (_HOOK_SRC.parent / _sib).is_file():
            shutil.copy(_HOOK_SRC.parent / _sib, hook_copy.parent / _sib)
    _approve(hookroot)

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


def _write_transcript_with_errors(transcript: pathlib.Path, tool_uses):
    """Like _write_transcript, but each item is (name, input, is_error) and gets an `id`
    on its tool_use block plus a paired tool_result message right after it — the shape a
    real transcript uses to report whether a call actually succeeded. `_write_transcript`
    itself never sets `id`, so plain tool_use blocks from it can never collide with an
    error id collected here."""
    lines = []
    for i, (name, inp, is_error) in enumerate(tool_uses):
        tool_id = f"toolu_test_{i}"
        lines.append(json.dumps({"message": {"content": [
            {"type": "tool_use", "id": tool_id, "name": name, "input": inp}]}}))
        lines.append(json.dumps({"message": {"content": [
            {"type": "tool_result", "tool_use_id": tool_id, "is_error": is_error,
             "content": "error" if is_error else "ok"}]}}))
    transcript.write_text("".join(line + "\n" for line in lines), encoding="utf-8")


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


class TestProjectScratchDirIsExempt:
    """_is_scratch_path (added 2026-09-09, PROJECT-A live incident).

    Defect: NON_CLOSEOUT_PREFIXES only ever listed FIXED /tmp/-style prefixes. A
    project's own scratch/ directory (e.g. `<project>/scratch/svg_to_png.html`) sits at
    a different absolute path per project, so no fixed-prefix entry could ever cover
    it — the exact rationale NON_CLOSEOUT_PREFIXES itself documents (a scratchpad
    artifact verified by non-Bash means is structurally unclearable) applied to a path
    the deny-list did not yet reach, and it re-blocked identically for 4 consecutive
    turns in the same session.

    Fix under test: any path with a `scratch` path component, anywhere, is exempt —
    segment-matched (Path(...).parts), not substring-matched, so it must NOT catch an
    unrelated name that merely contains "scratch" (e.g. "scratchpad-notes").
    """

    PROJECT_SCRATCH_HTML = "/workspace/example/Project X/scratch/svg_to_png.html"
    NESTED_SCRATCH_PY = "/workspace/example/Project X/scratch/sub/dir/throwaway.py"
    LOOKALIKE_NOT_SCRATCH = "/workspace/example/Project X/scratchpad-notes/app.py"

    def test_project_scratch_dir_html_does_not_block(self, tmp_path):
        hook, ws, proj, transcript = _fixture(tmp_path, WITHIN)
        _write_transcript(transcript, [
            ("Write", {"file_path": self.PROJECT_SCRATCH_HTML, "content": "<p>x</p>"}),
        ])
        r = _run_hook(hook, cwd=proj, transcript=transcript, project_dir_env=proj)
        assert r.returncode == 0, f"hook crashed: {r.stderr[:400]}"
        assert r.stdout.strip() == "", (
            f"project scratch/ artifact false-blocked: {r.stdout!r}")

    def test_nested_path_under_scratch_dir_is_also_exempt(self, tmp_path):
        hook, ws, proj, transcript = _fixture(tmp_path, WITHIN)
        _write_transcript(transcript, [
            ("Write", {"file_path": self.NESTED_SCRATCH_PY, "content": "x = 1"}),
        ])
        r = _run_hook(hook, cwd=proj, transcript=transcript, project_dir_env=proj)
        assert r.returncode == 0, f"hook crashed: {r.stderr[:400]}"
        assert r.stdout.strip() == "", (
            f"nested scratch/ path false-blocked: {r.stdout!r}")

    def test_scratchpad_lookalike_directory_is_not_exempt(self, tmp_path):
        """A directory that merely CONTAINS the substring "scratch" (e.g.
        scratchpad-notes/) must NOT be exempted — only an exact `scratch` component."""
        hook, ws, proj, transcript = _fixture(tmp_path, WITHIN)
        _write_transcript(transcript, [
            ("Write", {"file_path": self.LOOKALIKE_NOT_SCRATCH, "content": "x = 1"}),
        ])
        r = _run_hook(hook, cwd=proj, transcript=transcript, project_dir_env=proj)
        assert r.returncode == 0, f"hook crashed: {r.stderr[:400]}"
        assert '"decision": "block"' in r.stdout, (
            f"a scratchpad-notes/ lookalike was wrongly exempted: {r.stdout!r}")

    def test_scratch_dir_via_bash_write_is_also_exempt(self, tmp_path):
        """The exemption must apply through BOTH detection paths — the Edit/Write/
        NotebookEdit branch (tested above) and the Bash-write-target branch (M-13)."""
        hook, ws, proj, transcript = _fixture(tmp_path, WITHIN)
        _write_transcript(transcript, [
            ("Bash", {"command": f"cat > \"{self.PROJECT_SCRATCH_HTML}\" << 'EOF'\n<p>x</p>\nEOF"}),
        ])
        r = _run_hook(hook, cwd=proj, transcript=transcript, project_dir_env=proj)
        assert r.returncode == 0, f"hook crashed: {r.stderr[:400]}"
        assert r.stdout.strip() == "", (
            f"scratch/ Bash write false-blocked: {r.stdout!r}")


class TestVerifyArmed:
    def test_verify_armed_reads_from_project_not_cwd(self, tmp_path):
        import sys
        import pathlib
        sys.path.insert(0, str(pathlib.Path(__file__).parent))
        import stop_closeout_gate
        
        ws = tmp_path / "ws"
        proj = ws / "proj"
        (proj / "NEOCORTEX").mkdir(parents=True)
        (proj / "CLAUDE.md").touch()
        
        verify_dir = proj / ".verify"
        verify_dir.mkdir()
        (verify_dir / "armed").touch()
        
        edited = [str(proj / "src" / "main.py")]
        
        assert stop_closeout_gate._verify_armed(edited) is True

class TestVerifyMarkersM10b:
    def test_echo_test_bypass_is_blocked(self, tmp_path):
        import sys
        import pathlib
        sys.path.insert(0, str(pathlib.Path(__file__).parent))
        import stop_closeout_gate
        
        hook, ws, proj, transcript = _fixture(tmp_path, WITHIN)
        _write_transcript(transcript, [
            ("Edit", {"file_path": "/workspace/example/Project X/src/main.py"}),
            ("Bash", {"command": "echo test_bypass"}),
        ])
        
        r = _run_hook(hook, cwd=proj, transcript=transcript, project_dir_env=proj)
        # Should be BLOCKED because "echo test_bypass" does not clear the verification window
        assert '"decision": "block"' in r.stdout
        assert "since the last verification command" in r.stdout

    def test_real_verification_command_clears_window(self, tmp_path):
        import stop_closeout_gate
        hook, ws, proj, transcript = _fixture(tmp_path, WITHIN)
        _write_transcript(transcript, [
            ("Edit", {"file_path": "/workspace/example/Project X/src/main.py"}),
            ("Bash", {"command": "pytest test_main.py"}),
        ])
        
        r = _run_hook(hook, cwd=proj, transcript=transcript, project_dir_env=proj)
        # Should PASS because "pytest" is a valid verify marker not stripped
        assert r.stdout.strip() == ""


class TestBashEditDetection:
    """M-13 (founder ruling 2026-09-04): CLOSEOUT-GATE widened past Edit/Write/NotebookEdit
    to also see Bash-based edits (heredoc, sed -i, tee, cp/mv/rsync, dd of=) via the same
    tokenized write-target technique already proven in pretool_guard.py's l0_write_target().

    Fails-before-the-fix proof: test_heredoc_write_without_verification_blocks is exactly
    the CRUMBLES.md 2026-08-26 scenario (a session editing exclusively through Bash) and
    would have been silent pre-fix — `edited_code` stayed empty because Bash tool_use
    blocks were only ever scanned for VERIFY_MARKERS, never for writes.
    """

    REAL_PY = "/workspace/example/Project X/src/app.py"
    SCRATCH_PY = "/private/tmp/claude-501/abc123/scratchpad/throwaway.py"

    def test_heredoc_write_without_verification_blocks(self, tmp_path):
        hook, ws, proj, transcript = _fixture(tmp_path, WITHIN)
        _write_transcript(transcript, [
            ("Bash", {"command": f"cat > \"{self.REAL_PY}\" << 'EOF'\nprint('x')\nEOF"}),
        ])
        r = _run_hook(hook, cwd=proj, transcript=transcript, project_dir_env=proj)
        assert r.returncode == 0, f"hook crashed: {r.stderr[:400]}"
        assert '"decision": "block"' in r.stdout, (
            f"Bash-only edit did not block — the gap this fix closes: {r.stdout!r}")
        assert "app.py" in r.stdout

    def test_heredoc_write_then_verify_command_clears_window(self, tmp_path):
        hook, ws, proj, transcript = _fixture(tmp_path, WITHIN)
        _write_transcript(transcript, [
            ("Bash", {"command": f"cat > \"{self.REAL_PY}\" << 'EOF'\nprint('x')\nEOF"}),
            ("Bash", {"command": "python3.12 -m pytest app.py"}),
        ])
        r = _run_hook(hook, cwd=proj, transcript=transcript, project_dir_env=proj)
        assert r.returncode == 0, f"hook crashed: {r.stderr[:400]}"
        assert r.stdout.strip() == "", f"false block after real verification: {r.stdout!r}"

    def test_sed_inplace_edit_without_verification_blocks(self, tmp_path):
        hook, ws, proj, transcript = _fixture(tmp_path, WITHIN)
        _write_transcript(transcript, [
            ("Bash", {"command": f"sed -i '' 's/a/b/' \"{self.REAL_PY}\""}),
        ])
        r = _run_hook(hook, cwd=proj, transcript=transcript, project_dir_env=proj)
        assert r.returncode == 0, f"hook crashed: {r.stderr[:400]}"
        assert '"decision": "block"' in r.stdout, f"sed -i did not block: {r.stdout!r}"
        assert "app.py" in r.stdout
        assert "s/a/b/" not in r.stdout, (
            f"the sed expression itself leaked in as a fake target: {r.stdout!r}")

    def test_scratchpad_bash_write_stays_exempt(self, tmp_path):
        hook, ws, proj, transcript = _fixture(tmp_path, WITHIN)
        _write_transcript(transcript, [
            ("Bash", {"command": f"cat > {self.SCRATCH_PY} << 'EOF'\nx = 1\nEOF"}),
        ])
        r = _run_hook(hook, cwd=proj, transcript=transcript, project_dir_env=proj)
        assert r.returncode == 0, f"hook crashed: {r.stderr[:400]}"
        assert r.stdout.strip() == "", (
            f"scratchpad Bash write false-blocked: {r.stdout!r}")

    def test_quoted_path_with_spaces_is_detected(self, tmp_path):
        """The exact motivating case for tokenization over a plain regex (documented in
        pretool_guard.py's L0 guard): a write target whose path contains spaces must not
        slip through a naive `>\\s*\\S+` match."""
        spaced = "/workspace/example/Project X/src/my script.py"
        hook, ws, proj, transcript = _fixture(tmp_path, WITHIN)
        _write_transcript(transcript, [
            ("Bash", {"command": f'cat > "{spaced}" << \'EOF\'\nprint(1)\nEOF'}),
        ])
        r = _run_hook(hook, cwd=proj, transcript=transcript, project_dir_env=proj)
        assert r.returncode == 0, f"hook crashed: {r.stderr[:400]}"
        assert '"decision": "block"' in r.stdout, (
            f"quoted spaced path was not detected: {r.stdout!r}")
        assert "my script.py" in r.stdout

    def test_read_only_bash_commands_are_not_flagged(self, tmp_path):
        hook, ws, proj, transcript = _fixture(tmp_path, WITHIN)
        _write_transcript(transcript, [
            ("Bash", {"command": f"cat \"{self.REAL_PY}\""}),
            ("Bash", {"command": f"grep foo \"{self.REAL_PY}\""}),
            ("Bash", {"command": f"python3.12 \"{self.REAL_PY}\""}),
        ])
        r = _run_hook(hook, cwd=proj, transcript=transcript, project_dir_env=proj)
        assert r.returncode == 0, f"hook crashed: {r.stderr[:400]}"
        assert r.stdout.strip() == "", f"a read was misdetected as a write: {r.stdout!r}"

    def test_rm_after_write_clears_the_pending_set(self, tmp_path):
        """2026-09-09 live incident (PROJECT-A): a Write, then an `rm -f` of the exact same
        path several turns later, then no verification command anywhere — pre-fix this
        blocked forever, because `rm` was invisible to write-detection and nothing else
        removed the path from `edited_since_verify`. Post-fix: `rm` clears it, so the
        session stays silent with nothing left to verify."""
        hook, ws, proj, transcript = _fixture(tmp_path, WITHIN)
        _write_transcript(transcript, [
            ("Write", {"file_path": self.REAL_PY, "content": "print(1)"}),
            ("Bash", {"command": f'rm -f "{self.REAL_PY}"'}),
        ])
        r = _run_hook(hook, cwd=proj, transcript=transcript, project_dir_env=proj)
        assert r.returncode == 0, f"hook crashed: {r.stderr[:400]}"
        assert r.stdout.strip() == "", (
            f"deleted file still blocked turn-end: {r.stdout!r}")

    def test_rm_of_different_file_does_not_clear_an_unrelated_edit(self, tmp_path):
        """The other direction: `rm` must only clear the EXACT path it removes, never
        the whole pending set — an unrelated real edit must keep blocking."""
        other = "/workspace/example/Project X/src/other.py"
        hook, ws, proj, transcript = _fixture(tmp_path, WITHIN)
        _write_transcript(transcript, [
            ("Write", {"file_path": self.REAL_PY, "content": "print(1)"}),
            ("Bash", {"command": f'rm -f "{other}"'}),
        ])
        r = _run_hook(hook, cwd=proj, transcript=transcript, project_dir_env=proj)
        assert r.returncode == 0, f"hook crashed: {r.stderr[:400]}"
        assert '"decision": "block"' in r.stdout, (
            f"an unrelated rm wrongly cleared a real pending edit: {r.stdout!r}")
        assert "app.py" in r.stdout

    def test_bash_write_then_rm_via_bash_also_clears(self, tmp_path):
        """Both halves of the fix together: the file is created via a Bash heredoc
        (M-13's own detection path, not the Edit/Write/NotebookEdit branch), then
        removed via a separate Bash `rm` — must also clear."""
        hook, ws, proj, transcript = _fixture(tmp_path, WITHIN)
        _write_transcript(transcript, [
            ("Bash", {"command": f"cat > \"{self.REAL_PY}\" << 'EOF'\nprint('x')\nEOF"}),
            ("Bash", {"command": f'rm "{self.REAL_PY}"'}),
        ])
        r = _run_hook(hook, cwd=proj, transcript=transcript, project_dir_env=proj)
        assert r.returncode == 0, f"hook crashed: {r.stderr[:400]}"
        assert r.stdout.strip() == "", (
            f"Bash-written-then-Bash-removed file still blocked: {r.stdout!r}")

    def test_heredoc_body_false_positive_is_the_known_tradeoff(self, tmp_path):
        """Documents, rather than hides, the stated limitation: this tokenizer has no
        heredoc-body awareness, so a body containing a literal `;` followed by text that
        reads like a write statement creates a new (fake) segment. This test pins the
        CURRENT, accepted behavior — over-inclusion, never under-detection — so a future
        change to it is noticed rather than silently drifting. If this starts failing
        because the false positive stopped happening, that is an improvement; update the
        assertion rather than reintroducing the false positive to make it pass again."""
        hook, ws, proj, transcript = _fixture(tmp_path, WITHIN)
        _write_transcript(transcript, [
            ("Bash", {"command": (
                "cat > notes.md << 'EOF'\n"
                f"Some text; sed -i 's/x/y/' \"{self.REAL_PY}\"\n"
                "EOF")}),
        ])
        r = _run_hook(hook, cwd=proj, transcript=transcript, project_dir_env=proj)
        assert r.returncode == 0, f"hook crashed: {r.stderr[:400]}"
        # 2026-10-05 (hooks batch 2, Seat B B-16): writes are now read per segment from the
        # heredoc-stripped command, so the heredoc BODY no longer creates a fake write. The
        # limitation this test documented is fixed; per its own instruction, the assertion is updated.
        assert r.stdout.strip() == "", f"heredoc body was again treated as a write: {r.stdout!r}"


class TestErroredToolCallNotCountedAsEdit:
    """2026-09-14 live incident (example-project): a mistyped Edit call to a path that was
    missing a directory segment errored with "No changes to make" (identical old_string/
    new_string, nothing on disk to touch) — no write ever happened, yet the path kept
    re-appearing in the CLOSEOUT-GATE block message turns later. Root cause: a tool_use
    block is written the moment a call is MADE, before its result is known; the gate was
    reading tool_use alone and had no notion of whether the call actually succeeded."""

    REAL_PY = "/workspace/example/Project X/src/app.py"

    def test_errored_edit_is_not_counted(self, tmp_path):
        hook, ws, proj, transcript = _fixture(tmp_path, WITHIN)
        _write_transcript_with_errors(transcript, [
            ("Edit", {"file_path": self.REAL_PY, "old_string": "x", "new_string": "x"}, True),
        ])
        r = _run_hook(hook, cwd=proj, transcript=transcript, project_dir_env=proj)
        assert r.returncode == 0, f"hook crashed: {r.stderr[:400]}"
        assert r.stdout.strip() == "", (
            f"an errored (no-op) Edit call was still treated as a real edit: {r.stdout!r}")

    def test_errored_bash_write_is_not_counted(self, tmp_path):
        """Same fix, Bash-edit-detection path (M-13) — a heredoc write whose Bash call
        itself errored (e.g. permission denied) must not seed edited_since_verify either."""
        hook, ws, proj, transcript = _fixture(tmp_path, WITHIN)
        _write_transcript_with_errors(transcript, [
            ("Bash", {"command": f"cat > \"{self.REAL_PY}\" << 'EOF'\nprint('x')\nEOF"}, True),
        ])
        r = _run_hook(hook, cwd=proj, transcript=transcript, project_dir_env=proj)
        assert r.returncode == 0, f"hook crashed: {r.stderr[:400]}"
        assert r.stdout.strip() == "", (
            f"an errored Bash write was still treated as a real edit: {r.stdout!r}")

    def test_successful_edit_after_errored_one_still_blocks(self, tmp_path):
        """The fix must not overreach — only the specific errored tool_use id is skipped;
        a genuinely successful edit right after it must still block as normal."""
        hook, ws, proj, transcript = _fixture(tmp_path, WITHIN)
        _write_transcript_with_errors(transcript, [
            ("Edit", {"file_path": self.REAL_PY, "old_string": "x", "new_string": "x"}, True),
            ("Edit", {"file_path": self.REAL_PY, "old_string": "a", "new_string": "b"}, False),
        ])
        r = _run_hook(hook, cwd=proj, transcript=transcript, project_dir_env=proj)
        assert r.returncode == 0, f"hook crashed: {r.stderr[:400]}"
        assert '"decision": "block"' in r.stdout, (
            f"a genuinely successful edit was wrongly swallowed: {r.stdout!r}")
        assert "app.py" in r.stdout


class TestBrowserVerificationClearsWindow:
    """2026-09-14 live incident (example-project, a static HTML/CSS/JS page with no pytest/
    compile step of its own): real edits verified by actually rendering the page in the
    Browser pane (console errors read, DOM queried via JS, a screenshot taken) kept
    re-blocking every later turn — nothing in VERIFY_MARKERS is a Browser-pane MCP tool,
    so the gate could only ever recognize a Bash-invoked test runner as 'verification'."""

    REAL_HTML = "/workspace/example/Project X/index.html"

    def test_console_check_clears_the_window(self, tmp_path):
        hook, ws, proj, transcript = _fixture(tmp_path, WITHIN)
        _write_transcript(transcript, [
            ("Edit", {"file_path": self.REAL_HTML}),
            ("mcp__Claude_Browser__read_console_messages", {"onlyErrors": True}),
        ])
        r = _run_hook(hook, cwd=proj, transcript=transcript, project_dir_env=proj)
        assert r.returncode == 0, f"hook crashed: {r.stderr[:400]}"
        assert r.stdout.strip() == "", (
            f"a real browser console check did not clear the window: {r.stdout!r}")

    def test_javascript_eval_clears_the_window(self, tmp_path):
        hook, ws, proj, transcript = _fixture(tmp_path, WITHIN)
        _write_transcript(transcript, [
            ("Edit", {"file_path": self.REAL_HTML}),
            ("mcp__Claude_Browser__javascript_tool", {"action": "javascript_exec", "text": "1+1"}),
        ])
        r = _run_hook(hook, cwd=proj, transcript=transcript, project_dir_env=proj)
        assert r.returncode == 0, f"hook crashed: {r.stderr[:400]}"
        assert r.stdout.strip() == "", (
            f"a JS-eval DOM check did not clear the window: {r.stdout!r}")

    def test_screenshot_clears_the_window(self, tmp_path):
        hook, ws, proj, transcript = _fixture(tmp_path, WITHIN)
        _write_transcript(transcript, [
            ("Edit", {"file_path": self.REAL_HTML}),
            ("mcp__Claude_Browser__computer", {"action": "screenshot"}),
        ])
        r = _run_hook(hook, cwd=proj, transcript=transcript, project_dir_env=proj)
        assert r.returncode == 0, f"hook crashed: {r.stderr[:400]}"
        assert r.stdout.strip() == "", (
            f"a screenshot check did not clear the window: {r.stdout!r}")

    def test_non_screenshot_computer_action_does_not_clear_the_window(self, tmp_path):
        """A click or a keystroke is an interaction, not verification — it must NOT
        clear the window on its own (only a screenshot, or one of the read-only
        BROWSER_VERIFY_TOOLS, actually inspects anything)."""
        hook, ws, proj, transcript = _fixture(tmp_path, WITHIN)
        _write_transcript(transcript, [
            ("Edit", {"file_path": self.REAL_HTML}),
            ("mcp__Claude_Browser__computer", {"action": "left_click", "coordinate": [1, 2]}),
        ])
        r = _run_hook(hook, cwd=proj, transcript=transcript, project_dir_env=proj)
        assert r.returncode == 0, f"hook crashed: {r.stderr[:400]}"
        assert '"decision": "block"' in r.stdout, (
            f"a mere click was wrongly treated as verification: {r.stdout!r}")

#!/usr/bin/env python3
"""
test_stop_closeout_version_drift.py — fire/no-fire tests for the C3 skill
version-drift gate in stop_closeout_gate.py (Art. 12: hooks are periodically
re-tested with fire AND no-fire cases).

Defect this gate closes (2026-07-28): `build_index.py` already compared a skill's
frontmatter / title-line / metadata.json versions and `--strict` already exited 1 —
but NOTHING consumed that signal. The only automated caller (skill_index_regen.py)
runs lenient by contract and always exits 0, so the drift printed to stderr on every
run and was never read. `linkedin_scraper` sat split across FOUR sources
(frontmatter 2.3.0 / title v2.1 / metadata.json 2.2.0 / scripts/__init__.py 1.0.0).
An unread warning is not a gate.

The hook runs as a subprocess from a COPY inside a fixture tree, so ROOT — and
therefore the `_skills/` the gate sweeps and the telemetry file it appends to —
are the fixture's, never the real workspace's.

Run:
    python3.12 -m pytest .claude/hooks/test_stop_closeout_version_drift.py -v
"""

import json
import os
import pathlib
import shutil
import pytest
import subprocess
import sys

_HERE = pathlib.Path(__file__).resolve().parent
_HOOK_SRC = _HERE / "stop_closeout_gate.py"
def _locate_build_index():
    """Find _skills/build_index.py by searching upward, not by a fixed relative hop.

    `_HERE.parent.parent / "_skills"` is correct for exactly one layout (hooks at
    <root>/.claude/hooks/). In the PUBLISHED kit the hooks sit at
    adapters/claude-code/hooks/, so the same hop lands on adapters/_skills/ — which does
    not exist — and 12 of the 58 shipped tests died with FileNotFoundError on any clean
    clone. All three §5.5 reviewers hit it independently.
    """
    for parent in _HERE.parents:
        cand = parent / "_skills" / "build_index.py"
        if cand.is_file():
            return cand
    return None


_BUILD_INDEX_SRC = _locate_build_index()


def _fixture(tmp_path, skills: dict, with_build_index: bool = True):
    """Build a fixture workspace.

    `skills` maps skill-name -> (frontmatter_version, title_version_or_None,
    metadata_version_or_None). A metadata version of None writes NO metadata.json
    (the _NO_META sentinel path — absent metadata must never count as a mismatch).

    Returns (hook_copy, ws, transcript).
    """
    hookroot = tmp_path / "hookroot"
    hooks_dir = hookroot / ".claude" / "hooks"
    hooks_dir.mkdir(parents=True)
    hook_copy = hooks_dir / "stop_closeout_gate.py"
    shutil.copy(_HOOK_SRC, hook_copy)

    skills_dir = hookroot / "_skills"
    skills_dir.mkdir()
    if with_build_index:
        if _BUILD_INDEX_SRC is None:
            pytest.skip("_skills/build_index.py not found in any ancestor — "
                        "the C3 version-drift gate cannot be exercised without it")
        shutil.copy(_BUILD_INDEX_SRC, skills_dir / "build_index.py")

    for name, (fm_v, title_v, meta_v) in skills.items():
        d = skills_dir / name
        d.mkdir()
        title = f"# SKILL: {name}" + (f" (v{title_v})" if title_v else "")
        (d / "SKILL.md").write_text(
            f"---\nname: {name}\nversion: {fm_v}\nkind: library\nstatus: active\n"
            f"description: fixture skill.\n---\n\n{title}\n\n## Purpose\nFixture.\n",
            encoding="utf-8")
        if meta_v is not None:
            (d / "metadata.json").write_text(
                json.dumps({"name": name, "version": meta_v}), encoding="utf-8")

    # A governed project so the hook's other checks are exercised but stay quiet.
    ws = tmp_path / "ws"
    proj = ws / "proj"
    (proj / "NEOCORTEX").mkdir(parents=True)
    (ws / "CLAUDE.md").write_text("# workspace root\n", encoding="utf-8")
    (proj / "CLAUDE.md").write_text("# project\n", encoding="utf-8")
    (proj / "NEOCORTEX" / "JOURNAL.md").write_bytes(b"x" * 1024)   # well within 64 KB

    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text("", encoding="utf-8")   # no tool calls → no code-edit problem
    return hook_copy, ws, transcript


def _run(hook, cwd, transcript):
    env = dict(os.environ)
    env.pop("CLAUDE_PROJECT_DIR", None)
    return subprocess.run([sys.executable, str(hook)],
                          input=json.dumps({"transcript_path": str(transcript)}),
                          capture_output=True, text=True, cwd=str(cwd), env=env, timeout=60)


def _blocked(r):
    """(did_block, reason). The hook blocks by printing a decision JSON and exiting 0."""
    for line in (r.stdout or "").splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if d.get("decision") == "block":
            return True, d.get("reason", "")
    return False, ""


class TestVersionDriftFires:

    def test_title_line_drift_blocks(self, tmp_path):
        hook, ws, tr = _fixture(tmp_path, {"alpha": ("2.3.0", "2.1", "2.3.0")})
        blocked, reason = _blocked(_run(hook, ws, tr))
        assert blocked, "a frontmatter/title split must block turn-end"
        assert "alpha" in reason and "title-line" in reason

    def test_metadata_drift_blocks(self, tmp_path):
        hook, ws, tr = _fixture(tmp_path, {"beta": ("2.3.0", "2.3", "2.2.0")})
        blocked, reason = _blocked(_run(hook, ws, tr))
        assert blocked, "a frontmatter/metadata.json split must block turn-end"
        assert "beta" in reason and "metadata.json" in reason

    def test_reason_names_the_ssot_and_the_command(self, tmp_path):
        hook, ws, tr = _fixture(tmp_path, {"gamma": ("2.3.0", "2.1", "2.3.0")})
        _, reason = _blocked(_run(hook, ws, tr))
        assert "R-SHARED-4" in reason, "must name the SSOT rule, not just complain"
        assert "build_index.py --strict" in reason, "must give the reproduction command"

    def test_multiple_offenders_are_summarised_not_dumped(self, tmp_path):
        skills = {f"s{i}": ("2.0.0", "1.0", "2.0.0") for i in range(6)}
        hook, ws, tr = _fixture(tmp_path, skills)
        blocked, reason = _blocked(_run(hook, ws, tr))
        assert blocked
        assert "more)" in reason, "should summarise beyond the first few, not dump all"


class TestVersionDriftDoesNotFire:

    def test_aligned_versions_do_not_block(self, tmp_path):
        hook, ws, tr = _fixture(tmp_path, {"alpha": ("2.3.0", "2.3", "2.3.0")})
        blocked, reason = _blocked(_run(hook, ws, tr))
        assert not blocked, f"aligned skill must not block; got: {reason}"

    def test_abbreviated_title_is_not_drift(self, tmp_path):
        # 'v3.10' against frontmatter '3.10.4' is the fleet's normal convention.
        hook, ws, tr = _fixture(tmp_path, {"cal": ("3.10.4", "3.10", "3.10.4")})
        blocked, reason = _blocked(_run(hook, ws, tr))
        assert not blocked, f"component-truncated title must not count as drift; got: {reason}"

    def test_absent_metadata_json_is_not_drift(self, tmp_path):
        hook, ws, tr = _fixture(tmp_path, {"nometa": ("1.2.3", "1.2", None)})
        blocked, reason = _blocked(_run(hook, ws, tr))
        assert not blocked, f"absent metadata.json must be skipped, not flagged; got: {reason}"

    def test_titleless_skill_is_not_drift(self, tmp_path):
        hook, ws, tr = _fixture(tmp_path, {"notitle": ("1.2.3", None, "1.2.3")})
        blocked, reason = _blocked(_run(hook, ws, tr))
        assert not blocked, f"a title with no (vX.Y) token contradicts nothing; got: {reason}"


class TestFailOpen:

    def test_missing_build_index_does_not_block(self, tmp_path):
        """The guard must never wedge turn-end on its own unavailability."""
        hook, ws, tr = _fixture(tmp_path, {"alpha": ("2.3.0", "2.1", "2.2.0")},
                                with_build_index=False)
        blocked, reason = _blocked(_run(hook, ws, tr))
        assert not blocked, f"missing build_index.py must fail OPEN; got: {reason}"

    def test_unparseable_metadata_does_not_block(self, tmp_path):
        """Corrupt metadata.json is a different defect — not this gate's to raise."""
        hook, ws, tr = _fixture(tmp_path, {"alpha": ("2.3.0", "2.3", "2.3.0")})
        (tmp_path / "hookroot" / "_skills" / "alpha" / "metadata.json").write_text(
            "{not json", encoding="utf-8")
        blocked, reason = _blocked(_run(hook, ws, tr))
        assert not blocked, f"unparseable metadata must fail open; got: {reason}"

    def test_no_skills_dir_does_not_block(self, tmp_path):
        hook, ws, tr = _fixture(tmp_path, {})
        shutil.rmtree(tmp_path / "hookroot" / "_skills")
        blocked, reason = _blocked(_run(hook, ws, tr))
        assert not blocked, f"absent _skills/ must fail open; got: {reason}"

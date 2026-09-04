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


# ── M-9's staleness advisory (closeout_lint), wired 2026-08-26 ────────────────────────────
def test_staleness_advisory_FIRES_on_a_project_with_findings(tmp_path):
    """FIRES: a project whose MANIFEST carries a genre word in the status field.

    `closeout_lint`'s status-enum assertion is the cheapest one to reproduce here. What is
    being tested is the WIRING, not the check — the check has its own 41-test suite.
    """
    mod = _load()
    proj = _make_project(tmp_path, dirty=False)
    mf = proj / "NEOCORTEX" / "MANIFEST.json"
    d = json.loads(mf.read_text())
    d["files"] = [{"file": "x.md", "genre": "PLAN", "status": "REFLECTION", "hook": ""}]
    mf.write_text(json.dumps(d), encoding="utf-8")
    out = mod._staleness_advisory(proj, ledger_path=tmp_path / "ledger.jsonl")
    assert "finding(s)" in out, out


def test_staleness_advisory_SILENT_on_a_clean_project(tmp_path):
    """SILENT: a linter that never says nothing teaches people to ignore it."""
    mod = _load()
    proj = _make_project(tmp_path, dirty=False)
    assert mod._staleness_advisory(proj, ledger_path=tmp_path / "ledger.jsonl") == ""


def test_staleness_advisory_FAILS_OPEN_when_the_linter_is_missing(tmp_path, monkeypatch):
    """A hook must never raise. Missing script => "", and it ANNOUNCES that it is disabled.

    One silent half is all it takes for "disabled" to read as "clean" — the asymmetry the
    §5.5 re-review found in this hook's sibling check.
    """
    mod = _load()
    proj = _make_project(tmp_path, dirty=False)
    monkeypatch.setattr(mod, "ROOT", tmp_path / "nowhere")
    assert mod._staleness_advisory(proj, ledger_path=tmp_path / "ledger.jsonl") == ""


def test_staleness_advisory_can_never_block_a_turn(tmp_path):
    """THE PROPERTY THAT MATTERS on a live hook: advisory findings never enter `problems`.

    Which checks may BLOCK is a founder ruling, still owed. Being CONNECTED is not, and a
    wire that could block would have made this a decision instead of a build.
    """
    mod = _load()
    proj = _make_project(tmp_path, dirty=False)
    mf = proj / "NEOCORTEX" / "MANIFEST.json"
    d = json.loads(mf.read_text())
    d["files"] = [{"file": "x.md", "genre": "PLAN", "status": "REFLECTION", "hook": ""}]
    mf.write_text(json.dumps(d), encoding="utf-8")
    src = HOOK.read_text(encoding="utf-8")
    body = src[src.index("for proj_root in _advisory_roots("):src.index("    if problems:")]
    assert "problems" not in body, body


def test_advisory_roots_resolve_from_EDITED_FILES_not_cwd(tmp_path, monkeypatch):
    """THE DEAD CHANNEL, found by running the hook instead of trusting its tests.

    `_journal_project_root()` needs `$CLAUDE_PROJECT_DIR` or an ancestor of cwd holding BOTH
    `CLAUDE.md` and `NEOCORTEX/`. A session rooted at the WORKSPACE ROOT matches neither — the
    root has `CLAUDE.md` but no `NEOCORTEX/`, and the harness leaves the variable unset — so
    both advisories were skipped in silence. Measured: the hook exited 0 with no output on a
    payload that plainly edited a governed file. Fifth dead escalation channel in this
    workstream, and it would have shipped as "connected".
    """
    mod = _load()
    proj = _make_project(tmp_path, dirty=False)
    edited = str(proj / "src" / "thing.py")
    (proj / "src").mkdir()
    Path(edited).write_text("x\n", encoding="utf-8")
    assert mod._advisory_roots([edited], None) == [proj]


def test_advisory_roots_fall_back_when_nothing_was_edited(tmp_path):
    """A turn with no edits still advises on the resolvable project, as before."""
    mod = _load()
    proj = _make_project(tmp_path, dirty=False)
    assert mod._advisory_roots([], proj) == [proj]
    assert mod._advisory_roots([], None) == []


def test_advisory_roots_are_bounded(tmp_path):
    """Turn-end cost is bounded: each project costs ~0.3-1 s, so at most three are advised."""
    mod = _load()
    edited = []
    for n in range(5):
        proj = _make_project(tmp_path / f"w{n}", dirty=False)
        f = proj / "thing.py"
        f.write_text("x\n", encoding="utf-8")
        edited.append(str(f))
    assert len(mod._advisory_roots(edited, None)) == 3


def test_hook_survives_a_turn_with_no_resolvable_project(tmp_path):
    """REGRESSION: the first cut of this loop left `advisory` unbound when no project resolved.

    Every turn without an edited governed file would have raised NameError inside the hook.
    It passed a hand payload only because one project leaked the variable out of the loop.
    """
    mod = _load()
    tr = tmp_path / "t.jsonl"
    tr.write_text("", encoding="utf-8")
    payload = json.dumps({"transcript_path": str(tr)})
    r = subprocess.run([sys.executable, str(HOOK)], input=payload,
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, (r.returncode, r.stderr)
    assert "Traceback" not in r.stderr, r.stderr


# ── per-check attribution, added 2026-08-27 ───────────────────────────────────────────────
def test_staleness_advisory_NAMES_THE_CHECK_not_just_a_total(tmp_path):
    """The advisory must say WHICH check fired, not only how many findings there were.

    WHY THIS EXISTS. For its first 35 logged firings the advisory recorded a bare total, so the
    telemetry could never answer the one question the blocking ruling turns on: which checks
    over-fire on real traffic? The founder's 2026-08-26 ruling admitted six git-grounded checks
    because they sat at zero findings on a clean tree that day — a one-day snapshot. One of them
    (`series-count`, 18% measured precision) has since produced a finding on the governance
    project itself that was a filing CONVENTION and not a defect: it would have refused a correct
    commit. Attribution is the input to re-measuring that, and traffic you cannot attribute
    measures nothing.
    """
    mod = _load()
    proj = _make_project(tmp_path, dirty=False)
    mf = proj / "NEOCORTEX" / "MANIFEST.json"
    d = json.loads(mf.read_text())
    d["files"] = [{"file": "x.md", "genre": "PLAN", "status": "REFLECTION", "hook": ""}]
    mf.write_text(json.dumps(d), encoding="utf-8")
    out = mod._staleness_advisory(proj, ledger_path=tmp_path / "ledger.jsonl")
    assert "finding(s)" in out, out                       # the total still there — no regression
    assert "[" in out and "]" in out, f"no breakdown: {out!r}"
    assert "status-enum=" in out, f"breakdown does not NAME the check: {out!r}"


def test_staleness_advisory_WRITES_the_ledger_CONTENT_not_just_a_return_value(tmp_path):
    """The gap an independent verify_gate review found (2026-08-28): every test exercising this
    path injects `ledger_path=tmp_path/...` to keep it out of the shared real file, but none of
    them actually opened it back up -- so a regression in _append_ledger's JSONL shape, its
    check+path+detail hash id, or its append-not-overwrite behavior would pass every test here
    untouched, because they all assert only on the returned SUMMARY STRING, which is computed
    independently of what actually lands on disk."""
    mod = _load()
    proj = _make_project(tmp_path, dirty=False)
    mf = proj / "NEOCORTEX" / "MANIFEST.json"
    d = json.loads(mf.read_text())
    d["files"] = [{"file": "x.md", "genre": "PLAN", "status": "REFLECTION", "hook": ""}]
    mf.write_text(json.dumps(d), encoding="utf-8")
    ledger = tmp_path / "ledger.jsonl"

    mod._staleness_advisory(proj, ledger_path=ledger)
    lines = [ln for ln in ledger.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) == 1, lines
    rec = json.loads(lines[0])
    assert rec["check"] == "status-enum"
    assert rec["proj"] == proj.name
    assert set(rec) == {"id", "ts", "proj", "check", "path", "line", "detail"}
    first_id = rec["id"]

    # APPEND, not overwrite: a second call for the same still-present finding adds a second
    # line and keeps the SAME id (the whole point of hashing on check+path+detail, not line).
    mod._staleness_advisory(proj, ledger_path=ledger)
    lines2 = [ln for ln in ledger.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines2) == 2, lines2
    assert json.loads(lines2[1])["id"] == first_id


def test_staleness_advisory_IS_NOT_SILENTLY_SWALLOWED_when_findings_exist(tmp_path):
    """The bug this test exists for was mine, and fail-open hid it completely.

    Adding the attribution above, I used `re.findall` in a module that does not import `re`.
    The NameError was caught by the function's blanket `except Exception: return ""`, so the
    advisory went silent on a project with real findings and reported success by saying nothing
    — indistinguishable from clean. Fail-open is right for a hook and it is exactly what makes
    this class invisible, so the guard cannot be "don't make that mistake": it has to be an
    assertion that findings PRODUCE OUTPUT.
    """
    mod = _load()
    proj = _make_project(tmp_path, dirty=False)
    mf = proj / "NEOCORTEX" / "MANIFEST.json"
    d = json.loads(mf.read_text())
    d["files"] = [{"file": "x.md", "genre": "PLAN", "status": "REFLECTION", "hook": ""}]
    mf.write_text(json.dumps(d), encoding="utf-8")
    assert mod._staleness_advisory(proj, ledger_path=tmp_path / "ledger.jsonl").strip() != "", \
        "a project with findings produced an EMPTY advisory — an exception is being swallowed"


# ── verify_gate: project resolution from EDITED FILES, never cwd (2026-08-28) ─────────────
#
# THE DEFECT, proven live before this fix existed: `_verify_armed()` / `_has_valid_receipt()`
# read `Path.cwd()/.verify/...`. A receipt at a project's own `.verify/` was invisible to any
# session whose actual shell cwd was the workspace root — this repo's own documented "primary
# working directory," and where most governed work here happens. None of the tests below ever
# set the test runner's OS cwd to the fixture project — that absence is itself part of the
# proof: if the functions still read Path.cwd(), every test here would see the WRONG project
# (or none) and fail, since pytest's actual cwd is wherever it was invoked from.

def test_verify_project_root_resolves_from_the_edited_file_path(tmp_path):
    mod = _load()
    proj = _make_project(tmp_path, dirty=False)
    target = proj / "script.py"
    target.write_text("print('x')\n", encoding="utf-8")
    assert mod._verify_project_root([str(target)]) == proj


def test_verify_project_root_is_None_when_nothing_resolves(tmp_path):
    """A bare, ungoverned directory (no CLAUDE.md/NEOCORTEX ancestor) must resolve to
    nothing — not the nearest unrelated project, not a crash."""
    mod = _load()
    stray = tmp_path / "nowhere" / "file.py"
    stray.parent.mkdir(parents=True)
    stray.write_text("x\n", encoding="utf-8")
    assert mod._verify_project_root([str(stray)]) is None


def test_verify_armed_true_at_the_resolved_project_ROOT_not_cwd(tmp_path):
    mod = _load()
    proj = _make_project(tmp_path, dirty=False)
    target = proj / "script.py"
    target.write_text("print('x')\n", encoding="utf-8")
    (proj / ".verify").mkdir()
    (proj / ".verify" / "armed").write_text("", encoding="utf-8")
    assert mod._verify_armed([str(target)]) is True


def test_verify_armed_false_when_no_marker(tmp_path):
    mod = _load()
    proj = _make_project(tmp_path, dirty=False)
    target = proj / "script.py"
    target.write_text("print('x')\n", encoding="utf-8")
    assert mod._verify_armed([str(target)]) is False


def test_verify_armed_false_when_edited_files_resolve_to_no_project(tmp_path):
    mod = _load()
    stray = tmp_path / "nowhere" / "file.py"
    stray.parent.mkdir(parents=True)
    stray.write_text("x\n", encoding="utf-8")
    assert mod._verify_armed([str(stray)]) is False


def _write_valid_receipt(root, changed_paths) -> None:
    """A genuinely valid .verify/receipt.json, via the REAL receipt module — the same one
    `_has_valid_receipt` imports — not a hand-rolled schema."""
    sys.path.insert(0, str(ROOT / "_skills" / "verify_gate" / "scripts"))
    import receipt as receipt_mod  # noqa: E402
    vdir = Path(root) / ".verify"
    vdir.mkdir(exist_ok=True)
    verdict_path = vdir / "verdict.json"
    verdict_path.write_text('{"schema": "verify_gate.verdict.v1"}', encoding="utf-8")
    rec = receipt_mod.write_receipt(
        changed_paths=[str(p) for p in changed_paths], verdict_path=str(verdict_path),
        gate_exit=0, attestation_ref=str(vdir / "attestation.json"))
    (vdir / "receipt.json").write_text(json.dumps(rec.as_dict()), encoding="utf-8")


def test_has_valid_receipt_true_for_a_real_receipt_at_the_resolved_root(tmp_path):
    mod = _load()
    proj = _make_project(tmp_path, dirty=False)
    target = proj / "script.py"
    target.write_text("print('x')\n", encoding="utf-8")
    _write_valid_receipt(proj, [target])
    assert mod._has_valid_receipt([str(target)], [str(target)]) is True


def test_has_valid_receipt_false_when_absent(tmp_path):
    mod = _load()
    proj = _make_project(tmp_path, dirty=False)
    target = proj / "script.py"
    target.write_text("print('x')\n", encoding="utf-8")
    assert mod._has_valid_receipt([str(target)], [str(target)]) is False


def test_has_valid_receipt_false_when_the_changeset_no_longer_matches(tmp_path):
    """A receipt is bound to file CONTENT, not just presence — editing the file after the
    receipt was written must invalidate it (the same property `receipt.py` itself defends,
    exercised here through the resolved-root path rather than a hand-built path)."""
    mod = _load()
    proj = _make_project(tmp_path, dirty=False)
    target = proj / "script.py"
    target.write_text("print('x')\n", encoding="utf-8")
    _write_valid_receipt(proj, [target])
    target.write_text("print('CHANGED')\n", encoding="utf-8")
    assert mod._has_valid_receipt([str(target)], [str(target)]) is False

#!/usr/bin/env python3.12
"""test_stop_closeout_outcome.py — CLOSEOUT-GATE H1 (outcome, not substring), H2 (test-tamper
advisory), H9 (BLOCKED: safe exit) and S4-06 (block printed before the slow advisories).
PLAN_hooks_outcome_tamper_loopbound_2026-10-05.

The gate runs as a subprocess from a COPY inside a fixture workspace (<tmp>/ws/.claude/hooks/),
with its sibling modules, so every ROOT-relative write lands in the fixture.
HOOK_DIR=<dir> overrides where the gate (and siblings) are copied from: point it at the LIVE
hooks to see the RED half (every H1 masking case, H2, H9 and S4-06 fail there by design).

Run: python3.12 -m pytest test_stop_closeout_outcome.py -q
"""
import json
import os
import pathlib
import shutil
import subprocess
import sys
import time

import pytest

SRC = pathlib.Path(os.environ.get("HOOK_DIR") or pathlib.Path(__file__).resolve().parent)
SIBLINGS = ("verifier_recognizer.py", "hook_support.py")


@pytest.fixture
def ws():
    # NOT pytest's tmp_path: on macOS it lives under /private/var/folders/, which the gate exempts
    # as scratch (NON_CLOSEOUT_PREFIXES) — every edit would be invisible. The fixture lives next to
    # this file instead (OUTCOME_TEST_BASE overrides) and is removed after each test.
    import tempfile
    # Fixture base OUTSIDE every governed tree (2026-10-05: under the candidate dir, the gate's cwd walk-up
    # reached the REAL governance/ and blocked on its real JOURNAL) and outside the gate's exempt temp roots.
    base = pathlib.Path(os.environ.get("OUTCOME_TEST_BASE") or pathlib.Path.home() / "Library" / "Caches" / "hooks_batch2_tests" / "_tmp_outcome")
    base.parent.mkdir(parents=True, exist_ok=True)
    base.mkdir(exist_ok=True)
    tmp_path = pathlib.Path(tempfile.mkdtemp(dir=base))
    yield from _build(tmp_path)
    shutil.rmtree(tmp_path, ignore_errors=True)
    try:
        base.rmdir()
    except OSError:
        pass


def _build(tmp_path):
    ws = tmp_path / "ws"
    hooks = ws / ".claude" / "hooks"
    hooks.mkdir(parents=True)
    (ws / ".claude" / "settings.json").write_text("{}", encoding="utf-8")
    shutil.copy(SRC / "stop_closeout_gate.py", hooks / "stop_closeout_gate.py")
    for s in SIBLINGS:
        if (SRC / s).is_file():
            shutil.copy(SRC / s, hooks / s)
    _approve(ws)
    (ws / "CLAUDE.md").write_text("# ws\n", encoding="utf-8")
    for name in ("proj", "other"):
        p = ws / name
        (p / "NEOCORTEX").mkdir(parents=True)
        (p / "src").mkdir()
        (p / "tests").mkdir()
        (p / "CLAUDE.md").write_text("# p\n", encoding="utf-8")
        (p / "NEOCORTEX" / "JOURNAL.md").write_text("# j\n", encoding="utf-8")
    yield ws


def _approve(root):
    """B5-30 (reviewer A round 5): hook siblings run only as APPROVED bytes, so a fixture install gets its own approved
    list, as a landed install does (`hook_support.py --write-approved`)."""
    import importlib.util
    (root / "governance").mkdir(exist_ok=True)
    spec = importlib.util.spec_from_file_location("_fixture_hook_support", root / ".claude" / "hooks" / "hook_support.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    m.write_approved(root)


class T:
    """Transcript builder: the real shape (assistant tool_use + user tool_result, cwd, prompts)."""

    def __init__(self, cwd):
        self.lines, self.n, self.cwd = [], 0, str(cwd)

    def prompt(self, text="go on"):
        self.lines.append({"type": "user", "cwd": self.cwd, "message": {"role": "user", "content": text}})
        return self

    def say(self, text):
        self.lines.append({"type": "assistant", "cwd": self.cwd,
                           "message": {"content": [{"type": "text", "text": text}]}})
        return self

    def tool(self, name, inp, out="ok", err=False):
        self.n += 1
        tid = f"toolu_{self.n}"
        self.lines.append({"type": "assistant", "cwd": self.cwd, "message": {"content": [
            {"type": "tool_use", "id": tid, "name": name, "input": inp}]}})
        self.lines.append({"type": "user", "cwd": self.cwd, "message": {"content": [
            {"type": "tool_result", "tool_use_id": tid, "is_error": err,
             "content": (f"Exit code 1\n{out}" if err else out)}]}})
        return self

    def edit(self, path):
        return self.tool("Write", {"file_path": str(path), "content": "x = 1\n"})

    def bash(self, cmd, out="ok", err=False):
        return self.tool("Bash", {"command": cmd}, out, err)

    def write(self, path):
        path.write_text("".join(json.dumps(l) + "\n" for l in self.lines), encoding="utf-8")
        return path


def run(ws, t, timeout=30):
    tp = t.write(ws.parent / "t.jsonl")
    env = dict(os.environ)
    env.pop("CLAUDE_PROJECT_DIR", None)
    env.pop("GOVERNANCE_WORKSPACE", None)
    r = subprocess.run([sys.executable, str(ws / ".claude/hooks/stop_closeout_gate.py")],
                       input=json.dumps({"transcript_path": str(tp)}), capture_output=True,
                       text=True, cwd=str(ws), env=env, timeout=timeout)
    assert r.returncode == 0, r.stderr[-600:]
    return json.loads(r.stdout) if r.stdout.strip() else {}


def blocked(o):
    return o.get("decision") == "block"


# ---------------------------------------------------------------- H1: what clears the window
@pytest.mark.parametrize("cmd,out,err", [
    ("cat proj/tests/test_x.py", "def test(): pass", False),                 # S4-20
    ("ls --dry-run", "", False),                                             # old marker
    ("pytest -q 2>&1 | tail -5", "1 failed, 2 passed in 0.1s", False),       # masked AND failing
    ("pytest -q | tail -1", "", False),                                      # masked, unknowable
    ("pytest -q || true", "1 failed in 0.1s", False),                        # failure swallowed
    ("pytest -q; echo done", "done", False),                                 # status of echo
    ("pytest -q", "1 failed in 0.1s", True),                                 # a real failure
    ("pytest -q", "collected 0 items\nno tests ran", False),                 # empty run
    ("python3.12 harness.py", "RESULT: 3 passed, 1 failed", False),          # not a verifier
    # §3a (v0.7, 2026-10-06): moved here from test_h1_clears — compound forms never clear any more
    ("pytest -q 2>&1 | tail -3", "===== 3 passed in 0.1s =====", False),     # was: masked, visibly green
    ("set -o pipefail; pytest -q | tail -3", "3 passed in 0.1s", False),     # was: pipefail pipe
    ("bash -n x.sh && echo syntax-ok", "syntax-ok", False),                 # was: observable && echo
])
def test_h1_does_not_clear(ws, cmd, out, err):
    t = T(ws).prompt().edit(ws / "proj/src/app.py").bash(cmd, out, err)
    o = run(ws, t)
    assert blocked(o), f"{cmd!r} cleared the gate: {o}"


@pytest.mark.parametrize("cmd,out", [
    # §3a (v0.7, 2026-10-06): pytest outputs now carry the COMPLETE summary a real run prints ("1 passed"
    # alone was shorthand; a summary-printing runner clears only on its full summary line).
    ("cd proj && pytest tests/test_a.py -q", "1 passed in 0.01s"),           # P6: cd-relative path
    ("cd proj/src && pytest ../tests/test_a.py -q", "1 passed in 0.01s"),
    ("bash -n x.sh", ""),                                                    # §3a: plain form of `bash -n x.sh && echo`
    ("pytest -q", "3 passed in 0.1s"),
    ("cd proj && python3.12 -m pytest tests -q", "3 passed in 0.1s"),
    ("pytest -q 2>&1", "===== 3 passed in 0.1s ====="),                      # §3a: 2>&1 is the one redirect allowed
    ("python3.12 -m py_compile proj/src/app.py", ""),
    ("cd proj && python3 -m tests.test_api", "Ran 4 tests in 0.1s\n\nOK"),     # P6: test module
    ("pytest proj/tests/test_a.py -q", "1 passed in 0.01s"),                 # same project
    ("nice -n 10 pytest -q", "3 passed in 0.1s"),                            # B2-40
])
def test_h1_clears(ws, cmd, out):
    t = T(ws).prompt().edit(ws / "proj/src/app.py").bash(cmd, out)
    o = run(ws, t)
    assert not blocked(o), f"{cmd!r} should have cleared: {o}"


def test_h1_tests_of_another_project_clear_but_are_reported(ws):
    """H1e is advisory (P6 replay: precision unproven): clears, and says so."""
    t = T(ws).prompt().edit(ws / "proj/src/app.py").bash("pytest other/tests/test_a.py -q", "1 passed in 0.01s")
    o = run(ws, t)
    assert not blocked(o) and "VERIFY SCOPE (H1e" in o.get("systemMessage", ""), o


def test_h1_block_names_the_reason(ws):
    t = T(ws).prompt().edit(ws / "proj/src/app.py").bash("pytest -q | tail -1", "")
    o = run(ws, t)
    # §3a: the reason class is now "not a plain verifier" (was "not observable … piped")
    assert "not a plain verifier" in o["reason"] and "'|'" in o["reason"] and "on their own" in o["reason"], o


def test_h1_declared_command_clears(ws):
    (ws / "proj/.verify").mkdir()
    (ws / "proj/.verify/commands").write_text("# project checks\nmake verify-all\n", encoding="utf-8")
    t = T(ws / "proj").prompt().edit(ws / "proj/src/app.py").bash("make verify-all", "all good")
    o = run(ws, t)
    assert not blocked(o), o


# ---------------------------------------------------------------- H2: test-tamper advisory
# §3a (2026-10-06): fixture outputs below were shorthand ("4 passed"); they now carry the complete pytest
# summary a real run prints, since a summary-printing runner clears only on it. Test intent unchanged.
def test_h2_silent_on_plain_co_change_without_weakening(ws):
    """Tests written alongside code is ordinary work: no advisory without weakening evidence."""
    t = (T(ws).prompt().edit(ws / "proj/src/app.py").edit(ws / "proj/tests/test_app.py")
         .bash("pytest -q", "4 passed in 0.1s"))
    o = run(ws, t)
    assert not blocked(o)
    assert "TEST-TAMPER" not in o.get("systemMessage", ""), o


def test_h2_flags_a_strengthened_or_new_test_not_at_all_but_a_weakened_one(ws):
    proj = ws / "proj"
    tf = proj / "tests/test_app.py"
    tf.write_text("def test_a():\n    assert f() == 2\n", encoding="utf-8")
    g = ["git", "-C", str(proj)]
    subprocess.run(g + ["init", "-q"], check=True)
    subprocess.run(g + ["add", "tests/test_app.py"], check=True)
    subprocess.run(g + ["-c", "user.email=fixture-identity", "-c", "user.name=t", "commit", "-qm", "base"], check=True)
    tf.write_text("def test_a():\n    assert f() == 2\n    assert g() == 3\n", encoding="utf-8")
    t = T(ws).prompt().edit(proj / "src/app.py").edit(tf).bash("pytest -q", "1 passed in 0.01s")
    assert "TEST-TAMPER" not in run(ws, t).get("systemMessage", "")


@pytest.mark.parametrize("paths", [("proj/src/app.py",), ("proj/tests/test_app.py",)])
def test_h2_silent_on_code_only_or_tests_only(ws, paths):
    t = T(ws).prompt()
    for p in paths:
        t.edit(ws / p)
    o = run(ws, t.bash("pytest -q", "4 passed in 0.1s"))
    assert "TEST-TAMPER" not in o.get("systemMessage", ""), o


def test_h2_only_reports_the_current_turn(ws):
    t = (T(ws).prompt("first").edit(ws / "proj/src/app.py").edit(ws / "proj/tests/test_app.py")
         .bash("pytest -q", "4 passed in 0.1s").prompt("second").say("nothing else to do"))
    o = run(ws, t)
    assert "TEST-TAMPER" not in o.get("systemMessage", ""), o


def test_h2_counts_weakened_assertions(ws):
    proj = ws / "proj"
    tf = proj / "tests/test_app.py"
    tf.write_text("def test_a():\n    assert f() == 2\n", encoding="utf-8")
    g = ["git", "-C", str(proj)]
    subprocess.run(g + ["init", "-q"], check=True)
    subprocess.run(g + ["add", "tests/test_app.py"], check=True)
    subprocess.run(g + ["-c", "user.email=fixture-identity", "-c", "user.name=t", "commit", "-qm", "base"], check=True)
    deco = "@" + "pytest.mark.skip"          # split: the egress scanner reads `\n@x.y.z` as an address
    tf.write_text("import pytest\n" + deco + "\ndef test_a():\n    pass\n", encoding="utf-8")
    t = T(ws).prompt().edit(proj / "src/app.py").edit(tf).bash("pytest -q", "1 passed, 1 skipped in 0.01s")
    o = run(ws, t)
    msg = o.get("systemMessage", "")
    assert "1 assertion line(s) removed" in msg and "skip/xfail/only marker(s) added" in msg, o


# ---------------------------------------------------------------- H9: BLOCKED is a safe exit
REPORT = ("BLOCKED: the API returns 500 on staging.\nBlocker: upstream outage. Diagnosis: the auth "
          "service is down. Tried: three retries with logs attached. Recommendation: retry after 14:00.")


def test_h9_complete_blocked_report_is_honoured(ws):
    t = T(ws).prompt().edit(ws / "proj/src/app.py").bash("pytest -q", "1 failed", True).say(REPORT)
    o = run(ws, t)
    assert not blocked(o), o
    assert "BLOCKED declared" in o.get("systemMessage", "")


def test_h9_incomplete_blocked_report_still_blocks_and_says_what_is_missing(ws):
    t = T(ws).prompt().edit(ws / "proj/src/app.py").bash("pytest -q", "1 failed", True).say(
        "BLOCKED: it does not work.")
    o = run(ws, t)
    assert blocked(o) and "missing: blocker, diagnosis, what was tried, recommendation" in o["reason"], o


def test_h9_blocked_report_from_an_earlier_turn_does_not_count(ws):
    t = (T(ws).prompt().say(REPORT).prompt("continue").edit(ws / "proj/src/app.py")
         .bash("pytest -q", "1 failed", True))
    o = run(ws, t)
    assert blocked(o), o


# ---------------------------------------------------------------- S4-06: block before advisories
def test_s4_06_block_is_printed_before_the_slow_advisories(ws):
    scripts = ws / "governance" / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "continuity_sweep.py").write_text("import time, sys\ntime.sleep(6)\nsys.exit(1)\n")
    t = T(ws).prompt().edit(ws / "proj/src/app.py")
    tp = t.write(ws.parent / "t.jsonl")
    env = dict(os.environ)
    env.pop("CLAUDE_PROJECT_DIR", None)
    p = subprocess.Popen([sys.executable, str(ws / ".claude/hooks/stop_closeout_gate.py")],
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                         text=True, cwd=str(ws), env=env)
    p.stdin.write(json.dumps({"transcript_path": str(tp)}))
    p.stdin.close()
    t0 = time.monotonic()
    first = p.stdout.readline()
    elapsed = time.monotonic() - t0
    p.kill()
    p.wait()
    assert '"decision": "block"' in first and elapsed < 4, f"block arrived after {elapsed:.1f}s: {first!r}"


# ---------------------------------------------------------------- Seat A (MiMo Flash) findings, 2026-10-05
def test_seatA_f3_a_masked_sibling_does_not_ride_on_an_observable_one(ws):
    t = T(ws).prompt().edit(ws / "proj/src/app.py").bash("pytest -q 2>&1 | head -3 && node --check app.js", "..F.")
    assert blocked(run(ws, t))


def test_seatA_f4_a_failed_rm_does_not_clear_a_pending_edit(ws):
    t = T(ws).prompt().edit(ws / "proj/src/app.py").bash(f"rm {ws}/proj/src/app.py", "Operation not permitted", True)
    assert blocked(run(ws, t))


def test_seatA_f6_bash_c_verifier_no_longer_clears(ws):
    # §3a (2026-10-06): flipped. A `bash -c` string is not a plain verifier (Seat A F6 recognised it; the
    # v0.7 form clears only the plain command itself).
    t = T(ws).prompt().edit(ws / "proj/src/app.py").bash('bash -c "cd proj && pytest -q"', "3 passed in 0.1s")
    assert blocked(run(ws, t))


def test_seatA_f12_variable_heredoc_body_is_not_a_verifier(ws):
    t = T(ws).prompt().edit(ws / "proj/src/app.py").bash("cat <<$E\npytest -q\n$E", "pytest -q")
    assert blocked(run(ws, t))


def test_seatA_f15_a_sidechain_entry_is_not_the_founders_prompt(ws):
    t = T(ws).prompt().edit(ws / "proj/src/app.py").bash("pytest -q", "1 failed", True).say(REPORT)
    t.lines.append({"type": "user", "isSidechain": True, "cwd": str(ws), "message": {"role": "user", "content": "subagent task"}})
    assert not blocked(run(ws, t))


def _git_base(proj, tf, text):
    tf.write_text(text, encoding="utf-8")
    g = ["git", "-C", str(proj)]
    subprocess.run(g + ["init", "-q"], check=True)
    subprocess.run(g + ["add", str(tf.relative_to(proj))], check=True)
    subprocess.run(g + ["-c", "user.email=fixture-identity", "-c", "user.name=t", "commit", "-qm", "base"], check=True)
    return g


def test_seatA_f7_staged_weakening_is_seen(ws):
    proj = ws / "proj"
    tf = proj / "tests/test_app.py"
    g = _git_base(proj, tf, "def test_a():\n    assert f() == 2\n")
    tf.write_text("def test_a():\n    pass\n", encoding="utf-8")
    subprocess.run(g + ["add", "tests/test_app.py"], check=True)
    t = T(ws).prompt().edit(proj / "src/app.py").edit(tf).bash("pytest -q", "1 passed in 0.01s")
    assert "1 assertion line(s) removed" in run(ws, t).get("systemMessage", "")


def test_seatA_f7_relative_bash_written_test_path_is_resolved(ws):
    proj = ws / "proj"
    tf = proj / "tests/test_app.py"
    _git_base(proj, tf, "def test_a():\n    assert f() == 2\n")
    tf.write_text("def test_a():\n    pass\n", encoding="utf-8")
    t = (T(proj).prompt().edit(proj / "src/app.py")
         .bash("cat > tests/test_app.py <<'EOF'\ndef test_a():\n    pass\nEOF", "")
         .bash("pytest -q", "1 passed in 0.01s"))
    assert "1 assertion line(s) removed" in run(ws, t).get("systemMessage", "")


def test_seatA_f4_degraded_mode_clears_nothing(ws):
    # B5-31 (reviewer A round 5, expected flip): the degraded mode no longer replays the old substring rule; with the
    # recognizer missing nothing clears, and the block says why.
    (ws / ".claude/hooks/verifier_recognizer.py").unlink()
    t = T(ws).prompt().edit(ws / "proj/src/app.py").bash("cat proj/tests/test_x.py", "x")
    o = run(ws, t)
    assert blocked(o) and "recognizer is unavailable" in o.get("reason", ""), o
    t = T(ws).prompt().edit(ws / "proj/src/app.py").bash(f"rm {ws}/proj/src/app.py", "denied", True)
    assert blocked(run(ws, t))                     # old rule: an errored call is skipped entirely


# ---------------------------------------------------------------- Seat B (reviewer A Ultra) findings, 2026-10-05
@pytest.mark.parametrize("cmd,out", [
    ("set -e; pytest -q >/dev/null && echo ok; echo done", "done"),          # B-11
    ("set -e; false && pytest -q; echo done", "done"),
    ("( set -o pipefail ) ; pytest -q | tail -1", "AssertionError: nope"),   # B-12
    ("true && (pytest -q); echo done", "done"),
    ("pytest --version", "pytest 8.3.5"),                                    # B-13
    ("pytest --collect-only -q", "tests/test_a.py::test_one\n\n1 test collected in 0.01s"),
    ("swift test --list-tests", "MyTests.testOne"),
    ("npx playwright install", ""),
    ('pytest -q | grep -o "[0-9]* passed"', "2 passed"),                     # B-14
    ("npm test | head -20", "\x1b[32m✓\x1b[0m one good test"),
    ("pytest -q | tail -1", "\x1b[31mFAILED\x1b[0m tests/test_a.py - AssertionError: 0 failed"),   # B-15
    ("pytest -q | tail -1", "\rFAILED tests/test_a.py - AssertionError: 0 failed"),
])
def test_seatB_false_clears_now_block(ws, cmd, out):
    t = T(ws).prompt().edit(ws / "proj/src/app.py").bash(cmd, out)
    assert blocked(run(ws, t)), cmd


def test_seatB_b14_truncated_masked_output_does_not_establish_success(ws):
    out = "1 passed\n" + "A" * 5000 + "\n1 failed\n" + "B" * 5000 + "\n===== 3 passed in 0.1s ====="
    t = T(ws).prompt().edit(ws / "proj/src/app.py").bash("pytest -q | cat", out)
    assert blocked(run(ws, t))


def test_seatB_b16_an_edit_after_the_verifier_in_the_same_command_re_arms(ws):
    t = T(ws).prompt().bash(f"pytest -q\nsed -i '' 's/old/new/' {ws}/proj/src/app.py", "3 passed")
    assert blocked(run(ws, t))


def test_seatB_b17_a_failed_browser_inspection_does_not_clear(ws):
    t = T(ws).prompt().edit(ws / "proj/src/app.py")
    t.tool("mcp__Claude_Browser__read_page", {}, "page unavailable", True)
    assert blocked(run(ws, t))


def test_seatB_b31_application_log_text_is_not_a_runner_failure(ws):
    t = T(ws).prompt().edit(ws / "proj/src/app.py").bash(
        "pytest -s -q", "This diagnostic documents 1 failed request that was retried successfully\n2 passed in 0.1s")
    assert not blocked(run(ws, t))


# §3a (2026-10-06): flipped. These masked forms (pipe to tail) cleared on a visible complete summary; under
# the v0.7 form they never clear, and their PLAIN spellings (below) do.
@pytest.mark.parametrize("cmd,out", [
    ("pytest -q 2>&1 | tail -3", "===== 3 passed in 0.12s ====="),
    ("pytest -q | tail -1", "3 passed in 0.12s"),
    ("python3 -m unittest 2>&1 | tail -3", "Ran 4 tests in 0.010s\n\nOK"),
    ("bash adapters/claude-code/test_hooks.sh 2>&1 | tail -2", "RESULT: 37 passed, 0 failed"),
    ("python3.12 .claude/hooks/test_pretool_s4_bypasses.py | tail -1", "39/39 passed"),
])
def test_seatB_masked_runs_no_longer_clear_even_with_a_summary(ws, cmd, out):
    t = T(ws).prompt().edit(ws / "proj/src/app.py").bash(cmd, out)
    assert blocked(run(ws, t)), cmd


@pytest.mark.parametrize("cmd,out", [
    ("pytest -q 2>&1", "===== 3 passed in 0.12s ====="),
    ("pytest -q", "3 passed in 0.12s"),
    ("python3 -m unittest 2>&1", "Ran 4 tests in 0.010s\n\nOK"),
    ("bash adapters/claude-code/test_hooks.sh 2>&1", "RESULT: 37 passed, 0 failed"),
    ("python3.12 .claude/hooks/test_pretool_s4_bypasses.py", "39/39 passed"),
])
def test_s3a_plain_spellings_clear(ws, cmd, out):
    t = T(ws).prompt().edit(ws / "proj/src/app.py").bash(cmd, out)
    assert not blocked(run(ws, t)), cmd


# ---------------------------------------------------------------- reviewer A S4 FINAL rows (10-03 review), folded 2026-10-05
@pytest.mark.parametrize("rel", ["proj/src/App.tsx", "proj/src/main.go", "proj/src/lib.rs", "proj/src/View.swift",
                                 "proj/src/q.sql", "proj/src/Comp.vue"])
def test_s4_22_common_source_types_are_code(ws, rel):
    t = T(ws).prompt().edit(ws / rel)
    assert blocked(run(ws, t)), rel


def test_s4_44_notebook_edit_reads_notebook_path(ws):
    t = T(ws).prompt().tool("NotebookEdit", {"notebook_path": str(ws / "proj/analysis.ipynb"), "new_source": "x=1"})
    assert blocked(run(ws, t))


def test_s4_42_a_sed_on_a_new_line_is_an_edit(ws):
    t = T(ws).prompt().bash(f"cd {ws}/proj\nsed -i '' 's/old/new/' src/app.py", "")
    assert blocked(run(ws, t))


def test_s4_43_browser_inspection_does_not_clear_non_web_code(ws):
    t = T(ws).prompt().edit(ws / "proj/src/auth.py").tool("mcp__Claude_Browser__get_page_text", {}, "page text")
    assert blocked(run(ws, t))
    t = T(ws).prompt().edit(ws / "proj/web/index.html").tool("mcp__Claude_Browser__get_page_text", {}, "page text")
    assert not blocked(run(ws, t))


def test_s4_55_a_long_transcript_is_read_from_its_tail(ws):
    t = T(ws).prompt()
    for i in range(300):
        t.bash(f"echo filler {i}", "x" * 50)
    t.edit(ws / "proj/src/app.py")
    tp = t.write(ws.parent / "t.jsonl")
    env = dict(os.environ, CLOSEOUT_TRANSCRIPT_CAP="20000")
    env.pop("CLAUDE_PROJECT_DIR", None)
    r = subprocess.run([sys.executable, str(ws / ".claude/hooks/stop_closeout_gate.py")],
                       input=json.dumps({"transcript_path": str(tp)}), capture_output=True, text=True,
                       cwd=str(ws), env=env, timeout=30)
    o = json.loads(r.stdout)
    assert o.get("decision") == "block" and "truncated" in o.get("systemMessage", "").lower(), o


def test_s4_56_journal_bound_reaches_the_edited_project_from_the_root(ws):
    (ws / "proj/NEOCORTEX/JOURNAL.md").write_bytes(b"x" * (65 * 1024))
    t = T(ws).prompt().edit(ws / "proj/src/app.py").bash("pytest -q", "3 passed in 0.1s")
    o = run(ws, t)
    assert blocked(o) and "JOURNAL.md" in o["reason"], o


# ---------------------------------------------------------------- §3a (v0.7) + reviewer A round 2b, 2026-10-06
@pytest.mark.parametrize("cmd,out", [
    ("pytest -q && echo green", "3 passed in 0.1s\ngreen"),
    ("(set -e; pytest -q >/dev/null); echo done", "done"),                                 # B2-15
    ("pytest tests/unit.py; pytest tests/integration.py >/tmp/i.log; echo done", "3 passed in 0.02s\ndone"),  # B2-17
    ("make -n check", "pytest -q"),                                                        # B2-16
    ("pytest --fixtures-per-test", "fixtures used by test_a"),                             # B2-16
    ("go test -run '^$' ./...", "ok  \texample.com/a\t0.01s [no tests to run]"),            # B2-19
    ("cargo test", "test result: ok. 0 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out"),
])
def test_s3a_compound_no_run_and_empty_never_clear(ws, cmd, out):
    t = T(ws).prompt().edit(ws / "proj/src/app.py").bash(cmd, out)
    assert blocked(run(ws, t)), cmd


def test_s3a_block_message_says_run_tests_on_their_own(ws):
    t = T(ws).prompt().edit(ws / "proj/src/app.py").bash("pytest -q && echo green", "3 passed in 0.1s\ngreen")
    assert "run your tests on their own" in run(ws, t)["reason"]


def test_s3a_plain_runner_without_a_summary_does_not_clear(ws):
    t = T(ws).prompt().edit(ws / "proj/src/app.py").bash("pytest -q", "")
    o = run(ws, t)
    assert blocked(o) and "summary" in o["reason"], o


def test_s3a_absent_result_keeps_the_f17_contract_for_a_plain_verifier(ws):
    t = T(ws).prompt().edit(ws / "proj/src/app.py")
    t.lines.append({"type": "assistant", "cwd": t.cwd, "message": {"content": [
        {"type": "tool_use", "id": "toolu_absent", "name": "Bash", "input": {"command": "pytest -q"}}]}})
    assert not blocked(run(ws, t))


def test_s3a_a_redirect_only_write_after_a_verifier_does_not_clear(ws):                    # B2-21
    t = T(ws).prompt().edit(ws / "proj/src/app.py").bash("pytest -q && > proj/src/generated.py", "3 passed in 0.1s")
    assert blocked(run(ws, t))


def test_b2_20_browser_clears_ui_files_not_backend_ts(ws):
    t = T(ws).prompt().edit(ws / "proj/backend/auth.ts").tool("mcp__Claude_Browser__get_page_text", {}, "page text")
    assert blocked(run(ws, t))
    t = T(ws).prompt().edit(ws / "proj/web/App.tsx").tool("mcp__Claude_Browser__get_page_text", {}, "page text")
    assert not blocked(run(ws, t))


# B2-18 (residual after §3a): the gate's EDIT detection sees wrapped writers, formatters in write mode and
# every file-redirect spelling; check-only formatters are not edits.
@pytest.mark.parametrize("cmd", ["env cp /tmp/new.py proj/src/app.py", "sudo -u me cp /tmp/n.py proj/src/app.py",
                                 "black proj/src/app.py", "ruff format proj/src/app.py", "prettier --write proj/web/a.ts",
                                 "echo x &> proj/src/app.py", "echo x >| proj/src/app.py", "echo x >& proj/src/app.py",
                                 "nice -n 5 tee proj/src/app.py < /tmp/x"])
def test_b2_18_wrapped_and_formatter_writes_are_edits(ws, cmd):
    t = T(ws).prompt().bash(cmd, "")
    assert blocked(run(ws, t)), cmd


@pytest.mark.parametrize("cmd", ["black --check proj/src/app.py", "ruff check proj/src/app.py", "prettier --check proj/web/a.ts",
                                 "ruff format --check proj/src/app.py", "eslint proj/web/a.ts", "echo x 2>&1"])
def test_b2_18_check_only_formatters_are_not_edits(ws, cmd):
    t = T(ws).prompt().bash(cmd, "")
    assert not blocked(run(ws, t)), cmd


# ---------------------------------------------------------------- reviewer A round 3 (2026-10-06), H1
def test_b3_05_truncated_output_of_a_repo_script_does_not_clear(ws):
    out = "1 passed in 0.01s\n" + "A" * 5000 + "\nRESULT: 3 passed, 1 failed\n" + "B" * 5000 + "\nfinished"
    t = T(ws).prompt().edit(ws / "proj/src/app.py").bash("bash tests/run_tests.sh", out)
    assert blocked(run(ws, t))


def test_b3_06_a_background_launch_does_not_clear(ws):
    t = T(ws).prompt().edit(ws / "proj/src/app.py")
    t.tool("Bash", {"command": "bash -n src/broken.sh", "run_in_background": True},
           "Command running in background with ID: bash_1")
    assert blocked(run(ws, t))


@pytest.mark.parametrize("cmd,out", [
    ("time ! bash -n src/broken.sh", "src/broken.sh: line 2: syntax error near unexpected token"),   # B3-01
    ("bash -n src/broken#v1.sh || true", "syntax error"),                                          # B3-02
    ("env -S true bash -n src/broken.sh", ""),                                                     # B3-03
    ("npm test -- --passWithNoTests", "No tests found, exiting with code 0"),                      # B3-04
])
def test_b3_false_clears_now_block(ws, cmd, out):
    t = T(ws).prompt().edit(ws / "proj/src/app.py").bash(cmd, out)
    assert blocked(run(ws, t)), cmd


@pytest.mark.parametrize("cmd,out", [
    ("cd proj &&\npytest -q", "3 passed in 0.1s"),                                                  # B3-29
    ("make -C proj test", "all good"),                                                             # B3-30
])
def test_b3_ordinary_plain_forms_clear(ws, cmd, out):
    t = T(ws).prompt().edit(ws / "proj/src/app.py").bash(cmd, out)
    assert not blocked(run(ws, t)), cmd


# ---------------------------------------------------------------- (e) 2026-10-06, first field report after P7: write
# targets spelled through a shell variable (`$SP/x.py`) were counted as code edits at an unknown, non-exempt path.
@pytest.mark.parametrize("cmd", ["SP=/private/tmp/claude-501/x/scratchpad && cp /tmp/a.py $SP/b.py",
                                 'cat > "$S/probe.swift" <<EOF\nlet x = 1\nEOF',
                                 "cp /tmp/a.py ${UNSET_DIR_FOR_TEST}/b.py"])
def test_e_variable_write_paths_resolve_or_do_not_count(ws, cmd):
    t = T(ws).prompt().bash(cmd, "")
    assert not blocked(run(ws, t)), cmd


def test_e_a_variable_that_resolves_to_project_code_still_counts(ws):
    t = T(ws).prompt().bash(f"P={ws}/proj/src && cp /tmp/x.py $P/app.py", "")
    assert blocked(run(ws, t))


# ================================================================== reviewer A round 5 (2026-10-06, on the LANDED v0.9)
# B5-31: a refused recognizer (modified after approval) never falls back to the old substring rule.
@pytest.mark.parametrize("cmd,out", [("pytest -q || true", "1 failed in 0.1s"), ("pytest -q | tail -20", "1 failed in 0.1s"),
                                     ("pytest -q", "3 passed in 0.1s")])
def test_b5_31_a_refused_recognizer_clears_nothing(ws, cmd, out):
    vr = ws / ".claude" / "hooks" / "verifier_recognizer.py"
    vr.write_text(vr.read_text() + "\n# edited after approval\n")
    t = T(ws).prompt().edit(ws / "proj/src/app.py").bash(cmd, out)
    o = run(ws, t)
    assert blocked(o) and "recognizer is unavailable" in o.get("reason", ""), o


# B5-18: a `$VAR` write resolves with the value assigned BEFORE it, from real assignment statements only.
@pytest.mark.parametrize("tmpl,counts", [
    ('D={src}; cp /tmp/fix.py "$D/app.py"; D=/tmp', True),
    ('D=/tmp; cp /tmp/probe.py "$D/probe.py"; D={src}', False),
    ('D={src}; cp /tmp/fix.py "$D/app.py"; echo D=/tmp', True),
    ('D={src}; cp /tmp/fix.py "$D/app.py" # D=/tmp', True),
    ('D={src} && E=$D && cp /tmp/fix.py "$E/app.py"', True),
])
def test_b5_18_variable_writes_resolve_in_order(ws, tmpl, counts):
    t = T(ws).prompt().bash(tmpl.format(src=ws / "proj" / "src"), "")
    assert blocked(run(ws, t)) == counts, tmpl


# B5-09: `sed -Ei` / `sed -ni` edit in place; the gate counts the edit.
@pytest.mark.parametrize("flags", ["-Ei", "-ni", "-i.bak"])
def test_b5_09_sed_option_clusters_are_edits(ws, flags):
    t = T(ws).prompt().bash(f"sed {flags} 's/a/b/' {ws}/proj/src/app.py", "")
    assert blocked(run(ws, t)), flags

#!/usr/bin/env python3.12
"""test_pretool_h0_h4_h9.py — pretool_guard.py: H4a governor write-guard (ask), H0 fail-closed-to-ask,
H9 Art. 14 loop bound. PLAN_hooks_outcome_tamper_loopbound_2026-10-05.

The guard runs as a subprocess from a COPY in a fixture workspace (<base>/ws/.claude/hooks/), so its
ROOT is the fixture and every log write lands there. HOOK_DIR=<dir> overrides the source directory
(point it at the live hooks for the RED half). Dangerous literals for L0 are assembled at runtime,
same convention as test_pretool_l0_guard.py.

Run: python3.12 -m pytest test_pretool_h0_h4_h9.py -q
"""
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

import pytest

SRC = pathlib.Path(os.environ.get("HOOK_DIR") or pathlib.Path(__file__).resolve().parent)
# The launcher ships only where it exists: the public kit of 2026-10-09 is wired like the author's workspace, without it
# (founder ruling "A: live parity"); its tests then SKIP with this reason instead of failing for an adopter.
_NEEDS_LAUNCHER = pytest.mark.skipif(not (SRC / "pretool_guard.sh").is_file(),
                                     reason="pretool_guard.sh (the H0 launcher) is not part of this kit")
SIBLINGS = ("verifier_recognizer.py", "hook_support.py", "loop_bound.py")
L0 = "PAICodeConstitution" + "-2026.md"


@pytest.fixture
def ws():
    # Fixture base OUTSIDE every governed tree (2026-10-05: under the candidate dir, the gate's cwd walk-up
    # reached the REAL governance/ and blocked on its real JOURNAL) and outside the gate's exempt temp roots.
    base = pathlib.Path(os.environ.get("OUTCOME_TEST_BASE") or pathlib.Path.home() / "Library" / "Caches" / "hooks_batch2_tests" / "_tmp_pretool")
    base.parent.mkdir(parents=True, exist_ok=True)
    base.mkdir(exist_ok=True)
    tmp = pathlib.Path(tempfile.mkdtemp(dir=base))
    ws = tmp / "ws"
    hooks = ws / ".claude" / "hooks"
    hooks.mkdir(parents=True)
    (ws / "governance").mkdir()
    shutil.copy(SRC / "pretool_guard.py", hooks / "pretool_guard.py")
    for s in SIBLINGS:
        if (SRC / s).is_file():
            shutil.copy(SRC / s, hooks / s)
    _approve(ws)
    yield ws
    shutil.rmtree(tmp, ignore_errors=True)
    try:
        base.rmdir()
    except OSError:
        pass


def _approve(root):
    """B5-30 (reviewer A round 5): hook siblings run only as APPROVED bytes, so a fixture install gets its own approved
    list, as a landed install does (`hook_support.py --write-approved`)."""
    import importlib.util
    (root / "governance").mkdir(exist_ok=True)
    spec = importlib.util.spec_from_file_location("_fixture_hook_support", root / ".claude" / "hooks" / "hook_support.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    m.write_approved(root)


def guard(ws, cmd=None, raw=None, transcript=None, tool="Bash"):
    if raw is None:
        payload = {"tool_name": tool, "tool_input": {"command": cmd}, "cwd": str(ws)}
        if transcript is not None:
            payload["transcript_path"] = str(transcript)
        raw = json.dumps(payload)
    r = subprocess.run([sys.executable, str(ws / ".claude/hooks/pretool_guard.py")], input=raw,
                       capture_output=True, text=True, cwd=str(ws), timeout=30)
    assert r.returncode == 0, r.stderr[-500:]
    if not r.stdout.strip():
        return "allow", ""
    h = json.loads(r.stdout)["hookSpecificOutput"]
    return h["permissionDecision"], h["permissionDecisionReason"]


# ------------------------------------------------------------------ H4a: governor writes ask
GOVERNOR_WRITES = [
    "echo '{}' > .claude/settings.json",
    "cat new.json > {ws}/.claude/settings.local.json",
    "sed -i '' 's/22:30/00:00/' governance/machine_config.yaml",           # S4-22
    "perl -pi -e 's/a/b/' governance/machine_config.yaml",
    "echo x >> governance/routing_policy.yaml",
    "sudo tee governance/hooks_approved.sha256 < /tmp/h",
    "cp /tmp/x.py .claude/hooks/pretool_guard.py",
    "cp /tmp/pretool_guard.py .claude/hooks/",
    "tee .claude/hooks/new_hook.py < /tmp/y",
    "rm governance/.claude/settings.json",                                 # S4-23 escape: ask, not deny
    "rm -rf .claude",
    "rm .claude/hooks/*.py",
    "chmod -x .claude/hooks/stop_closeout_gate.py",
    "mv .claude/hooks/pretool_guard.py /tmp/",
    "cd .claude && echo '{}' > settings.json",
    "git checkout -- .claude/hooks/pretool_guard.py",
    "ln -sf /tmp/evil.py .claude/hooks/pretool_guard.py",
    "rsync -a --delete /tmp/empty/ governance/adapters/" "claude-code/",   # split: the publish redaction R13 must not rewrite it
    "echo '{}' > /srv/example/other-project/.claude/settings.json",
    "echo \"x > .claude/settings.json",                                    # unterminated quote: fallback
]

ALLOWED = [
    "cat .claude/settings.json",
    "grep -n deny .claude/hooks/pretool_guard.py",
    "python3.12 .claude/hooks/test_pretool_s4_bypasses.py",
    "cp .claude/hooks/pretool_guard.py /tmp/backup.py",
    "shasum -a 256 .claude/hooks/*.py",
    "diff .claude/hooks/pretool_guard.py /tmp/candidate.py",
    "sed -n 1,20p governance/machine_config.yaml",
    "echo hi > governance/NEOCORTEX/NOTE_x.md",
    "cp a.md governance/",
    "git diff .claude/hooks",
    "rm /tmp/scratch.txt",                       # (`git checkout main` moved to SEATB_MUST_ASK: B-05)
    "ls -la governance",
]


@pytest.mark.parametrize("cmd", GOVERNOR_WRITES)
def test_h4a_governor_write_asks(ws, cmd):
    d, why = guard(ws, cmd.replace("{ws}", str(ws)))
    assert d == "ask" and "GOVERNOR WRITE" in why, (cmd, d, why)


@pytest.mark.parametrize("cmd", ALLOWED)
def test_h4a_reads_and_unrelated_writes_stay_silent(ws, cmd):
    assert guard(ws, cmd) == ("allow", ""), cmd


def test_deny_still_wins_over_ask(ws):
    d, why = guard(ws, f"cp draft.md {L0}")
    assert d == "deny" and "L0" in why


# ------------------------------------------------------------------ H0: fail closed-to-ask
def test_h0_unreadable_payload_asks(ws):
    d, why = guard(ws, raw="this is not json")
    assert d == "ask" and "GUARD ERROR" in why


def test_h0_non_object_payload_asks(ws):
    d, why = guard(ws, raw="[]")
    assert d == "ask" and "GUARD ERROR" in why


def test_h0_matcher_crash_asks_and_is_logged(ws):
    driver = (
        "import sys, io, json; sys.path.insert(0, %r); import pretool_guard as g\n"
        "def boom(*a, **k): raise RuntimeError('injected')\n"
        "g.l0_write_target = boom\n"
        "sys.stdin = io.StringIO(json.dumps({'tool_input': {'command': 'ls'}, 'cwd': %r}))\n"
        "g.main()\n" % (str(ws / ".claude/hooks"), str(ws)))
    r = subprocess.run([sys.executable, "-c", driver], capture_output=True, text=True, timeout=30)
    h = json.loads(r.stdout)["hookSpecificOutput"]
    assert h["permissionDecision"] == "ask" and "RuntimeError" in h["permissionDecisionReason"]
    log = (ws / "governance" / "hook_errors.jsonl").read_text()
    assert "pretool_guard.matchers" in log and "injected" in log


def test_h0_broken_sibling_does_not_disarm_the_deny_guards(ws):
    (ws / ".claude/hooks/loop_bound.py").write_text("this is not python(\n")
    assert guard(ws, "ls") == ("allow", "")
    d, _ = guard(ws, f"cp draft.md {L0}")
    assert d == "deny"
    # B4-18 (reviewer A round 4): siblings load lazily, so loop_bound is only loaded (and its failure logged) when a
    # verifier command needs the H9 path; the DENY path above never loaded it.
    assert guard(ws, "pytest -q") == ("allow", "")
    assert "pretool_guard.import.loop_bound" in (ws / "governance" / "hook_errors.jsonl").read_text()


def test_osascript_contact_delete_still_denied(ws):
    raw = json.dumps({"tool_input": {"script": "tell application \"Contacts\" to " + "del" + "ete per" + "son 1"}})
    d, _ = guard(ws, raw=raw)
    assert d == "deny"


# ------------------------------------------------------------------ H9: Art. 14 loop bound
class T:
    def __init__(self):
        self.lines, self.n = [], 0

    def prompt(self, text="fix it"):
        self.lines.append({"type": "user", "message": {"role": "user", "content": text}})
        return self

    def tool(self, name, inp, out, err):
        self.n += 1
        tid = f"t{self.n}"
        self.lines.append({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": tid, "name": name, "input": inp}]}})
        self.lines.append({"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": tid, "is_error": err,
             "content": f"Exit code 1\n{out}" if err else out}]}})
        return self

    def fail(self, out="FAILED tests/test_a.py::test_x - AssertionError: assert 1 == 2\n1 failed in 0.12s"):
        return self.tool("Bash", {"command": "pytest -q"}, out, True)

    def ok(self):
        return self.tool("Bash", {"command": "pytest -q"}, "3 passed in 0.1s", False)

    def edit(self):
        return self.tool("Edit", {"file_path": "/x/src/a.py", "old_string": "1", "new_string": "2"}, "ok", False)

    def save(self, p):
        p.write_text("".join(json.dumps(l) + "\n" for l in self.lines))
        return p


def lb(ws, t, cmd="pytest -q"):
    return guard(ws, cmd, transcript=t.save(ws / "t.jsonl"))


def test_h9_first_red_is_free(ws):
    assert lb(ws, T().prompt().fail()) == ("allow", "")


def test_h9_tdd_red_then_green_is_free(ws):
    assert lb(ws, T().prompt().fail().edit().ok()) == ("allow", "")


def test_h9_two_cycles_still_free(ws):
    assert lb(ws, T().prompt().fail().edit().fail().edit().fail()) == ("allow", "")


def test_h9_three_cycles_same_failure_asks(ws):
    t = T().prompt().fail().edit().fail().edit().fail().edit().fail()
    d, why = lb(ws, t)
    assert d == "ask" and "Art. 14 STOP" in why and "3 fix-verify cycles" in why, why


def test_h9_numbers_and_paths_do_not_split_the_signature(ws):
    t = T().prompt()
    for i in range(4):
        t.fail(f"FAILED /tmp/run{i}/tests/test_a.py::test_x - assert {i} == 99\n1 failed in 0.{i}s")
        t.edit()
    d, _ = lb(ws, t)
    assert d == "ask"


def test_h9_signature_churn_hits_the_turn_cap(ws):
    t = T().prompt()
    for i in range(6):
        t.fail(f"FAILED tests/test_{'abcdef'[i]}.py - NameError: name_{'abcdef'[i]}\n1 failed")
    d, why = lb(ws, t)
    assert d == "ask" and "6 failing verification runs" in why, why


def test_h9_only_asks_before_a_verifier(ws):
    t = T().prompt().fail().edit().fail().edit().fail().edit().fail()
    assert lb(ws, t, cmd="git status") == ("allow", "")


def test_h9_founder_prompt_resets(ws):
    t = T().prompt().fail().edit().fail().edit().fail().edit().fail().prompt("try once more")
    assert lb(ws, t) == ("allow", "")


def test_h9_an_approved_run_resets_the_count(ws):
    t = T().prompt().fail().edit().fail().edit().fail().edit().fail().edit().fail()
    assert lb(ws, t) == ("allow", "")


def test_h9_denied_run_is_ignored(ws):
    t = T().prompt().fail().edit().fail().edit().fail().edit()
    t.tool("Bash", {"command": "pytest -q"}, "The user doesn't want to proceed with this tool use.", True)
    assert lb(ws, t) == ("allow", "")


def test_h9_bash_writes_count_as_edits(ws):
    t = T().prompt().fail()
    for _ in range(3):
        t.tool("Bash", {"command": "cat > src/a.py <<'EOF'\nx = 2\nEOF"}, "", False)
        t.fail()
    d, _ = lb(ws, t)
    assert d == "ask"


def test_h9_broken_transcript_fails_open_and_is_logged(ws):
    d = guard(ws, "pytest -q", transcript=ws / "governance")      # a directory, not a file
    assert d == ("allow", "")
    assert "loop_bound" in (ws / "governance" / "hook_errors.jsonl").read_text()


# ------------------------------------------------------------------ H0 launcher (wired only at D6)
def launch(ws, raw):
    shutil.copy(SRC / "pretool_guard.sh", ws / ".claude/hooks/pretool_guard.sh")
    r = subprocess.run(["sh", str(ws / ".claude/hooks/pretool_guard.sh")], input=raw,
                       capture_output=True, text=True, cwd=str(ws), timeout=60)
    assert r.returncode == 0
    if not r.stdout.strip():
        return "allow", ""
    h = json.loads(r.stdout)["hookSpecificOutput"]
    return h["permissionDecision"], h["permissionDecisionReason"]


def _p(cmd):
    return json.dumps({"tool_input": {"command": cmd}})


@_NEEDS_LAUNCHER
def test_launcher_passes_decisions_through(ws):
    assert launch(ws, _p("ls")) == ("allow", "")
    assert launch(ws, _p(f"cp draft.md {L0}"))[0] == "deny"
    assert launch(ws, _p("echo x > .claude/settings.json"))[0] == "ask"


@_NEEDS_LAUNCHER
def test_launcher_asks_when_the_guard_cannot_even_import(ws):
    (ws / ".claude/hooks/pretool_guard.py").write_text("def broken(:\n")
    d, why = launch(ws, _p("ls"))
    assert d == "ask" and "exited with status" in why


@_NEEDS_LAUNCHER
def test_launcher_asks_when_the_guard_is_missing(ws):
    (ws / ".claude/hooks/pretool_guard.py").unlink()
    d, why = launch(ws, _p("ls"))
    assert d == "ask" and "missing" in why


@_NEEDS_LAUNCHER
def test_launcher_asks_on_garbage_output(ws):
    (ws / ".claude/hooks/pretool_guard.py").write_text("print('hello')\n")
    d, why = launch(ws, _p("ls"))
    assert d == "ask" and "not a decision" in why


# ------------------------------------------------------------------ P6 replay false asks (2026-10-05)
REPLAY_FALSE_ASKS = [
    "python3.12 - <<'EOF'\nimport os\n# trash old files, then rm -rf /\nprint('rm .claude/settings.json')\nEOF",
    "cat > notes.md <<EOF\nThe regime never reached governance/.claude/settings.json via the S4 path\nEOF",
    "rm -rf \"$TMPDIR\"/x && mkdir -p \"$D\"/.claude",
    "mkdir -p /tmp/fx/.claude && echo '{}' > /tmp/fx/.claude/settings.json",
    "cp -R fixture /private/tmp/edge/.claude/hooks/",
    "rm -f *.js",
    "rm -f build/*.html",
    "git add adapters/claude-code/install_adapters.py",
]
REPLAY_TRUE_ASKS = [
    "rm -rf *",
    "cd governance && mv .claude/settings.json /tmp/settings.bak",
    "cat > .claude/settings.json <<'EOF'\n{}\nEOF",
]


@pytest.mark.parametrize("cmd", REPLAY_FALSE_ASKS)
def test_p6_replay_false_asks_are_gone(ws, cmd):
    assert guard(ws, cmd) == ("allow", ""), cmd


@pytest.mark.parametrize("cmd", REPLAY_TRUE_ASKS)
def test_p6_replay_true_asks_remain(ws, cmd):
    assert guard(ws, cmd)[0] == "ask", cmd


# ------------------------------------------------------------------ Seat A (MiMo Flash) findings, 2026-10-05
def test_seatA_f1_self_approval_of_the_hash_list_asks(ws):
    d, why = guard(ws, "python3.12 .claude/hooks/hook_support.py --write-approved")
    assert d == "ask" and "GOVERNOR" in why


def test_seatA_f5_osascript_do_shell_script_is_governed(ws):
    script = 'do shell script "sed -i \'\' \'s/02:40/00:00/\' governance/machine_config.yaml"'
    raw = json.dumps({"tool_name": "mcp__Control_your_Mac__osascript", "tool_input": {"script": script}, "cwd": str(ws)})
    d, why = guard(ws, raw=raw)
    assert d == "ask" and "GOVERNOR" in why


def test_seatA_f8_compound_write_and_test_counts_as_an_edit(ws):
    t = T().prompt()
    for _ in range(4):
        t.tool("Bash", {"command": "cat > src/a.py <<'EOF'\nx = 2\nEOF\npytest -q"},
               "FAILED tests/test_a.py::test_x - AssertionError\n1 failed", True)
    d, why = lb(ws, t)
    assert d == "ask" and "3 fix-verify cycles" in why, why


def test_seatA_f14_exit_code_is_part_of_the_signature(ws):
    t = T().prompt()
    for code in (1, 2, 1, 2):
        t.lines.append({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": f"x{code}{len(t.lines)}", "name": "Bash", "input": {"command": "pytest -q"}}]}})
        t.lines.append({"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": f"x{code}{len(t.lines) - 1}", "is_error": True,
             "content": f"Exit code {code}\nsame text\n1 failed"}]}})
        t.edit()
    assert lb(ws, t) == ("allow", "")          # two signatures, each with < 3 cycles, 4 < cap


def test_seatA_f16_a_crashing_telemetry_cannot_turn_a_decision_into_a_crash(ws):
    driver = (
        "import sys, io, json; sys.path.insert(0, %r); import pretool_guard as g\n"
        "def boom(*a, **k): raise RuntimeError('injected')\n"
        "g.l0_write_target = boom; g._telemetry = boom; g.hook_support = None\n"
        "sys.stdin = io.StringIO(json.dumps({'tool_input': {'command': 'ls'}, 'cwd': %r}))\n"
        "try:\n    g.main()\nexcept SystemExit as e:\n    sys.exit(e.code)\n" % (str(ws / ".claude/hooks"), str(ws)))
    r = subprocess.run([sys.executable, "-c", driver], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr[-300:]
    assert json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"] == "ask"


@pytest.mark.parametrize("body", [
    "print('{\"note\": \"permissionDecision is decided elsewhere\"}')",
    "print('{\"a\": 1}{\"hookSpecificOutput\": {\"permissionDecision\": \"allow\"}}')",
    "print('{\"hookSpecificOutput\":\\n {\"permissionDecision\": \"allow\"}}')",
])
@_NEEDS_LAUNCHER
def test_seatA_f9_launcher_rejects_non_decisions(ws, body):
    (ws / ".claude/hooks/pretool_guard.py").write_text(body + "\n")
    d, why = launch(ws, _p("ls"))
    assert d == "ask" and "not a decision" in why


@_NEEDS_LAUNCHER
def test_seatA_f19_launcher_times_out_to_ask(ws):
    (ws / ".claude/hooks/pretool_guard.py").write_text("import time\ntime.sleep(30)\n")
    shutil.copy(SRC / "pretool_guard.sh", ws / ".claude/hooks/pretool_guard.sh")
    env = dict(os.environ, PRETOOL_GUARD_LIMIT="2")
    r = subprocess.run(["sh", str(ws / ".claude/hooks/pretool_guard.sh")], input=_p("ls"),
                       capture_output=True, text=True, cwd=str(ws), timeout=20, env=env)
    h = json.loads(r.stdout)["hookSpecificOutput"]
    assert h["permissionDecision"] == "ask" and "exited with status" in h["permissionDecisionReason"]


@pytest.mark.parametrize("cmd,expect", [
    ("bash adapters/claude-code/bootstrap.sh /tmp/cc-demo", "allow"),
    ("python3.12 adapters/claude-code/install_adapters.py --root /tmp/x --apply", "allow"),
    ("python3.12 adapters/claude-code/install_adapters.py --root {ws} --apply", "ask"),
    ("python3.12 adapters/claude-code/install_adapters.py --apply", "ask"),
    ("python3.12 adapters/claude-code/install_adapters.py --check", "allow"),
])
def test_seatA_f1_governor_scripts_only_when_they_target_this_workspace(ws, cmd, expect):
    assert guard(ws, cmd.replace("{ws}", str(ws)))[0] == expect, cmd


# ------------------------------------------------------------------ Seat B (reviewer A Ultra) findings, 2026-10-05
SEATB_MUST_ASK = [
    "cp x .claude/hooks/pretool_guard.py 2>/dev/null",                     # B-01
    "cp x .claude/hooks/pretool_guard.py > /tmp/cp.log",
    "cp x .claude/hooks/pretool_guard.py </dev/null",
    "cp -t .claude/hooks x.py",                                            # B-02
    "install -t .claude/hooks x.py",
    "cp --target-directory=.claude/hooks x.py",
    "cp -R candidate/.claude .",                                           # B-03
    "rsync -a candidate/.claude/ .claude/",
    "tar -xf hooks.tar -C .claude/hooks",                                  # B-04
    "unzip -o kit.zip -d .claude/hooks",
    "ditto newhooks .claude/hooks",
    'find .claude/hooks -name "*.py" -delete',
    "find .claude/hooks -exec rm {} +",
    "git apply fix.patch",                                                 # B-05
    "git apply --directory=.claude/hooks fix.patch",
    "git stash pop",
    "git reset --hard HEAD~1",
    "git checkout main",
    "git -C . checkout -- .claude/hooks/pretool_guard.py",
    "git clean -fd .claude/hooks",
    "nice -n 10 cp x .claude/hooks/pretool_guard.py",                      # B-06
    "sudo -u root cp x .claude/hooks/pretool_guard.py",
    "pushd .claude && cp /tmp/x hooks/pretool_guard.py",                   # B-07
    "(echo ready); cp x .claude/hooks/pretool_guard.py",
    "(cd /tmp ) ; cp /tmp/x .claude/hooks/pretool_guard.py",
    'cp /tmp/x .claude/"hooks"/pretool_guard.py',                          # B-08
    "rm -rf .claude/{hooks,staging}",
    "cp /tmp/x .CLAUDE/hooks/pretool_guard.py",
    "vim .claude/hooks/pretool_guard.py",                                  # form change: unknown verb + protected path
]
SEATB_MUST_STAY_SILENT = [
    "rsync --dry-run -av src/ .claude/hooks/",                             # B-29
    "rsync -n -av src/ .claude/hooks/",
    "patch --dry-run .claude/hooks/pretool_guard.py < fix.diff",
    "git apply --check .claude/hooks/fix.patch",
    "echo bootstrap.sh",                                                   # B-30
    "cat .claude/hooks/hook_support.py --write-approved",
    "git status",
    "git log --oneline -5",
    "git diff .claude/hooks",
    "jq . .claude/settings.json",
    "pytest .claude/hooks/test_pretool_h0_h4_h9.py -q",
    "python3.12 .claude/hooks/test_pretool_s4_bypasses.py",
    "bash -n .claude/hooks/session_anchor.sh",
    "shasum -a 256 .claude/hooks/*.py",
    "ls -la .claude/hooks",
    "cp .claude/hooks/pretool_guard.py /tmp/backup.py",
]


@pytest.mark.parametrize("cmd", SEATB_MUST_ASK)
def test_seatB_governor_bypasses_now_ask(ws, cmd):
    subprocess.run(["git", "-C", str(ws), "init", "-q"], check=True)   # a REAL repo holding the governor
    d, why = guard(ws, cmd)                                              # (an empty .git dir lets git walk up)
    assert d == "ask" and "GOVERNOR" in why, (cmd, d, why)


@pytest.mark.parametrize("cmd", SEATB_MUST_STAY_SILENT)
def test_seatB_no_false_governor_ask(ws, cmd):
    subprocess.run(["git", "-C", str(ws), "init", "-q"], check=True)
    assert guard(ws, cmd) == ("allow", ""), cmd


def test_seatB_git_worktree_ops_outside_the_governor_repo_stay_silent(ws):
    other = ws.parent / "otherrepo"
    (other / ".git").mkdir(parents=True)
    raw = json.dumps({"tool_input": {"command": "git reset --hard HEAD~1"}, "cwd": str(other)})
    assert guard(ws, raw=raw) == ("allow", "")


def test_seatB_b09_parenthesised_or_unresolvable_do_shell_script(ws):
    for script in ('do shell script ("cp x .claude/hooks/pretool_guard.py")',
                   'set c to "cp x " & "y"\ndo shell script c'):
        raw = json.dumps({"tool_name": "mcp__Control_your_Mac__osascript", "tool_input": {"script": script}, "cwd": str(ws)})
        assert guard(ws, raw=raw)[0] == "ask", script


def test_seatB_b19_huge_heredoc_with_stray_quote_is_fast_and_still_denies(ws):
    import time
    c = "cat > notes.txt <<'EOF'\n\"" + "\nline" * 6000 + "\nEOF\nrm -rf _archive"
    t = time.monotonic()
    d, _ = guard(ws, c)
    assert time.monotonic() - t < 5 and d == "deny"


def test_seatB_b20_unreadable_policy_asks_for_extraction_commands(ws):
    assert not (ws / "governance" / "machine_config.yaml").exists()
    d, why = guard(ws, "python3 unified_extractor.py --all")
    assert d == "ask" and "machine_config" in why


def test_seatB_b22_permission_text_in_a_test_failure_is_still_a_failure(ws):
    t = T().prompt()
    for _ in range(4):
        t.tool("Bash", {"command": "pytest -q"},
               "FAILED tests/test_access.py - AssertionError: permission to use resource\n1 failed in 0.1s", True)
        t.edit()
    assert lb(ws, t)[0] == "ask"


def test_seatB_b23_formatter_edits_count(ws):
    t = T().prompt().fail()
    for _ in range(3):
        t.tool("Bash", {"command": "ruff format src/app.py"}, "1 file reformatted", False)
        t.fail()
    assert lb(ws, t)[0] == "ask"


def test_seatB_b24_green_runs_reset_the_cycle_count(ws):
    t = T().prompt()
    for _ in range(3):
        t.fail().edit().ok()
    t.fail()
    assert lb(ws, t) == ("allow", "")


# ------------------------------------------------------------------ replay 2026-10-05 (post-Seat-B) precision
def test_replay_shell_fed_heredoc_body_is_checked(ws):
    d, _ = guard(ws, "bash <<'EOF'\ncp x " + L0 + "\nEOF")
    assert d == "deny"
    d, why = guard(ws, "sh <<EOF\ncp x .claude/hooks/pretool_guard.py\nEOF")
    assert d == "ask" and "GOVERNOR" in why


@pytest.mark.parametrize("cmd", [
    "git reset -q HEAD~1",                       # mixed reset: worktree untouched
    "rm -rf */.pytest_cache/*",                  # glob cannot reach .claude/hooks (dot rule + rest)
    "find . -name '*.pyc' -delete",              # name filter cannot match a governor file
    "python3 - <<'EOF'\nprint('cp x " + L0 + "')\nEOF",
])
def test_replay_false_asks_removed(ws, cmd):
    (ws / ".git").mkdir(exist_ok=True)
    assert guard(ws, cmd) == ("allow", ""), cmd


def test_replay_find_name_matching_hooks_still_asks(ws):
    assert guard(ws, "find .claude/hooks -name '*.py' -delete")[0] == "ask"


def _real_repo(ws):
    g = ["git", "-C", str(ws)]
    subprocess.run(g + ["init", "-q", "-b", "main"], check=True)
    (ws / ".claude/hooks/hook_a.py").write_text("A = 1\n")
    (ws / "notes.txt").write_text("n\n")
    subprocess.run(g + ["add", "-A"], check=True)
    subprocess.run(g + ["-c", "user.email=fixture-identity", "-c", "user.name=t", "commit", "-qm", "base"], check=True)
    subprocess.run(g + ["checkout", "-qb", "docs"], check=True)
    (ws / "notes.txt").write_text("n2\n")
    subprocess.run(g + ["-c", "user.email=fixture-identity", "-c", "user.name=t", "commit", "-qam", "docs only"], check=True)
    subprocess.run(g + ["checkout", "-qb", "hooks", "main"], check=True)
    (ws / ".claude/hooks/hook_a.py").write_text("A = 2\n")
    subprocess.run(g + ["-c", "user.email=fixture-identity", "-c", "user.name=t", "commit", "-qam", "hook change"], check=True)
    subprocess.run(g + ["checkout", "-q", "main"], check=True)


def test_replay_git_ops_ask_only_when_git_says_governor_files_change(ws):
    _real_repo(ws)
    assert guard(ws, "git merge --ff-only docs") == ("allow", "")
    assert guard(ws, "git checkout -q docs") == ("allow", "")
    d, why = guard(ws, "git merge --ff-only hooks")
    assert d == "ask" and "would change governor files" in why
    assert guard(ws, "git cherry-pick hooks")[0] == "ask"
    assert guard(ws, "git cherry-pick docs") == ("allow", "")


# ------------------------------------------------------------------ reviewer A S4 FINAL S4-46
def test_s4_46_window_patterns_match_the_executed_script(ws):
    (ws / "governance" / "machine_config.yaml").write_text('forbidden_windows:\n  - window: "00:00-23:59"\n')
    for cmd in ("python3.12 scripts/embed_corpus.py", "python3.12 scripts/ingest_all.py --rebuild-index",
                "python3 unified_extractor.py"):
        assert guard(ws, cmd)[0] == "deny", cmd
    for cmd in ("grep -n embedding notes.md", "cat scripts/ingest_all.py"):
        assert guard(ws, cmd) == ("allow", ""), cmd


def test_s4_40_l0_refusal_names_the_lawful_path(ws):
    d, why = guard(ws, f"cp ratified.md {L0}")
    assert d == "deny" and "DRAFT_L0_amendment" in why and "S4-40" in why


# ------------------------------------------------------------------ reviewer A round-2 probe (residual B-19), 2026-10-05
@pytest.mark.parametrize("L,n", [(250, 199), (1000, 199), (5000, 40)])
def test_round2_open_quote_inputs_stay_fast(ws, L, n):
    import time
    c = 'echo "' + ("\n" + "x" * L) * n + '"'
    t = time.monotonic()
    guard(ws, c)
    assert time.monotonic() - t < 3.0, (L, n)


def test_round2_oversize_command_still_denies_an_l0_write(ws):
    c = "cat big.txt " + "y " * 40000 + f"; cp x {L0}"
    assert guard(ws, c)[0] == "deny"


# ------------------------------------------------------------------ reviewer A round 2b, DENY path (B2-23, B2-24), 2026-10-06
ARC_TOKEN = "_arch" + "ive"


@pytest.mark.parametrize("prefix", ["cp " * 27000, "rm " * 16000, "x" * 49000 + " "])
def test_b2_23_large_inputs_reach_the_archive_deny_fast(ws, prefix):
    import time
    c = prefix + "; rm -rf " + ARC_TOKEN
    t = time.monotonic()
    d, _ = guard(ws, c)
    assert d == "deny" and time.monotonic() - t < 3.0


def test_b2_23_oversize_l0_write_still_denied_fast(ws):
    import time
    c = "cp " * 27000 + f"; cp x {L0}"
    t = time.monotonic()
    assert guard(ws, c)[0] == "deny" and time.monotonic() - t < 3.0


@pytest.mark.parametrize("ti", [[], "", False, 0, None, "rm -rf x"])
def test_b2_24_non_object_tool_input_asks(ws, ti):
    d, why = guard(ws, raw=json.dumps({"tool_input": ti}))
    assert d == "ask" and "GUARD ERROR" in why, ti


# B2-23 controls after the fix: the bounded windows stay fast on verb-dense text below the oversize limit,
# a deny whose verb sits further than one window from the token becomes an ASK (never an allow), and
# ordinary long prose naming an archive stays silent.
def _dense(verb, tail_token, k=1300):      # each verb run stays under one window (4,096 chars)
    return ((verb * k + "; echo " + tail_token + " ; " + verb * k + "\n") * 6)[:49000]


@pytest.mark.parametrize("cmd", [_dense("rm ", ARC_TOKEN), _dense("find ", ARC_TOKEN, 800), _dense("cd ", "x" + ARC_TOKEN),
                                 "'" + _dense("cp ", L0)[:48000]], ids=["rm", "find", "cd", "cp-unparsed"])
def test_b2_23_dense_normal_path_is_fast_and_silent(ws, cmd):
    import time
    t = time.monotonic()
    d, _ = guard(ws, cmd)
    assert d == "allow" and time.monotonic() - t < 3.0


def test_b2_23_verb_beyond_the_window_asks_not_allows(ws):
    # B4-17 (2026-10-06): the parsed archive check sees the whole command, so this real deletion now DENIES
    # (it used to be only an incomplete-window ASK); never an allow either way.
    d, why = guard(ws, "rm -rf " + "y " * 6000 + ARC_TOKEN)
    assert d == "deny" or (d == "ask" and "B2-23" in why)


def test_b2_23_long_prose_heredoc_naming_an_archive_stays_silent(ws):
    body = ("Moved the old plan to " + ARC_TOKEN + "/2026-10-05/ and kept the note; see the index.\n") * 5000
    assert guard(ws, "cat > n.md <<'EOF'\n" + body + "EOF")[0] == "allow"


# B2-35 / B2-29 (reviewer A round 2b): check, diff and lint modes of formatters write nothing — no governor ask,
# and no loop-bound "edit"; real write modes still ask.
@pytest.mark.parametrize("cmd", ["black --check .claude/hooks/pretool_guard.py", "ruff check .claude/hooks/pretool_guard.py",
                                 "ruff format --check .claude/hooks/pretool_guard.py", "prettier --check .claude/settings.json",
                                 "eslint .claude/hooks/script.js", "clang-format --dry-run .claude/hooks/helper.cpp",
                                 "black --diff .claude/hooks/pretool_guard.py"])
def test_b2_35_check_only_formatters_do_not_ask(ws, cmd):
    assert guard(ws, cmd)[0] == "allow", cmd


@pytest.mark.parametrize("cmd", ["black .claude/hooks/pretool_guard.py", "ruff format .claude/hooks/pretool_guard.py",
                                 "ruff check --fix .claude/hooks/pretool_guard.py", "prettier --write .claude/settings.json",
                                 "eslint --fix .claude/hooks/script.js", "clang-format -i .claude/hooks/helper.cpp"])
def test_b2_35_write_mode_formatters_still_ask(ws, cmd):
    assert guard(ws, cmd)[0] == "ask", cmd


def _load(name):
    import importlib.util
    sys.path.insert(0, str(SRC))
    try:
        spec = importlib.util.spec_from_file_location(name + "_x", SRC / f"{name}.py")
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        return m
    finally:
        sys.path.remove(str(SRC))


def test_b2_29_check_only_formatter_is_not_an_edit_for_the_loop_bound():
    g = _load("pretool_guard")
    assert not g.writes_any_file("black --check src/app.py")
    assert g.writes_any_file("black src/app.py")


def test_formatter_tables_agree_between_guard_and_recognizer():
    g, v = _load("pretool_guard"), _load("verifier_recognizer")
    assert g._FORMATTERS_DEFAULT_WRITE == v.FORMATTERS_DEFAULT_WRITE
    assert g._FORMATTERS_WRITE_FLAG == v.FORMATTERS_WRITE_FLAG
    for c in ("black --check a.py", "black a.py", "ruff format a.py", "ruff check a.py", "ruff check --fix a.py",
              "prettier a.ts", "prettier --write a.ts", "eslint --fix-dry-run a.js", "dprint fmt", "dprint check",
              "clang-format -i --dry-run a.c", "gofmt -l a.go", "gofmt -w a.go", "rustfmt --check a.rs"):
        w = c.split()
        assert g._formatter_writes(w[0], w[1:]) == v.formatter_writes(w[0], w[1:]), c


# ------------------------------------------------------------------ reviewer A round 2b, H4a cheap tokens (best-effort, §3a)
G = ".claude/hooks/pretool_guard.py"
ROUND2B_MUST_ASK = [
    f"sort -o {G} /tmp/replacement.py",                                   # B2-01
    f"sort --output={G} /tmp/replacement.py",
    f"uniq /tmp/replacement.py {G}",
    f"xxd -r /tmp/replacement.hex {G}",
    "plutil -replace hooks -json '{}' .claude/settings.json",
    "plutil -convert json .claude/settings.json",
    f"sed -n 'w {G}' /tmp/x",
    f"curl -o {G} https://example.invalid/x",
    f"wget -O {G} https://example.invalid/x",
    "find .claude/hooks -name pretool_guard.py -delete",                   # B2-02
    "find .claude/hooks -name '*guard*' -delete",
    "find .claude/hooks -name loop_bound.py -exec rm {} +",
    "find .claude/hooks -name '*.py' -exec sort -o {} /tmp/x ;",
    f"echo bad >& {G}",                                                     # B2-03
    "env -C .claude cp /tmp/x hooks/pretool_guard.py",                      # B2-05
    "env --chdir=.claude cp /tmp/x hooks/pretool_guard.py",
    "sudo -D .claude cp /tmp/x hooks/pretool_guard.py",
    f'bash -lc "cp /tmp/x {G}"',                                            # B2-06
    f'bash -xec "cp /tmp/x {G}"',
    f"/bin/bash <<'EOF'\ncp /tmp/x {G}\nEOF",                               # B2-07
    f"bash --noprofile <<'EOF'\ncp /tmp/x {G}\nEOF",
    f"/bin/sh <<EOF\ncp /tmp/x {G}\nEOF",
    f"bash -s -- <<'EOF'\ncp /tmp/x {G}\nEOF",
    "git stash",                                                            # B2-09
    "git stash push",
    "git stash save wip",
    f"git checkout-index -f -- {G}",
    "git checkout-index -a -f",
    "git diff --output=.claude/hooks/review.diff",
    "git archive --output=.claude/hooks/bundle.tar HEAD",
    "git archive -o .claude/hooks/bundle.tar HEAD",
    "git config --file .claude/hooks/dev.gitconfig user.name Agent",
    "git restore -- .",                                                     # B2-10
    "git checkout -- .",
    "git restore -- .claude",
    "git checkout -- governance",
    "git mv .claude disabled-claude",
    "git checkout -f HEAD",                                                 # B2-11
    "git --work-tree=. --git-dir=.git reset --hard",
    f"rsync --log-file={G} /tmp/x /tmp/y",                                  # B2-12
    f"rsync --log-file {G} /tmp/x /tmp/y",
    "osacompile -o .claude/hooks/compiled.scpt /tmp/good.applescript",
    f"time -o {G} true",
    "git apply --check --apply fix.patch",                                  # B2-13
    "python3.12 .claude/hooks/HOOK_SUPPORT.PY --write-approved",            # B-36
    "cd /definitely-nonexistent-directory; cp /tmp/x governance/machine_config.yaml",   # B2-04 (cheap half)
    "cd .claude/hooks; pushd /tmp; popd; cp /tmp/x pretool_guard.py",
]
ROUND2B_MUST_STAY_SILENT = [
    "git clean -n .claude/hooks",                                           # B2-36
    "git clean -nd .claude/hooks",
    "git clean --dry-run .claude/hooks",
    "unzip -l kit.zip -d .claude/hooks",
    "unzip -t kit.zip -d .claude/hooks",
    f"unzip -p kit.zip {G}",
    f"sort -o /tmp/sorted.txt {G}",                                         # B2-01 controls: reads stay free
    f"sort {G}",
    f"uniq {G}",
    f"xxd {G}",
    "plutil -p .claude/settings.json",
    "plutil -lint .claude/settings.json",
    "find .claude/hooks -name '*.pyc' -delete",                            # B2-02 control
    "git diff --stat",
    "git archive -o /tmp/b.tar HEAD",
    "bash -lc 'echo hi'",
    "/bin/bash -n .claude/hooks/session_anchor.sh",
    "env -C /tmp cp /tmp/x y.py",
]


@pytest.mark.parametrize("cmd", ROUND2B_MUST_ASK)
def test_round2b_governor_writes_ask(ws, cmd):
    subprocess.run(["git", "-C", str(ws), "init", "-q"], check=True)
    d, why = guard(ws, cmd)
    assert d == "ask" and "GOVERNOR" in why, (cmd, d, why)


@pytest.mark.parametrize("cmd", ROUND2B_MUST_STAY_SILENT)
def test_round2b_no_false_governor_ask(ws, cmd):
    subprocess.run(["git", "-C", str(ws), "init", "-q"], check=True)
    assert guard(ws, cmd) == ("allow", ""), cmd


def test_b2_08_applescript_newline_escape_is_decoded(ws):
    script = 'do shell script "echo ready\\ncp /tmp/x .claude/hooks/pretool_guard.py"'
    raw = json.dumps({"tool_name": "mcp__Control_your_Mac__osascript", "tool_input": {"script": script}, "cwd": str(ws)})
    assert guard(ws, raw=raw)[0] == "ask"


def test_b2_39_a_fixture_copy_of_the_approval_writer_does_not_ask(ws):
    fx = ws.parent / "fixture" / ".claude" / "hooks"
    fx.mkdir(parents=True)
    (fx / "hook_support.py").write_text("# fixture copy\n")
    assert guard(ws, f"python3.12 {fx}/hook_support.py --write-approved") == ("allow", "")
    assert guard(ws, "python3.12 .claude/hooks/hook_support.py --write-approved")[0] == "ask"


# ------------------------------------------------------------------ H9 after reviewer A rounds 1 and 2b (B-33, B-34, B2-26..28)
def test_b_33_each_verifier_run_in_one_call_counts(ws):
    t = T().prompt().tool("Bash", {"command": "set +e; " + "; ".join(["pytest -q"] * 6)},
                          "FAILED tests/test_a.py::test_x - AssertionError\n1 failed in 0.1s\n" * 6, True)
    d, why = lb(ws, t)
    assert d == "ask" and "6 failing verification runs" in why, why


def test_b_34_capturing_output_to_a_log_is_not_a_fix(ws):
    t = T().prompt()
    for _ in range(4):
        t.tool("Bash", {"command": "pytest -q > /tmp/test.log 2>&1"}, "", True)
    assert lb(ws, t) == ("allow", "")


def test_b2_26_a_no_run_green_does_not_reset_the_bound(ws):
    t = T().prompt()
    for _ in range(3):
        t.edit().fail()
        t.tool("Bash", {"command": "make -n check"}, "pytest -q", False)
    t.edit().fail()
    d, why = lb(ws, t)
    assert d == "ask" and "Art. 14 STOP" in why, why


def test_b2_26_a_summary_less_compound_exit_0_is_not_a_pass(ws):
    t = T().prompt()
    for _ in range(3):
        t.edit().fail()
        t.tool("Bash", {"command": "pytest -q && echo done"}, "done", False)
    t.edit().fail()
    assert lb(ws, t)[0] == "ask"


def test_b2_27_hidden_outcomes_are_bounded_too(ws):
    t = T().prompt()
    for _ in range(6):
        t.edit().tool("Bash", {"command": "pytest -q > /tmp/test.log || true"}, "", False)
    d, why = lb(ws, t)
    assert d == "ask" and "could not be seen" in why, why


def test_b2_28_ansi_colour_does_not_split_the_signature(ws):
    t = T().prompt()
    for i in range(4):
        red = "\x1b[31m" if i % 2 else ""
        t.fail(f"{red}FAILED\x1b[0m tests/test_a.py::test_x - AssertionError\n1 failed in 0.1s" if red
               else "FAILED tests/test_a.py::test_x - AssertionError\n1 failed in 0.1s")
        t.edit()
    assert lb(ws, t)[0] == "ask"


def test_b2_28_mcp_file_edits_count_as_fixes(ws):
    t = T().prompt().fail()
    for _ in range(3):
        t.tool("mcp__filesystem__edit_file", {"path": "/x/src/a.py"}, "ok", False)
        t.fail()
    assert lb(ws, t)[0] == "ask"


def test_b2_27_a_masked_run_with_a_visible_pass_is_progress_not_blind(ws):
    # replay 8 (2026-10-06): 20 of 30 sampled loop asks were blind-cap asks on `pytest | tail` runs that SHOWED a pass
    t = T().prompt()
    for _ in range(6):
        t.edit().tool("Bash", {"command": "pytest -q 2>&1 | tail -3"}, "===== 3 passed in 0.1s =====", False)
    assert lb(ws, t) == ("allow", "")


# ------------------------------------------------------------------ reviewer A round 3 (2026-10-06): approval-list writes
@pytest.mark.parametrize("cmd", [
    "python3.12 -X dev .claude/hooks/hook_support.py --write-approved",          # B3-08
    "python3.12 -W ignore .claude/hooks/hook_support.py --write-approved",
    "sed -n '1,$w governance/hooks_approved.sha256' /tmp/new-baseline",          # B3-09
    "sed -n '1w governance/hooks_approved.sha256' /tmp/new-baseline",
    "sed -n '$w governance/hooks_approved.sha256' /tmp/new-baseline",
    "awk '{print > \"governance/hooks_approved.sha256\"}' /tmp/new-baseline",   # belt: any non-read verb
    "python3 -c \"open('governance/hooks_approved.sha256','w').write('x')\"",
])
def test_b3_08_09_approval_list_writes_ask(ws, cmd):
    d, why = guard(ws, cmd)
    assert d == "ask" and "GOVERNOR" in why, (cmd, d, why)


@pytest.mark.parametrize("cmd", ["cat governance/hooks_approved.sha256", "shasum -c governance/hooks_approved.sha256",
                                 "grep pretool governance/hooks_approved.sha256", "git diff governance/hooks_approved.sha256",
                                 "wc -l governance/hooks_approved.sha256"])
def test_b3_09_reading_the_approval_list_stays_silent(ws, cmd):
    assert guard(ws, cmd) == ("allow", ""), cmd


# ------------------------------------------------------------------ reviewer A round 3 (2026-10-06): DENY path
def test_b3_14_a_quoted_archive_operand_behind_long_heredoc_data_still_denies(ws):
    c = "cat > notes.md <<'EOF'\n" + "prose " * 1600 + '\nEOF\nmv "' + ARC_TOKEN + '/a;b;c.md" /tmp/c.md'
    assert guard(ws, c)[0] == "deny"


@pytest.mark.parametrize("tmpl", ["cp /tmp/r.md {L} 2>/dev/null", "env -u PYTHONPATH cp /tmp/r.md {L}",
                                  "nice -n 10 cp /tmp/r.md {L}", "cat /tmp/r.md >& {L}", "sort -o {L} /tmp/r.md",
                                  "printf '%s\\n' 'Use cat <<EOF to write a file'\ncp /tmp/r.md {L}",
                                  "# Example: cat <<EOF\ncp /tmp/r.md {L}"])
def test_b3_15_16_l0_writes_deny(ws, tmpl):
    d, why = guard(ws, tmpl.format(L=L0))
    assert d == "deny" and "L0" in why, (tmpl, d, why)


@pytest.mark.parametrize("tmpl", ["cat {L}", "pandoc {L} -o /tmp/out.pdf", "cp {L} /tmp/x.md", "grep -n Art {L}",
                                  "sort {L}", "wc -l {L}", "cat > n.md <<'EOF'\ncp x {L}\nEOF"])
def test_b3_15_l0_reads_stay_allowed(ws, tmpl):
    assert guard(ws, tmpl.format(L=L0))[0] == "allow", tmpl


def test_b3_14_long_executable_text_with_a_cut_quoted_operand_asks(ws):
    # > 8 KB of EXECUTABLE text (no heredoc): the quote-aware window cannot see the whole operand -> ask, not allow
    c = "echo " + "x " * 5000 + '; mv "' + ARC_TOKEN + '/a;b;c.md" /tmp/c.md'
    assert guard(ws, c)[0] in ("ask", "deny")


# ------------------------------------------------------------------ reviewer A round 3 (2026-10-06): loop bound accounting
def test_b3_19_a_seventh_run_in_the_same_call_is_not_an_approval(ws):
    t = T().prompt().tool("Bash", {"command": "set +e; " + "; ".join(["pytest -q"] * 7)},
                          "1 failed in 0.1s\n" * 7, True)
    assert lb(ws, t)[0] == "ask"


def test_b3_20_hidden_runs_count_per_verifier(ws):
    t = T().prompt().tool("Bash", {"command": "; ".join(["pytest -q >/tmp/test.log || true"] * 6)}, "", False)
    d, why = lb(ws, t)
    assert d == "ask" and "could not be seen" in why, why


def test_b3_21_batched_red_fix_green_is_not_counted_all_red(ws):
    t = T().prompt()
    for _ in range(3):
        t.edit().tool("Bash", {"command": "pytest -q; cp /tmp/fix.py src/app.py; pytest -q"},
                      "1 failed in 0.1s\n1 passed in 0.1s", False)
    assert lb(ws, t) == ("allow", "")


def test_b3_22_collection_only_inside_bash_c_does_not_reset(ws):
    t = T().prompt()
    for _ in range(3):
        t.edit().fail()
        t.tool("Bash", {"command": "bash -lc 'pytest --override-ini=addopts=--collect-only -q'"},
               "tests/test_api.py::test_x\n\n1 test collected in 0.01s", False)
    t.edit().fail()
    assert lb(ws, t)[0] == "ask"


def test_b3_23_a_denied_write_is_not_a_fix(ws):
    t = T().prompt().fail()
    for _ in range(3):
        t.n += 1
        tid = f"t{t.n}"
        t.lines.append({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": tid, "name": "Bash", "input": {"command": "cp /tmp/fix.py src/app.py && pytest -q"}}]}})
        t.lines.append({"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": tid, "is_error": True, "content": "User denied"}]}})
        t.fail()
    assert lb(ws, t) == ("allow", "")


# ------------------------------------------------------------------ reviewer A round 3 (2026-10-06): H4a misses and false asks
@pytest.mark.parametrize("cmd", [
    "env -C.claude cp /tmp/x hooks/pretool_guard.py",                                        # B3-25
    "sudo -D.claude cp /tmp/x hooks/pretool_guard.py",
    "rsync --backup --backup-dir={ws}/.claude/hooks /tmp/replacement.py /tmp/pretool_guard.py",  # B3-26
    "rsync -b --backup-dir {ws}/.claude/hooks /tmp/replacement.py /tmp/pretool_guard.py",
    "rustfmt --emit files .claude/hooks/helper.rs",                                          # B3-27
    "rustfmt --emit=files .claude/hooks/helper.rs",
])
def test_b3_25_27_governor_writes_ask(ws, cmd):
    d, why = guard(ws, cmd.replace("{ws}", str(ws)))
    assert d == "ask" and "GOVERNOR" in why, (cmd, d, why)


@pytest.mark.parametrize("cmd", [
    "black --exclude .claude/hooks/ src/app.py",                                             # B3-32
    "find .claude/hooks -type f -exec black --check {} +",
    "git restore --staged -- .",                                                             # B3-33
    "git rm --cached -- .claude/hooks/pretool_guard.py",
    "git config --file adapters/claude-code/.gitconfig --get core.hooksPath",
    "xattr -lr .claude/hooks/pretool_guard.py",                                              # B3-34
    "rustfmt --emit stdout .claude/hooks/helper.rs",
])
def test_b3_32_34_no_false_governor_ask(ws, cmd):
    subprocess.run(["git", "-C", str(ws), "init", "-q"], check=True)
    assert guard(ws, cmd) == ("allow", ""), cmd


def test_b3_27_formatter_tables_still_agree_on_emit():
    g, v = _load("pretool_guard"), _load("verifier_recognizer")
    for args in (["--emit", "files", "x.rs"], ["--emit=files", "x.rs"], ["--emit", "stdout", "x.rs"], ["--check", "x.rs"]):
        assert g._formatter_writes("rustfmt", args) == v.formatter_writes("rustfmt", args), args
    assert v.formatter_writes("rustfmt", ["--emit", "files", "x.rs"]) is True


def test_b3_19_exact_input_one_summary_for_seven_failing_runs(ws):
    t = T().prompt().tool("Bash", {"command": "set +e; " + "; ".join(["pytest -q"] * 7)}, "1 failed in 0.1s", True)
    assert lb(ws, t)[0] == "ask"


# ------------------------------------------------------------------ reviewer A round 4 (2026-10-06): DENY path
def test_b4_13_a_file_named_l0_copied_into_its_directory_denies(ws):
    (ws / L0).write_text("# L0\n")
    for c in (f"cp /tmp/{L0} .", f"install -m 644 /tmp/{L0} .", f"cp /tmp/{L0} {ws}/"):
        assert guard(ws, c)[0] == "deny", c
    assert guard(ws, f"cp {L0} /tmp/") == ("allow", "")


def test_b4_14_git_checkout_ref_path_without_separator_denies_l0(ws):
    for c in (f"git checkout HEAD~1 {L0}", f"git checkout main {L0}"):
        assert guard(ws, c)[0] == "deny", c


@pytest.mark.parametrize("pre", ["cat > notes.sql <<SQL-END\nselect 1;\nSQL-END\n", "echo $((1<<3))\n",
                                 "dd if=/dev/zero bs=$((1<<20)) count=1 of=/tmp/blank\n"])
def test_b4_15_heredoc_lookalikes_do_not_hide_an_l0_write(ws, pre):
    assert guard(ws, pre + f"cp /tmp/x {L0}")[0] == "deny"


@pytest.mark.parametrize("sh", [f"rm -rf ~/Dev/NEOCORTEX/" + ARC_TOKEN, f"mv ~/Dev/NEOCORTEX/{ARC_TOKEN}/2026-01.md /tmp/",
                                f"cp /tmp/draft.md ~/Dev/{L0}"])
def test_b4_16_deny_checks_run_on_the_decoded_do_shell_script(ws, sh):
    raw = json.dumps({"tool_name": "mcp__Control_your_Mac__osascript",
                      "tool_input": {"script": 'do shell script "' + sh + '"'}, "cwd": str(ws)})
    assert guard(ws, raw=raw)[0] == "deny", sh


@pytest.mark.parametrize("c", [f"/bin/rm -rf NEOCORTEX/{ARC_TOKEN}", f"\\rm -rf NEOCORTEX/{ARC_TOKEN}",
                               f"(rm -rf NEOCORTEX/{ARC_TOKEN})", f"cd NEOCORTEX && /bin/rm -rf {ARC_TOKEN}",
                               f"/bin/mv NEOCORTEX/{ARC_TOKEN}/2026-01.md /tmp/",
                               f"rsync -a --remove-source-files NEOCORTEX/{ARC_TOKEN}/ /tmp/old/",
                               f"find NEOCORTEX/{ARC_TOKEN} -type f -exec mv {{}} /tmp/ \\;",
                               f"rm -rf NEOCORTEX/_Archive"])
def test_b4_17_archive_deletion_spellings_deny(ws, c):
    assert guard(ws, c)[0] == "deny", c


def test_b4_33_index_only_git_rm_of_an_archive_is_not_denied(ws):
    assert guard(ws, f"git rm -r --cached NEOCORTEX/{ARC_TOKEN}")[0] != "deny"
    assert guard(ws, f"git rm -r NEOCORTEX/{ARC_TOKEN}")[0] == "deny"


# ------------------------------------------------------------------ reviewer A round 4: H4a / H4c-adjacent
@pytest.mark.parametrize("cmd", [
    "python3 -O .claude/hooks/hook_support.py --write-approved",                         # B4-36
    "python3 -OO .claude/hooks/hook_support.py --write-approved",
    "black .", "ruff format .", "prettier --write .", "black .claude",                   # B4-25 (at the workspace root)
    "cd .claude/hooks && curl -sSLO https://example.com/pretool_guard.py",               # B4-27
    "cd .claude/hooks && wget https://example.com/pretool_guard.py",
])
def test_b4_h4a_writes_ask(ws, cmd):
    d, why = guard(ws, cmd)
    assert d == "ask" and "GOVERNOR" in why, (cmd, d, why)


def test_b4_26_patch_targets_come_from_the_diff_headers(ws):
    diff = ws.parent / "fix.diff"
    diff.write_text("--- a/.claude/hooks/pretool_guard.py\n+++ b/.claude/hooks/pretool_guard.py\n@@ -1 +1 @@\n-x\n+y\n")
    for c in (f"patch -p1 < {diff}", f"patch -p1 -i {diff}"):
        d, why = guard(ws, c)
        assert d == "ask" and "GOVERNOR" in why, (c, d, why)
    other = ws.parent / "other.diff"
    other.write_text("--- a/src/app.py\n+++ b/src/app.py\n@@ -1 +1 @@\n-x\n+y\n")
    assert guard(ws, f"patch -p1 < {other}") == ("allow", "")


@pytest.mark.parametrize("cmd", ["awk '{print $2}' governance/hooks_approved.sha256",                   # B4-30
                                 "sed -n 1,5p governance/hooks_approved.sha256", "sort -k2 governance/hooks_approved.sha256",
                                 "cut -c1-12 governance/hooks_approved.sha256", "column -t governance/hooks_approved.sha256",
                                 "cp governance/hooks_approved.sha256 /tmp/approved.bak",
                                 "rm -rf .claude/hooks/__pycache__", "rm -rf .claude/hooks/.pytest_cache"])   # B4-32
def test_b4_30_32_reads_and_cache_cleanups_stay_silent(ws, cmd):
    assert guard(ws, cmd) == ("allow", ""), cmd


@pytest.mark.parametrize("tool,inp", [("Write", {"file_path": "{ws}/governance/hooks_approved.sha256", "content": "x"}),
                                      ("Edit", {"file_path": "{ws}/governance/hooks_approved.sha256", "old_string": "a", "new_string": "b"}),
                                      ("MultiEdit", {"file_path": "{ws}/.claude/hooks/pretool_guard.py", "edits": []}),
                                      ("Write", {"file_path": "{ws}/.claude/settings.json", "content": "{}"})])
def test_b4_08_edit_and_write_tools_on_the_governor_ask(ws, tool, inp):
    inp = {k: (v.replace("{ws}", str(ws)) if isinstance(v, str) else v) for k, v in inp.items()}
    d, why = guard(ws, raw=json.dumps({"tool_name": tool, "tool_input": inp, "cwd": str(ws)}))
    assert d == "ask" and "GOVERNOR" in why, (tool, d, why)


def test_b4_08_edit_tool_elsewhere_stays_silent(ws):
    raw = json.dumps({"tool_name": "Write", "tool_input": {"file_path": str(ws / "proj" / "a.py"), "content": "x"}, "cwd": str(ws)})
    assert guard(ws, raw=raw) == ("allow", "")


@_NEEDS_LAUNCHER
def test_launcher_alarm_is_below_the_registered_hook_timeout():
    import re as _re
    limit = int(_re.search(r"^LIMIT=(\d+)", (SRC / "pretool_guard.sh").read_text(), _re.M).group(1))
    settings = pathlib.Path("$WORKSPACE/.claude/settings.json")
    registered = 10
    if settings.is_file():
        cfg = json.loads(settings.read_text())
        for entry in cfg.get("hooks", {}).get("PreToolUse", []):
            for h in entry.get("hooks", []):
                if "pretool_guard" in h.get("command", ""):
                    registered = int(h.get("timeout", 60))
    assert limit < registered, (limit, registered)


# ------------------------------------------------------------------ reviewer A round 4 (2026-10-06): loop bound
def test_b4_20_a_syntax_check_green_does_not_reset_a_test_failure(ws):
    t = T().prompt()
    for _ in range(4):
        t.edit()
        t.tool("Bash", {"command": "python3 -m py_compile src/app.py"}, "", False)
        t.fail("FAILED tests/test_app.py::test_total - assert 41 == 42\n1 failed, 7 passed in 0.31s")
    assert lb(ws, t)[0] == "ask"


def test_b4_21_a_last_run_that_only_collected_is_not_a_pass(ws):
    # 4 edit/batch cycles reach 3 same-signature cycles; a 5th call would only run after an approval (which resets)
    t = T().prompt()
    for _ in range(4):
        t.edit().tool("Bash", {"command": "pytest -q; pytest --override-ini=addopts=--collect-only -q"},
                      "1 failed in 0.1s\ntests/test_api.py::test_x\n\n1 test collected in 0.01s", False)
    assert lb(ws, t)[0] == "ask"


def test_b4_37_a_same_call_green_clears_a_reached_threshold(ws):
    t = T().prompt()
    for _ in range(5):
        t.fail()
    t.edit().tool("Bash", {"command": "pytest -q; cp /tmp/fix.py src/app.py; pytest -q"}, "1 failed in 0.1s\n1 passed in 0.1s", False)
    assert lb(ws, t) == ("allow", "")


def test_b4_22_an_ask_for_another_reason_carries_the_loop_bound(ws):
    t = T().prompt()
    for _ in range(6):
        t.fail()
    d, why = lb(ws, t, cmd="cp /tmp/fix.py .claude/hooks/loop_bound.py && pytest -q")
    assert d == "ask" and "GOVERNOR" in why and "Art. 14" in why, why


def test_copying_l0_out_into_a_fresh_package_dir_is_allowed(ws):
    # reported 2026-10-06 by the Coding Constitution session: the LIVE guard denied a copy of L0 OUT (a read)
    (ws / L0).write_text("# L0\n")
    pkg = ws.parent / "seat package dir"
    pkg.mkdir()
    c = f'cp /tmp/a.txt "{pkg}/" && cp /tmp/b.md "{pkg}/" && cp {ws}/{L0} "{pkg}/"'
    assert guard(ws, c)[0] == "allow", c
    assert guard(ws, f"cp {ws}/{L0} {ws.parent}/")[0] == "allow"


def test_b4_29_git_pull_and_abort_are_answered_by_git_not_always_asked(ws):
    g = ["git", "-C", str(ws)]
    ident = ["-c", "user.email=fixture-identity", "-c", "user.name=t"]
    subprocess.run(g + ["init", "-q", "-b", "main"], check=True)
    subprocess.run(g + ["add", "-A"], check=True)
    subprocess.run(g + ident + ["commit", "-qm", "base"], check=True)
    remote = ws.parent / "remote.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(ws), str(remote)], check=True)
    subprocess.run(g + ["remote", "add", "origin", str(remote)], check=True)
    subprocess.run(g + ["fetch", "-q", "origin"], check=True)
    subprocess.run(g + ["branch", "-q", "-u", "origin/main"], check=True)
    for c in ("git merge --abort", "git rebase --continue"):
        assert guard(ws, c) == ("allow", ""), c
    # B5-13 (reviewer A round 5, expected flip): the local @{upstream} is the last FETCHED state, so a pull asks
    for c in ("git pull", "git pull --rebase", "git pull origin main"):
        assert guard(ws, c)[0] == "ask", c


# L0 fallback precision (Coding Constitution session, conformance s1e, 2026-10-06): when the tokenizer cannot parse
# valid shell (quotes nested in "$(...)"), the fallback must still tell a READ of L0 from a write.
@pytest.mark.parametrize("tail", ['; for p in a; do printf "%s\\n" "$(grep -c "$p" /x)"; done'])
def test_l0_fallback_allows_reads_of_l0(ws, tail):
    for head in (f"cp {ws}/{L0} /tmp/pkg/", f"sed -n 1,5p {L0}", f"dd if={L0} of=/tmp/x bs=1k", f"grep -c Art {L0}"):
        assert guard(ws, head + tail)[0] == "allow", head


@pytest.mark.parametrize("head", ["cp /tmp/x {L}", 'cp /tmp/x "{L}"', "sed -i '' 's/a/b/' {L}", "dd if=/tmp/x of={L}",
                                  "echo x > {L}", "tee {L} < /tmp/x"])
def test_l0_fallback_still_denies_writes(ws, head):
    c = head.format(L=L0) + '; for p in a; do printf "%s\\n" "$(grep -c "$p" /x)"; done'
    assert guard(ws, c)[0] == "deny", c


# ================================================================== reviewer A round 5 (2026-10-06, on the LANDED v0.9)
# B5-05 (regression vs pre-landing): the heredoc delimiter is the shell word AFTER quote removal; `<<\EOF`,
# `<<-\EOF` and `<<E"OF"` end at a line `EOF`, so the commands after it are executable text.
@pytest.mark.parametrize("op", ["<<\\EOF", "<<-\\EOF", '<<E"OF"', "<<'EOF'", "<< \\EOF"])
def test_b5_05_quoted_heredoc_delimiters_do_not_hide_later_commands(ws, op):
    end = "\tEOF" if op.startswith("<<-") else "EOF"
    assert guard(ws, f"cat > /tmp/notes.txt {op}\nhello\n{end}\ncp /tmp/x {L0}")[0] == "deny", op
    assert guard(ws, f"cat > /tmp/n {op}\nx\n{end}\n/bin/rm -rf NEOCORTEX/{ARC_TOKEN}")[0] == "deny", op
    d, why = guard(ws, f"cat > /tmp/n {op}\nx\n{end}\ncp /tmp/x .claude/hooks/pretool_guard.py")
    assert d == "ask" and "GOVERNOR WRITE" in why, (op, d, why)


def test_b5_05_quoted_heredoc_body_stays_data_when_nothing_follows(ws):
    assert guard(ws, "cat > /tmp/n <<\\EOF\nrm -rf /tmp/old\nEOF\necho done") == ("allow", "")


# B5-08 (regression vs pre-landing): an unparseable logging tail must not hide an L0 copy that is followed by a
# redirection or a trailing option.
@pytest.mark.parametrize("head", ["cp /tmp/draft.md {L} 2>/dev/null", "cp -v /tmp/x {L} >/dev/null",
                                  "install -m 644 /tmp/x {L} -v", "mv /tmp/x {L} 2>&1"])
def test_b5_08_l0_copy_with_trailing_redirect_and_unparseable_tail_denies(ws, head):
    c = head.format(L=L0) + ' && echo "copied at $(date "+%H:%M")"'
    assert guard(ws, c)[0] == "deny", c


def test_b5_08_copy_of_l0_out_with_trailing_redirect_stays_allowed(ws):
    assert guard(ws, f'cp {L0} /tmp/pkg/ 2>/dev/null && echo "at $(date "+%H")"')[0] == "allow"


# B5-09: short-option clusters holding `i` are in-place edits for sed (`-Ei`, `-ni`), like perl/ruby already were.
@pytest.mark.parametrize("c", ["sed -Ei '' 's/old/new/' {L}", "sed -Ei 's/old/new/' {L}", "sed -ni 's/old/new/p' {L}"])
def test_b5_09_sed_option_clusters_deny_l0(ws, c):
    assert guard(ws, c.format(L=L0))[0] == "deny", c


def test_b5_09_sed_clusters_ask_on_the_governor_and_reads_stay_silent(ws):
    d, why = guard(ws, "sed -Ei '' 's/a/b/' .claude/hooks/pretool_guard.py")
    assert d == "ask" and "GOVERNOR WRITE" in why, why
    assert guard(ws, f"sed -E 's/a/b/' {L0}") == ("allow", "")
    assert guard(ws, "sed -n 1,5p governance/hooks_approved.sha256") == ("allow", "")


# B5-06: the approval list is rewritten by the module/runpy spellings and by clustered sed: all ask.
@pytest.mark.parametrize("c", ["cd .claude/hooks && python3 -m hook_support --write-approved",
                               "PYTHONPATH=.claude/hooks python3 -m hook_support --write-approved",
                               "python3 -m runpy .claude/hooks/hook_support.py --write-approved",
                               "sed -Ei '' 's/^0/1/' governance/hooks_approved.sha256"])
def test_b5_06_approval_list_rewrites_ask(ws, c):
    d, why = guard(ws, c)
    assert d == "ask" and "GOVERNOR" in why, (c, d, why)


# B5-16: `git checkout L0` (no tree-ish) and `--ours L0` restore L0 from the index.
@pytest.mark.parametrize("c", ["git checkout {L}", "git checkout --ours {L}", "git checkout -f {L}"])
def test_b5_16_checkout_without_tree_ish_denies_l0(ws, c):
    assert guard(ws, c.format(L=L0))[0] == "deny", c


# B5-10: `git apply` / `git am` of a diff whose headers name L0.
@pytest.mark.parametrize("verb", ["git apply", "git apply --3way", "git am"])
def test_b5_10_git_apply_of_an_l0_diff_denies(ws, verb):
    d = ws.parent / "l0.diff"
    d.write_text(f"diff --git a/{L0} b/{L0}\n--- a/{L0}\n+++ b/{L0}\n@@ -1 +1 @@\n-a\n+b\n")
    assert guard(ws, f"{verb} {d}")[0] == "deny", verb
    o = ws.parent / "other.diff"
    o.write_text("--- a/src/app.py\n+++ b/src/app.py\n@@ -1 +1 @@\n-a\n+b\n")
    assert guard(ws, f"{verb} {o}")[0] != "deny", verb


# B5-07: archive removal through git's cwd forms, archivers that delete their input, and Finder.
@pytest.mark.parametrize("c", ["cd NEOCORTEX/{A} && git rm -r .", "git -C NEOCORTEX/{A} rm -r .",
                               "cd NEOCORTEX/{A} && git mv old.md ../old.md", "zip -rm /tmp/old.zip NEOCORTEX/{A}",
                               "zip -r -m /tmp/old.zip NEOCORTEX/{A}", "tar --remove-files -czf /tmp/a.tgz NEOCORTEX/{A}",
                               "cd NEOCORTEX/{A} && git clean -fdx"])
def test_b5_07_archive_removal_spellings_deny(ws, c):
    assert guard(ws, c.format(A=ARC_TOKEN))[0] == "deny", c


@pytest.mark.parametrize("script", ['tell application "Finder" to delete POSIX file "/srv/example/Dev/NEOCORTEX/{A}"',
                                    'tell application "Finder" to move POSIX file "/srv/example/Dev/NEOCORTEX/{A}" to trash'])
def test_b5_07_finder_delete_of_an_archive_denies(ws, script):
    raw = json.dumps({"tool_name": "mcp__Control_your_Mac__osascript",
                      "tool_input": {"script": script.format(A=ARC_TOKEN)}, "cwd": str(ws)})
    assert guard(ws, raw=raw)[0] == "deny", script


def test_b5_07_archive_reads_and_index_only_forms_stay_allowed(ws):
    for c in (f"git -C NEOCORTEX/{ARC_TOKEN} log --oneline", f"zip -r /tmp/old.zip NEOCORTEX/{ARC_TOKEN}",
              f"tar -czf /tmp/a.tgz NEOCORTEX/{ARC_TOKEN}", f"git rm --cached -r NEOCORTEX/{ARC_TOKEN}"):
        assert guard(ws, c)[0] != "deny", c


# B5-36: removing or moving out a directory that CONTAINS an archive.
@pytest.mark.parametrize("c", ["rm -rf NEOCORTEX", "rm -rf ./NEOCORTEX/", "trash NEOCORTEX", "mv NEOCORTEX /tmp/old_neo",
                               "rm -rf proj/NEOCORTEX", "cd proj && rm -rf *", "find proj/NEOCORTEX -delete"])
def test_b5_36_removing_an_archive_ancestor_denies(ws, c):
    (ws / "proj" / "NEOCORTEX" / ARC_TOKEN).mkdir(parents=True)
    assert guard(ws, c)[0] == "deny", c


# Replay 16: an archive found only DEEPER (a project root, a review clone, a staging copy) is an approvable ASK.
@pytest.mark.parametrize("c", ["rm -rf proj", "find proj -delete", "rm -rf seat_clone"])
def test_b5_36_a_deeper_archive_asks(ws, c):
    (ws / "proj" / "NEOCORTEX" / ARC_TOKEN).mkdir(parents=True)
    (ws / "seat_clone" / "governance" / "NEOCORTEX" / ARC_TOKEN).mkdir(parents=True)
    d, why = guard(ws, c)
    assert d == "ask" and "ARCHIVE ANCESTOR" in why, (c, d, why)


def test_b5_36_removing_a_directory_without_an_archive_stays_allowed(ws):
    (ws / "build" / "out").mkdir(parents=True)
    assert guard(ws, "rm -rf build") == ("allow", "")


# B5-17: a literal `do shell script` that a DENY rule matches is denied even when another one is unresolvable.
def test_b5_17_decoded_deny_wins_over_the_unresolved_ask(ws):
    script = f'set p to "x"\ndo shell script p\ndo shell script "rm -rf ~/Dev/NEOCORTEX/{ARC_TOKEN}"'
    raw = json.dumps({"tool_name": "mcp__Control_your_Mac__osascript", "tool_input": {"script": script}, "cwd": str(ws)})
    assert guard(ws, raw=raw)[0] == "deny"


# B5-32 / B5-33: the extraction window looks at the EXECUTED job: runner prefixes do not hide it, and reads of
# a file or a search for the words never trigger it.
@pytest.mark.parametrize("c,want", [("uv run python3 embed_corpus.py", "deny"), ("poetry run python3 embed_corpus.py", "deny"),
                                    ("uv run ingest_all.py", "deny"), ("npx tsx embed_corpus.ts", "deny"),
                                    ("rg -n chromadb pyproject.toml", "allow"), ("rg -n vectorization src", "allow"),
                                    ("cat docs/vectorization_plan.md", "allow"), ("python3 embed_corpus.py", "deny")])
def test_b5_32_33_extraction_window_follows_the_executed_job(ws, c, want):
    (ws / "governance" / "machine_config.yaml").write_text('forbidden_windows:\n  - window: "00:00-23:59"\n')
    assert guard(ws, c)[0] == want, c


# B5-15: patch input forms: attached `-iFILE`, a pipe, an inline heredoc (in a repository holding the governor), `-d`.
def _hook_diff(path, prefix="a/.claude/hooks/"):
    path.write_text(f"--- {prefix}pretool_guard.py\n+++ {prefix.replace('a/', 'b/', 1)}pretool_guard.py\n@@ -1 +1 @@\n-a\n+b\n")
    return path


def test_b5_15_patch_input_forms_ask(ws):
    (ws / ".git").mkdir()
    d = _hook_diff(ws.parent / "fix.diff")
    for c in (f"patch -p1 -i{d}", f"cat {d} | patch -p1",
              "patch -p1 <<'EOF'\n--- a/.claude/hooks/pretool_guard.py\n+++ b/.claude/hooks/pretool_guard.py\nEOF"):
        dd, why = guard(ws, c)
        assert dd == "ask" and "GOVERNOR" in why, (c, dd, why)
    d2 = _hook_diff(ws.parent / "fix2.diff", "a/x/hooks/")
    dd, why = guard(ws, f"patch -d .claude -p2 < {d2}")
    assert dd == "ask" and "GOVERNOR" in why, (dd, why)


def test_b5_15_piped_patch_outside_a_governor_repository_stays_silent(ws):
    other = ws.parent / "other_repo"
    (other / ".git").mkdir(parents=True)
    raw = json.dumps({"tool_name": "Bash", "tool_input": {"command": "cat /tmp/x.diff | patch -p1"}, "cwd": str(other)})
    assert guard(ws, raw=raw) == ("allow", "")


# B5-14 / B5-35: formatters behind npx/uv/poetry/hatch, option clusters and ruff's default operand reach the governor.
@pytest.mark.parametrize("c", ["npx prettier --write .", "autopep8 -ri .", "npx prettier --write .claude/settings.json",
                               "npx --yes prettier --write .claude/settings.json", "autopep8 -ri .claude/hooks",
                               "ruff format", "ruff check --fix", "uv run ruff format .", "uv run black .",
                               "poetry run black .", "uv run ruff check --fix .", "hatch run black ."])
def test_b5_14_35_formatter_spellings_ask(ws, c):
    dd, why = guard(ws, c)
    assert dd == "ask" and "GOVERNOR" in why, (c, dd, why)


@pytest.mark.parametrize("c", ["ruff check", "ruff format --check", "uv run black --check .", "npx prettier --check .",
                               "uv run pytest .claude/hooks/test_verifier_recognizer.py -q",          # B5-34
                               "poetry run pytest .claude/hooks/test_verifier_recognizer.py -q"])
def test_b5_14_34_check_modes_and_runner_tests_stay_silent(ws, c):
    assert guard(ws, c) == ("allow", ""), c


# B5-23: cache and Finder-metadata cleanup by name never reaches the governor.
def test_b5_23_find_cleanup_of_caches_stays_silent(ws):
    (ws / ".claude" / "hooks" / "__pycache__").mkdir()
    (ws / ".claude" / "hooks" / ".DS_Store").write_text("")
    for c in ("find . -name __pycache__ -type d -exec rm -rf {} +", "find . -name .DS_Store -delete"):
        assert guard(ws, c) == ("allow", ""), c


# B5-12 / B5-22 (loop bound, pure): the verifier's identity decides which failures a pass ends; a batch's final
# unittest `OK` / TAP `# fail 0` is its last outcome.
def _lb_pure():
    import importlib.util
    for name in ("verifier_recognizer", "loop_bound"):
        spec = importlib.util.spec_from_file_location(name, SRC / f"{name}.py")
        m = importlib.util.module_from_spec(spec)
        sys.modules[name] = m
        spec.loader.exec_module(m)
    return sys.modules["loop_bound"], sys.modules["verifier_recognizer"]


def _ev(lb, vr, cmd, out, err=False):
    return lb.run_events(cmd, vr.analyse(cmd), err, ("Exit code 1\n" + out) if err else out)


def test_b5_12_an_unrelated_green_does_not_end_a_failure_episode():
    lb, vr = _lb_pure()
    red = lambda: _ev(lb, vr, "pytest -q tests/test_app.py", "1 failed in 0.1s", True)
    other = lambda: _ev(lb, vr, "pytest -q tests/test_util.py", "3 passed in 0.1s")
    subset = lambda: _ev(lb, vr, "pytest -q -k test_other", "3 passed in 0.1s")
    node = lambda: _ev(lb, vr, "node --test", "# pass 3\n# fail 0\n")
    for green in (other, subset, node):
        ev, asked = [], []
        for _ in range(5):                         # the guard asks BEFORE the next run, so look after each red
            ev += [("edit",)] + red()
            asked.append(bool(lb.evaluate(ev)))
            ev += green()
        assert any(asked), green


def test_b5_12_the_same_or_a_whole_suite_green_still_ends_it():
    lb, vr = _lb_pure()
    red = lambda: _ev(lb, vr, "pytest -q tests/test_app.py", "1 failed in 0.1s", True)
    for green in (lambda: _ev(lb, vr, "pytest -q tests/test_app.py", "1 passed in 0.1s"),
                  lambda: _ev(lb, vr, "pytest -q", "9 passed in 0.3s")):
        ev, asked = [], []
        for _ in range(5):
            ev += [("edit",)] + red()
            asked.append(bool(lb.evaluate(ev)))
            ev += green()
        assert not any(asked)


@pytest.mark.parametrize("cmd,out", [
    ("python3 -m unittest tests.test_app; cp /tmp/fix.py src/app.py; python3 -m unittest tests.test_app",
     "Ran 1 test in 0.001s\n\nFAILED (failures=1)\nRan 1 test in 0.001s\n\nOK\n"),
    ("node --test; cp /tmp/fix.js src/app.js; node --test", "# pass 0\n# fail 1\n# pass 1\n# fail 0\n"),
])
def test_b5_22_a_batch_ending_green_is_a_pass(cmd, out):
    lb, vr = _lb_pure()
    F = _ev(lb, vr, "python3 -m unittest tests.test_app", "Ran 1 test in 0.001s\n\nFAILED (failures=1)", True)
    ev = F * 5 + [("edit",)] + _ev(lb, vr, cmd, out)
    assert ("run", True) in [e[:2] for e in ev]
    assert lb.evaluate(ev + [("edit",)]) is None


# B5-19: the loop-bound analysis runs within the hook's time budget; past it the H9 reason is skipped, never the ask.
def test_b5_19_the_loop_bound_check_has_a_deadline():
    import importlib.util
    import time
    spec = importlib.util.spec_from_file_location("_g_b519", SRC / "pretool_guard.py")
    g = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(g)

    class SlowLB:
        @staticmethod
        def check(data, wt):
            time.sleep(10)
            return "never"
    g._H9_BUDGET = time.monotonic() - g._T_START + 0.3
    t0 = time.monotonic()
    with pytest.raises(g._H9Timeout):
        g._h9_check(SlowLB, {})
    assert time.monotonic() - t0 < 2


# Replay 15 (2026-10-06): an EMPTY operand (`rm -f ""`) is not the current directory; `rm -rf .` at a root holding an
# archive still denies.
def test_b5_36_an_empty_or_unresolved_operand_is_not_an_archive_ancestor(ws):
    (ws / "proj" / "NEOCORTEX" / ARC_TOKEN).mkdir(parents=True)
    for c in ('rm -f ""', "rm -f ''", 'rm -f "$UNSET_X"', 'rm -rf "${D}"'):
        assert guard(ws, c)[0] != "deny", c
    assert guard(ws, "rm -rf .")[0] == "ask"                 # the archive is deeper here: approvable
    (ws / ARC_TOKEN).mkdir()
    assert guard(ws, "rm -rf .")[0] == "deny"                # a root that DIRECTLY holds an archive (as the workspace)


# Replay 15: `python3 -m pytest` is the whole suite, not a `-m` marker selection (B5-12 identity).
def test_b5_12_python_dash_m_runner_is_not_a_selection():
    lb, vr = _lb_pure()
    red = lambda: _ev(lb, vr, "python3 -m pytest tests/test_app.py -q", "1 failed in 0.1s", True)
    green = lambda: _ev(lb, vr, "python3 -m pytest -q", "9 passed in 0.3s")
    ev, asked = [], []
    for _ in range(5):
        ev += [("edit",)] + red()
        asked.append(bool(lb.evaluate(ev)))
        ev += green()
    assert not any(asked)
    assert _ev(lb, vr, "pytest -q -m slow", "2 passed in 0.1s")[-1][4][2] == ("slow",)


# Founder ruling 2026-10-06 (B5-12, COVER_MODE "middle"): a whole-suite red then the failing file green ends the episode.
def test_b5_12_middle_a_suite_red_then_a_targeted_green_ends_it():
    lb, vr = _lb_pure()
    assert lb.COVER_MODE == "middle"
    # G6-03 (expected flip): the red run must NAME the failing file for a targeted green to end it
    red = lambda: _ev(lb, vr, "pytest -q", "FAILED tests/test_app.py::test_x - AssertionError\n1 failed, 8 passed in 0.3s", True)
    green = lambda: _ev(lb, vr, "pytest -q tests/test_app.py", "3 passed in 0.1s")
    ev, asked = [], []
    for _ in range(5):
        ev += [("edit",)] + red()
        asked.append(bool(lb.evaluate(ev)))
        ev += green()
    assert not any(asked)


# Replay 17: `rm` WITHOUT -r cannot remove a directory, so a glob matching the archive directory is no archive removal;
# `rm -r` with the same glob still denies.
def test_b5_36_non_recursive_rm_of_a_glob_never_removes_the_archive_dir(ws):
    neo = ws / "proj" / "NEOCORTEX"
    (neo / ARC_TOKEN).mkdir(parents=True)
    (neo / "old.tmp").write_text("x")
    for c in ("cd proj/NEOCORTEX && rm -f *", "cd proj/NEOCORTEX && rm *.tmp", "rm -f proj/NEOCORTEX/*"):
        assert guard(ws, c)[0] != "deny", c
    for c in ("cd proj/NEOCORTEX && rm -rf *", "rm -r proj/NEOCORTEX/*", "rm -fR proj/NEOCORTEX/*"):
        assert guard(ws, c)[0] == "deny", c


# ================================================================== Round 6 (Gemini Pro, 2026-10-06, on the LIVE v0.10)
# G6-01: a runner's GLOBAL options before its subcommand (`uv -q run`, `poetry -C proj run`, `npm --prefix web exec`)
# do not hide the child job (extraction window) nor a formatter (governor ask).
@pytest.mark.parametrize("c", ["uv -q run ingest_all.py", "uv --quiet run python3 embed_corpus.py",
                               "poetry -C proj run python3 embed_corpus.py", "npm --prefix web exec tsx embed_corpus.ts",
                               "uv --directory proj run python3 embed_corpus.py"])
def test_g6_01_runner_global_options_do_not_hide_the_job(ws, c):
    (ws / "governance" / "machine_config.yaml").write_text('forbidden_windows:\n  - window: "00:00-23:59"\n')
    assert guard(ws, c)[0] == "deny", c


@pytest.mark.parametrize("c", ["uv -q run black .", "poetry --quiet run black ."])
def test_g6_01_runner_global_options_do_not_hide_a_formatter(ws, c):
    d, why = guard(ws, c)
    assert d == "ask" and "GOVERNOR" in why, (c, d, why)


# G6-02: the tracked `cd` / `git -C` text is normalised: leaving an archive with `..` is not being inside it.
def test_g6_02_leaving_an_archive_with_dotdot_is_not_inside_it(ws):
    for c in (f"cd NEOCORTEX/{ARC_TOKEN} && cd .. && rm -rf old", f"git -C NEOCORTEX/{ARC_TOKEN}/.. rm -r old",
              f"cd NEOCORTEX/{ARC_TOKEN}/.. && rm -rf old"):
        assert guard(ws, c)[0] != "deny", c
    assert guard(ws, f"cd NEOCORTEX/{ARC_TOKEN} && rm -rf old")[0] == "deny"
    assert guard(ws, f"cd NEOCORTEX && cd {ARC_TOKEN} && rm -rf old")[0] == "deny"


# G6-03 (founder ruling 2026-10-06 "Fix all, refine middle"): a targeted green ends a WHOLE-SUITE red only if it runs
# the test files that red run named as failing; an unrelated targeted green ends nothing.
def test_g6_03_refined_middle_unrelated_targeted_green_does_not_end_a_suite_red():
    lb, vr = _lb_pure()
    red_out = "F........\n=== short test summary info ===\nFAILED tests/test_app.py::test_total - AssertionError\n1 failed, 8 passed in 0.3s"
    red = lambda: _ev(lb, vr, "pytest -q", red_out, True)
    for green, should_ask in ((lambda: _ev(lb, vr, "pytest -q tests/test_unrelated.py", "3 passed in 0.1s"), True),
                              (lambda: _ev(lb, vr, "pytest -q tests/test_app.py", "4 passed in 0.1s"), False),
                              (lambda: _ev(lb, vr, "pytest -q", "9 passed in 0.3s"), False)):
        ev, asked = [], []
        for _ in range(6):
            ev += [("edit",)] + red()
            asked.append(bool(lb.evaluate(ev)))
            ev += green()
        assert any(asked) == should_ask, should_ask


def test_g6_03_a_suite_red_that_names_no_failing_file_is_ended_only_by_the_suite():
    lb, vr = _lb_pure()
    red = lambda: _ev(lb, vr, "pytest -q", "1 failed, 8 passed in 0.3s", True)
    ev, asked = [], []
    for _ in range(6):
        ev += [("edit",)] + red()
        asked.append(bool(lb.evaluate(ev)))
        ev += _ev(lb, vr, "pytest -q tests/test_app.py", "4 passed in 0.1s")
    assert any(asked)


# ================================================================== v0.12 (2026-10-07): Codex re-check seat C2 (via the
# Coding Constitution session), confirmed on the LIVE v0.11 by this session's probes; founder: "Fix as v0.12, then land"
# N1 (regression of G6-02): `..` is resolved PHYSICALLY: through an existing symlink named like an archive, `x/<archive>/..`
# lands in the real archive's parent folder, which IS inside the real archive.
def test_n1_dotdot_through_a_symlinked_archive_is_resolved_physically(ws):
    (ws / "real" / ARC_TOKEN / "sub").mkdir(parents=True)
    (ws / "a").mkdir()
    os.symlink(ws / "real" / ARC_TOKEN / "sub", ws / "a" / ARC_TOKEN)
    for c in (f"git -C a/{ARC_TOKEN}/.. clean -fd", f"cd a/{ARC_TOKEN}/.. && rm -rf old", f"cd a/{ARC_TOKEN} && cd .. && rm -rf old",
              f"rm -rf a/{ARC_TOKEN}/../old"):
        assert guard(ws, c)[0] == "deny", c
    # G6-02 still holds where nothing is a symlink (and a missing folder cannot be entered at all)
    assert guard(ws, f"cd NEOCORTEX/{ARC_TOKEN} && cd .. && rm -rf old")[0] != "deny"


# N2: runner global options have PER-RUNNER arity: npm's `-p` (--parseable) takes no value, so `exec` stays the subcommand.
@pytest.mark.parametrize("c", ["npm -p exec -- tsx embed_corpus.ts", "npm --parseable exec -- tsx embed_corpus.ts",
                               "pnpm -w exec tsx embed_corpus.ts", "npm -w web exec tsx embed_corpus.ts"])
def test_n2_runner_option_arity_is_per_runner(ws, c):
    (ws / "governance" / "machine_config.yaml").write_text('forbidden_windows:\n  - window: "00:00-23:59"\n')
    assert guard(ws, c)[0] == "deny", c


# N3: a green that SELECTS inside the named file (`file.py::other_test`, `-k other`) never ends a whole-suite red.
def test_n3_a_selected_green_inside_the_named_file_does_not_end_a_suite_red():
    lb, vr = _lb_pure()
    red_out = "FAILED tests/test_app.py::test_total - AssertionError\n1 failed, 8 passed in 0.3s"
    for green_cmd in ("pytest -q tests/test_app.py::test_unrelated", "pytest -q tests/test_app.py -k unrelated"):
        ev, asked = [], []
        for _ in range(6):
            ev += [("edit",)] + _ev(lb, vr, "pytest -q", red_out, True)
            asked.append(bool(lb.evaluate(ev)))
            ev += _ev(lb, vr, green_cmd, "1 passed in 0.1s")
        assert any(asked), green_cmd

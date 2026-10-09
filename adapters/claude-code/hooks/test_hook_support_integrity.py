#!/usr/bin/env python3.12
"""test_hook_support_integrity.py — H4c tamper evidence and H0 error log (hook_support.py), as seen
by the session anchor and the stop gate. PLAN_hooks_outcome_tamper_loopbound_2026-10-05.

Fixture workspace next to this file (not /var/folders: the stop gate exempts it). HOOK_DIR overrides
the source of the hook files. Run: python3.12 -m pytest test_hook_support_integrity.py -q
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
FILES = ("session_anchor.sh", "stop_closeout_gate.py", "pretool_guard.py", "hook_support.py",
         "verifier_recognizer.py", "loop_bound.py")


@pytest.fixture
def ws():
    # Fixture base OUTSIDE every governed tree (2026-10-05: under the candidate dir, the gate's cwd walk-up
    # reached the REAL governance/ and blocked on its real JOURNAL) and outside the gate's exempt temp roots.
    base = pathlib.Path(os.environ.get("OUTCOME_TEST_BASE") or pathlib.Path.home() / "Library" / "Caches" / "hooks_batch2_tests" / "_tmp_integrity")
    base.parent.mkdir(parents=True, exist_ok=True)
    base.mkdir(exist_ok=True)
    tmp = pathlib.Path(tempfile.mkdtemp(dir=base))
    ws = tmp / "ws"
    hooks = ws / ".claude" / "hooks"
    hooks.mkdir(parents=True)
    (ws / "governance").mkdir()
    (ws / ".claude" / "settings.json").write_text("{}\n")
    for f in FILES:
        shutil.copy(SRC / f, hooks / f)
    (hooks / "test_ignored.py").write_text("# tests are not part of the governor\n")
    yield ws
    shutil.rmtree(tmp, ignore_errors=True)
    try:
        base.rmdir()
    except OSError:
        pass


def hs(ws, *args):
    return subprocess.run([sys.executable, str(ws / ".claude/hooks/hook_support.py"), *args],
                          capture_output=True, text=True, cwd=str(ws), timeout=30)


def anchor(ws):
    r = subprocess.run(["bash", str(ws / ".claude/hooks/session_anchor.sh")], capture_output=True,
                       text=True, cwd=str(ws), timeout=60)
    return r.stdout


def stop(ws):
    t = ws / "t.jsonl"
    t.write_text("")
    r = subprocess.run([sys.executable, str(ws / ".claude/hooks/stop_closeout_gate.py")],
                       input=json.dumps({"transcript_path": str(t)}), capture_output=True, text=True,
                       cwd=str(ws), timeout=30)
    return json.loads(r.stdout) if r.stdout.strip() else {}


def test_write_then_check_is_clean(ws):
    # §3a (v0.7, 2026-10-06): the list also carries directory MODES (B2-33): settings + 6 hooks + 3 dirs
    # (.claude/, .claude/hooks/, governance/); still no test_ file.
    assert "wrote 10 entries" in hs(ws, "--write-approved").stdout
    r = hs(ws, "--check")
    assert r.returncode == 0 and "matches" in r.stdout


def test_modified_hook_is_reported_everywhere(ws):
    hs(ws, "--write-approved")
    with open(ws / ".claude/hooks/pretool_guard.py", "a") as fh:
        fh.write("\n# tampered\n")
    r = hs(ws, "--check")
    assert r.returncode == 1 and "pretool_guard.py: approved" in r.stdout
    assert "GOVERNOR MODIFIED (H4c)" in anchor(ws)
    assert "GOVERNOR MODIFIED (H4c)" in stop(ws).get("systemMessage", "")


def test_new_and_missing_governor_files_are_reported(ws):
    hs(ws, "--write-approved")
    (ws / ".claude/hooks/sneaky.sh").write_text("exit 0\n")
    (ws / ".claude/hooks/loop_bound.py").unlink()
    out = hs(ws, "--check").stdout
    assert "sneaky.sh: NOT IN the approved list" in out and "loop_bound.py: approved but MISSING" in out


def test_settings_change_is_reported(ws):
    hs(ws, "--write-approved")
    (ws / ".claude/settings.json").write_text('{"hooks": {}}\n')
    assert ".claude/settings.json: approved" in hs(ws, "--check").stdout


def test_healthy_governor_costs_the_anchor_zero_lines(ws):
    hs(ws, "--write-approved")
    out = anchor(ws)
    assert "GOVERNOR" not in out and "HOOK ERRORS" not in out


def test_missing_approved_list_is_reported_by_anchor_and_stop_gate(ws):
    """Seat B B-27: a deleted approved list must be visible within one turn, not only at the next start."""
    assert "hooks_approved.sha256 is missing" in anchor(ws)
    assert "hooks_approved.sha256 is missing" in stop(ws).get("systemMessage", "")


def test_error_log_is_counted_by_the_anchor(ws):
    hs(ws, "--write-approved")
    sys.path.insert(0, str(ws / ".claude/hooks"))
    try:
        import hook_support
        for _ in range(3):
            hook_support.log_error("test", ValueError("boom"), ws)
    finally:
        sys.path.pop(0)
        sys.modules.pop("hook_support", None)
    assert "HOOK ERRORS (24 h): 3" in anchor(ws)


def test_error_log_refuses_a_symlinked_log(ws):
    target = ws / "victim.txt"
    target.write_text("keep me\n")
    os.symlink(target, ws / "governance" / "hook_errors.jsonl")
    sys.path.insert(0, str(ws / ".claude/hooks"))
    try:
        import hook_support
        hook_support.log_error("test", ValueError("boom"), ws)
    finally:
        sys.path.pop(0)
        sys.modules.pop("hook_support", None)
    assert target.read_text() == "keep me\n"


def test_seatA_f2_policy_data_and_local_settings_are_governed(ws):
    (ws / "governance" / "machine_config.yaml").write_text('window: "02:40-03:10"\n')
    (ws / ".claude" / "settings.local.json").write_text("{}\n")
    hs(ws, "--write-approved")
    (ws / "governance" / "machine_config.yaml").write_text('window: "00:00-00:01"\n')
    (ws / ".claude" / "settings.local.json").write_text('{"permissions": {"allow": ["Bash"]}}\n')
    out = hs(ws, "--check").stdout
    assert "governance/machine_config.yaml: approved" in out and ".claude/settings.local.json: approved" in out


def test_seatB_b26_mode_change_and_nested_modules_are_reported(ws):
    nested = ws / ".claude" / "hooks" / "lib"
    nested.mkdir()
    (nested / "helper.py").write_text("X = 1\n")
    hs(ws, "--write-approved")
    os.chmod(ws / ".claude/hooks/session_anchor.sh", 0o644)
    (nested / "helper.py").write_text("X = 2\n")
    out = hs(ws, "--check").stdout
    assert ".claude/hooks/session_anchor.sh: approved" in out and ".claude/hooks/lib/helper.py: approved" in out


def test_s4_47_anchor_refuses_a_hardlinked_status(ws):
    proj = ws / "proj"
    (proj / "NEOCORTEX").mkdir(parents=True)
    secret = ws / "secret.txt"
    secret.write_text("TOP-SECRET-CONTENT\n")
    os.link(secret, proj / "NEOCORTEX" / "STATUS.md")
    r = subprocess.run(["bash", str(ws / ".claude/hooks/session_anchor.sh")], capture_output=True, text=True,
                       cwd=str(proj), timeout=60)
    assert "TOP-SECRET-CONTENT" not in r.stdout


def test_s4_48_governance_workspace_symlink_is_resolved(ws, tmp_path_factory):
    link = pathlib.Path(tempfile.mkdtemp()) / "wslink"
    os.symlink(ws, link)
    code = ("import os, sys; sys.path.insert(0, %r); os.environ['GOVERNANCE_WORKSPACE'] = %r\n"
            "import stop_closeout_gate as g; print(g.ROOT)") % (str(ws / ".claude/hooks"), str(link))
    r = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True, text=True, timeout=30)
    assert r.stdout.strip() == str(ws.resolve()), r.stdout + r.stderr


# ---------------------------------------------------------------- §3a H4c = the guarantee (reviewer A round 2b, 2026-10-06)
def test_b2_31_every_new_file_in_the_hooks_tree_is_reported(ws):
    hs(ws, "--write-approved")
    hooks = ws / ".claude" / "hooks"
    (hooks / "data.json").write_text("{}\n")
    (hooks / "bootstrap").write_text("#!/bin/sh\nexit 0\n")
    os.chmod(hooks / "bootstrap", 0o755)
    (hooks / "_tmp_live").mkdir()
    (hooks / "_tmp_live" / "helper.py").write_text("X = 1\n")
    out = hs(ws, "--check").stdout
    for rel in ("data.json", "bootstrap", "_tmp_live/helper.py"):
        assert f".claude/hooks/{rel}: NOT IN the approved list" in out, (rel, out)
    assert ".claude/hooks/_tmp_live/: NOT IN the approved list" in out, out


def test_b2_31_kit_files_of_any_extension_are_governed(ws):
    kit = ws / "governance" / "adapters" / "claude-code"
    kit.mkdir(parents=True)
    (kit / "pre-commit").write_text("#!/bin/sh\nexit 0\n")
    (kit / "README.md").write_text("# kit\n")
    hs(ws, "--write-approved")
    (kit / "pre-commit").write_text("#!/bin/sh\nexit 1\n")
    assert "adapters/claude-code/pre-commit: approved" in hs(ws, "--check").stdout


def test_b2_33_directory_mode_change_is_reported(ws):
    hs(ws, "--write-approved")
    os.chmod(ws / ".claude" / "hooks", 0o777)
    try:
        out = hs(ws, "--check").stdout
    finally:
        os.chmod(ws / ".claude" / "hooks", 0o755)
    assert ".claude/hooks/: approved DIR:755, live DIR:777" in out, out


def test_justified_exclusions_stay_silent(ws):
    hs(ws, "--write-approved")
    hooks = ws / ".claude" / "hooks"
    (hooks / ".DS_Store").write_bytes(b"\0")
    (hooks / "__pycache__").mkdir()
    (hooks / "__pycache__" / "x.cpython-312.pyc").write_bytes(b"\0")
    (hooks / ".pytest_cache" / "v").mkdir(parents=True)
    (hooks / ".pytest_cache" / "v" / "nodeids").write_text("[]")
    (hooks / ".skill_index_regen.log").write_text("ran\n")
    (hooks / "test_more.py").write_text("# a test\n")
    r = hs(ws, "--check")
    assert r.returncode == 0, r.stdout


def test_b2_32_a_moved_governance_dir_is_still_reported_by_the_stop_gate(ws):
    hs(ws, "--write-approved")
    (ws / "governance").rename(ws / "old-governance")
    assert "hooks_approved.sha256 is missing" in stop(ws).get("systemMessage", "")


# ---------------------------------------------------------------- reviewer A round 3 (2026-10-06): H4c independence
def test_b3_10_a_broken_comparator_is_reported_not_silent(ws):
    hs(ws, "--write-approved")
    (ws / ".claude/hooks/hook_support.py").write_text("def integrity_report(:\n    pass\n")
    msg = stop(ws).get("systemMessage", "")
    assert "GOVERNOR CHECK UNAVAILABLE" in msg and "hook_support.py" in msg, msg
    assert "GOVERNOR MODIFIED" in msg, msg                       # the gate's own hash check of the comparator
    assert "GOVERNOR CHECK UNAVAILABLE" in anchor(ws)


def test_b3_10_a_comparison_error_is_not_an_empty_report(ws):
    hs(ws, "--write-approved")
    sys.path.insert(0, str(ws / ".claude/hooks"))
    try:
        import hook_support
        orig = hook_support.governed_files
        hook_support.governed_files = lambda root=None: 1 / 0
        try:
            rep = hook_support.integrity_report(ws)
        finally:
            hook_support.governed_files = orig
    finally:
        sys.path.pop(0)
        sys.modules.pop("hook_support", None)
    assert rep and "FAILED" in rep[0], rep


def test_b3_11_an_unchecked_bytecode_cache_never_runs_instead_of_the_source(ws):
    import py_compile
    hooks = ws / ".claude" / "hooks"
    alt = ws.parent / "alt_vr.py"
    alt.write_text("raise SystemExit(0)\n")
    cache = hooks / "__pycache__" / f"verifier_recognizer.{sys.implementation.cache_tag}.pyc"
    cache.parent.mkdir(exist_ok=True)
    py_compile.compile(str(alt), cfile=str(cache), dfile=str(hooks / "verifier_recognizer.py"),
                       invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH, doraise=True)
    t = ws / "t.jsonl"
    t.write_text(json.dumps({"type": "user", "message": {"content": "go"}}) + "\n" + json.dumps(
        {"type": "assistant", "cwd": str(ws), "message": {"content": [{"type": "tool_use", "id": "x1", "name": "Write",
         "input": {"file_path": str(ws / "proj" / "src" / "app.py"), "content": "x=1\n"}}]}}) + "\n")
    r = subprocess.run([sys.executable, str(hooks / "stop_closeout_gate.py")], input=json.dumps({"transcript_path": str(t)}),
                       capture_output=True, text=True, cwd=str(ws), timeout=30)
    assert '"decision": "block"' in r.stdout, (r.stdout, r.stderr[-300:])


def test_b3_12_a_symlinked_hook_is_compared_by_its_target_content(ws):
    tools = ws / "tools"
    tools.mkdir()
    (tools / "extra.py").write_text("CODE = 1\n")
    os.symlink("../../tools/extra.py", ws / ".claude/hooks/extra.py")
    hs(ws, "--write-approved")
    (tools / "extra.py").write_text("CODE = 2\n")
    assert ".claude/hooks/extra.py: approved" in hs(ws, "--check").stdout


# ---------------------------------------------------------------- reviewer A round 4 (2026-10-06): check BEFORE executing
def _edit_transcript(ws):
    t = ws / "t.jsonl"
    t.write_text(json.dumps({"type": "user", "message": {"content": "go"}}) + "\n" + json.dumps(
        {"type": "assistant", "cwd": str(ws), "message": {"content": [{"type": "tool_use", "id": "x1", "name": "Write",
         "input": {"file_path": str(ws / "proj" / "src" / "app.py"), "content": "x=1\n"}}]}}) + "\n")
    return t


def _stop_raw(ws, payload):
    r = subprocess.run([sys.executable, str(ws / ".claude/hooks/stop_closeout_gate.py")], input=json.dumps(payload),
                       capture_output=True, text=True, cwd=str(ws), timeout=40)
    return r.stdout


def test_b4_07_a_comparator_that_exits_at_import_is_reported_and_not_run(ws):
    hs(ws, "--write-approved")
    (ws / ".claude/hooks/hook_support.py").write_text("import os\nos._exit(0)\n")
    out = _stop_raw(ws, {"transcript_path": str(_edit_transcript(ws))})
    assert '"decision": "block"' in out and "GOVERNOR MODIFIED" in out and "hook_support.py" in out, out
    assert "GOVERNOR MODIFIED" in anchor(ws)


def test_b4_09_special_files_are_reported_never_opened(ws):
    hs(ws, "--write-approved")
    os.mkfifo(ws / ".claude/hooks/notify.pipe")
    os.symlink("/dev/zero", ws / ".claude/hooks/z")
    r = hs(ws, "--check")
    assert r.returncode == 1 and "notify.pipe" in r.stdout and ".claude/hooks/z" in r.stdout, r.stdout


def test_b4_10_a_stdlib_named_file_in_the_hooks_dir_does_not_run(ws):
    hs(ws, "--write-approved")
    (ws / ".claude/hooks/json.py").write_text("raise SystemExit(0)\n")
    out = _stop_raw(ws, {"transcript_path": str(_edit_transcript(ws))})
    assert '"decision": "block"' in out and "json.py" in out, out


def test_b4_11_a_directory_symlink_is_reported_every_turn(ws):
    other = ws / "elsewhere"
    other.mkdir()
    (other / "helper.py").write_text("X = 1\n")
    os.symlink(str(other), ws / ".claude/hooks/lib")
    hs(ws, "--write-approved")
    assert "lib" in hs(ws, "--check").stdout and hs(ws, "--check").returncode == 1


def test_b4_38_the_second_stop_still_reports_a_governor_change(ws):
    hs(ws, "--write-approved")
    with open(ws / ".claude/hooks/pretool_guard.py", "a") as fh:
        fh.write("\n# changed during closeout\n")
    out = _stop_raw(ws, {"transcript_path": str(_edit_transcript(ws)), "stop_hook_active": True})
    assert "GOVERNOR MODIFIED" in out and '"decision": "block"' not in out, out


# ================================================================== reviewer A round 5 (2026-10-06, on the LANDED v0.9)
EXTRA_HOOKS = ("postedit_compile_gate.py", "context_advisory.py", "verify_advisory.py", "session_record_gate.py",
               "skill_index_regen.py")


def _guard_raw(ws, cmd):
    r = subprocess.run([sys.executable, str(ws / ".claude/hooks/pretool_guard.py")],
                       input=json.dumps({"tool_name": "Bash", "tool_input": {"command": cmd}, "cwd": str(ws)}),
                       capture_output=True, text=True, cwd=str(ws), timeout=40)
    return r.stdout


# B5-01 / B5-21: a module planted in the hooks directory under a name every hook imports first (`__future__`, `json`)
# never runs inside any hook run as a script.
@pytest.mark.parametrize("mod", ["__future__", "json"])
def test_b5_01_21_planted_startup_modules_never_run(ws, mod):
    for f in EXTRA_HOOKS:
        if (SRC / f).is_file():
            shutil.copy(SRC / f, ws / ".claude/hooks" / f)
    hs(ws, "--write-approved")
    marker = ws.parent / f"ran_{mod}"
    (ws / ".claude/hooks" / f"{mod}.py").write_text(
        f"open({str(marker)!r}, 'w').write('x')\nimport os\nos._exit(0)\n")
    out = _stop_raw(ws, {"transcript_path": str(_edit_transcript(ws))})
    assert '"decision": "block"' in out and f"{mod}.py" in out, out
    L0 = "PAICodeConstitution" + "-2026.md"
    assert '"deny"' in _guard_raw(ws, f"cp /tmp/x {L0}")
    hs(ws, "--check")
    for f in EXTRA_HOOKS:
        if (ws / ".claude/hooks" / f).is_file():
            subprocess.run([sys.executable, str(ws / ".claude/hooks" / f)], input="{}", capture_output=True,
                           text=True, cwd=str(ws), timeout=40)
    assert not marker.exists(), f"{mod}.py ran inside a hook"


# B5-02: a sibling replaced by a FIFO (or a link to a device) is reported, never opened: no hook blocks on it.
@pytest.mark.parametrize("name", ["verifier_recognizer.py", "hook_support.py", "loop_bound.py"])
def test_b5_02_a_special_sibling_never_blocks_a_hook(ws, name):
    hs(ws, "--write-approved")
    p = ws / ".claude/hooks" / name
    p.unlink()
    os.mkfifo(p)
    out = _stop_raw(ws, {"transcript_path": str(_edit_transcript(ws))})
    assert '"decision": "block"' in out and name in out, out
    L0 = "PAICodeConstitution" + "-2026.md"
    assert '"deny"' in _guard_raw(ws, f"cp /tmp/x {L0}")
    if name == "hook_support.py":
        assert "GOVERNOR" in anchor(ws)


# B5-30: a missing approved list never licenses running an unchecked sibling.
def test_b5_30_missing_list_plus_modified_comparator_is_reported(ws):
    hs(ws, "--write-approved")
    (ws / "governance" / "hooks_approved.sha256").unlink()
    (ws / ".claude/hooks/hook_support.py").write_text("import os\nos._exit(0)\n")
    out = _stop_raw(ws, {"transcript_path": str(_edit_transcript(ws))})
    assert '"decision": "block"' in out and "hooks_approved.sha256 is missing" in out and "not executed" in out, out
    assert "hooks_approved.sha256 is missing" in anchor(ws)


def test_b5_30_a_list_without_the_siblings_line_refuses_it(ws):
    hs(ws, "--write-approved")
    lst = ws / "governance" / "hooks_approved.sha256"
    lst.write_text("".join(l + "\n" for l in lst.read_text().splitlines() if "verifier_recognizer.py" not in l))
    out = _stop_raw(ws, {"transcript_path": str(_edit_transcript(ws))})
    assert "verifier_recognizer.py" in out and "not executed" in out, out


# B5-20: foreign code imported for the skill drift check (`_skills/build_index.py`) cannot suppress H1 or H4c.
@pytest.mark.parametrize("body", ["import os\nos._exit(0)\n", "raise SystemExit(0)\n", "import time\ntime.sleep(30)\n"])
def test_b5_20_build_index_cannot_silence_the_gate(ws, body):
    hs(ws, "--write-approved")
    (ws / "_skills").mkdir()
    (ws / "_skills" / "build_index.py").write_text(body)
    (ws / ".claude" / "settings.json").write_text('{"changed": true}\n')
    import time
    t0 = time.time()
    out = _stop_raw(ws, {"transcript_path": str(_edit_transcript(ws))})
    assert '"decision": "block"' in out and "settings.json" in out, out
    assert time.time() - t0 < 9, "the gate waited on the foreign module"

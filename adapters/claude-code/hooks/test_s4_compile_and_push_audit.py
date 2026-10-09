#!/usr/bin/env python3.12
"""test_s4_compile_and_push_audit.py — reviewer A S4 FINAL rows on postedit_compile_gate.py and post_push_audit.sh
(S4-07, S4-22, S4-25/S4-51, S4-44, S4-45), folded into hooks batch 2 on 2026-10-05.

Hooks run as subprocesses from copies in a fixture workspace next to this file (HOOK_DIR overrides the source).
Run: python3.12 -m pytest test_s4_compile_and_push_audit.py -q
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


@pytest.fixture
def ws():
    # Fixture base OUTSIDE every governed tree (2026-10-05: under the candidate dir, the gate's cwd walk-up
    # reached the REAL governance/ and blocked on its real JOURNAL) and outside the gate's exempt temp roots.
    base = pathlib.Path(os.environ.get("OUTCOME_TEST_BASE") or pathlib.Path.home() / "Library" / "Caches" / "hooks_batch2_tests" / "_tmp_s4")
    base.parent.mkdir(parents=True, exist_ok=True)
    base.mkdir(exist_ok=True)
    tmp = pathlib.Path(tempfile.mkdtemp(dir=base))
    ws = tmp / "ws"
    (ws / ".claude" / "hooks").mkdir(parents=True)
    for f in ("postedit_compile_gate.py", "post_push_audit.sh"):
        shutil.copy(SRC / f, ws / ".claude" / "hooks" / f)
    yield ws
    shutil.rmtree(tmp, ignore_errors=True)
    try:
        base.rmdir()
    except OSError:
        pass


def compile_gate(ws, payload, env=None):
    r = subprocess.run([sys.executable, str(ws / ".claude/hooks/postedit_compile_gate.py")], input=json.dumps(payload),
                       capture_output=True, text=True, cwd=str(ws), timeout=60, env=env or dict(os.environ))
    assert r.returncode == 0, r.stderr[-400:]
    return (json.loads(r.stdout) if r.stdout.strip() else {}), r.stderr


def push_audit(ws, cmd):
    r = subprocess.run(["bash", str(ws / ".claude/hooks/post_push_audit.sh")],
                       input=json.dumps({"tool_input": {"command": cmd}}), capture_output=True, text=True, timeout=30)
    return '"decision": "block"' in r.stdout


@pytest.mark.skipif(not shutil.which("node"), reason="node absent")
def test_s4_22_js_syntax_is_checked(ws):
    f = ws / "app.js"
    f.write_text("function (\n")
    o, _ = compile_gate(ws, {"tool_input": {"file_path": str(f)}})
    assert o.get("decision") == "block"


def test_s4_22_zsh_syntax_is_checked(ws):
    f = ws / "x.zsh"
    f.write_text("if then fi fi\n")
    o, _ = compile_gate(ws, {"tool_input": {"file_path": str(f)}})
    assert o.get("decision") == "block"


def test_s4_44_notebook_path_is_read(ws):
    f = ws / "a.ipynb"
    f.write_text("{not json")
    o, _ = compile_gate(ws, {"tool_input": {"notebook_path": str(f)}})
    assert o.get("decision") == "block"


def test_s4_07_no_fallback_to_the_system_python(ws):
    f = ws / "m.py"
    f.write_text("match 1:\n    case 1:\n        pass\n")
    env = dict(os.environ, PATH="/usr/bin:/bin")
    o, _ = compile_gate(ws, {"tool_input": {"file_path": str(f)}}, env)
    assert o.get("decision") != "block"            # valid 3.10+ syntax must never be judged by 3.9


def test_s4_51_absent_lint_script_is_announced(ws):
    (ws / "governance").mkdir()
    f = ws / "governance" / "routing_policy.yaml"
    f.write_text("x: 1\n")
    _o, err = compile_gate(ws, {"tool_input": {"file_path": str(f)}})
    assert "DISABLED" in err


@pytest.mark.parametrize("cmd,fires", [
    ('git -C "/srv/example/Project With Spaces" push', True),
    ("git -c push.default=current push", True),
    ("git push origin main", True),
    ("cd repo && git -C . push --tags", True),
    ("git status", False),
    ("echo 'git push'", False),
    ("git pushd", False),
])
def test_s4_45_push_forms(ws, cmd, fires):
    assert push_audit(ws, cmd) is fires, cmd


# S4-25 PARTIAL (reviewer A round 2b re-verification): an unexpected exception in the compile gate exits 0 (advisory
# class: fail-open) and is logged, never a traceback with exit 1.
@pytest.mark.parametrize("payload", ['{"tool_input": {"file_path": 5}}', '[]'])
def test_s4_25_unexpected_exception_fails_open_quietly(ws, payload):
    r = subprocess.run([sys.executable, str(ws / ".claude/hooks/postedit_compile_gate.py")], input=payload,
                       capture_output=True, text=True, cwd=str(ws), timeout=30)
    assert r.returncode == 0 and "Traceback" not in r.stderr, (r.returncode, r.stderr[-300:])

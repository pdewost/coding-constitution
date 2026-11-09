#!/usr/bin/env python3.12
"""
test_install_adapters_canonical_root.py -- v0.13.2: the installer must not rewrite its own canonical settings.

Invariant under test: no run of install_adapters.py, in any mode, may change the bytes of
ROOT/.claude/settings.json.

Hermetic: every fixture workspace lives under tempfile.gettempdir(). The installer is only ever run with --root
pointing at such a folder. The kit is located relative to THIS file: bootstrap.sh and hooks/ beside the installer.
A dependency that cannot be found FAILS the test (never skips).

Run:  python3.12 -m pytest -q adapters/claude-code/test_install_adapters_canonical_root.py
"""
import hashlib
import json
import os
import pathlib
import shutil
import shlex
import subprocess
import sys
import tempfile

import pytest

KIT = pathlib.Path(__file__).resolve().parent
INSTALLER = KIT / "install_adapters.py"
PY = sys.executable

# The installer is run through this shim so a configured extra target does not exist-check against a
# fixture workspace; everything else is the real main().
_RUNNER = (
    "import sys, importlib.util\n"
    "p = sys.argv[1]\n"
    "sys.argv = [p] + sys.argv[2:]\n"
    "spec = importlib.util.spec_from_file_location('install_adapters', p)\n"
    "m = importlib.util.module_from_spec(spec)\n"
    "spec.loader.exec_module(m)\n"
    "m.EXTRA_TARGETS = ()\n"
    "m.main()\n"
)


def _first_existing(label, *cands):
    for c in cands:
        if c.exists():
            return c
    pytest.fail(f"dependency not found: {label}; looked in " + ", ".join(str(c) for c in cands))


def _locate():
    assert INSTALLER.is_file(), f"installer not found beside the test: {INSTALLER}"
    bootstrap = _first_existing("bootstrap.sh", KIT / "bootstrap.sh",
                                KIT.parent.parent / "publish_kit" / "bootstrap.sh")
    canon = _first_existing("settings.canonical.json", KIT / "settings.canonical.json",
                            KIT.parent.parent / "publish_kit" / "settings.canonical.json")
    hooks = KIT / "hooks"
    if not (hooks / "hook_support.py").is_file():
        hooks = _first_existing("hook_support.py (private .claude/hooks)",
                                KIT.parent.parent.parent / ".claude" / "hooks" / "hook_support.py").parent
    return bootstrap, canon, hooks


def _sha(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def _run(args, cwd=None):
    env = {k: v for k, v in os.environ.items() if k != "GOVERNANCE_WORKSPACE"}
    r = subprocess.run(args, capture_output=True, text=True, cwd=cwd, env=env, timeout=120)
    return r.returncode, r.stdout + r.stderr


def install(ws, *flags):
    return _run([PY, "-c", _RUNNER, str(INSTALLER), "--root", str(ws), *flags])


def hook_check(ws):
    return _run([PY, str(ws / ".claude" / "hooks" / "hook_support.py"), "--check"])


@pytest.fixture(scope="module")
def template(tmp_path_factory):
    """A workspace bootstrapped exactly as an adopter would: scratch kit -> bootstrap.sh -> approved list."""
    base = pathlib.Path(tempfile.mkdtemp(prefix="canon_root_tpl_", dir=tempfile.gettempdir())).resolve()
    bootstrap, canon, hooks = _locate()
    kit = base / "repo" / "adapters" / "claude-code"
    (kit / "hooks").mkdir(parents=True)
    shutil.copy(bootstrap, kit / "bootstrap.sh")
    shutil.copy(canon, kit / "settings.canonical.json")
    for f in list(hooks.glob("*.py")) + list(hooks.glob("*.sh")):
        shutil.copy(f, kit / "hooks" / f.name)
    for extra in ("handoff_resolve.py", "anchor_render.py"):
        if (KIT / extra).is_file():
            shutil.copy(KIT / extra, kit / extra)
    ws = base / "ws"
    rc, out = _run(["bash", str(kit / "bootstrap.sh"), str(ws)], cwd=str(base))
    print(out[-600:])
    assert rc == 0, f"bootstrap failed rc={rc}\n{out}"
    assert (ws / ".claude" / "settings.json").is_file()
    rc, out = hook_check(ws)
    assert (rc, out.strip()) == (0, "governor matches the approved list"), out
    yield ws
    shutil.rmtree(base, ignore_errors=True)


@pytest.fixture
def ws(template):
    """A fresh copy of the template, with NEOCORTEX/ at the root (the case under test)."""
    base = pathlib.Path(tempfile.mkdtemp(prefix="canon_root_case_", dir=tempfile.gettempdir())).resolve()
    dest = base / "ws"
    shutil.copytree(template, dest, symlinks=True)
    (dest / "NEOCORTEX").mkdir()
    yield dest
    shutil.rmtree(base, ignore_errors=True)


def installed(ws):
    return json.loads((ws / "governance" / "adapters" / "INSTALLED.json").read_text())


def settings_of(ws):
    return ws / ".claude" / "settings.json"


def make_absolute(ws):
    """Simulate a root already rewritten by an earlier installer: hook commands with absolute paths."""
    p = settings_of(ws)
    data = json.loads(p.read_text())
    n = 0
    for entries in data["hooks"].values():
        for entry in entries:
            for hook in entry.get("hooks", []):
                c = hook.get("command", "")
                if "CLAUDE_PROJECT_DIR" in c:
                    hook["command"] = c.replace('"$CLAUDE_PROJECT_DIR"', str(ws)).replace("$CLAUDE_PROJECT_DIR", str(ws))
                    n += 1
    assert n > 0
    p.write_text(json.dumps(data, indent=2) + "\n")
    return n


NOTICE_HEAD = "NOTICE: the hook commands in .claude/settings.json at the workspace root name absolute paths"


def notice_action(ws):
    return f"(cd {shlex.quote(str(ws))} && python3.12 .claude/hooks/hook_support.py --write-approved)"


# -- 1-6: red on the unfixed installer ------------------------------------------------------------

def test_1_apply_leaves_root_settings_bytes_unchanged(ws):
    before = _sha(settings_of(ws))
    rc, out = install(ws, "--apply")
    after = _sha(settings_of(ws))
    print(rc, before, after, out)
    assert rc == 0
    assert after == before
    assert "CANONICAL (not rewritten)  ." in out
    assert "WRITING" not in out
    rc, out = install(ws, "--apply")
    print(rc, out)
    assert rc == 0
    assert "No-op: all 0 project(s) already current, 1 canonical (not rewritten). INSTALLED.json unchanged." in out
    rc, out = install(ws, "--dry-run")
    assert "Dry-run summary: 0 would change, 0 already current, 1 canonical (not rewritten)." in out


def test_2_hook_check_still_matches_after_apply(ws):
    rc, out = install(ws, "--apply")
    assert rc == 0, out
    rc, out = hook_check(ws)
    print(rc, out)
    assert (rc, out.strip()) == (0, "governor matches the approved list")


def test_3_canonical_edit_updates_root_row_hash(ws):
    rc, out = install(ws, "--apply")
    assert rc == 0, out
    first = installed(ws)
    assert len(first) == 1
    data = json.loads(settings_of(ws).read_text())
    data["permissions"] = {"deny": ["Read(//tmp/canon_root_test_marker)"]}
    settings_of(ws).write_text(json.dumps(data, indent=2) + "\n")
    edited = _sha(settings_of(ws))
    expected = hashlib.sha256(json.dumps(
        {"hooks": data["hooks"], "permissions": {"deny": ["Read(//tmp/canon_root_test_marker)"]}},
        sort_keys=True).encode()).hexdigest()
    rc, out = install(ws, "--apply")
    rows = installed(ws)
    print(rc, first[0]["policy_source_hash"], expected, rows, out)
    assert rc == 0
    assert _sha(settings_of(ws)) == edited
    assert len(rows) == 1
    assert rows[0]["project_path"] == str(ws)
    assert rows[0]["policy_source_hash"] == expected
    assert rows[0]["policy_source_hash"] != first[0]["policy_source_hash"]
    assert "updated (1 entries)" in out


def test_4_check_passes_after_moving_a_portable_root(ws):
    rc, out = install(ws, "--apply")
    assert rc == 0, out
    moved = ws.parent / "ws_moved"
    ws.rename(moved)
    rc, out = install(moved, "--check")
    print(rc, out)
    assert rc == 0
    assert "CHECK PASSED: all 1 armed projects clean (0 drift)." in out
    assert "NOTICE" not in out


def test_5_already_absolute_root_notice_unchanged_and_lint_after_move(ws):
    n = make_absolute(ws)
    before = _sha(settings_of(ws))
    outs = {}
    for mode in ("--apply", "--dry-run", "--check"):
        rc, out = install(ws, mode)
        outs[mode] = (rc, out)
        print(mode, rc, out)
        assert rc == 0, (mode, out)
        assert out.count(NOTICE_HEAD) == 1, mode
        assert notice_action(ws) in out
        assert "compare it with adapters/claude-code/settings.canonical.json" in out
        assert _sha(settings_of(ws)) == before, mode
    moved = ws.parent / "ws_moved"
    ws.rename(moved)
    rc, out = install(moved, "--check")
    print(rc, out)
    assert rc == 1
    assert f"CHECK FAILED: 0 missing, 0 drifted, 0 skipped (not checked), {n} lint problem(s)." in out
    assert "program not found" in out
    assert out.count(NOTICE_HEAD) == 1


def test_5c_notice_fires_when_the_workspace_path_has_a_space(ws):
    """An earlier installer shell-quoted a path with a space: '/x/my ws/.claude/hooks'/hook.sh"""
    spaced = ws.parent / "my ws"
    ws.rename(spaced)
    p = settings_of(spaced)
    data = json.loads(p.read_text())
    quoted = shlex.quote(str(spaced / ".claude" / "hooks")) + "/"
    n = 0
    for entries in data["hooks"].values():
        for entry in entries:
            for hook in entry.get("hooks", []):
                c = hook.get("command", "")
                if '"$CLAUDE_PROJECT_DIR"/.claude/hooks/' in c:
                    hook["command"] = c.replace('"$CLAUDE_PROJECT_DIR"/.claude/hooks/', quoted)
                    n += 1
    print(n, quoted)
    assert n > 0
    p.write_text(json.dumps(data, indent=2) + "\n")
    rc, out = install(spaced, "--dry-run")
    print(rc, out)
    assert rc == 0
    assert out.count(NOTICE_HEAD) == 1
    assert notice_action(spaced) in out
    spaced.rename(ws)


def test_5b_portable_root_prints_no_notice(ws):
    for mode in ("--apply", "--dry-run", "--check"):
        rc, out = install(ws, mode)
        print(mode, rc, out)
        assert rc == 0
        assert "NOTICE" not in out, mode


def test_6_symlink_back_to_root_gives_one_row(ws):
    os.symlink(str(ws), str(ws / "x"))
    before = _sha(settings_of(ws))
    rc, out = install(ws, "--apply")
    rows = installed(ws)
    print(rc, rows, out)
    assert rc == 0
    assert _sha(settings_of(ws)) == before
    assert [r["project_path"] for r in rows] == [str(ws)]
    assert out.count("CANONICAL (not rewritten)") == 1
    assert "Discovered 1 target(s)" in out


def test_6b_a_child_reached_by_an_alias_sorting_first_is_still_armed(ws):
    """ROOT/a -> ROOT/z, with ROOT/z/NEOCORTEX: the real folder must be the one kept, not the alias."""
    (ws / "z" / "NEOCORTEX").mkdir(parents=True)
    (ws / "a").symlink_to(ws / "z")
    rc, out = install(ws, "--apply")
    print(rc, out)
    assert rc == 0
    assert "SKIP" not in out
    assert (ws / "z" / ".claude" / "settings.json").is_file()
    rows = sorted(r["project_path"] for r in installed(ws))
    print(rows)
    assert rows == sorted([str(ws), str(ws / "z")])


# -- 7-10: green today, guarding against the fix breaking them -----------------------------------

def test_7_installed_json_has_one_armed_row_and_verify_fires_has_a_row(ws):
    rc, out = install(ws, "--apply")
    assert rc == 0, out
    rows = installed(ws)
    print(rows)
    assert len(rows) == 1
    assert rows[0]["has_adapter"] is True
    assert rows[0]["project_path"] == str(ws)
    vf = KIT / "verify_fires.py"
    assert vf.is_file(), f"verify_fires.py not found beside the installer: {vf}"
    rc, out = _run([PY, str(vf), "--root", str(ws)])
    print(rc, out)
    assert rc != 2
    assert "lists no project" not in out


def test_8_second_apply_is_a_noop(ws):
    rc, out = install(ws, "--apply")
    assert rc == 0, out
    inst = ws / "governance" / "adapters" / "INSTALLED.json"
    first = inst.read_bytes()
    rc, out = install(ws, "--apply")
    print(rc, out)
    assert rc == 0
    assert "No-op: all " in out and "INSTALLED.json unchanged." in out
    assert inst.read_bytes() == first


def test_8b_malformed_installed_json_is_rewritten_not_fatal(ws):
    rc, out = install(ws, "--apply")
    assert rc == 0, out
    inst = ws / "governance" / "adapters" / "INSTALLED.json"
    inst.write_text("{not json")
    rc, out = install(ws, "--apply")
    print(rc, out)
    assert rc == 0
    rows = installed(ws)
    assert len(rows) == 1 and rows[0]["has_adapter"] is True


def test_9_child_project_is_written_with_absolute_paths(ws):
    child = ws / "child"
    (child / "NEOCORTEX").mkdir(parents=True)
    rc, out = install(ws, "--apply")
    print(rc, out)
    assert rc == 0
    text = (child / ".claude" / "settings.json").read_text()
    prefix = str(ws / ".claude" / "hooks") + "/"
    assert prefix in text
    assert "CLAUDE_PROJECT_DIR" not in text
    rows = {r["project_path"]: r for r in installed(ws)}
    assert sorted(rows) == sorted([str(ws), str(child)])
    assert rows[str(child)]["last_action"] == "written"


def test_10_symlinked_dot_claude_at_root_is_still_refused(ws):
    real = ws.parent / "real_claude"
    (ws / ".claude").rename(real)
    os.symlink(str(real), str(ws / ".claude"))
    before = _sha(real / "settings.json")
    rc, out = install(ws, "--apply")
    print(rc, out)
    assert rc == 0
    assert "SKIP (symlink)  ." in out
    assert "CANONICAL (not rewritten)" not in out
    assert _sha(real / "settings.json") == before

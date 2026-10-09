#!/usr/bin/env python3
"""postedit_compile_gate.py — COMPILE-GATE policy (POLICY_CORE.md), Claude Code adapter.

PostToolUse hook (matcher: Edit|Write|NotebookEdit). Syntax-checks the edited
file IMMEDIATELY AFTER the write; on failure emits {"decision":"block"} so the error
is fed straight back to the agent, who must repair it (2025 §6.1 Compiler+ gate,
mechanized). It runs after the write and does not roll it back (S4-50, final).
Coverage (S4-22, 2026-10-05): .py (python3.12 only, never the 3.9 system python:
S4-07), .sh/.bash (bash -n), .zsh (zsh -n), .js/.mjs/.cjs (node --check), .json and
.ipynb (JSON parse), .applescript (osacompile). A checker that is unavailable is
ANNOUNCED on stderr as DISABLED, never silently passed (S4-25/S4-51).

Deliberately NOT here: Python import smoke tests — importing executes module
top-level code, which a hook must never do silently. That step lives in the
sprint_closeout procedure where the agent runs it knowingly.
"""
import os as _os0, sys as _sys0
# B4-10 / B5-01 (reviewer A rounds 4-5): Python puts this script's directory first on sys.path, so a file in .claude/hooks/
# named like a stdlib module (json.py, __future__.py, ...) would run inside this hook before any check. Drop that
# entry FIRST: no import may precede this block (not even `from __future__`, which is itself an import).
if __name__ == "__main__" and _sys0.path and \
        _os0.path.realpath(_sys0.path[0] or ".") == _os0.path.dirname(_os0.path.realpath(__file__)):
    del _sys0.path[0]
import datetime
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent.parent


def _safe_append(root: Path, rel_parts: tuple, text: str) -> None:
    """Append `text` to root/<rel_parts...> via an O_NOFOLLOW dir-fd walk: every path
    component is opened with O_NOFOLLOW, so a symlink planted at the log path (or at an
    intermediate dir) cannot redirect the write; the final target is refused if it is
    not a regular file or is hardlinked (st_nlink>1), so it cannot clobber another file
    via a hardlink. `root` is the .resolve()'d trusted anchor. Best-effort, fixed-path
    sink — the caller swallows OSError (a symlink raises ELOOP, i.e. write is skipped)."""
    import os
    import stat
    dfd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        *dirs, name = rel_parts
        for d in dirs:
            nfd = os.open(d, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=dfd)
            os.close(dfd)
            dfd = nfd
        # O_NONBLOCK so a FIFO planted at the path returns instead of hanging the open;
        # the S_ISREG check below then rejects it (parity with the adapters' safe write).
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=dfd)
        try:
            st = os.fstat(fd)
            if stat.S_ISREG(st.st_mode) and st.st_nlink == 1:
                os.write(fd, text.encode())
        finally:
            os.close(fd)
    finally:
        os.close(dfd)


def block(reason: str) -> None:
    try:
        _safe_append(ROOT, ("governance", "pilot_telemetry.log"),
                     f"{datetime.datetime.now().isoformat(timespec='seconds')} COMPILE-BLOCK {reason[:60]}\n")
    except OSError:
        pass
    print(json.dumps({"decision": "block",
                      "reason": f"COMPILE-GATE (Art. 1): {reason}"}))
    sys.exit(0)


def run(cmd: list[str], timeout: int = 30) -> tuple[int, str]:
    """Run a checker as an ARGV LIST (no shell=True) — F9 invariant: file paths flow in as
    list elements, never interpolated into a shell string, so a hostile file_path cannot
    inject a command. The checkers below are read-only (py_compile / bash -n / osacompile
    -o /dev/null) — none execute the edited file's logic. Do NOT switch this to shell=True."""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stderr or p.stdout or "").strip()[-500:]
    except subprocess.TimeoutExpired:
        return 1, f"timeout after {timeout}s"
    except FileNotFoundError:
        _disabled(f"checker {cmd[0]} not found")
        return 0, ""  # checker unavailable: never block falsely, but say so (S4-25)


def _disabled(why: str) -> None:
    print(f"COMPILE-GATE check DISABLED: {why}", file=sys.stderr)


def _python312():
    """python3.12 only (S4-07): PATH first, then the fixed install locations; None if absent."""
    for c in ("python3.12", "/usr/local/bin/python3.12", "/opt/homebrew/bin/python3.12"):
        w = shutil.which(c) if "/" not in c else (c if os.access(c, os.X_OK) else None)
        if w:
            return w
    return None


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)
    # F9: file_path comes from the hook payload; it is only ever passed to run() as an argv
    # element (never a shell string) and must be an existing regular file. Reading/compiling
    # an arbitrary path is read-only and non-executing — see run()'s no-shell invariant.
    ti = data.get("tool_input", {}) or {}
    fp = ti.get("file_path") or ti.get("notebook_path") or ""     # S4-44: NotebookEdit sends notebook_path
    path = Path(fp)
    if not fp or not path.is_file():
        sys.exit(0)

    if fp.endswith(".py"):
        py = _python312()
        if py:
            rc, err = run([py, "-m", "py_compile", fp])
            if rc != 0:
                block(f"py_compile failed for {fp}: {err}")
        else:
            _disabled("python3.12 not found (the 3.9 system python would misjudge 3.10+ syntax)")
    elif fp.endswith((".sh", ".bash")):
        rc, err = run(["bash", "-n", fp])
        if rc != 0:
            block(f"bash -n failed for {fp}: {err}")
    elif fp.endswith(".zsh"):
        if shutil.which("zsh"):
            rc, err = run(["zsh", "-n", fp])
            if rc != 0:
                block(f"zsh -n failed for {fp}: {err}")
        else:
            _disabled("zsh not found")
    elif fp.endswith((".js", ".mjs", ".cjs")):
        if shutil.which("node"):
            rc, err = run(["node", "--check", fp])
            if rc != 0:
                block(f"node --check failed for {fp}: {err}")
        else:
            _disabled("node not found")
    elif fp.endswith((".json", ".ipynb")):
        try:
            with open(fp, encoding="utf-8") as fh:
                json.load(fh)
        except ValueError as exc:
            block(f"invalid JSON in {fp}: {exc}")
        except OSError:
            pass
    elif fp.endswith(".applescript"):
        try:
            if "¬" in path.read_text(errors="replace"):
                block(f"'¬' line-continuation found in {fp} — use explicit string "
                      f"concatenation (2025 §13 lesson, encoding hazard).")
        except OSError:
            sys.exit(0)
        if shutil.which("osacompile"):
            rc, err = run(["osacompile", "-o", "/dev/null", fp], timeout=20)
            if rc != 0:
                block(f"osacompile failed for {fp}: {err}")

    # Art. 9 mechanization (M-11): validate dispatch class declarations
    if (path.name == "routing_policy.yaml" and "governance" in path.parts) or ("_skills" in path.parts and fp.endswith(".md")):
        py = _python312()
        lint_script = ROOT / "governance" / "scripts" / "lint_dispatch_class.py"
        if py and lint_script.is_file():
            rc, err = run([py, str(lint_script)])
            if rc != 0:
                block(f"Art. 9 dispatch-class lint failed:\n{err}")
        else:
            _disabled(f"Art. 9 dispatch-class lint: {'python3.12 not found' if not py else str(lint_script) + ' not found'} (S4-51)")

    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as _exc:
        # S4-25 (reviewer A round 2b re-verification): an unexpected crash used to exit 1 with a traceback. This gate
        # is fail-open (H0 advisory class): log one line to governance/hook_errors.jsonl and exit 0.
        try:
            import hashlib
            import stat
            import types
            # B5-21 (reviewer A round 5): the comparator module runs here only as approved bytes of a bounded regular file
            _p = Path(__file__).resolve().parent / "hook_support.py"
            _st = os.lstat(_p)
            _lst = (_p.parent.parent.parent / "governance" / "hooks_approved.sha256").read_text(encoding="utf-8")
            _want = next((ln.split(None, 1)[0] for ln in _lst.splitlines()
                          if ln.rstrip().endswith("  .claude/hooks/hook_support.py")), None)
            if stat.S_ISREG(_st.st_mode) and _st.st_size <= 4_000_000 and _want:
                _fd = os.open(_p, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
                try:
                    _b = os.read(_fd, 4_000_001)
                finally:
                    os.close(_fd)
                if "{}:{:o}".format(hashlib.sha256(_b).hexdigest(), _st.st_mode & 0o7777) == _want:
                    _hsm = types.ModuleType("hook_support")
                    _hsm.__file__ = str(_p)
                    exec(compile(_b, str(_p), "exec", dont_inherit=True), _hsm.__dict__)   # B3-11: source only
                    _hsm.log_error("postedit_compile_gate", _exc)
        except Exception:
            pass
        sys.exit(0)

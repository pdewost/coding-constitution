#!/usr/bin/env python3
"""postedit_compile_gate.py — COMPILE-GATE policy (POLICY_CORE.md), Claude Code adapter.

PostToolUse hook (matcher: Edit|Write|NotebookEdit). Syntax-checks the edited
file; on failure emits {"decision":"block"} so the error is fed straight back
to the agent (2025 §6.1 Compiler+ gate, mechanized).

Deliberately NOT here: Python import smoke tests — importing executes module
top-level code, which a hook must never do silently. That step lives in the
sprint_closeout procedure where the agent runs it knowingly.
"""
import datetime
import json
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
        _safe_append(ROOT, ("governance", "hook_telemetry.log"),
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
        return 0, ""  # checker unavailable: pass silently rather than block falsely


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)
    # F9: file_path comes from the hook payload; it is only ever passed to run() as an argv
    # element (never a shell string) and must be an existing regular file. Reading/compiling
    # an arbitrary path is read-only and non-executing — see run()'s no-shell invariant.
    fp = (data.get("tool_input", {}) or {}).get("file_path", "")
    path = Path(fp)
    if not fp or not path.is_file():
        sys.exit(0)

    if fp.endswith(".py"):
        py = shutil.which("python3.12") or shutil.which("python3") or ""
        if py:
            rc, err = run([py, "-m", "py_compile", fp])
            if rc != 0:
                block(f"py_compile failed for {fp}: {err}")
    elif fp.endswith(".sh"):
        rc, err = run(["bash", "-n", fp])
        if rc != 0:
            block(f"bash -n failed for {fp}: {err}")
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
    sys.exit(0)


if __name__ == "__main__":
    main()

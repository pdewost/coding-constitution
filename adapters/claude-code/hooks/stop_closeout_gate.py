#!/usr/bin/env python3
"""stop_closeout_gate.py — CLOSEOUT-GATE policy (POLICY_CORE.md), Claude Code adapter.

Stop hook. Parses the session transcript (JSONL) and blocks the turn-end ONCE
when closeout duties are visibly unmet:
  1. Code files were edited but no verification command ran (no pytest /
     py_compile / bash -n / osacompile / test invocation in any Bash call).
  2. The working project's NEOCORTEX/JOURNAL.md exceeds its 64 KB bound
     (NEOCORTEX_SPEC §2 rotation rule).

Loop safety: if stop_hook_active is set (we already blocked once this turn),
exit silently — a gate that loops is worse than no gate.
"""
import datetime
import json
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


CODE_SUFFIXES = (".py", ".sh", ".applescript", ".js", ".ts", ".html", ".css")
VERIFY_MARKERS = ("pytest", "py_compile", "bash -n", "osacompile",
                  "npm test", "python3 -m unittest", "test_", "--dry-run")


def _safe_open_transcript(tpath: str):
    """Defensively open the externally-supplied transcript path (F7): O_NOFOLLOW (refuse a
    symlinked final component) + O_NONBLOCK + regular-file-only (no /dev/* or FIFOs, so a
    special-file transcript can't make the read never end). Returns a text stream or None —
    fail-open: an unreadable/odd transcript just skips the closeout scan, never false-blocks."""
    import os
    import stat
    if not isinstance(tpath, str) or not tpath:
        return None   # F7: non-string/empty transcript_path (e.g. JSON null) -> fail-open, no crash
    try:
        fd = os.open(tpath, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            os.close(fd)
            return None
    except OSError:
        os.close(fd)
        return None
    return os.fdopen(fd, encoding="utf-8", errors="replace")


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)
    if data.get("stop_hook_active"):
        sys.exit(0)

    edited_code, verified = [], False
    tpath = data.get("transcript_path", "")
    fh = _safe_open_transcript(tpath)
    if fh is None:
        sys.exit(0)   # F7: can't safely read the transcript -> no closeout assertion (fail-open)
    try:
        read_bytes = 0
        for line in fh:
            read_bytes += len(line)
            if read_bytes > 64 * 1024 * 1024:   # F7 cap: stop after 64 MB (giant-file guard)
                break
            try:
                entry = json.loads(line)
            except Exception:
                continue
            msg = entry.get("message") or {}
            for blk in msg.get("content") or []:
                if not isinstance(blk, dict) or blk.get("type") != "tool_use":
                    continue
                name, ti = blk.get("name", ""), blk.get("input", {}) or {}
                if name in ("Edit", "Write", "NotebookEdit"):
                    fp = str(ti.get("file_path", ""))
                    if fp.endswith(CODE_SUFFIXES):
                        edited_code.append(fp)
                elif name == "Bash":
                    cmd = str(ti.get("command", ""))
                    if any(m in cmd for m in VERIFY_MARKERS):
                        verified = True
    except OSError:
        sys.exit(0)
    finally:
        fh.close()

    problems = []
    if edited_code and not verified:
        sample = ", ".join(sorted(set(edited_code))[:3])
        problems.append(
            f"code was edited ({sample}…) but no verification command ran this "
            f"session — run the relevant tests/checks, or state explicitly why "
            f"verification is impossible (Art. 1)")
    journal = Path.cwd() / "NEOCORTEX" / "JOURNAL.md"
    try:
        if journal.is_file() and journal.stat().st_size > 64 * 1024:
            problems.append(
                f"NEOCORTEX/JOURNAL.md is {journal.stat().st_size // 1024} KB "
                f"(bound: 64 KB) — rotate per NEOCORTEX_SPEC §2")
    except OSError:
        pass

    if problems:
        try:
            _safe_append(ROOT, ("governance", "hook_telemetry.log"),
                         f"{datetime.datetime.now().isoformat(timespec='seconds')} CLOSEOUT-BLOCK {problems[0][:60]}\n")
        except OSError:
            pass
        print(json.dumps({"decision": "block",
                          "reason": "CLOSEOUT-GATE: " + "; ".join(problems) + "."}))
    sys.exit(0)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""verify_advisory.py — VERIFY-GATE in ADVISORY mode (Claude Code adapter, Stop hook).

Observability only. After a code-edited turn, it checks whether an independent verification
(a valid change-set-bound VERIFY-RECEIPT) exists for the files edited THIS turn. If not, it
records PENDING to telemetry — and **never blocks, never warns the user, never fails loud.**

This is the advisory-first rollout hook: deploy via install_adapters so verify_fires can report which projects
are not yet verify-armed, with ZERO disruption to active development. A project flips to BLOCKING
only when it is explicitly armed (a wired reviewer + a proven receipt) — that is a separate,
opt-in step, NOT this hook.

Design guarantees (see governance/policy/POLICY_CORE.md VERIFY-GATE):
  - exit 0 always (advisory); the only block in the regime's verify path is the armed CLOSEOUT path.
  - change-set = the files edited this turn (from the transcript), never the whole repo.
  - fail-silent: any error => exit 0.
"""
from __future__ import annotations

import datetime
import json
import os
import sys
from pathlib import Path

# ROOT = the hook's OWN install dir (3-up from .claude/hooks/), NOT CLAUDE_PROJECT_DIR. The support
# code it imports (the verify_gate skill) and the regime telemetry live WITH the hook in the workspace
# root — so they must be resolved from __file__, never from the active project. Using CLAUDE_PROJECT_DIR
# here would (a) point at a project that has no _skills/verify_gate (the check silently never fires) and
# (b) let a malicious project plant _skills/verify_gate/scripts/receipt.py and execute it at Stop
# (CWE-94/427). The receipt DATA + the edited files are project-relative (cwd), handled below — not via ROOT.
ROOT = Path(__file__).resolve().parent.parent.parent
CODE_SUFFIXES = (".py", ".sh", ".applescript", ".js", ".ts", ".html", ".css")


def _safe_open_transcript(tpath):
    """O_NOFOLLOW + regular-file-only + O_NONBLOCK (same discipline as stop_closeout_gate)."""
    import stat
    if not isinstance(tpath, str) or not tpath:
        return None
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


def _safe_append(root: Path, rel_parts: tuple, text: str) -> None:
    """Append `text` to root/<rel_parts...> via an O_NOFOLLOW dir-fd walk (byte-identical to the
    sibling stop_closeout_gate helper): every component is opened O_NOFOLLOW so a planted symlink
    cannot redirect the write; the final target is refused if not a regular file or is hardlinked
    (st_nlink>1). Best-effort, fixed-path sink — the caller swallows OSError."""
    import stat
    dfd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        *dirs, name = rel_parts
        for d in dirs:
            nfd = os.open(d, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=dfd)
            os.close(dfd)
            dfd = nfd
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=dfd)
        try:
            st = os.fstat(fd)
            if stat.S_ISREG(st.st_mode) and st.st_nlink == 1:
                os.write(fd, text.encode())
        finally:
            os.close(fd)
    finally:
        os.close(dfd)


def _telemetry(event: str) -> None:
    ts = datetime.datetime.now().isoformat(timespec="seconds")
    try:
        _safe_append(ROOT.resolve(), ("governance", "hook_telemetry.log"), f"{ts} {event}\n")
    except OSError:
        pass


def _edited_code_files(transcript_path: str) -> list[str]:
    fh = _safe_open_transcript(transcript_path)
    if fh is None:
        return []
    edited: list[str] = []
    try:
        read = 0
        for line in fh:
            read += len(line)
            if read > 64 * 1024 * 1024:
                break
            try:
                entry = json.loads(line)
            except Exception:
                continue
            for blk in (entry.get("message") or {}).get("content") or []:
                if not isinstance(blk, dict) or blk.get("type") != "tool_use":
                    continue
                if blk.get("name") in ("Edit", "Write", "NotebookEdit"):
                    fp = str((blk.get("input") or {}).get("file_path", ""))
                    if fp.endswith(CODE_SUFFIXES) and os.path.isfile(fp):
                        edited.append(fp)
    except OSError:
        return []
    finally:
        fh.close()
    return edited


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)
    if data.get("stop_hook_active"):
        sys.exit(0)  # loop safety

    edited = _edited_code_files(data.get("transcript_path", ""))
    if not edited:
        sys.exit(0)  # no code edits this turn — nothing to verify

    # Look for a valid VERIFY-RECEIPT for exactly these files. (verify_gate is a workspace skill;
    # absence of the skill or any error => advisory PENDING, never a failure.)
    verified = False
    try:
        sys.path.insert(0, str(ROOT / "_skills" / "verify_gate" / "scripts"))
        import receipt as receipt_mod  # type: ignore
        rpath = Path(os.getcwd()) / ".verify" / "receipt.json"
        ledger = str(Path(os.getcwd()) / ".verify" / "_consumed_nonces.json")
        if rpath.is_file():
            rec = json.loads(rpath.read_text(encoding="utf-8"))
            ok, _ = receipt_mod.validate_receipt(rec, changed_paths=sorted(set(edited)), ledger_path=ledger)
            verified = bool(ok)
    except Exception:
        verified = False

    if verified:
        _telemetry(f"VERIFY-PROVEN {len(edited)} file(s)")
    else:
        # Advisory only: record PENDING, never block, never warn the user.
        _telemetry(f"VERIFY-PENDING (advisory) {len(edited)} edited, no valid receipt")
    sys.exit(0)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""verify_advisory.py — VERIFY-GATE in ADVISORY mode (Claude Code adapter, Stop hook).

Observability only. After a code-edited turn, it checks whether an independent verification
(a valid change-set-bound VERIFY-RECEIPT) exists for the files edited THIS turn. If not, it
records PENDING to telemetry — and **never blocks, never warns the user, never fails loud.**

This is the advisory-first rollout hook: deploy it via install_adapters with ZERO disruption to active development.
(verify_fires.py proves SessionStart only; it says nothing about this Stop hook.) A project flips to BLOCKING
only when it is explicitly armed (a wired reviewer + a proven receipt) — that is a separate,
opt-in step, NOT this hook.

Design guarantees (see spec/POLICY_CORE.md VERIFY-GATE):
  - exit 0 always (advisory); the only block in the regime's verify path is the armed CLOSEOUT path.
  - change-set = the files edited this turn (from the transcript), never the whole repo.
  - fail-silent: any error => exit 0.
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


def _real_tool_use_ids(transcript_path: str) -> set:
    """v0.7.3: every tool_use id in a record whose type is not "api-request-blob" (bounded, safe open, fail-open to empty)."""
    ids: set = set()
    fh = _safe_open_transcript(transcript_path)
    if fh is None:
        return ids
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
            if not isinstance(entry, dict) or entry.get("type") == "api-request-blob":
                continue
            for blk in (entry.get("message") or {}).get("content") or []:
                if isinstance(blk, dict) and blk.get("type") == "tool_use" and blk.get("id"):
                    ids.add(blk["id"])
    except OSError:
        pass
    finally:
        fh.close()
    return ids


def _edited_code_files(transcript_path: str) -> list[str]:
    fh = _safe_open_transcript(transcript_path)
    if fh is None:
        return []
    edited: list[str] = []
    real_ids = _real_tool_use_ids(transcript_path)      # v0.7.3: a blob copy is skipped only when a real record holds its call
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
            is_blob = isinstance(entry, dict) and entry.get("type") == "api-request-blob"
            for blk in (entry.get("message") or {}).get("content") or []:
                if not isinstance(blk, dict) or blk.get("type") != "tool_use":
                    continue
                if is_blob and blk.get("id") in real_ids:
                    continue                   # v0.7.2/3: a stored copy of a tool_use the real records hold is not a second edit
                # an unmatched blob tool_use is uncertain: counted like a real edit (advisory only; over-inclusion never blocks)
                if blk.get("name") in ("Edit", "Write", "NotebookEdit"):
                    fp = str((blk.get("input") or {}).get("file_path", ""))
                    if fp.endswith(CODE_SUFFIXES) and os.path.isfile(fp):
                        edited.append(fp)
    except OSError:
        return []
    finally:
        fh.close()
    return edited


def _load_hook_support():
    """hook_support.py from its approved bytes (lstat, regular, bounded, no link, no blocking open; exec from those bytes,
    never the importer), the way stop_closeout_gate.py's `_load_sibling` loads it. The verify_gate loader and the
    armed-ness probe live there (GF-1), so this hook carries no second copy of either. Raises on any refusal."""
    import hashlib
    import stat
    import types
    rel = ".claude/hooks/hook_support.py"
    path = ROOT / rel
    st = os.lstat(path)
    if not stat.S_ISREG(st.st_mode) or st.st_size > 4_000_000:
        raise RuntimeError("hook_support.py: not a regular file of bounded size")
    want = None
    for ln in (ROOT / "governance" / "hooks_approved.sha256").read_text(encoding="utf-8", errors="replace").splitlines():
        parts = ln.strip().split(None, 1)
        if len(parts) == 2 and parts[1].strip() == rel:
            want = parts[0]
    if want is None:
        raise RuntimeError("hook_support.py: no approved entry")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise RuntimeError("hook_support.py: changed type while opening")
        chunks, total = [], 0
        while total <= 4_000_000:
            b = os.read(fd, 65536)
            if not b:
                break
            chunks.append(b)
            total += len(b)
    finally:
        os.close(fd)
    data = b"".join(chunks)
    if "{}:{:o}".format(hashlib.sha256(data).hexdigest(), st.st_mode & 0o7777) != want:
        raise RuntimeError("hook_support.py: bytes differ from the approved list")
    mod = types.ModuleType("hook_support")
    mod.__file__ = str(path)
    exec(compile(data, str(path), "exec", dont_inherit=True), mod.__dict__)
    return mod


def _advisory_outcome(edited: list):
    """(verified, reason) for this turn, GF-1(d)'s advisory column. Never raises, never blocks. `reason` is "" when the
    plain PENDING text applies (a receipt is missing or not valid at an armed-or-not root), else the table's text."""
    try:
        hs = _load_hook_support()
    except BaseException as exc:
        if isinstance(exc, KeyboardInterrupt):
            raise
        return False, "verify_gate script failed: hook_support: %s: %s" % (type(exc).__name__, str(exc)[:160])
    res = hs.vg_resolve(ROOT, edited)
    if not res["ok"]:
        try:
            hs.vg_probe(edited)
        except BaseException as exc:
            if isinstance(exc, KeyboardInterrupt):
                raise
            return False, "verify_gate armed state unreadable"
        return False, res["text"]
    root = res["root"]
    if root is None:
        return False, res["reason"]       # "no project root" or "ambiguous project root"
    try:
        rpath = root / ".verify" / "receipt.json"
        ledger = str(root / ".verify" / "_consumed_nonces.json")
        if not rpath.is_file():
            return False, ""
        rec = json.loads(rpath.read_text(encoding="utf-8"))
        ok, _why = res["mods"]["receipt"].validate_receipt(rec, changed_paths=sorted(set(edited)), ledger_path=ledger)
        return bool(ok), ""
    except BaseException as exc:
        if isinstance(exc, KeyboardInterrupt):
            raise
        return False, "verify_gate script failed: receipt: %s: %s" % (type(exc).__name__, str(exc)[:160])


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

    # Look for a valid VERIFY-RECEIPT for exactly these files, at the project root that receipt.project_root names from
    # the edited paths (never the working directory, GF-2(c)). Any failure => advisory PENDING, never a failure.
    verified, reason = _advisory_outcome(edited)

    if verified:
        _telemetry(f"VERIFY-PROVEN {len(edited)} file(s)")
    else:
        # Advisory only: record PENDING, never block, never warn the user.
        _telemetry(f"VERIFY-PENDING (advisory) {len(edited)} edited, no valid receipt" + (f": {reason}" if reason else ""))
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except BaseException as _exc:         # fail-open like the Stop hook: no exception exits non-zero (GF-1(c))
        if isinstance(_exc, KeyboardInterrupt):
            raise
        try:
            _telemetry(f"VERIFY-PENDING (advisory) hook error: {type(_exc).__name__}")
        except BaseException:
            pass
        sys.exit(0)

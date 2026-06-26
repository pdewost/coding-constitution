#!/usr/bin/env python3
"""pretool_guard.py — DENY-ARCHIVE, DENY-CONTACT-DELETE, DENY-WINDOW policies
(POLICY_CORE.md), Claude Code adapter.

PreToolUse hook (matchers: Bash, mcp__Control_your_Mac__osascript).
stdin: JSON payload; command text is tool_input.command (Bash) or
tool_input.script / tool_input.code (osascript MCP variants).
Deny via hookSpecificOutput.permissionDecision; silent exit 0 otherwise.

Forbidden-window data comes from governance/machine_config.yaml — values are
parsed from that file, never hardcoded here (POLICY_CORE rule). Parsing is
line-based on the known keys so this script has no third-party dependencies.
"""
import datetime
import json
import re
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


def _safe_read(root: Path, rel_parts: tuple):
    """Read root/<rel_parts...> via an O_NOFOLLOW dir-fd walk (symlinked components and
    non-regular targets refused). Returns the text, or None on any error/refusal — so a
    symlink planted at the path cannot trick the hook into opening an unrelated file."""
    import os
    import stat
    try:
        dfd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    except OSError:
        return None
    try:
        *dirs, name = rel_parts
        for d in dirs:
            nfd = os.open(d, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=dfd)
            os.close(dfd)
            dfd = nfd
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dfd)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                return None
            chunks = []
            while True:
                chunk = os.read(fd, 65536)
                if not chunk:
                    break
                chunks.append(chunk)
            return b"".join(chunks).decode(errors="replace")
        finally:
            os.close(fd)
    except OSError:
        return None
    finally:
        try:
            os.close(dfd)
        except OSError:
            pass

def _telemetry(event: str) -> None:
    import datetime
    try:
        _safe_append(ROOT, ("governance", "hook_telemetry.log"),
                     f"{datetime.datetime.now().isoformat(timespec='seconds')} {event}\n")
    except OSError:
        pass


# Commands considered "heavy extraction/vectorization" for DENY-WINDOW.
# Adopters: extend this pattern with your own heavy / long-running command names.
EXTRACTION_RE = re.compile(
    r"extract|ingest|vectoriz|embed|chromadb|reindex|bulk_",
    re.IGNORECASE,
)
# Deny destruction of archives. Layer 1: rm/rmdir/unlink/shred touching them directly.
ARCHIVE_RM_RE = re.compile(
    r"(^|[;&|\s])(rm|rmdir|unlink|shred)\s[^;&|]*(_archive|ANTIGRAVITY-2025\.md)"
)
# Layer 2 (F4): find/xargs deletion bypasses a literal `rm <archive>` match —
#   find _archive -delete | find . -path '*_archive*' -exec rm {} + | find _archive | xargs rm
ARCHIVE_FIND_RE = re.compile(
    r"\bfind\b[^;&|]*(_archive|ANTIGRAVITY-2025\.md)[^;&|]*(-delete\b|-exec(dir)?\s+(rm|unlink|shred|trash)\b)"
    r"|(_archive|ANTIGRAVITY-2025\.md)[^;&|]*\|\s*xargs\b[^;&|]*\b(rm|unlink|shred)\b",
    re.IGNORECASE,
)
# mv whose SOURCE (first non-flag arg) is an archive == moving OUT. (F5) Handles quoted
# args containing spaces, and the bare `_archive` directory with OR without a trailing
# slash (`_archive(?:/|\b)`, matching rm/find which catch the bare token). The archive
# token must be followed by whitespace (a further dest arg) so that moving INTO _archive
# (archive as the final DEST, e.g. `mv x.md _archive`) stays allowed.
ARCHIVE_MV_OUT_RE = re.compile(
    r"(^|[;&|\s])mv\s+(-\S+\s+)*"
    r'("[^"]*(?:_archive(?:/|\b)|ANTIGRAVITY-2025\.md)[^"]*"'      # "double-quoted source"
    r"|'[^']*(?:_archive(?:/|\b)|ANTIGRAVITY-2025\.md)[^']*'"       # 'single-quoted source'
    r"|\S*(?:_archive(?:/|\b)|ANTIGRAVITY-2025\.md)\S*)"             # unquoted source
    r"\s",                                                            # trailing WS => a dest follows => archive is the SOURCE
    re.IGNORECASE,
)


def deny(reason: str) -> None:
    _telemetry(f"PRETOOL-DENY {reason[:60]}")
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }))
    sys.exit(0)


def forbidden_window() -> str | None:
    """Return the window string if now is inside a machine_config window."""
    text = _safe_read(ROOT, ("governance", "machine_config.yaml"))
    if text is None:
        return None
    m = re.search(r'window:\s*"(\d{2}):(\d{2})-(\d{2}):(\d{2})"', text)
    if not m:
        return None
    start = int(m.group(1)) * 60 + int(m.group(2))
    end = int(m.group(3)) * 60 + int(m.group(4))
    now = datetime.datetime.now()
    cur = now.hour * 60 + now.minute
    inside = (start <= cur or cur < end) if start > end else (start <= cur < end)
    return f"{m.group(1)}:{m.group(2)}-{m.group(3)}:{m.group(4)}" if inside else None


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)
    ti = data.get("tool_input", {}) or {}
    cmd = " ".join(str(ti.get(k, "")) for k in ("command", "script", "code"))
    if not cmd.strip():
        sys.exit(0)

    # DEFENCE-IN-DEPTH, not a hard sandbox: this matches literal command text only.
    # Shell variable indirection (e.g. OP=person; delete $OP), Unicode homoglyphs, and
    # multi-step command composition are NOT detected. Do not over-rely on this guard.
    if re.search(r"delete\s+(person|people|every\s+person)", cmd, re.IGNORECASE):
        deny("DENY-CONTACT-DELETE (Art. 4 — irreversible contact-data loss): "
             "AppleScript 'delete person' is forbidden. Use group-membership "
             "changes or ask the user to delete manually.")

    # DEFENCE-IN-DEPTH, not a complete sandbox: catches the common direct, find/xargs,
    # and quoted-mv forms — a determined shell can still evade (variables, globs, `mv -t`,
    # base64/eval). Art. 6's real protection: the user performs genuine archive ops manually.
    if ARCHIVE_RM_RE.search(cmd) or ARCHIVE_FIND_RE.search(cmd) or ARCHIVE_MV_OUT_RE.search(cmd):
        deny("DENY-ARCHIVE (Art. 6): archives (_archive/, ANTIGRAVITY-2025.md) are "
             "never deleted or moved OUT by an agent (moving INTO _archive/ is "
             "fine). If genuinely intended, the user performs it manually.")

    window = forbidden_window()
    if window and EXTRACTION_RE.search(cmd):
        deny(f"DENY-WINDOW (machine_config.yaml): extraction/vectorization is "
             f"forbidden during {window} (per your machine_config.yaml). Defer or "
             f"ask the user to override manually.")

    sys.exit(0)


if __name__ == "__main__":
    main()

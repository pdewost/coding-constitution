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
import shlex
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
        _safe_append(ROOT, ("governance", "pilot_telemetry.log"),
                     f"{datetime.datetime.now().isoformat(timespec='seconds')} {event}\n")
    except OSError:
        pass


# Commands considered "heavy extraction/vectorization" for DENY-WINDOW.
EXTRACTION_RE = re.compile(
    r"unified_extractor|unified_sync|vectoriz|chromadb|bulk_ingest|build_relationship_edges",
    re.IGNORECASE,
)
# Deny destruction of archives. Layer 1: rm/rmdir/unlink/shred touching them directly.
ARCHIVE_RM_RE = re.compile(
    r"(^|[;&|\s])(rm|rmdir|unlink|shred)\s[^;&|]*(_archive|ANTIGRAVITY-2025\.md|PAICodeConstitution-2026\.md)"
)
# Layer 2 (F4): find/xargs deletion bypasses a literal `rm <archive>` match —
#   find _archive -delete | find . -path '*_archive*' -exec rm {} + | find _archive | xargs rm
ARCHIVE_FIND_RE = re.compile(
    r"\bfind\b[^;&|]*(_archive|ANTIGRAVITY-2025\.md|PAICodeConstitution-2026\.md)[^;&|]*(-delete\b|-exec(dir)?\s+(rm|unlink|shred|trash)\b)"
    r"|(_archive|ANTIGRAVITY-2025\.md|PAICodeConstitution-2026\.md)[^;&|]*\|\s*xargs\b[^;&|]*\b(rm|unlink|shred)\b",
    re.IGNORECASE,
)
# mv whose SOURCE (first non-flag arg) is an archive == moving OUT. (F5) Handles quoted
# args containing spaces, and the bare `_archive` directory with OR without a trailing
# slash (`_archive(?:/|\b)`, matching rm/find which catch the bare token). The archive
# token must be followed by whitespace (a further dest arg) so that moving INTO _archive
# (archive as the final DEST, e.g. `mv x.md _archive`) stays allowed.
ARCHIVE_MV_OUT_RE = re.compile(
    r"(^|[;&|\s])mv\s+(-\S+\s+)*"
    r'("[^"]*(?:_archive(?:/|\b)|ANTIGRAVITY-2025\.md|PAICodeConstitution-2026\.md)[^"]*"'      # "double-quoted source"
    r"|'[^']*(?:_archive(?:/|\b)|ANTIGRAVITY-2025\.md|PAICodeConstitution-2026\.md)[^']*'"       # 'single-quoted source'
    r"|\S*(?:_archive(?:/|\b)|ANTIGRAVITY-2025\.md|PAICodeConstitution-2026\.md)\S*)"             # unquoted source
    r"\s",                                                            # trailing WS => a dest follows => archive is the SOURCE
    re.IGNORECASE,
)

# P0.0a -- L0 WRITE-GUARD (PLAN_lifecycle_routing_evolution_2026-08-24.md).
# The three regexes above cover DELETION and move-OUT. They do NOT cover OVERWRITE, which is
# the form the L0 invariant actually fears: "never edited mid-session without founder consent".
# The `.claude/settings.json` deny entries cover the Edit/Write TOOLS; this covers the shell,
# and the shell is the gap that matters -- in auto mode file changes go through Bash heredocs,
# so a write-guard blind to `> file` would be inert in the workspace's own default mode.
#
# WHY THIS IS TOKENIZED AND NOT A REGEX, measured 2026-08-26. A character-class regex
# (`>>?\\s*[^\\s;&|]*<file>`) passes 28 of 29 cases and fails the one that matters: the
# PUBLISHED CLONE's path contains SPACES, so `> "GitHub pdewost repositories/.../L0"` slips
# straight through -- half the rule's stated scope, silently. Widening the class to allow
# quoted spans then re-admits `grep ">" L0` as a false deny, because a regex cannot tell a
# redirect operator from a `>` inside a quoted PATTERN. Same class as the F5 quoted-`mv`
# bug this file already carries a fix for. shlex(posix=False) keeps the quotes ON the token,
# which is exactly the distinction the regex could not make.
#
# READS MUST STAY ALLOWED. The cold start reads L0 every session, so only WRITE TARGETS
# match: `cat L0`, `grep x L0`, `wc -l L0`, `cat L0 > copy.md` all stay silent.
#
# Matched by BASENAME, so both live copies are covered by one rule: the workspace-root
# authoritative L0 and the published clone. Deliberately fail-CLOSED on ambiguity.
L0_BASENAME = "PAICodeConstitution-2026.md"
# Partial net for a command shlex cannot parse (unbalanced quotes). Catches the common
# UNQUOTED forms only -- stated as a limitation, not advertised as equivalent cover.
L0_WRITE_FALLBACK_RE = re.compile(
    r">>?\s*[^\s;&|]*PAICodeConstitution-2026\.md"
    r"|\b(sed|tee|truncate|install|dd|cp|mv|rsync)\b[^;&|]*PAICodeConstitution-2026\.md",
    re.IGNORECASE,
)
# Verbs whose LAST non-flag argument is a destination that gets overwritten.
_DEST_VERBS = {"cp", "mv", "rsync", "install"}
# Verbs that write to a named file wherever it appears in their arguments.
_NAMED_WRITE_VERBS = {"tee", "truncate"}


def _unquote(tok: str) -> str:
    if len(tok) >= 2 and tok[0] == tok[-1] and tok[0] in "\"'":
        return tok[1:-1]
    return tok


def _is_l0(tok: str) -> bool:
    return _unquote(tok).rstrip("/").split("/")[-1].lower() == L0_BASENAME.lower()


def l0_write_target(cmd: str) -> bool:
    """True if `cmd` writes to, overwrites or truncates a live copy of L0.

    Reads return False by construction: the file must appear as a redirect target, as a
    write verb's operand, or as a copy destination -- never merely as an argument.
    """
    try:
        lx = shlex.shlex(cmd, posix=False, punctuation_chars=True)
        lx.whitespace_split = True
        toks = list(lx)
    except ValueError:
        return bool(L0_WRITE_FALLBACK_RE.search(cmd))

    # Split into pipeline/sequence segments so each segment has its own leading verb.
    segments, cur = [], []
    for tok in toks:
        if tok in (";", "|", "||", "&&", "&"):
            segments.append(cur)
            cur = []
        else:
            cur.append(tok)
    segments.append(cur)

    for seg in segments:
        # 1. Redirection: a BARE `>`/`>>` operator (a quoted one keeps its quotes and so
        #    never compares equal) whose target is a live copy of L0.
        for i, tok in enumerate(seg):
            if tok in (">", ">>") and i + 1 < len(seg) and _is_l0(seg[i + 1]):
                return True
        words = [w for w in seg if w not in (">", ">>", "<", "2>", "&>")]
        if not words:
            continue
        verb = _unquote(words[0]).split("/")[-1].lower()
        args = words[1:]
        # 2. `sed -i` edits in place; the file is an operand, not a redirect target.
        if verb == "sed" and any(a.startswith("-i") for a in args) and any(_is_l0(a) for a in args):
            return True
        # 3. Verbs that write a file named anywhere in their arguments.
        if verb in _NAMED_WRITE_VERBS and any(_is_l0(a) for a in args):
            return True
        # 4. `dd of=<L0>`.
        if verb == "dd" and any(a.lower().startswith("of=") and _is_l0(_unquote(a)[3:]) for a in args):
            return True
        # 5. Copy/move verbs: the LAST non-flag argument is the destination.
        if verb in _DEST_VERBS:
            operands = [a for a in args if not a.startswith("-")]
            if operands and _is_l0(operands[-1]):
                return True
    return False


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
        deny("DENY-CONTACT-DELETE (Art. 4 / the 2026-03 contact-deletion incident): AppleScript "
             "'delete person' is forbidden. Use group-membership changes or ask "
             "the user to delete manually.")

    # DEFENCE-IN-DEPTH, not a complete sandbox: catches the common direct, find/xargs,
    # and quoted-mv forms — a determined shell can still evade (variables, globs, `mv -t`,
    # base64/eval). Art. 6's real protection: the user performs genuine archive ops manually.
    if l0_write_target(cmd):
        deny("DENY-ARCHIVE / L0 WRITE-GUARD (Constitution invariant 1): "
             "PAICodeConstitution-2026.md is L0 and is never edited in place by an agent -- "
             "the founder owns every lift. Reads are fine. If a constitutional change is "
             "genuinely intended, record the consent in NEOCORTEX/JOURNAL.md FIRST (Art. 6) "
             "and have the founder make the edit.")

    if ARCHIVE_RM_RE.search(cmd) or ARCHIVE_FIND_RE.search(cmd) or ARCHIVE_MV_OUT_RE.search(cmd):
        deny("DENY-ARCHIVE (Art. 6): archives (_archive/, ANTIGRAVITY-2025.md) are "
             "never deleted or moved OUT by an agent (moving INTO _archive/ is "
             "fine). If genuinely intended, the user performs it manually.")

    window = forbidden_window()
    if window and EXTRACTION_RE.search(cmd):
        deny(f"DENY-WINDOW (machine_config.yaml): extraction/vectorization is "
             f"forbidden during {window} (02:40 LaunchAgent collision). Defer or "
             f"ask the user to override manually.")

    sys.exit(0)


if __name__ == "__main__":
    main()

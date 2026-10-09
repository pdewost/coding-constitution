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
import re
import shlex
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent

# Sibling modules (PLAN_hooks_outcome_tamper_loopbound_2026-10-05). Each is optional: a missing or
# broken one must never take the DENY guards down with it, so its import failure is only recorded.
_PRELOAD_ISSUES = []


class _NotApproved(Exception):
    """A sibling whose bytes differ from the approved list: reported, never executed (reviewer A B4-07)."""


def _approved_entry(rel: str):
    root = Path(__file__).resolve().parent.parent.parent
    try:
        lst = (root / "governance" / "hooks_approved.sha256").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for ln in lst.splitlines():
        parts = ln.strip().split(None, 1)
        if len(parts) == 2 and parts[1].strip() == rel:
            return parts[0]
    return None


def _load_sibling(name: str):
    """Load a sibling hook module FROM ITS SOURCE and register it (reviewer A B3-11: a cached .pyc never runs in its
    place). B4-07 (reviewer A round 4): the exact bytes are checked against the approved list BEFORE they run; a
    mismatch is recorded in _PRELOAD_ISSUES and the module is NOT executed. B5-02 (reviewer A round 5): only a regular
    file of bounded size is opened, without following a link and without blocking (a FIFO, a device or a huge
    file is reported, never read). B5-30: NO approved entry (list missing, unreadable, or without this file) is a
    refusal too: a damaged trust anchor never means "run it unchecked"."""
    import hashlib
    import os as _o
    import stat as _st
    import types
    path = Path(__file__).resolve().parent / (name + ".py")
    rel = ".claude/hooks/" + name + ".py"
    try:
        st = _o.lstat(path)
    except OSError:
        raise
    if not _st.S_ISREG(st.st_mode) or st.st_size > 4_000_000:
        _PRELOAD_ISSUES.append(f"{rel}: not a regular file of bounded size (not executed)")
        raise _NotApproved(rel)
    want = _approved_entry(rel)
    if want is None:
        _PRELOAD_ISSUES.append(f"{rel}: no approved entry (governance/hooks_approved.sha256 is missing, unreadable or "
                               f"has no line for this file), so it was not executed")
        raise _NotApproved(rel)
    fd = _o.open(path, _o.O_RDONLY | getattr(_o, "O_NOFOLLOW", 0) | getattr(_o, "O_NONBLOCK", 0))
    try:
        if not _st.S_ISREG(_o.fstat(fd).st_mode):
            _PRELOAD_ISSUES.append(f"{rel}: changed type while opening (not executed)")
            raise _NotApproved(rel)
        chunks, total = [], 0
        while total <= 4_000_000:
            b = _o.read(fd, 65536)
            if not b:
                break
            chunks.append(b)
            total += len(b)
    finally:
        _o.close(fd)
    data = b"".join(chunks)
    have = "{}:{:o}".format(hashlib.sha256(data).hexdigest(), st.st_mode & 0o7777)
    if have != want:
        _PRELOAD_ISSUES.append(f"{rel}: approved {want[:12]}, live {have[:12]} (not executed)")
        raise _NotApproved(rel)
    mod = types.ModuleType(name)
    mod.__file__ = str(path)
    sys.modules[name] = mod
    try:
        exec(compile(data, str(path), "exec", dont_inherit=True), mod.__dict__)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return mod


# B4-18 (reviewer A round 4): siblings load LAZILY and checked (above), never before the DENY matchers: hook_support only
# when a governor basename list or an error log needs it, loop_bound (and verifier_recognizer) only on the H9 path.
# A module global set to None (the replay does this) disables that sibling.
_LAZY = object()
_IMPORT_ERRORS = []
hook_support = loop_bound = verifier_recognizer = _LAZY


def _sibling(name: str):
    cur = globals().get(name, _LAZY)
    if cur is not _LAZY:
        return cur
    globals()[name] = None                             # no re-entry while loading (an error log may ask for it)
    if name == "loop_bound" and _sibling("verifier_recognizer") is None:
        return None                                    # B4-35: loop_bound must never import verifier_recognizer itself
    try:
        mod = _load_sibling(name)
    except BaseException as exc:
        if isinstance(exc, KeyboardInterrupt):
            raise
        _IMPORT_ERRORS.append((name, exc))
        if name != "hook_support":
            _log_error(f"pretool_guard.import.{name}", exc)
        return None
    globals()[name] = mod
    return mod


def _log_error(where: str, exc: BaseException) -> None:
    try:
        hs = _sibling("hook_support")
        if hs is not None:
            hs.log_error(where, exc, ROOT)
        else:
            _telemetry(f"HOOK-ERROR {where} {type(exc).__name__}")
    except Exception:
        pass                                   # logging must never become the failure (Seat A F16)


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
# S4-46 (final, 2026-10-05): the window also blocks plainly named heavy jobs (embed_corpus, ingest_all,
# --rebuild-index), matched on the EXECUTED script/module/verb names and flags (not on any text, so
# `grep embedding notes.md` stays free), plus any `extraction_patterns:` regex listed in machine_config.yaml.
EXTRACTION_NAME_RE = re.compile(r"embed|ingest|reindex|rebuild[-_]?index|vectori[sz]",
                                re.IGNORECASE)


def _is_extraction_cmd(cmd: str) -> bool:
    # B5-33 (reviewer A round 5): the legacy patterns too are matched on the executed job (names, `-m`, `-c` code), never
    # on read operands (`rg -n chromadb pyproject.toml`, `cat docs/vectorization_plan.md`).
    extra = []
    text = _safe_read(ROOT, ("governance", "machine_config.yaml")) or ""
    for m in re.finditer(r"^\s*-\s*extraction_pattern:\s*\"?([^\"\n]+)\"?\s*$", text, re.M):
        try:
            extra.append(re.compile(m.group(1), re.IGNORECASE))
        except re.error:
            continue
    try:
        segs = _scoped_segments(cmd, "")
    except Exception:
        segs = [(cmd.split(), [], "", None)]
    for words, _t, _h, _i in segs:
        w = _strip_wrappers_guard(list(words))
        if not w:
            continue
        names = [_unquote(w[0]).split("/")[-1]]
        script, sargs = _executed_script(w)
        if script:
            names.append(script)
        for opt in ("-m", "-c"):
            if opt in w and _INTERPRETER_RE.match(names[0].lower()):
                k = w.index(opt)
                if k + 1 < len(w):
                    names.append(_unquote(w[k + 1]))
        names += [a for a in sargs if a.startswith("--")]
        for n in names:
            if EXTRACTION_NAME_RE.search(n) or EXTRACTION_RE.search(n) or any(rx.search(n) for rx in extra):
                return True
    return False
# Deny destruction of archives. Layer 1: rm/rmdir/unlink/shred touching them directly.
ARCHIVE_RM_RE = re.compile(
    # B4-33 (reviewer A round 4): not after `git` (index-only `git rm --cached` is decided by the parsed check
    # `archive_parsed_hit`, which also denies a real `git rm` and catches `/bin/rm`, `\rm`, `(rm ...)`, B4-17).
    r"(^|[;&|]|(?<!\bgit)\s)(rm|rmdir|unlink|shred|trash|gio\s+trash)\s[^;&|]*"
    r"(_archive|ANTIGRAVITY-2025\.md|PAICodeConstitution-2026\.md)"
)
# Layer 2 (F4): find/xargs deletion bypasses a literal `rm <archive>` match —
#   find _archive -delete | find . -path '*_archive*' -exec rm {} + | find _archive | xargs rm
ARCHIVE_FIND_RE = re.compile(
    r"\bfind\b[^;&|]*(_archive|ANTIGRAVITY-2025\.md|PAICodeConstitution-2026\.md)[^;&|]*(-delete\b|-exec(dir)?\s+(rm|unlink|shred|trash)\b)"
    r"|(_archive|ANTIGRAVITY-2025\.md|PAICodeConstitution-2026\.md)[^;&|]*\|\s*xargs\b[^;&|]*\b(rm|unlink|shred)\b",
    re.IGNORECASE,
)
# Layer 3 (S4-14/-19/-13): the destructive verb need not name the archive on its own line.
#   cd NEOCORTEX/_archive && rm -rf old | rsync -a --delete /tmp/empty/ _archive/ | git clean -fdx _archive
_ARCH = r"(_archive|ANTIGRAVITY-2025\.md|PAICodeConstitution-2026\.md)"
_NOT_UP = r"(?!/\.\.(?:[/\s;&|]|$))"      # G6-02 (Gemini round 6): `cd <archive>/..` leaves the archive
ARCHIVE_CD_RM_RE = re.compile(
    r"\bcd\s+\S*" + _ARCH + _NOT_UP + r"\S*\s*(;|&&|\|\|)\s*(sudo\s+)?(rm|rmdir|unlink|shred|trash)\b"
    r"|\bcd\s+\S*" + _ARCH + _NOT_UP + r"\S*\s*(;|&&|\|\|)[^;&|]*\bfind\b[^;&|]*-delete\b",
    re.IGNORECASE,
)
ARCHIVE_SYNC_CLEAN_RE = re.compile(
    r"\brsync\b[^;&|]*--delete[^;&|]*" + _ARCH
    + r"|\bgit\s+clean\b[^;&|]*" + _ARCH,
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
# PUBLISHED CLONE's path contains SPACES, so `> "GitHub example repositories/.../L0"` slips
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
# 2026-10-06 (conformance s1e, Coding Constitution session): the fallback must still tell a READ from a write: copy
# verbs only when L0 is the segment's LAST word (the destination), sed only with -i/--in-place, dd only as `of=`.
# B5-08 (reviewer A round 5): "last word" ignores trailing redirections and options (`cp x L0 2>/dev/null`, `install x L0 -v`).
L0_WRITE_FALLBACK_RE = re.compile(
    r">>?\s*[^\s;&|]*PAICodeConstitution-2026\.md"
    r"|\b(tee|truncate|patch|ed|ex)\b[^;&|]*PAICodeConstitution-2026\.md"
    r"|\bsed\b[^;&|]*\s(-[A-Za-z]*i|--in-place)[^;&|]*PAICodeConstitution-2026\.md"
    r"|\bdd\b[^;&|]*\bof=[^\s;&|]*PAICodeConstitution-2026\.md"
    r"|\b(cp|mv|rsync|install|ditto|ln)\b[^;&|]*\s['\"]?[^\s;&|]*PAICodeConstitution-2026\.md['\"]?"
    r"(?:[ \t]+(?:[0-9]*[<>]{1,2}&?[^\s;&|]*|-[^\s;&|]*))*[ \t]*(?=$|[;&|\n])",   # B5-08: `2>/dev/null`, `-v` after it
    re.IGNORECASE | re.MULTILINE,
)

# B2-23 (reviewer A round 2b, 2026-10-06; PLAN §3a "DENY path"): the patterns above scan `[^;&|]*` from every
# verb they meet, so on a long command with no match they are QUADRATIC (81 KB of `cp ` took 12 s in the
# L0 fallback alone, past the hook's 10 s timeout, and a timeout proceeds). On LARGE input every DENY-path
# regex now runs only on bounded windows around a literal token found by a linear search: the token's
# shell segment plus the one before and the one after, each cut at _WIN characters (one regex call on a
# window is then bounded). Every match of these patterns contains the token and lies in its own segment,
# or reaches into the next one (`cd <archive>; rm`, `| xargs rm`); the previous segment only serves a
# quoted operand holding a separator. So the scan is INCOMPLETE when the token's own or next segment was
# cut, or when the whole scan passes _WIN_BUDGET seconds; the caller then ASKS (never allows). The
# oversize path asks anyway.
_LARGE_INPUT = 8_192          # below this the full regexes run exactly as before (measured < 0.2 s)
_WIN = 4_096
_WIN_BUDGET = 1.5             # seconds for all windows of one command (hook timeout: 10 s)
_WIN_SEPS = ";&|\n"
_WS_RE = re.compile(r"\s")
ARCHIVE_TOKEN_RE = re.compile(r"_archive|ANTIGRAVITY-2025\.md|PAICodeConstitution-2026\.md", re.IGNORECASE)
L0_TOKEN_RE = re.compile(r"PAICodeConstitution-2026\.md", re.IGNORECASE)


def _rsep(text: str, lo: int, hi: int) -> int:
    return max(text.rfind(c, lo, hi) for c in _WIN_SEPS)


def _fsep(text: str, lo: int, hi: int) -> int:
    found = [k for k in (text.find(c, lo, hi) for c in _WIN_SEPS) if k != -1]
    return min(found) if found else -1


def _window(text: str, i: int, j: int) -> tuple:
    """(start, end, complete) of the previous + own + next segment around text[i:j], each <= _WIN chars.
    complete is False when the token's OWN or NEXT segment had to be cut."""
    n, complete, start, pos = len(text), True, 0, i
    for step in range(2):                       # own segment start, then the previous segment's start
        lo = max(0, pos - _WIN)
        k = _rsep(text, lo, pos)
        if k == -1:
            if lo > 0:
                complete = complete and step == 1
                m = _WS_RE.search(text, lo, pos)   # never start a window mid-word (`farm` -> `rm`)
                start = m.start() if m else pos
            else:
                start = 0
            break
        start, pos = k + 1, k
        while pos > 0 and (text[pos - 1] in _WIN_SEPS or text[pos - 1].isspace()):
            pos -= 1
    end, pos = n, j
    for _ in range(2):                          # own segment end, then the next segment's end
        hi = min(n, pos + _WIN)
        k = _fsep(text, pos, hi)
        if k == -1:
            complete = complete and hi >= n
            end = hi
            break
        end, pos = k, k
        while pos < n and (text[pos] in _WIN_SEPS or text[pos].isspace()):
            pos += 1
    return start, max(end, j), complete


def _bounded_search(regexes, text: str, token_rx, quote_aware: bool = False) -> tuple:
    """(first match of any regex, or None; complete). Small input: the full regexes on the whole text."""
    if len(text) <= _LARGE_INPUT:
        for rx in regexes:
            m = rx.search(text)
            if m:
                return m, True
        return None, True
    import time
    t0, complete, prev = time.monotonic(), True, (-1, -1)
    for m in token_rx.finditer(text):
        if time.monotonic() - t0 > _WIN_BUDGET:
            return None, False
        a, b, ok = _window(text, m.start(), m.end())
        if quote_aware and ok:
            o1 = _rsep(text, max(0, m.start() - _WIN), m.start()) + 1
            o2 = _fsep(text, m.end(), min(len(text), m.end() + _WIN))
            own = text[o1:(o2 if o2 != -1 else len(text))]
            if own.count('"') % 2 or own.count("'") % 2:
                ok = False                              # B3-14: a separator inside quotes cut the token's segment
        complete = complete and ok
        if prev[0] <= a and b <= prev[1]:       # inside the window just scanned: nothing new to see
            continue
        prev = (a, b)
        w = text[a:b]
        for rx in regexes:
            hit = rx.search(w)
            if hit:
                return hit, complete
    return None, complete


_ARCHIVE_RES = (ARCHIVE_RM_RE, ARCHIVE_FIND_RE, ARCHIVE_MV_OUT_RE, ARCHIVE_CD_RM_RE, ARCHIVE_SYNC_CLEAN_RE)
_ARCH_TOK_RE = re.compile(r"_archive|antigravity-2025\.md|paicodeconstitution-2026\.md", re.IGNORECASE)
_DESTROY = {"rm", "rmdir", "unlink", "shred", "trash", "srm"}
_ARCH_SKIP_DIRS = {"node_modules", ".git", ".venv", "venv", "__pycache__", ".pytest_cache", "dist", "build", ".cache",
                   ".tox", ".mypy_cache", "site-packages"}
_ARCH_SCAN_CACHE = {}


def _has_archive_below(d: str, depth: int = 3, budget: int = 4000) -> bool:
    """B5-36: a `_archive` directory, ANTIGRAVITY-2025.md or a NEOCORTEX directory within `depth` levels (breadth
    first, symlinks not followed, heavy build/vendor directories skipped). Past `budget` entries the scan stops and
    answers False: a documented limit (the named-path checks still run)."""
    if d in _ARCH_SCAN_CACHE:
        return _ARCH_SCAN_CACHE[d]
    found, seen, level = False, 0, [d]
    for _dep in range(depth):
        nxt = []
        for cur in level:
            try:
                with os.scandir(cur) as it:
                    for e in it:
                        seen += 1
                        n = e.name.lower()
                        if n in ("_archive", "antigravity-2025.md", "neocortex"):
                            found = True
                            break
                        if seen > budget:
                            break
                        if n not in _ARCH_SKIP_DIRS and e.is_dir(follow_symlinks=False):
                            nxt.append(e.path)
            except OSError:
                continue
            if found or seen > budget:
                break
        if found or seen > budget:
            break
        level = nxt
    _ARCH_SCAN_CACHE[d] = found
    return found


_ARCH_DEEP = []      # replay 16: directories holding an archive DEEPER down (copies, clones, project roots): an ask


def _arch_ancestor(op: str, base) -> bool:
    """B5-36 (reviewer A round 5): `op` names (or globs to) a directory that DIRECTLY holds an archive: a NEOCORTEX
    directory by name, or one with `_archive/` or ANTIGRAVITY-2025.md as a direct child -> DENY. Replay 16: an archive
    found only deeper (review clones, staging copies, a project root) is recorded in _ARCH_DEEP and ASKED, because
    a DENY cannot be approved and legitimate removals of such copies exist."""
    import glob as _glob
    p = os.path.expanduser(_unquote(op))
    if not p.strip() or "$" in p or "`" in p:
        return False                           # replay 15: `rm -f ""` is not the cwd; an unresolved variable is unknown
    if os.path.basename(os.path.normpath(p)).lower() == "neocortex":
        return True
    full = p if os.path.isabs(p) else os.path.join(base or os.getcwd(), p)
    matches = _glob.glob(full)[:200] if any(c in p for c in "*?[") else [full]
    for m in matches:
        if os.path.basename(os.path.normpath(m)).lower() == "neocortex" or _ARCH_TOK_RE.search(os.path.basename(m)):
            return True
        if os.path.isdir(m) and not os.path.islink(m):
            if os.path.isdir(os.path.join(m, "_archive")) or os.path.isfile(os.path.join(m, "ANTIGRAVITY-2025.md")):
                return True
            if _has_archive_below(os.path.normpath(m)):
                _ARCH_DEEP.append(os.path.normpath(m))
    return False


def _phys_join(base, t: str) -> str:
    """N1 (v0.12, Codex re-check seat C2): `base`/`t` resolved the way the kernel does: each EXISTING symlink component is
    followed before a later `..` applies (so `x/<archive link>/..` is the real archive's parent folder); components that
    do not exist stay textual (a missing folder cannot be entered, G6-02)."""
    p = t if os.path.isabs(t) else os.path.join(base or os.getcwd(), t)
    cur = os.sep
    for comp in p.split(os.sep):
        if comp in ("", "."):
            continue
        if comp == "..":
            cur = os.path.dirname(cur.rstrip(os.sep)) or os.sep
            continue
        nxt = os.path.join(cur, comp)
        cur = os.path.realpath(nxt) if os.path.islink(nxt) else nxt
    return cur


def archive_parsed_hit(cmd: str, cwd: str = "") -> bool:
    """B4-17 (reviewer A round 4): archive destruction or move-out decided on PARSED commands (executable basename, so
    `/bin/rm`, `\\rm`, `(rm ...)` count), plus rsync --remove-source-files and find -exec mv. False on a parse
    failure (the regex layer still runs). B4-33: `git rm --cached` is index-only."""
    try:
        segs = _scoped_segments(cmd, cwd)
    except Exception:
        return False
    txt_here = cwd or ""                       # B5-07: the cd TEXT is kept even when the directory does not exist
    for words, _t, here, _i in segs:
        w = _strip_wrappers_guard(list(words))
        if not w:
            continue
        verb = _unquote(w[0]).lstrip("\\").split("/")[-1].lower()
        args = [_unquote(a) for a in w[1:]]
        if verb in ("cd", "pushd") and args:
            t = os.path.expanduser(args[-1])
            txt_here = _phys_join(txt_here, t)   # G6-02 `cd ..` leaves; N1: through a symlink, physically
            continue
        base = here or txt_here
        ops = [a for a in args if not a.startswith("-")]
        arch = [a for a in ops if _ARCH_TOK_RE.search(a)]
        in_arch = bool(_ARCH_TOK_RE.search(here or "")) or bool(_ARCH_TOK_RE.search(txt_here))

        def gone(paths, dirs=True):            # B5-36: an operand that IS, or CONTAINS, an archive
            # G6-02: `NEOCORTEX/<archive>/../old` names `NEOCORTEX/old`; N1: an operand with `..` is resolved physically
            def eff(x):
                if not x.strip() or ".." not in x.split("/"):
                    return x
                return _phys_join(base, os.path.expanduser(x))
            return any(_ARCH_TOK_RE.search(eff(x)) or (dirs and _arch_ancestor(x, base)) for x in paths)
        # replay 17: only a RECURSIVE rm (and trash/srm) removes a directory; a non-recursive rm with a glob that
        # matches the archive directory cannot remove it
        rec = verb in ("trash", "srm") or any(a == "--recursive" or (re.match(r"^-[A-Za-z]*[rR]", a) and
                                                                     not a.startswith("--")) for a in args)
        if verb in _DESTROY and ((in_arch and ops) or gone(ops, rec)):
            return True
        if verb in ("mv", "gmv"):
            srcs = ops if any(a in ("-t", "--target-directory") or a.startswith("--target-directory=") for a in args) \
                else ops[:-1]
            if (in_arch and srcs) or gone(srcs):
                return True
        if verb == "rsync" and "--remove-source-files" in args and gone(ops[:-1]):
            return True
        if verb == "find":
            roots = []
            for a in args:
                if a.startswith(("-", "(", "!")):
                    break
                roots.append(a)
            low = [a.lower() for a in args]
            deleting = "-delete" in low or any(
                a in ("-exec", "-execdir", "-ok", "-okdir") and low[k + 1].lstrip("\\").split("/")[-1] in (_DESTROY | {"mv", "gmv"})
                for k, a in enumerate(low[:-1]))
            pats = [low[k + 1] for k, a in enumerate(low[:-1]) if a in ("-name", "-iname", "-path", "-ipath",
                                                                        "-regex", "-iregex", "-wholename")]
            import fnmatch as _fn
            # an unfiltered find (or one whose filter names an archive) removes everything under its roots; a
            # filtered cleanup (`-name '*.pyc'`) is judged by its named paths only (documented limit)
            whole = not pats or any(_fn.fnmatch(n, q) or q in n for q in pats
                                    for n in ("_archive", "neocortex", "antigravity-2025.md"))
            if deleting and (arch or in_arch or (whole and gone(roots or ["."]))):
                return True
        # B5-07 (reviewer A round 5): archivers that delete their input
        if verb == "zip" and ("--move" in args or any(re.match(r"^-[A-Za-z0-9]*m", a) for a in args
                                                       if not a.startswith("--"))) and gone(ops[1:]):
            return True
        if verb in ("tar", "gtar", "bsdtar") and "--remove-files" in args and (in_arch or gone(ops)):
            return True
        if verb == "git" and args:
            i, g_txt = 0, base                 # B5-07: git's own -C moves where it works
            while i < len(args) and args[i].startswith("-"):
                if args[i] == "-C" and i + 1 < len(args):
                    t = os.path.expanduser(args[i + 1])
                    g_txt = _phys_join(g_txt, t)   # G6-02; N1: physically, through symlinks
                    i += 2
                    continue
                i += 2 if args[i] in ("-c", "--namespace", "--git-dir", "--work-tree") else 1
            if i >= len(args):
                continue
            sub, rest = args[i].lower(), args[i + 1:]
            g_in = in_arch or bool(_ARCH_TOK_RE.search(g_txt or ""))
            rops = [a for a in rest if not a.startswith("-")]
            if sub == "rm" and "--cached" not in rest and ((g_in and rops) or gone(rops)):
                return True
            if sub == "mv" and ((g_in and rops[:-1]) or gone(rops[:-1])):
                return True
            if sub == "clean" and g_in and any(a == "--force" or re.match(r"^-[a-zA-Z]*f", a) for a in rest
                                                if not a.startswith("--") or a == "--force"):
                return True
    return False


# Verbs whose LAST non-flag argument is a destination that gets overwritten.
_DEST_VERBS = {"cp", "mv", "rsync", "install"}
# Verbs that write to a named file wherever it appears in their arguments.
_NAMED_WRITE_VERBS = {"tee", "truncate", "patch", "ed", "ex"}
# Verbs that only launch another command: the REAL verb is the next non-assignment word.
_WRAPPER_VERBS = {"sudo", "doas", "env", "command", "builtin", "exec", "nohup", "time", "nice", "ionice", "stdbuf"}
_REDIRECT_OPS = (">", ">>", ">|", "&>", "&>>")


def _unquote(tok: str) -> str:
    if len(tok) >= 2 and tok[0] == tok[-1] and tok[0] in "\"'":
        return tok[1:-1]
    return tok


def _is_l0(tok: str) -> bool:
    return _unquote(tok).rstrip("/").split("/")[-1].lower() == L0_BASENAME.lower()


_MAX_OPEN_QUOTE_LINES = 200
_MAX_OPEN_QUOTE_CHARS = 8_000
_MAX_CHECKED_CHARS = 50_000            # heredoc-stripped command text beyond this is not parsed: ASK


def _tokenize(cmd: str) -> list:
    """shlex tokens of `cmd`, with an explicit ';' between physical lines (a newline ends a
    command exactly as `;` does, S4-12). Parsed LINE BY LINE so a `#` comment ends at its own
    newline (shlex's comment handling would otherwise swallow an injected separator and the
    next command with it), and a quoted span that runs across lines is re-joined and parsed
    whole. Raises ValueError if a quote is still open at the end (caller falls back)."""
    toks, buf = [], ""
    cmd = re.sub(r"\\\r?\n", " ", cmd)      # backslash-newline is a line CONTINUATION, not a separator
    for line in cmd.replace("\r", "\n").split("\n"):
        buf = line if not buf else buf + "\n" + line
        lx = shlex.shlex(buf, posix=False, punctuation_chars=True)
        lx.whitespace_split = True
        try:
            toks.extend(list(lx))
        except ValueError:
            # Seat B B-19: re-parsing an ever-growing open-quote buffer was quadratic (27 s on a 30 KB
            # heredoc with a stray quote, past the 10 s hook timeout). Give up early: the caller's
            # fallback regex then decides, in linear time.
            if buf.count("\n") > _MAX_OPEN_QUOTE_LINES or len(buf) > _MAX_OPEN_QUOTE_CHARS:
                # reviewer A round 2 (residual B-19): a LINE bound alone still allowed ~50 KB re-parsed 200
                # times (2.9 s per check); the buffer is now bounded in CHARACTERS too.
                raise ValueError("open quote spans too much text")
            continue          # open quote: keep accumulating the next line
        toks.append(";")
        buf = ""
    if buf:
        raise ValueError("unterminated quote")
    return toks



def l0_write_target(cmd: str) -> bool:
    """True if `cmd` writes to, overwrites or truncates a live copy of L0.

    Reads return False by construction: the file must appear as a redirect target, as a
    write verb's operand, or as a copy destination -- never merely as an argument.
    """
    cmd = _strip_heredocs(cmd)             # Seat B B-19: heredoc bodies are data, not commands
    try:
        toks = _tokenize(cmd)
    except ValueError:
        m, complete = _bounded_search((L0_WRITE_FALLBACK_RE,), cmd, L0_TOKEN_RE)   # B2-23
        return bool(m) or not complete

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
            if tok in _REDIRECT_OPS and i + 1 < len(seg) and _is_l0(seg[i + 1]):
                return True
        words = [w for w in seg if w not in _REDIRECT_OPS + ("<", "2>")]
        # Strip leading NAME=value assignments and wrapper verbs (+ their flags / assignments).
        while words:
            head = _unquote(words[0]).split("/")[-1].lower()
            if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", words[0]):
                words = words[1:]
            elif head in _WRAPPER_VERBS:
                words = words[1:]
                while words and (words[0].startswith("-") or re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", words[0])):
                    words = words[1:]
            else:
                break
        if not words:
            continue
        verb = _unquote(words[0]).split("/")[-1].lower()
        args = words[1:]
        # 2. `sed -i` edits in place; the file is an operand, not a redirect target.
        if verb == "sed" and _sed_in_place(args) and any(_is_l0(a) for a in args):   # B5-09: `-Ei` too
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


# H4a -- GOVERNOR WRITE-GUARD (PLAN_hooks_outcome_tamper_loopbound_2026-10-05 §3 H4; form changed after
# the Seat B (reviewer A) round of 2026-10-05, whose B-01..B-09 showed that enumerating write FORMS leaks:
# redirect targets read as copy destinations, `-t DIR`, recursive copies into an ancestor, tar/unzip/
# ditto/find, git commands that rewrite the worktree without naming a file, wrapper option values,
# subshells and pushd, embedded quotes, braces and the case-insensitive volume).
# The guard now ASKS when a command
#   (a) writes a protected path by a form it understands (redirects, copy/move/install/link/rsync/ditto
#       with -t and recursive copies into an ancestor, in-place editors, tee/truncate/touch/patch/dd,
#       rm/chmod & co, find -delete/-exec, tar -x/unzip, git path writers), or
#   (b) runs a git operation that rewrites the worktree of a repository HOLDING the governor, or
#   (c) runs a governor-writing script (GOVERNOR_SCRIPTS) at this workspace, or
#   (d) uses ANY verb that is not known read-only (or an interpreter/verifier) with a protected path in
#       its arguments — the conservative fallback that makes the unknown writer an ask, not a bypass.
# ASK, never deny (PLAN D5). Documented limits (still): shell variable indirection, eval/base64, a
# program that opens the file itself (`python3 -c "open(...)"`). H4c (hook_support.integrity_report)
# is the backstop that makes any such write visible within one turn.
SETTINGS_NAMES = ("settings.json", "settings.local.json")
_PROTECTED_BASENAMES = {"settings.json", "settings.local.json", "machine_config.yaml", "routing_policy.yaml",
                        "hooks_approved.sha256"}
_TEMP_ROOTS = ("/tmp/", "/private/tmp/", "/var/folders/", "/private/var/folders/")
_DESTRUCTIVE_VERBS = {"rm", "rmdir", "unlink", "shred", "trash", "srm", "chmod", "chown", "chflags", "chgrp"}
_ARG_WRITE_VERBS = {"tee", "truncate", "ed", "ex", "touch"} | _DESTRUCTIVE_VERBS
_COPY_VERBS = {"cp", "mv", "install", "ln", "rsync", "ditto", "scp", "gcp", "gmv"}
_COPY_VALUE_OPTS = {"install": {"-m", "-o", "-g", "-f", "-B", "-S", "--mode", "--owner", "--group"},
                    "rsync": {"-e", "--exclude", "--include", "--filter", "-f", "--rsh", "--files-from",
                              "--exclude-from", "--include-from", "--chmod", "--log-file", "--backup-dir"},
                    "cp": {"-S", "--suffix"}, "mv": {"-S", "--suffix"}, "ln": {"-S", "--suffix"},
                    "scp": {"-i", "-P", "-o", "-F", "-c", "-l", "-S"}, "ditto": {"--arch", "-arch"}}
_WRAPPER_OPTS = {"sudo": {"-u", "-g", "-C", "-h", "-p", "-r", "-t", "-U", "-D", "-T", "-R"},
                 "doas": {"-u", "-C"}, "nice": {"-n"}, "ionice": {"-c", "-n", "-p"},
                 "env": {"-u", "-C", "-S", "-P", "--chdir", "--unset"}, "stdbuf": {"-i", "-o", "-e"},
                 "time": {"-f", "-o", "--output", "--format"},
                 "timeout": {"-s", "-k", "--signal", "--kill-after"}, "gtimeout": {"-s", "-k"},
                 "caffeinate": {"-t", "-w"}, "exec": {"-a"}, "command": set(), "builtin": set(),
                 "nohup": set(), "xargs": {"-I", "-L", "-n", "-P", "-s", "-E", "-J", "-R", "-d", "-a"}}
_INTERPRETER_RE = re.compile(r"^(python\d*(\.\d+)?|bash|sh|zsh|dash|ksh|node|ruby|deno|bun|tsx|ts-node|osascript|source|\.)$")
_READ_ONLY_VERBS = {
    "cat", "less", "more", "head", "tail", "grep", "egrep", "fgrep", "rg", "ag", "ack", "wc", "diff", "cmp",
    "comm", "ls", "stat", "file", "du", "df", "shasum", "sha256sum", "sha1sum", "sha512sum", "md5", "md5sum",
    "cksum", "b2sum", "jq", "sort", "uniq", "cut", "tr", "column", "nl", "od", "xxd", "hexdump", "strings",
    "echo", "printf", "test", "[", "true", "false", "realpath", "readlink", "basename", "dirname", "which",
    "whereis", "type", "open", "code", "pbcopy", "bat", "tree", "date", "pwd", "id", "whoami", "uname",
    "plutil", "mdls", "mdfind", "lsof", "ps", "pgrep", "shellcheck", "pytest", "py.test", "npm", "npx",
    "make", "otool", "nm", "codesign", "spctl", "defaults", "security", "launchctl", "osacompile",
    "sqlite3", "curl", "wget", "ssh", "ping", "dig", "host", "say", "sleep", "wait", "kill", "history",
    "awk", "sed", "perl", "yq", "xattr", "find", "tar", "unzip", "zipinfo", "git", "cd", "pushd", "popd",
    "set", "export", "unset", "alias", "local", "read", "for", "do", "done", "while", "if", "then",
    "else", "fi", "case", "esac", "in", "{", "}", "return", "exit", "break", "continue",
}
_GIT_READ = {"status", "diff", "log", "show", "blame", "grep", "ls-files", "ls-tree", "rev-parse", "cat-file",
             "describe", "shortlog", "reflog", "fetch", "remote", "help", "version", "add", "commit", "push",
             "tag", "branch", "config", "notes", "worktree", "rev-list", "merge-base", "name-rev", "bisect",
             "check-ignore", "check-attr", "count-objects", "fsck", "gc", "show-ref", "for-each-ref", "var",
             "archive", "bundle", "format-patch", "request-pull", "submodule", "lfs", "range-diff"}
_GIT_TREE_WRITERS = {"reset", "checkout", "switch", "stash", "apply", "am", "merge", "rebase", "pull",
                     "cherry-pick", "revert", "clean", "restore", "read-tree", "sparse-checkout"}
_GOVERNOR_NAME_SAMPLES = ("x.py", "x.sh", "settings.json", "settings.local.json", "machine_config.yaml",
                          "routing_policy.yaml", "hooks_approved.sha256", "x.json", "x.yaml", "x.yml", "hooks",
                          "claude-code", ".claude", "adapters", "governance")
_FORMATTER_VERBS = {"black", "isort", "autopep8", "yapf", "prettier", "eslint", "gofmt", "goimports", "rustfmt",
                    "swiftformat", "clang-format", "shfmt", "ruff", "autoflake", "dprint", "biome"}
# B2-35 / B2-29 (reviewer A round 2b): check, diff and lint modes write nothing. Copy of verifier_recognizer's
# table (this DENY path takes no import dependency); test_pretool_h0_h4_h9.py checks the copies agree.
_FORMATTERS_DEFAULT_WRITE = {"black": {"--check", "--diff"}, "isort": {"--check", "--check-only", "-c", "--diff"},
                             "rustfmt": {"--check"}, "swiftformat": {"--lint", "--dryrun"}}
_FORMATTERS_WRITE_FLAG = {"autopep8": {"-i", "--in-place"}, "yapf": {"-i", "--in-place"},
                          "prettier": {"--write", "-w"}, "eslint": {"--fix"}, "gofmt": {"-w"}, "goimports": {"-w"},
                          "clang-format": {"-i"}, "shfmt": {"-w", "--write"}, "autoflake": {"-i", "--in-place"},
                          "biome": {"--write", "--apply", "--fix"}}


_FORMATTER_VALUED = {"--exclude", "--extend-exclude", "--force-exclude", "--include", "--config", "-l", "--line-length",
                     "-t", "--target-version", "--required-version", "--ignore-path", "--stdin-filename", "--select",
                     "--ignore", "--extend-select", "--fixable", "--unfixable", "--per-file-ignores", "--settings-path",
                     "--sp", "--skip", "--skip-glob", "--filter", "--format", "--style", "--assume-filename",
                     "--config-path", "--emit"}


def _formatter_writes(verb: str, args: list):
    """True / False if `verb` is a known formatter (does this invocation write its operands?), else None."""
    flags = {_unquote(a).split("=")[0] for a in args if a.startswith("-")}
    nf = [_unquote(a) for a in args if not a.startswith("-")]
    if verb == "ruff":
        if nf[:1] == ["format"]:
            return not (flags & {"--check", "--diff"})
        return bool(flags & {"--fix"}) and not (flags & {"--diff", "--no-fix"})
    if verb == "rustfmt":
        # B3-27 (reviewer A round 3): `--emit files` WRITES; only `--emit stdout` (and --check) do not
        emit = next((a.split("=", 1)[1] for a in args if a.startswith("--emit=")), None)
        if emit is None and "--emit" in args and args.index("--emit") + 1 < len(args):
            emit = args[args.index("--emit") + 1]
        if "--check" in flags:
            return False
        return emit is None or emit == "files"
    if verb == "dprint":
        return nf[:1] == ["fmt"]
    if verb in _FORMATTERS_WRITE_FLAG:
        if verb == "clang-format" and flags & {"--dry-run", "-n"}:
            return False
        wf = _FORMATTERS_WRITE_FLAG[verb]
        shorts = {f[1] for f in wf if len(f) == 2}       # B5-14 (reviewer A round 5): `autopep8 -ri` holds `-i`
        clustered = verb in ("autopep8", "yapf", "autoflake", "gofmt", "goimports", "shfmt") and any(
            len(f) > 2 and not f.startswith("--") and set(f[1:]) & shorts for f in flags)
        return (bool(flags & wf) or clustered) and "--fix-dry-run" not in flags
    if verb in _FORMATTERS_DEFAULT_WRITE:
        return not (flags & _FORMATTERS_DEFAULT_WRITE[verb])
    return None


_GOV_BASENAMES = None


def _governed_basenames() -> set:
    """Lower-case basenames of the live governor files (H4c's inventory), cached per run. Used where a
    finite list of invented sample names used to stand in for them (B2-02) and for an unknown cwd (B2-04)."""
    global _GOV_BASENAMES
    if _GOV_BASENAMES is None:
        names = set()
        try:
            hs = _sibling("hook_support")
            if hs is not None:
                names = {e.rstrip("/").split("/")[-1].lower() for e in hs.governed_files(ROOT)
                         if not e.endswith("/") and "/" in e}
        except Exception:
            names = set()
        try:
            names |= {n.lower() for n in os.listdir(os.path.join(str(ROOT), ".claude", "hooks"))}
        except OSError:
            pass
        # B5-23 (reviewer A round 5): caches and Finder metadata never execute and H4c excludes them
        names = {n for n in names if n not in ("__pycache__", ".pytest_cache", ".ds_store")
                 and not n.endswith((".pyc", ".log"))}
        _GOV_BASENAMES = names
    return _GOV_BASENAMES


def _protected_locations() -> list:
    r = str(ROOT)
    return [os.path.join(r, ".claude", "hooks"), os.path.join(r, ".claude", "settings.json"),
            os.path.join(r, ".claude", "settings.local.json"),
            os.path.join(r, "governance", "adapters", "claude-code"),
            os.path.join(r, "governance", "machine_config.yaml"),
            os.path.join(r, "governance", "routing_policy.yaml"),
            os.path.join(r, "governance", "hooks_approved.sha256")]


def _dequote_embedded(t: str) -> str:
    """`.claude/"hooks"/x` -> `.claude/hooks/x` (Seat B B-08); left alone when it cannot be parsed."""
    if '"' in t or "'" in t or "\\" in t:
        try:
            parts = shlex.split(t, posix=True)
            if len(parts) == 1:
                return parts[0]
        except ValueError:
            pass
    return t


def _brace_expand(t: str, limit: int = 32) -> list:
    m = re.search(r"\{([^{}]*,[^{}]*)\}", t)
    if not m:
        return [t]
    out = []
    for alt in m.group(1).split(","):
        out.extend(_brace_expand(t[:m.start()] + alt + t[m.end():], limit))
        if len(out) >= limit:
            break
    return out[:limit]


def _governor_path(tok: str, cwd, destructive: bool):
    """The protected path `tok` writes, or None. `cwd` None = unknown directory (conservative match on
    names). `destructive` also catches an ANCESTOR of a protected location (`rm -rf governance`).
    Comparison is case-insensitive (the volume is: `.CLAUDE` is `.claude`, Seat B B-08)."""
    import fnmatch
    t = _dequote_embedded(_unquote(tok))
    if not t or t.startswith("-") or t in ("/dev/null", "/dev/stdout", "/dev/stderr", "-"):
        return None
    if "$" in t or "`" in t:
        return None                                            # documented limit: variable indirection
    if _MODE["l0"]:
        # B3-15 (reviewer A round 3): the L0 DENY reuses this parser, matching the L0 BASENAME only
        return next((e for e in _brace_expand(t) if _is_l0(e)), None)
    locs = [l.lower() for l in _protected_locations()]
    for e in _brace_expand(t):
        e = os.path.expanduser(e)
        if not os.path.isabs(e) and cwd is None:
            parts_l = [x.lower() for x in Path(e).parts]
            if ".claude" in parts_l or "hooks" in parts_l or (parts_l and (
                    parts_l[-1] in _PROTECTED_BASENAMES or parts_l[-1] in _governed_basenames())):
                return e + " (directory unknown after pushd/popd/cd -)"
            continue
        p = e if os.path.isabs(e) else os.path.join(cwd or str(ROOT), e)
        cands = {os.path.normpath(p)}
        try:
            cands.add(os.path.realpath(os.path.normpath(p)))
        except OSError:
            pass
        for cand in cands:
            cl = cand.lower()
            parts = Path(cl).parts
            base = parts[-1] if parts else ""
            parent = parts[-2] if len(parts) > 1 else ""
            temp = (cl + "/").startswith(_TEMP_ROOTS)
            gi = next((i for i, part in enumerate(parts) if any(ch in part for ch in "*?[")), None)
            if not temp:
                if parent == ".claude" and (base in SETTINGS_NAMES or
                                            (gi == len(parts) - 1 and any(fnmatch.fnmatch(n, base) for n in SETTINGS_NAMES))):
                    return cand
                for i, part in enumerate(parts[:-1]):
                    if part == ".claude" and (parts[i + 1] == "hooks" or fnmatch.fnmatch("hooks", parts[i + 1])):
                        return cand
            if gi is None:
                for loc in locs:
                    if cl == loc or cl.startswith(loc + os.sep):
                        return cand
                    if destructive and loc.startswith(cl.rstrip(os.sep) + os.sep):
                        return cand
                continue
            base_dir = os.path.join(*parts[:gi]) if gi else os.sep
            pattern, rest = parts[gi], parts[gi + 1:]
            for loc in locs:
                if base_dir == loc or base_dir.startswith(loc + os.sep):
                    return cand
                if not loc.startswith(base_dir.rstrip(os.sep) + os.sep):
                    continue
                remainder = loc[len(base_dir.rstrip(os.sep)) + 1:].split(os.sep)
                pats = [pattern, *rest]

                def _m(name, pat):                      # shell globbing: `*` never matches a leading dot
                    return fnmatch.fnmatch(name, pat) and (not name.startswith(".") or pat.startswith("."))
                k = 0
                while k < len(pats) and k < len(remainder) and _m(remainder[k], pats[k]):
                    k += 1
                if k == len(pats) and (len(pats) >= len(remainder) or destructive):
                    return cand                          # the glob reaches the location, or an ancestor (destructive)
                if k == len(remainder) and len(pats) > len(remainder):
                    return cand                          # the glob reaches INSIDE the location
    return None


# B4-15 (reviewer A round 4): the delimiter is the whole shell word (`<<SQL-END`), not its first `\w` run
_HEREDOC_RE = re.compile(r"(?<!<)<<-?\s*(['\"]?)([^\s'\"<>|;&()]+)\1")   # Seat A F12: `<<$E` too
_PUNCT_RUN = re.compile(r"&&|\|\||\|&|;;|&>>|&>|>>|>&|<&|>\||<<<|<<|[;&|()<>]")
_SEP = (";", "|", "||", "&&", "&", "|&")
_W_REDIRECTS = (">", ">>", ">|", "&>", "&>>")
_ALL_REDIRECTS = _W_REDIRECTS + ("<", "<<", "<<<", ">&", "<&")


# B2-07 (reviewer A round 2b): `/bin/bash <<EOF`, `bash --noprofile <<EOF`, `bash -s -- <<EOF` run their body too.
_SHELL_FED_RE = re.compile(r"(?:^|[;&|(]\s*|\s)(?:[^\s;&|()<>]*/)?(?:bash|sh|zsh|dash|ksh)(?:\s+-{1,2}[A-Za-z-]*)*\s*<<")


def _heredoc_word(line: str, i: int):
    """B5-05 (reviewer A round 5): the delimiter after `<<`/`<<-` at index i, AFTER shell quote removal, with the index
    past it; None if there is no word. `<<\\EOF`, `<<E"OF"`, `<<'EOF'` and `<< EOF` all end at a line `EOF`."""
    j, n = i + 2, len(line)
    if j < n and line[j] == "-":
        j += 1
    while j < n and line[j] in " \t":
        j += 1
    out = []
    while j < n:
        c = line[j]
        if c in " \t<>|;&()":
            break
        if c == "\\":
            out.append(line[j + 1:j + 2])
            j += 2
            continue
        if c in "'\"":
            k = line.find(c, j + 1)
            if k < 0:
                return None
            out.append(line[j + 1:k])
            j = k + 1
            continue
        out.append(c)
        j += 1
    d = "".join(out)
    return (d, j) if d else None


def _heredoc_delims(line: str, q: str) -> tuple:
    """(delimiters of the REAL `<<` operators of one physical line, quote state after it). reviewer A B3-16: a `<<EOF`
    inside quotes or a comment is text, and must not make the next (executable) lines vanish as heredoc data."""
    out, i, n, word_start = [], 0, len(line), True
    while i < n:
        c = line[i]
        if q:
            if c == "\\" and q == '"':
                i += 2
                continue
            if c == q:
                q = ""
            i += 1
            continue
        if c == "\\":
            i, word_start = i + 2, False
            continue
        if c in "'\"":
            q, i, word_start = c, i + 1, False
            continue
        if c == "#" and word_start:
            break
        if line.startswith("$((", i) or line.startswith("((", i):
            depth, j = 0, i                             # B4-15: `<<` inside arithmetic is a shift, not a heredoc
            while j < n:
                if line[j] == "(":
                    depth += 1
                elif line[j] == ")":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            i, word_start = j + 1, False
            continue
        if line.startswith("<<", i) and not line.startswith("<<<", i) and (i == 0 or line[i - 1] != "<"):
            w = _heredoc_word(line, i)
            if w:
                out.append(w[0])
                i, word_start = w[1], False
                continue
        word_start = c in " \t;&|()"
        i += 1
    return out, q


def _strip_heredocs(cmd: str) -> str:
    """Heredoc BODIES are data, not commands (same rule as verifier_recognizer.strip_heredocs,
    duplicated so this guard has no import dependency on its DENY path)."""
    lines = cmd.replace("\r", "\n").split("\n")
    out, i, q = [], 0, ""
    while i < len(lines):
        ln = lines[i]
        out.append(ln)
        i += 1
        delims, q = _heredoc_delims(ln, q)
        if not delims:
            continue
        shell_fed = bool(_SHELL_FED_RE.search(ln))   # `bash <<EOF` runs its body: keep it (replay 2026-10-05)
        for d in delims:
            while i < len(lines) and lines[i].strip() != d:
                if shell_fed:
                    out.append(lines[i])
                i += 1
            i += 1
    return "\n".join(out)


_SEG_CACHE = {}


def _scoped_segments(cmd: str, cwd: str) -> list:
    """Cached per (command, cwd) for this hook run (the DENY and H4a checks parse the same text)."""
    key = (cmd, cwd)
    if key not in _SEG_CACHE:
        if len(_SEG_CACHE) > 8:
            _SEG_CACHE.clear()
        try:
            _SEG_CACHE[key] = _scoped_segments_uncached(cmd, cwd)
        except ValueError as exc:
            _SEG_CACHE[key] = exc
    v = _SEG_CACHE[key]
    if isinstance(v, ValueError):
        raise v
    return [tuple(x) for x in v]


def _scoped_segments_uncached(cmd: str, cwd: str) -> list:
    """[(words, write_redirect_targets, here)] in order. `here` follows cd/pushd and is restored at
    the end of a subshell; it becomes None (unknown) after popd, `cd -`, or a cd to an unresolvable
    target. Raises ValueError on an unterminated quote (caller falls back)."""
    toks = [p for t in _tokenize(_strip_heredocs(cmd))
            for p in (_PUNCT_RUN.findall(t) if len(t) > 1 and all(c in "();<>|&" for c in t) else [t])]
    here, stack, out, cur, dstack = (cwd or str(ROOT)), [], [], [], []

    def flush():
        nonlocal cur, here
        words, targets, skip, stdin = [], [], None, None
        for i, w in enumerate(cur):
            if skip == "<":
                stdin = w                                   # B4-26: the segment's stdin file
            if skip is not None:
                # B2-03: `>& file` writes the file (only `>&2` / `>&-` duplicate a descriptor)
                if skip in _W_REDIRECTS or (skip == ">&" and not _unquote(w).isdigit() and _unquote(w) != "-"):
                    targets.append(w)
                skip = None
                continue
            if w in _ALL_REDIRECTS:
                skip = w
                if words and words[-1].isdigit():
                    words.pop()
                continue
            words.append(w)
        cur = []
        if not words and not targets:
            return
        v = _unquote(words[0]).lower() if words else ""
        if v in ("cd", "pushd", "popd"):
            arg = [x for x in words[1:] if not x.startswith("-")]
            tgt = _dequote_embedded(_unquote(arg[0])) if arg else ""
            if v == "popd":
                here = dstack.pop() if dstack else None        # B2-04: a real directory stack
            elif not tgt or tgt == "-" or "$" in tgt or "`" in tgt or here is None:
                here = None
            else:
                if v == "pushd":
                    dstack.append(here)
                tgt = os.path.expanduser(tgt)
                here = os.path.normpath(tgt if os.path.isabs(tgt) else os.path.join(here, tgt))
                if not os.path.isdir(here):
                    here = None                                # B2-04: the cd may fail; where we are is unknown
        out.append((words, targets, here, stdin))

    for t in toks:
        if t == "(":
            flush()
            stack.append(here)
        elif t == ")":
            flush()
            if stack:
                here = stack.pop()
        elif t in _SEP:
            flush()
        else:
            cur.append(t)
    flush()
    return out


def _wrapper_effects(words: list) -> tuple:
    """(chdir targets, side-output files) named by the wrapper chain: `env -C DIR` / `--chdir=DIR`,
    `sudo -D DIR` (B2-05), `time -o FILE` / `--output=FILE` (B2-12)."""
    chdirs, outs = [], []
    while words:
        w0 = _unquote(words[0])
        head = w0.split("/")[-1].lower()
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", w0) or w0 in ("!", "{", "then", "do", "else"):
            words = words[1:]
            continue
        if head not in _WRAPPER_OPTS:
            break
        opts = _WRAPPER_OPTS[head]
        words = words[1:]
        while words and (words[0].startswith("-") or re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", words[0])):
            w = _unquote(words[0])
            words = words[1:]
            val = None
            if "=" in w and w.startswith("--"):
                w, val = w.split("=", 1)
            elif w in opts and words:
                val = _unquote(words[0])
                words = words[1:]
            if val is None and len(w) > 2 and not w.startswith("--") and w[:2] in opts:
                w, val = w[:2], w[2:]                    # B3-25: attached short value (`env -C.claude`)
            if val is None:
                continue
            if (head == "env" and w in ("-C", "--chdir")) or (head == "sudo" and w in ("-D", "--chdir")):
                chdirs.append(val)
            elif head == "time" and w in ("-o", "--output"):
                outs.append(val)
        if head in ("timeout", "gtimeout") and words:
            words = words[1:]
    return chdirs, outs


# B5-32/B5-34/B5-35 (reviewer A round 5): dependency runners run their child in the project environment; the child is
# the verb that matters (`uv run black .` formats, `uv run pytest x` reads, `npx tsx embed.ts` is the job).
_RUNNERS = {"uv": "run", "poetry": "run", "pipenv": "run", "hatch": "run", "pdm": "run", "rye": "run",
            "pnpm": ("exec", "dlx"), "npm": "exec", "yarn": ("exec", "dlx"), "bun": "x"}
_RUNNER_VALUED = {"--with", "--with-requirements", "--python", "-p", "--project", "--directory", "--env-file",
                  "--group", "--extra", "--index", "--package", "--from", "-e", "--env", "-c", "--call"}
# G6-01 (Gemini round 6): GLOBAL options of the runner itself, before its subcommand (`uv -q run`, `poetry -C proj run`,
# `npm --prefix web exec`, `hatch -e test run`); those listed here take a value
# N2 (v0.12, Codex re-check seat C2): arity is PER RUNNER (npm's `-p` is the boolean --parseable, pnpm's `-w` the boolean
# --workspace-root); an option not listed for that runner takes no value
_RUNNER_GLOBAL_VALUED = {
    "uv": {"--directory", "--project", "--config-file", "--cache-dir", "--python"},
    "poetry": {"-C", "--directory", "-P", "--project"},
    "pipenv": set(), "rye": set(),
    "hatch": {"-e", "--env", "-p", "--project"},
    "pdm": {"-p", "--project"},
    "pnpm": {"-C", "--dir", "--filter", "-F"},
    "npm": {"--prefix", "-w", "--workspace"},
    "yarn": {"--cwd"},
    "bun": {"--cwd"},
}


def _strip_runner(words: list) -> list:
    if not words:
        return words
    head = _unquote(words[0]).split("/")[-1].lower()
    if head in ("npx", "bunx", "pnpx"):
        rest = words[1:]
    elif head in _RUNNERS:
        j = 1                                    # G6-01: the runner's own global options come first
        while j < len(words) and _unquote(words[j]).startswith("-"):
            a = _unquote(words[j])
            j += 1
            if a in _RUNNER_GLOBAL_VALUED.get(head, set()) and "=" not in a and j < len(words):
                j += 1
        subs = _RUNNERS[head] if isinstance(_RUNNERS[head], tuple) else (_RUNNERS[head],)
        if j < len(words) and _unquote(words[j]) in subs:
            rest = words[j + 1:]
        else:
            return words
    else:
        return words
    while rest and _unquote(rest[0]).startswith("-"):
        a = _unquote(rest[0])
        rest = rest[1:]
        if a == "--":
            break
        if a in _RUNNER_VALUED and rest:
            rest = rest[1:]
    return rest


def _strip_wrappers_guard(words: list) -> list:
    words = _strip_runner(words)
    while words:
        w0 = _unquote(words[0])
        head = w0.split("/")[-1].lower()
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", w0) or w0 in ("!", "{", "then", "do", "else"):
            words = words[1:]
            continue
        if head not in _WRAPPER_OPTS:
            break
        opts = _WRAPPER_OPTS[head]
        words = words[1:]
        while words and (words[0].startswith("-") or re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", words[0])):
            w = _unquote(words[0])
            words = words[1:]
            if w in opts and words:
                words = words[1:]                                  # the option's value (Seat B B-06)
        if head in ("timeout", "gtimeout") and words:
            words = words[1:]                                      # the duration
        words = _strip_runner(words)
    return words


# B4-36 (reviewer A round 4): option values depend on the interpreter: Python's -O/-OO take NO value (shells' -O does).
_INTERP_VALUED_BY = {"python": {"-X", "-W", "--check-hash-based-pycs"},
                     "shell": {"-o", "-O", "--rcfile", "--init-file"},
                     "node": {"-r", "--require", "--loader", "--import", "--experimental-loader"}}


def _interp_valued(verb: str) -> set:
    v = verb.lower()
    if v.startswith("python"):
        return _INTERP_VALUED_BY["python"]
    if v in ("bash", "sh", "zsh", "dash", "ksh", "source", "."):
        return _INTERP_VALUED_BY["shell"]
    if v in ("node", "deno", "bun"):
        return _INTERP_VALUED_BY["node"]
    return set()


def _executed_script_raw(words: list) -> str:
    """The executed script TOKEN as written (path included), or ""."""
    v = _unquote(words[0]).split("/")[-1]
    if _INTERPRETER_RE.match(v.lower()):
        skip = False
        for a in words[1:]:
            ua = _unquote(a)
            if skip:
                skip = False
                continue
            if ua in ("-m", "-c", "-e"):
                return ""
            if ua in _interp_valued(v):
                skip = True
                continue
            if not ua.startswith("-"):
                return a
        return ""
    return words[0]


def _executed_script(words: list):
    """(script basename, its args) actually EXECUTED by this simple command (Seat B B-30)."""
    v = _unquote(words[0]).split("/")[-1]
    if _INTERPRETER_RE.match(v.lower()):
        rest = words[1:]
        skip = False
        for i, a in enumerate(rest):
            a = _unquote(a)
            if skip:
                skip = False
                continue
            if a in ("-m", "-c", "-e"):
                return None, []
            if a in _interp_valued(v):              # B3-08: `python3 -X dev script.py`, `-W ignore`, `bash -o x`
                skip = True
                continue
            if not a.startswith("-"):
                return a.split("/")[-1], [_unquote(x) for x in rest[i + 1:]]
        return None, []
    return v, [_unquote(x) for x in words[1:]]


def _repo_root(here):
    if here is None:
        return None
    d = Path(here)
    for c in (d, *d.parents):
        if (c / ".git").exists():
            return str(c)
    return None


def _repo_holds_governor(repo) -> bool:
    if repo is None:
        return False
    rl = repo.lower().rstrip(os.sep)
    return any(l.lower() == rl or l.lower().startswith(rl + os.sep) for l in _protected_locations())


def _script_root_is_this_workspace(raw_script: str, here) -> bool:
    """B2-39: a governor script acts on ITS OWN root (`ROOT = parent.parent.parent` for the hooks). True
    when the executed copy is this workspace's (or cannot be resolved: the conservative answer)."""
    t = os.path.expanduser(_dequote_embedded(_unquote(raw_script)))
    if "/" not in t:
        return True
    if not os.path.isabs(t):
        if here is None:
            return True
        t = os.path.join(here, t)
    try:
        rp = os.path.realpath(os.path.normpath(t))
    except OSError:
        return True
    parts = rp.split(os.sep)
    if len(parts) >= 4 and parts[-3].lower() == ".claude" and parts[-2].lower() == "hooks":
        return os.sep.join(parts[:-3]).lower() == os.path.realpath(str(ROOT)).lower()
    return True


def _opt_values(args: list, names: set, short_attached: tuple = ()) -> list:
    """Values of `--name=V`, `--name V`, `-x V` and attached `-xV` (for the given short options)."""
    out, i = [], 0
    while i < len(args):
        a = _unquote(args[i])
        if "=" in a and a.split("=", 1)[0] in names:
            out.append(a.split("=", 1)[1])
        elif a in names and i + 1 < len(args):
            out.append(_unquote(args[i + 1]))
            i += 1
        else:
            for so in short_attached:
                if a.startswith(so) and len(a) > len(so) and not a.startswith("--"):
                    out.append(a[len(so):])
        i += 1
    return out


def _is_url(a: str) -> bool:
    return bool(re.match(r"^['\"]?(https?|ftp)://", a))


def _url_basename(a: str) -> str:
    u = _unquote(a).split("?", 1)[0].split("#", 1)[0].rstrip("/")
    return u.rsplit("/", 1)[-1] or "index.html"


def _conditional_write_targets(verb: str, args: list) -> list:
    """B2-01 / B2-12: utilities that are READ-ONLY by default but write a NAMED output in some modes."""
    nf = [_unquote(a) for a in args if not a.startswith("-")]
    fl = {_unquote(a).split("=")[0] for a in args if a.startswith("-")}
    if verb == "sort":
        return _opt_values(args, {"-o", "--output"}, ("-o",))
    if verb == "uniq":
        vals = {"-f", "-s", "-w", "--skip-fields", "--skip-chars", "--check-chars"}
        ops, i = [], 0
        while i < len(args):
            a = _unquote(args[i])
            if a in vals:
                i += 2
                continue
            if not a.startswith("-") or a == "-":
                ops.append(a)
            i += 1
        return ops[1:2]
    if verb == "xxd":
        vals = {"-c", "-g", "-l", "-s", "-o", "-n", "-cols", "-len", "-seek", "-groupsize", "-offset", "-name"}
        ops, i = [], 0
        while i < len(args):
            a = _unquote(args[i])
            if a in vals:
                i += 2
                continue
            if not a.startswith("-") or a == "-":
                ops.append(a)
            i += 1
        return ops[1:2]
    if verb == "plutil":
        if fl & {"-replace", "-insert", "-remove", "-convert", "-create"}:
            o = _opt_values(args, {"-o"})
            if o:
                return [x for x in o if x != "-"]
            return nf[-1:]
        return []
    if verb in ("sed", "gsed"):
        out = []
        for a in args:
            # B3-09 (reviewer A round 3): addressed forms too (`1w`, `$w`, `1,$w`, `/re/w`), and GNU `W`
            for m in re.finditer(r"(?:^|[;{}\n!,$0-9]|/[A-Za-z0-9]*)\s*[wW]\s+([^;\s}]+)", _unquote(a)):
                out.append(m.group(1))
        return out
    if verb == "curl":
        out = _opt_values(args, {"-o", "--output", "-D", "--dump-header", "-c", "--cookie-jar", "--trace",
                                 "--trace-ascii", "--stderr", "--output-dir"}, ("-o",))
        if any(_unquote(a) in ("-O", "--remote-name", "--remote-name-all") or
               (re.match(r"^-[A-Za-z]*O[A-Za-z]*$", _unquote(a)) and not a.startswith("--")) for a in args):
            odir = (_opt_values(args, {"--output-dir"}) or [""])[0]
            out += [os.path.join(odir, _url_basename(a)) if odir else _url_basename(a) for a in args if _is_url(a)]
        return out                                          # B4-27: `-O` writes basename(URL)
    if verb == "wget":
        out = _opt_values(args, {"-O", "--output-document", "-o", "--output-file", "-a", "--append-output",
                                 "-P", "--directory-prefix"}, ("-O", "-P"))
        if not _opt_values(args, {"-O", "--output-document"}, ("-O",)):
            pdir = (_opt_values(args, {"-P", "--directory-prefix"}, ("-P",)) or [""])[0]
            out += [os.path.join(pdir, _url_basename(a)) if pdir else _url_basename(a) for a in args if _is_url(a)]
        return out                                          # B4-27: plain `wget URL` writes basename(URL)
    if verb == "rsync":
        return _opt_values(args, {"--log-file", "--write-batch", "--only-write-batch", "--backup-dir"})   # B3-26
    if verb == "osacompile":
        return _opt_values(args, {"-o"})
    return []


_COND_WRITE_VERBS = {"sort", "uniq", "xxd", "plutil", "sed", "gsed", "curl", "wget", "osacompile", "rsync"}


def _sed_in_place(args: list) -> bool:
    """B5-09 (reviewer A round 5): `-i`, `-i.bak`, `--in-place[=SUF]` and any short-option cluster holding `i` (`-Ei`,
    `-ni`) edit sed's operands in place. One parser for the L0 DENY, H4a and the approval-list reader rule."""
    return any(_unquote(a).startswith("--in-place") or re.match(r"^-[A-Za-z]*i", _unquote(a)) for a in args)


_APPROVAL_LIST_READERS = {"cat", "less", "more", "head", "tail", "grep", "egrep", "fgrep", "rg", "wc", "diff", "cmp",
                          "shasum", "sha256sum", "ls", "stat", "file", "echo", "printf", "bat", "jq", "md5"}
_MODE = {"l0": False}


def _approval_list_read(verb: str, args: list, named: list) -> bool:
    """B4-30 (reviewer A round 4): ordinary reads of the approval list stay silent: known readers, filters whose parsed
    write set is empty, awk without output redirection, the list as a COPY SOURCE, git index/read commands."""
    if verb in _APPROVAL_LIST_READERS or verb in ("cut", "column", "nl", "paste", "fold", "tac", "rev"):
        return True
    if verb in ("sort", "uniq", "xxd", "sed", "gsed") and not _conditional_write_targets(verb, args) and \
            not any(_unquote(a).startswith(("-i", "--in-place")) for a in args) and \
            not (verb in ("sed", "gsed") and _sed_in_place(args)):              # B5-06/B5-09: `sed -Ei` writes
        return True
    if verb in ("awk", "gawk") and not any(">" in _unquote(a) or "system" in _unquote(a) for a in args):
        return True
    ops = [i for i, a in enumerate(args) if not a.startswith("-")]
    if verb in ("cp", "rsync", "ditto", "install", "scp") and ops and all(i != ops[-1] for i in named):
        return True
    return verb == "git" and len(args) > 0 and _unquote(args[0]).lower() in (
        "diff", "log", "show", "status", "blame", "add", "ls-files")


def _segment_hit(words: list, here, cwd: str, stdin=None):
    """(protected hit or None, writes_something: bool) for one simple command."""
    chdirs, side_outs = _wrapper_effects(list(words))
    for d in chdirs:                                   # B2-05: `env -C DIR cmd` runs cmd in DIR
        d = os.path.expanduser(_dequote_embedded(d))
        here = None if (here is None or "$" in d) else os.path.normpath(d if os.path.isabs(d) else os.path.join(here, d))
    for o in side_outs:                                # B2-12: `time -o FILE cmd`
        h = _governor_path(o, here, False)
        if h:
            return h, True
    words = _strip_wrappers_guard(list(words))
    if not words:
        return None, False
    verb = _unquote(words[0]).split("/")[-1].lower()
    args = words[1:]
    # B3-08/09 (reviewer A round 3): the approval list is what H4c trusts, so ANY command naming it whose verb is not
    # a known reader asks — however the write is spelled (awk print >, python -c open(), sed w ...).
    named = [i for i, a in enumerate(args) if "hooks_approved.sha256" in _unquote(a).lower()]
    if not _MODE["l0"] and named and not _approval_list_read(verb, args, named):
        return "governance/hooks_approved.sha256", True
    flags = [_unquote(a) for a in args if a.startswith("-")]
    ops = [a for a in args if not a.startswith("-")]

    def first(paths, destructive=False):
        for a in paths:
            h = _governor_path(a, here, destructive)
            if h:
                return h
        return None

    # (c) governor-writing scripts, only when EXECUTED and aimed at this workspace
    script, sargs = _executed_script(words) if not _MODE["l0"] else (None, [])
    if script:
        for name, flag, target in GOVERNOR_SCRIPTS:
            # B-36: the volume is case-insensitive (`HOOK_SUPPORT.PY` is the approval writer)
            if script.lower() == name and (flag is None or flag in sargs):
                if name == "hook_support.py" and not _script_root_is_this_workspace(_executed_script_raw(words), here):
                    continue                           # B2-39: a fixture copy writes its own fixture's list
                if name == "install_adapters.py":
                    arg = sargs[sargs.index("--root") + 1] if "--root" in sargs and sargs.index("--root") + 1 < len(sargs) else None
                elif name == "bootstrap.sh":
                    arg = next((x for x in sargs if not x.startswith("-")), None)
                else:
                    arg = None
                if arg is None or _governor_path(arg, here, True) or os.path.normpath(
                        os.path.join(here or cwd or str(ROOT), os.path.expanduser(arg))).lower() == str(ROOT).lower():
                    return target, True
    # B5-06 (reviewer A round 5): `python3 -m hook_support --write-approved` (cwd or PYTHONPATH) and `-m runpy <path>` run
    # the approval writer without an executed-script token; any interpreter call naming both asks.
    if not _MODE["l0"] and _INTERPRETER_RE.match(verb) and "--write-approved" in [_unquote(a) for a in args] and \
            any("hook_support" in _unquote(a).lower() for a in args) and (script or "").lower() != "hook_support.py":
        return "governance/hooks_approved.sha256", True
    if verb in ("bash", "sh", "zsh", "dash", "ksh"):
        # B2-06: combined short flags (`-lc`, `-xec`) carry the -c too
        for i, a in enumerate(args):
            ua = _unquote(a)
            if ua == "-c" or (re.match(r"^-[a-zA-Z]*c[a-zA-Z]*$", ua) and not ua.startswith("--")):
                j = i + 1
                while j < len(args) and _unquote(args[j]).startswith("-"):
                    j += 1
                if j < len(args):
                    return governor_write_target(_unquote(args[j]), here or cwd), False
                break
            if not ua.startswith("-"):
                break
    if verb in _COND_WRITE_VERBS:
        cw = _conditional_write_targets(verb, args)
        for t in cw:
            h = _governor_path(t, here, False)
            if h:
                return h, True
        if cw and verb not in ("rsync",):
            return None, True
    if verb == "git":
        return _git_hit(args, here)
    if verb == "find":
        roots = []
        for a in args:
            if a.startswith(("-", "(", "!")):
                break
            roots.append(a)
        rest = [_unquote(a) for a in args[len(roots):]]
        for k, x in enumerate(rest[:-1]):              # -fprint FILE & co write FILE
            if x in ("-fprint", "-fprint0", "-fprintf", "-fls"):
                h = _governor_path(rest[k + 1], here, False)
                if h:
                    return h, True
        names = [rest[k + 1] for k, x in enumerate(rest[:-1]) if x in ("-name", "-iname")]
        import fnmatch as _fn
        samples = set(_GOVERNOR_NAME_SAMPLES) | _governed_basenames()     # B2-02: the REAL governor names
        if names and not any(_fn.fnmatch(n.lower(), pat.lower()) for pat in names for n in samples):
            return None, ("-delete" in rest)             # e.g. `find . -name '*.pyc' -delete`: cannot touch the governor
        def _exec_writes(k):
            v = rest[k + 1].split("/")[-1].lower() if k + 1 < len(rest) else ""
            if v in _FORMATTER_VERBS:                    # B3-32: `-exec black --check {} +` writes nothing
                end = next((j for j in range(k + 2, len(rest)) if rest[j] in (";", "+", "\\;")), len(rest))
                return bool(_formatter_writes(v, rest[k + 2:end]))
            return v not in _READ_ONLY_VERBS or v in _COND_WRITE_VERBS      # B2-02: `-exec sort -o {}`
        deleting = "-delete" in rest or any(
            x in ("-exec", "-execdir", "-ok", "-okdir") and _exec_writes(k) for k, x in enumerate(rest))
        return (first(roots or ["."], True), True) if deleting else (None, False)
    if verb in ("tar", "gtar", "bsdtar"):
        joined = " ".join(_unquote(a) for a in args[:1])
        if "-x" in flags or "--extract" in flags or (args and not args[0].startswith("-") and "x" in joined) or \
                any(re.match(r"^-[a-zA-Z]*x", f) for f in flags):
            dest = "."
            for i, a in enumerate(args):
                ua = _unquote(a)
                if ua in ("-C", "--directory") and i + 1 < len(args):
                    dest = args[i + 1]
                elif ua.startswith("--directory="):
                    dest = ua.split("=", 1)[1]
            return first([dest], True), True
        return None, False
    if verb == "unzip":
        if any(re.match(r"^-[a-zA-Z]*[ltpvZ]", f) for f in flags):
            return None, False                          # B2-36: list / test / pipe-to-stdout modes write nothing
        dest = "."
        for i, a in enumerate(args):
            if _unquote(a) == "-d" and i + 1 < len(args):
                dest = args[i + 1]
        return first([dest], True), True
    if verb in _COPY_VERBS:
        if verb == "rsync" and ("--dry-run" in flags or any(re.match(r"^-[a-zA-Z]*n[a-zA-Z]*$", f) for f in flags)):
            return None, False
        vopts = _COPY_VALUE_OPTS.get(verb, set())
        tdir, operands, i = None, [], 0
        while i < len(args):
            a = _unquote(args[i])
            if a in ("-t", "--target-directory") and i + 1 < len(args):
                tdir = args[i + 1]
                i += 2
                continue
            if a.startswith("--target-directory="):
                tdir = a.split("=", 1)[1]
            elif re.match(r"^-t.+", a) and verb in ("cp", "mv", "install", "ln", "gcp", "gmv"):
                tdir = a[2:]
            elif a in vopts and i + 1 < len(args):
                i += 2
                continue
            elif not a.startswith("-"):
                operands.append(args[i])
            i += 1
        recursive = verb in ("rsync", "ditto") or any(
            f in ("--recursive", "--archive") or re.match(r"^-[a-zA-Z]*[rRa]", f) for f in flags)
        if not operands and tdir is None:
            return None, False
        dest = tdir if tdir is not None else operands[-1]
        srcs = operands if tdir is not None else operands[:-1]
        h = first([dest], recursive)
        if h:
            return h, True
        if _MODE["l0"]:
            # B4-13 (reviewer A round 4): copying L0 OUT stays a read, but a source NAMED like L0 copied into a directory
            # that already holds an L0 (that is not the source itself) overwrites it.
            dd = os.path.expanduser(_dequote_embedded(_unquote(dest)))
            dd = dd if os.path.isabs(dd) else os.path.join(here or cwd or str(ROOT), dd)
            for src in srcs:
                if _is_l0(src):
                    su = os.path.expanduser(_dequote_embedded(_unquote(src)))
                    su = su if os.path.isabs(su) else os.path.join(here or cwd or str(ROOT), su)
                    joined = os.path.join(dd, os.path.basename(su))
                    try:
                        same = os.path.exists(su) and os.path.samefile(su, joined)
                    except OSError:
                        same = False
                    if os.path.isdir(dd) and os.path.lexists(joined) and not same:
                        return joined, True
        for src in ([] if _MODE["l0"] else srcs):      # L0 mode: copying L0 OUT is a read
            joined = os.path.join(_dequote_embedded(_unquote(dest)), _dequote_embedded(_unquote(src)).rstrip("/").split("/")[-1])
            h = _governor_path(joined, here, False) or (verb in ("mv", "gmv") and _governor_path(src, here, True))
            if h:
                return h, True
        return None, True
    if (verb in ("sed", "gsed") and _sed_in_place(flags)) or (verb in ("perl", "ruby") and any(
            f.startswith("-i") or f.startswith("--in-place") or re.match(r"^-\w*i", f) for f in flags)):   # B5-09
        return first(ops), True
    if verb in ("awk", "gawk") and "inplace" in " ".join(_unquote(a) for a in args):
        return first(ops), True
    if verb == "patch":
        if "--dry-run" in flags or "-C" in flags or "--check" in flags:
            return None, False                                     # Seat B B-29: no-write modes
        pops = [o for o in ops if o not in (_opt_values(args, {"-i", "--input", "-o", "--output", "-r", "-B", "-z", "-d",
                                                               "--directory", "-D", "-F", "-g", "-V", "-Y", "-p"}))]   # B5-15
        if pops:
            return first(pops), True                       # `patch FILE [PATCH]`: FILE is the target, headers unused
        # B4-26 (reviewer A round 4): with no FILE operand the targets are in the diff's headers (`-i DIFF` or `< DIFF`)
        src = (_opt_values(args, {"-i", "--input"}, ("-i",)) or [stdin])[0] if (stdin or "-i" in flags or
                                                                                any(f.startswith(("--input", "-i")) for f in flags)) else None
        pd = (_opt_values(args, {"-d", "--directory"}, ("-d",)) or [None])[0]     # B5-15: `-d DIR` moves the base
        if pd:
            pd = os.path.expanduser(_unquote(pd))
            here = None if here is None and not os.path.isabs(pd) else os.path.normpath(
                pd if os.path.isabs(pd) else os.path.join(here, pd))
        if src:
            sp = os.path.expanduser(_unquote(src))
            sp = sp if os.path.isabs(sp) else os.path.join(here or cwd or str(ROOT), sp)
            pn = next((int(f[2:]) for f in flags if re.match(r"^-p\d+$", f)), None)
            try:
                with open(sp, encoding="utf-8", errors="replace") as fh:
                    text = fh.read(1_000_000)
            except OSError:
                return f"the files named inside the unreadable patch {src}", True
            for m in re.finditer(r"^(?:\+\+\+|---) (\S+)", text, re.M):
                name = m.group(1)
                if name == "/dev/null":
                    continue
                cand = "/".join(name.split("/")[pn:]) if pn else name
                h = _governor_path(cand, here, False) or (
                    pn is None and os.path.basename(name).lower() in _governed_basenames() and name)
                if h:
                    return h, True
            return None, True
        # B5-15 (reviewer A round 5): the diff comes from a pipe or a heredoc this parser does not read: in a repository
        # that holds the governor (or an unknown directory) it asks.
        if here is None or _repo_holds_governor(_repo_root(here)):
            return "the files named inside a patch read from a pipe or heredoc", True
        return None, True
    if verb == "dd":
        return first([_unquote(a)[3:] for a in args if _unquote(a).lower().startswith("of=")]), True
    if verb == "xattr":
        if any(re.match(r"^-[a-zA-Z]*[wdc]", f) for f in flags):     # B3-34: -r (recursive) alone writes nothing
            return first(ops), True
        return None, False
    if verb in ("rm", "rmdir", "trash") and ops and all(
            os.path.basename(_unquote(o).rstrip("/")) in ("__pycache__", ".pytest_cache") and
            not os.path.islink(os.path.join(here or cwd or str(ROOT), _unquote(o).rstrip("/"))) for o in ops):
        return None, True                              # B4-32: caches never execute (B3-11) and are not inventoried
    if verb in _ARG_WRITE_VERBS:
        return first(ops, verb in _DESTRUCTIVE_VERBS), True
    if verb in _FORMATTER_VERBS:
        # B3-32: option VALUES (`--exclude .claude/hooks/`, `--config x`) are not files the formatter writes
        fops, i = [], 0
        while i < len(args):
            a = _unquote(args[i])
            if a in _FORMATTER_VALUED:
                i += 2
                continue
            if not a.startswith("-"):
                fops.append(args[i])
            i += 1
        # B4-25 (reviewer A round 4): `black .` / `prettier --write .` recurse: an ANCESTOR of the governor is a hit
        if verb in ("ruff", "dprint") and fops and _unquote(fops[0]) in ("format", "check", "fmt"):
            fops = fops[1:] or ["."]                     # B5-14: `ruff format` / `ruff check --fix` default to cwd
        return (first(fops, True), True) if _formatter_writes(verb, args) else (None, False)
    if verb in _READ_ONLY_VERBS or _INTERPRETER_RE.match(verb) or verb.startswith("python"):
        return None, False
    # (d) unknown verb: conservative — a protected path in its arguments is an ask (never in L0 mode: a DENY
    # must not fire on a reader it does not know, e.g. `pandoc L0 -o out.pdf`)
    if _MODE["l0"]:
        return None, False
    return first(ops), False


_GIT_OUTPUT_OPTS = {"--output", "-o", "--output-directory", "--file", "-f"}


def _git_hit(args: list, here):
    i, g_here, alt_tree = 0, here, False
    while i < len(args):
        a = _unquote(args[i])
        if a == "-C" and i + 1 < len(args):
            t = os.path.expanduser(_unquote(args[i + 1]))
            g_here = None if g_here is None else os.path.normpath(t if os.path.isabs(t) else os.path.join(g_here, t))
            i += 2
            continue
        if a.split("=")[0] in ("--git-dir", "--work-tree"):
            alt_tree = True                              # B2-11: which repository/worktree is touched is not resolved
            i += 1 if "=" in a else 2
            continue
        if a in ("-c", "--namespace") and i + 1 < len(args):
            i += 2
            continue
        if a.startswith("-"):
            i += 1
            continue
        break
    if i >= len(args):
        return None, False
    sub = _unquote(args[i]).lower()
    rest = [_unquote(a) for a in args[i + 1:]]
    rflags = [a for a in rest if a.startswith("-")]
    # B2-09: explicit output files of otherwise read-only subcommands (diff --output, archive -o, config --file)
    if sub == "config" and {"--get", "--get-all", "--get-regexp", "--list", "-l", "--get-urlmatch",
                            "--get-color", "--get-colorbool"} & set(rflags):
        return None, False                                         # B3-33: a config READ names its file
    if (sub == "restore" and {"--staged", "-S"} & set(rflags) and not {"--worktree", "-W"} & set(rflags)) \
            or (sub == "rm" and "--cached" in rflags):
        return None, False                                         # B3-33: index-only, the worktree is untouched
    if sub in ("diff", "log", "show", "archive", "format-patch", "config", "bundle", "blame"):
        for o in _opt_values(rest, _GIT_OUTPUT_OPTS, ("-o",) if sub in ("archive", "format-patch") else ()):
            h = _governor_path(o, g_here, False)
            if h:
                return h, True
    if sub in _GIT_READ:
        return None, False
    if sub == "stash" and rest and rest[0] in ("list", "show", "drop", "clear", "create", "store"):
        return None, False                               # B2-09: bare stash / push / save DO rewrite the worktree
    if sub == "apply" and ({"--check", "--stat", "--numstat", "--summary"} & set(rflags)) and "--apply" not in rflags:
        return None, False                                         # Seat B B-29 / B2-13: no-write modes
    if sub == "clean" and ({"-n", "--dry-run"} & set(rflags) or any(re.match(r"^-[a-zA-Z]*n", f) for f in rflags
                                                                    if not f.startswith("--"))):
        return None, False                                         # B2-36: dry run
    if alt_tree and sub in _GIT_TREE_WRITERS | {"rm", "mv", "checkout-index"}:
        return "an unresolved worktree (git --work-tree/--git-dir with a worktree-writing command)", True
    if sub == "checkout-index":                                    # B2-09: writes from the index
        if {"-a", "--all"} & set(rflags):
            return (f"the worktree of {_repo_root(g_here) or 'an unknown repository'} (git checkout-index --all)"
                    if g_here is None or _repo_holds_governor(_repo_root(g_here)) else None), True
        for pth in [a for a in rest if not a.startswith("-")]:
            h = _governor_path(pth, g_here, True)
            if h:
                return h, True
        return None, True
    # B4-14 (reviewer A round 4): `git checkout <tree-ish> <path>...` (no `--`) writes those paths too
    if sub == "checkout" and "--" not in rest:
        cnf = [a for a in rest if not a.startswith("-")]
        # B5-16 (reviewer A round 5): without a tree-ish the FIRST operand is a path too (`git checkout L0`, `--ours L0`);
        # a branch name is never a protected path, so every operand is checked.
        for pth in cnf:
            h = _governor_path(pth, g_here, True)
            if h:
                return h, True
    # B2-10: a path writer aimed at an ANCESTOR (`git restore -- .`, `git mv .claude x`) reaches the governor
    if sub in ("checkout", "restore", "rm", "mv") and "--" in rest:
        paths = rest[rest.index("--") + 1:]
        for pth in paths:
            h = _governor_path(pth, g_here, True)
            if h:
                return h, True
        return None, True
    if sub in ("rm", "mv", "restore"):
        for pth in [a for a in rest if not a.startswith("-")]:
            h = _governor_path(pth, g_here, True)
            if h:
                return h, True
        return None, True
    if sub in ("checkout", "switch") and {"-f", "--force", "--discard-changes"} & set(rflags):
        repo = _repo_root(g_here)
        if g_here is None or _repo_holds_governor(repo):        # B2-11: a forced checkout discards local edits
            return f"the worktree of {repo or 'an unknown repository'} (git {sub} --force)", True
    if sub == "checkout" and ({"-b", "-B", "--orphan"} & set(rflags)) and len([a for a in rest if not a.startswith("-")]) <= 1:
        return None, False                                         # new branch from HEAD: worktree unchanged
    if sub == "clean" and [a for a in rest if not a.startswith("-")]:
        for pth in [a for a in rest if not a.startswith("-")]:
            h = _governor_path(pth, g_here, True)
            if h:
                return h, True
        return None, True
    if sub == "reset" and not ({"--hard", "--merge", "--keep"} & set(rflags)):
        return None, False                                   # mixed/soft reset: the worktree is untouched
    if sub in _GIT_TREE_WRITERS:
        if _MODE["l0"]:
            if sub in ("apply", "am"):                   # B5-10 (reviewer A round 5): the diff's own headers name L0
                for pth in [a for a in rest if not a.startswith("-")]:
                    fp = os.path.expanduser(pth)
                    fp = fp if os.path.isabs(fp) else os.path.join(g_here or str(ROOT), fp)
                    try:
                        with open(fp, encoding="utf-8", errors="replace") as fh:
                            text = fh.read(1_000_000)
                    except OSError:
                        continue
                    for m in re.finditer(r"^(?:\+\+\+ |--- |diff --git )(?:[ab]/)?(\S+)", text, re.M):
                        if _is_l0(m.group(1)):
                            return m.group(1), True
            return None, True                            # L0 mode never asks git (no query-driven DENY)
        repo = _repo_root(g_here)
        if g_here is not None and not _repo_holds_governor(repo):
            return None, True
        touched = _git_would_touch_governor(repo, sub, rest) if repo else None
        if touched is False:
            return None, True                               # git itself says no protected path changes
        return (f"the worktree of {repo or 'an unknown repository'} (git {sub} would change governor files"
                f"{'' if touched else ', or could not be checked'})"), True
    return None, False


def _git(repo: str, *args, timeout: float = 2.0):
    import subprocess
    r = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(r.stderr[:120])
    return [l for l in r.stdout.splitlines() if l.strip()]


def _git_would_touch_governor(repo: str, sub: str, rest: list):
    """True / False from git's own answer; None when it cannot be asked (the caller then ASKS).
    Only the protected paths are queried, so the cost is one small git call."""
    rl = repo.rstrip(os.sep)
    rels = [os.path.relpath(l, rl) for l in _protected_locations() if l.lower().startswith(rl.lower() + os.sep)]
    if not rels:
        return False
    nf = [a for a in rest if not a.startswith("-")]
    try:
        if sub == "pull":
            # B5-13 (reviewer A round 5): the local @{upstream} is the LAST FETCHED state, not what this pull fetches, so it
            # cannot show that the pull leaves the governor alone: the pull asks (B4-29's diff dropped).
            return None
        if sub in ("merge", "rebase", "cherry-pick", "revert", "am") and not nf:
            # --abort / --continue / --skip: they rewrite only what is already in progress in the worktree
            return bool(_git(repo, "status", "--porcelain", "--", *rels))
        if sub in ("cherry-pick", "revert"):
            if not nf:
                return False                                 # --abort / --continue / --skip
            return any(bool(_git(repo, "diff", "--name-only", f"{c}^", c, "--", *rels)) for c in nf)
        if sub in ("checkout", "switch"):
            target = nf[0] if nf else None
            return bool(_git(repo, "diff", "--name-only", "HEAD", target, "--", *rels)) if target else None
        if sub in ("merge", "rebase"):
            target = nf[-1] if nf else None
            return bool(_git(repo, "diff", "--name-only", f"HEAD...{target}", "--", *rels)) if target else None
        if sub == "reset":
            target = nf[0] if nf else "HEAD"
            return bool(_git(repo, "diff", "--name-only", target, "--", *rels))
        if sub == "stash" and (not nf or nf[0] in ("push", "save")):
            return bool(_git(repo, "status", "--porcelain", "--", *rels))     # B2-09: what it would put away
        if sub == "stash":
            ref = nf[1] if len(nf) > 1 else "stash@{0}"
            names = _git(repo, "stash", "show", "--name-only", "--include-untracked", ref)
            return any(n == r or n.startswith(r.rstrip("/") + "/") for n in names for r in rels)
        if sub in ("apply", "am"):
            patches = [p for p in nf if os.path.isfile(os.path.join(rl, p)) or os.path.isfile(p)]
            if not patches:
                return None
            prefix = next((a.split("=", 1)[1] for a in rest if a.startswith("--directory=")), "")
            for pth in patches:
                fp = pth if os.path.isabs(pth) else os.path.join(rl, pth)
                with open(fp, encoding="utf-8", errors="replace") as fh:
                    for ln in fh:
                        m = re.match(r"^(?:\+\+\+ b/|--- a/|diff --git a/)(\S+)", ln)
                        if m:
                            n = os.path.join(prefix, m.group(1)) if prefix else m.group(1)
                            if any(n == r or n.startswith(r.rstrip("/") + "/") for r in rels):
                                return True
            return False
        if sub == "clean":
            names = _git(repo, "clean", "-n", "-d", *[a for a in rest if a.startswith("-") and a not in ("-f", "--force")])
            return any(any(r in n for r in rels) for n in names)
    except Exception:
        return None
    return None


GOVERNOR_TOKEN_RE = re.compile(r"\.claude/|machine_config\.yaml|routing_policy\.yaml|hooks_approved\.sha256"
                               r"|adapters/claude-code", re.IGNORECASE)
GOVERNOR_FALLBACK_RE = re.compile(
    r"(>|\b(sed\s+-[A-Za-z]*i|tee|truncate|cp|mv|rsync|rm|unlink|trash|chmod|install|dd|patch|ln|tar|unzip|ditto|find)\b)[^;&|\n]*"
    r"(\.claude/(settings(\.local)?\.json|hooks)|machine_config\.yaml|routing_policy\.yaml|hooks_approved\.sha256"
    r"|adapters/claude-code)",
    re.IGNORECASE,
)

# Scripts that WRITE the governor themselves, recognised as the EXECUTED script + flag (Seat A F1; Seat B
# B-30: merely naming them, `echo bootstrap.sh`, is not running them).
GOVERNOR_SCRIPTS = (
    ("hook_support.py", "--write-approved", "governance/hooks_approved.sha256"),
    ("install_adapters.py", "--apply", "the fleet's generated .claude/settings.json files"),
    ("bootstrap.sh", None, ".claude/hooks (canonical install)"),
)


def _applescript_archive_removal(script: str) -> bool:
    """B5-07 (reviewer A round 5): Finder/System Events `delete X`, `move X to trash` or `move X to ...` where X is a string
    literal naming an archive (or a NEOCORTEX directory). Moving something INTO an archive stays allowed."""
    for ln in script.replace("\r", "\n").split("\n"):
        m = re.search(r"\b(delete|remove|move)\b(?:\s+(?:POSIX\s+file|file|folder|item|alias|disk\s+item))*\s*\(?\s*\"((?:[^\"\\]|\\.)*)\"",
                      ln, re.IGNORECASE)
        if not m:
            continue
        lit = _applescript_unescape(m.group(2))[0]
        if _ARCH_TOK_RE.search(lit) or os.path.basename(lit.rstrip("/:")).lower() == "neocortex":
            return True
    return False


def _applescript_unescape(body: str) -> tuple:
    """(decoded literal, has an escape we do not model). B2-08: AppleScript decodes \\n \\r \\t too."""
    out, i, bad = [], 0, False
    table = {"n": "\n", "r": "\r", "t": "\t", '"': '"', "\\": "\\"}
    while i < len(body):
        c = body[i]
        if c == "\\" and i + 1 < len(body):
            nxt = body[i + 1]
            if nxt in table:
                out.append(table[nxt])
            else:
                bad = True
                out.append(nxt)
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out), bad


def _shell_strings_in_osascript(script: str) -> tuple:
    """(literal shell strings, count of `do shell script` expressions that are NOT a plain literal).
    Seat A F5; Seat B B-09: `do shell script ("…")` is a literal too; anything else is unresolvable."""
    out, unresolved = [], 0
    for m in re.finditer(r"do\s+shell\s+script\b", script, re.IGNORECASE):
        tail = script[m.end():]
        lm = re.match(r'\s*\(?\s*"((?:[^"\\]|\\.)*)"\s*\)?', tail)
        if lm and not re.match(r"\s*&", tail[lm.end():]):
            lit, bad = _applescript_unescape(lm.group(1))
            if bad:
                unresolved += 1
            out.append(lit)
        else:
            unresolved += 1
    return out, unresolved


def _analyse_writes(cmd: str, cwd: str = ""):
    """(first protected hit or None, writes_something)."""
    try:
        segs = _scoped_segments(cmd, cwd)
    except ValueError:
        if _MODE["l0"]:
            return None, False                           # L0 mode: l0_write_target's own fallback decides
        m, complete = _bounded_search((GOVERNOR_FALLBACK_RE,), _strip_heredocs(cmd), GOVERNOR_TOKEN_RE)  # B2-23
        if not m and not complete:
            return "an unparsed long command (window limit)", True
        return (m.group(3) if m else None), bool(m) or bool(re.search(r"(?<![<>&])>>?\s*[^\s&>]", cmd))
    any_write = False
    for words, targets, here, stdin in segs:
        for t in targets:
            ut = _unquote(t)
            # B-34: capturing output to a temp file or a log is not a source edit (loop_bound's "fix")
            if ut not in ("/dev/null", "/dev/stdout", "/dev/stderr") and not t.isdigit() \
                    and not ut.startswith(_TEMP_ROOTS) and not ut.endswith((".log", ".out")):
                any_write = True
            h = _governor_path(t, here, False)
            if h:
                return h, True
        if not words:
            continue
        h, w = _segment_hit(words, here, cwd, stdin)
        any_write = any_write or w
        if h:
            return h, True
    return None, any_write


def l0_parsed_write(cmd: str, cwd: str = "") -> bool:
    """B3-15 (reviewer A round 3): the governor's repaired write parser (redirect operands, wrapper values, `>&`,
    conditional writers, `-t`, side outputs) aimed at the L0 basename. False on any parse failure: the
    tokenized `l0_write_target` and its fallback still run."""
    _MODE["l0"] = True
    try:
        hit = _analyse_writes(cmd, cwd)[0]
        return bool(hit) and _is_l0(hit)
    except Exception:
        return False
    finally:
        _MODE["l0"] = False


def governor_write_target(cmd: str, cwd: str = ""):
    """The first protected path (or worktree) the command would modify, or None."""
    return _analyse_writes(cmd, cwd)[0]


def writes_any_file(cmd: str) -> bool:
    """True if the command writes some file (loop_bound's edit detection; formatters count, Seat B B-23)."""
    try:
        return _analyse_writes(cmd, "")[1]
    except Exception:
        return False


def _quiet_telemetry(event: str) -> None:
    try:
        _telemetry(event)
    except Exception:
        pass


_H9_DATA = None      # B4-22: the payload of a verifier call whose loop-bound reason must ride on ANY ask
_T_START = __import__("time").monotonic()
_H9_BUDGET = 6.0     # B5-19 (reviewer A round 5): seconds after hook start by which the loop bound must have answered


class _H9Timeout(Exception):
    pass


def _h9_check(lb, data):
    """B5-19: `loop_bound.check` within the hook's remaining budget (registered timeout 10 s). Past it the H9 reason
    is skipped and logged, so an ask already decided (GOVERNOR, GUARD LIMIT, GUARD ERROR) still prints in time."""
    import signal
    import time as _t
    left = _H9_BUDGET - (_t.monotonic() - _T_START)
    if left <= 0.05:
        raise _H9Timeout("no time left")

    def _alarm(_s, _f):
        raise _H9Timeout("loop bound over budget")
    old = signal.signal(signal.SIGALRM, _alarm)
    signal.setitimer(signal.ITIMER_REAL, left)
    try:
        return lb.check(data, writes_any_file)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)


def ask(reason: str) -> None:
    global _H9_DATA
    if _H9_DATA is not None:
        # B4-22 (reviewer A round 4): another ask on a verifier call used to pre-empt the H9 ask, and approving it then
        # counted as approving the loop. The loop-bound reason now travels in the same ask.
        d, _H9_DATA = _H9_DATA, None
        try:
            lb = _sibling("loop_bound")
            extra = _h9_check(lb, d) if lb is not None else None
            if extra and extra not in reason:
                reason = reason + " | ALSO " + extra
        except Exception as exc:
            _log_error("loop_bound", exc)
    # Seat A F16: print the decision FIRST and always exit 0 — a telemetry failure must never
    # turn a decision into a crash (a non-zero exit makes the harness discard the decision).
    try:
        print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "ask",
            "permissionDecisionReason": reason,
        }
    }))
        sys.stdout.flush()
    finally:
        _quiet_telemetry(f"PRETOOL-ASK {reason[:60]}")
        sys.exit(0)


def deny(reason: str) -> None:
    try:                                       # Seat A F16: decision first, exit 0 always
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        }))
        sys.stdout.flush()
    finally:
        _quiet_telemetry(f"PRETOOL-DENY {reason[:60]}")
        sys.exit(0)


def forbidden_window() -> 'str | None':
    """Return the window string if now is inside ANY machine_config window.

    Every `window: "HH:MM-HH:MM"` line is read (S4: only the first used to count, so a second
    window added to the file would have been silently ignored by the hook)."""
    text = _safe_read(ROOT, ("governance", "machine_config.yaml"))
    if text is None:
        return "UNREADABLE"          # Seat B B-20: the caller ASKS instead of silently allowing
    now = datetime.datetime.now()
    cur = now.hour * 60 + now.minute
    for m in re.finditer(r'window:\s*"(\d{2}):(\d{2})-(\d{2}):(\d{2})"', text):
        start = int(m.group(1)) * 60 + int(m.group(2))
        end = int(m.group(3)) * 60 + int(m.group(4))
        inside = (start <= cur or cur < end) if start > end else (start <= cur < end)
        if inside:
            return f"{m.group(1)}:{m.group(2)}-{m.group(3)}:{m.group(4)}"
    return None


def _deny_checks(cmd: str, cwd: str) -> None:
    """L0 write, archive destruction and extraction-window DENYs on one command text (the Bash command, and each
    decoded `do shell script` string of an AppleScript payload, B4-16)."""
    # DEFENCE-IN-DEPTH, not a complete sandbox: catches the common direct, find/xargs,
    # and quoted-mv forms — a determined shell can still evade (variables, globs, `mv -t`,
    # base64/eval). Art. 6's real protection: the user performs genuine archive ops manually.
    if l0_write_target(cmd) or l0_parsed_write(cmd, cwd):
        # Founder ruling S4-40 (2026-10-05, option A): agents never write L0, not even in a review the
        # founder convened; the lawful path is draft-then-founder-applies, so no forgeable exception exists.
        deny("DENY-ARCHIVE / L0 WRITE-GUARD (Constitution invariant 1; founder ruling S4-40, 2026-10-05): "
             "PAICodeConstitution-2026.md is L0 and agents never write it, even during a Constitutional "
             "Review the founder convened (Art. 11). Reads are fine. Lawful path: record the review and the "
             "consent in NEOCORTEX/JOURNAL.md (Art. 6), DRAFT the amendment in "
             "governance/NEOCORTEX/DRAFT_L0_amendment_<YYYY-MM-DD>.md, and the founder applies it from his "
             "own terminal (founder-run script: line-budget check, diff, commit).")

    flat = re.sub(r"\\\r?\n", " ", cmd).replace("\r", "\n").replace("\n", " ; ")   # a newline separates commands like `;`
    arch_hit, arch_complete = _bounded_search(_ARCHIVE_RES, flat, ARCHIVE_TOKEN_RE)   # B2-23: bounded windows
    # B3-14 (reviewer A round 3): the EXECUTABLE text (heredoc data removed) is scanned too; it is usually short, so
    # the full patterns run on it, and a quote cut by a window there is incomplete (-> ask), never complete.
    flat_x = re.sub(r"\\\r?\n", " ", _strip_heredocs(cmd)).replace("\r", "\n").replace("\n", " ; ")
    x_hit, x_complete = (_bounded_search(_ARCHIVE_RES, flat_x, ARCHIVE_TOKEN_RE, quote_aware=True)
                         if flat_x != flat else (None, True))   # no heredoc: the same text was just scanned
    arch_hit = arch_hit or x_hit or archive_parsed_hit(cmd, cwd)       # B4-17: parsed executable basenames
    arch_complete = arch_complete and x_complete
    if arch_hit:
        deny("DENY-ARCHIVE (Art. 6): archives (_archive/, ANTIGRAVITY-2025.md) are "
             "never deleted or moved OUT by an agent (moving INTO _archive/ is "
             "fine). If genuinely intended, the user performs it manually.")
    if _ARCH_DEEP:
        where, _ARCH_DEEP[:] = _ARCH_DEEP[0], []
        ask(f"ARCHIVE ANCESTOR (Art. 6, B5-36): this command deletes or moves out {where}, which holds an archive "
            f"(_archive/, ANTIGRAVITY-2025.md or a NEOCORTEX directory) further down. Approve only if that copy may go "
            f"(e.g. a review clone or staging copy); a live project's archive is never removed by an agent.")
    if not arch_complete:
        ask("GUARD LIMIT (H0, B2-23): this long command names an archive or L0 in more places, or inside longer "
            "segments, than the DENY check scans within the hook's time budget. Approve only if it deletes or "
            "moves nothing out of an archive.")

    window = forbidden_window() if _is_extraction_cmd(cmd) else None
    if window == "UNREADABLE":
        ask("GUARD ERROR (H0, DENY-WINDOW): governance/machine_config.yaml could not be read, so the "
            "forbidden extraction window cannot be checked for this extraction/vectorization command. "
            "Approve only if no forbidden window applies now.")
    if window:
        deny(f"DENY-WINDOW (machine_config.yaml): extraction/vectorization is "
             f"forbidden during {window} (02:40 LaunchAgent collision). Defer or "
             f"ask the user to override manually.")



def _guard(data: dict) -> None:
    """The DENY matchers (+ the H4a governor ask). Any exception escaping this function is turned
    into an ASK by main() (H0): the DENY class fails closed-to-ask, never open, never to a hard deny."""
    ti = data.get("tool_input", {})
    if not isinstance(ti, dict):
        # B2-24 (reviewer A round 2b): a falsy non-object ([], "", false, 0, null) used to become {} and ALLOW.
        # The hook cannot see a command in it: a guard error, which main() turns into an ASK (H0).
        raise TypeError(f"tool_input is {type(ti).__name__}, not an object")
    if data.get("tool_name") in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        # B4-08 (reviewer A round 4): the Edit/Write tools reach the approval list and the hooks too. This branch runs only where
        # the settings route these tools to the guard (the published settings do not: they route Bash and the osascript
        # tool); L0 itself is denied for Edit/Write by the settings' static permissions.
        fp = str(ti.get("file_path") or ti.get("notebook_path") or "")
        if fp and _is_l0(fp):
            deny("DENY-ARCHIVE / L0 WRITE-GUARD (Constitution invariant 1; founder ruling S4-40): agents never write "
                 "PAICodeConstitution-2026.md. Draft the amendment in governance/NEOCORTEX/DRAFT_L0_amendment_<date>.md.")
        hit = _governor_path(fp, str(data.get("cwd") or "") or str(ROOT), False) if fp else None
        if hit or (fp and os.path.basename(fp).lower() == "hooks_approved.sha256"):
            ask(f"GOVERNOR WRITE (H4a, {data.get('tool_name')} tool): this edit modifies {hit or fp}, part of the "
                f"enforcement layer. The founder approves each such write.")
        sys.exit(0)
    cmd = " ".join(str(ti.get(k, "")) for k in ("command", "script", "code"))
    if not cmd.strip():
        sys.exit(0)

    # DEFENCE-IN-DEPTH, not a hard sandbox: this matches literal command text only.
    # Shell variable indirection (e.g. OP=person; delete $OP), Unicode homoglyphs, and
    # multi-step command composition are NOT detected. Do not over-rely on this guard.
    if re.search(r"\bdelete\b(\s+|\s*\(\s*)(the\s+|every\s+|all\s+|first\s+|last\s+)?(person|people)\b(?!\s*\|)", cmd, re.IGNORECASE):
        deny("DENY-CONTACT-DELETE (Art. 4 / the 2026-03 contact-deletion incident): AppleScript "
             "'delete person' is forbidden. Use group-membership changes or ask "
             "the user to delete manually.")

    if "command" not in ti and _applescript_archive_removal(cmd):
        deny("DENY-ARCHIVE (Art. 6): this AppleScript deletes, trashes or moves out an archive (_archive/, "
             "ANTIGRAVITY-2025.md, or a NEOCORTEX directory). If genuinely intended, the user performs it manually.")

    # Oversize commands (reviewer A round 2, residual B-19): only LINEAR checks run on them — the L0 fallback
    # pattern and the archive patterns still DENY; anything else ASKS before any tokenizing.
    if len(_strip_heredocs(cmd)) > _MAX_CHECKED_CHARS:
        flat_big = re.sub(r"\\\r?\n", " ", cmd).replace("\r", "\n").replace("\n", " ; ")
        if _bounded_search((L0_WRITE_FALLBACK_RE,), cmd, L0_TOKEN_RE)[0]:          # B2-23: windows only
            deny("DENY-ARCHIVE / L0 WRITE-GUARD (Constitution invariant 1): an oversize command writes "
                 "PAICodeConstitution-2026.md (linear check). Agents never write L0 (founder ruling S4-40).")
        if _bounded_search(_ARCHIVE_RES, flat_big, ARCHIVE_TOKEN_RE)[0]:
            deny("DENY-ARCHIVE (Art. 6): archives (_archive/, ANTIGRAVITY-2025.md) are never deleted or moved "
                 "OUT by an agent (oversize command, linear check).")
        ask(f"GUARD LIMIT (H0): this command is longer than {_MAX_CHECKED_CHARS:,} characters (heredoc bodies "
            f"excluded), too long to check within the hook's time budget. Approve only if you know what it does.")

    _deny_checks(cmd, str(data.get("cwd") or ""))

    # Bash command, or every `do shell script` string of an osascript payload (Seat A F5, Seat B B-09).
    if "command" in ti:
        shells = [str(ti.get("command", ""))]
    else:
        shells, unresolved = _shell_strings_in_osascript(cmd)
        for sh in shells:
            _deny_checks(sh, str(data.get("cwd") or ""))   # B4-16; B5-17: every decoded DENY before the unresolved ask
        if unresolved:
            ask("GOVERNOR CHECK (H4a): this AppleScript runs `do shell script` on an expression that is not a "
                "plain string literal, so the shell command cannot be checked. Approve only if you know what it runs.")
    for sh in shells:
        if len(_strip_heredocs(sh)) > _MAX_CHECKED_CHARS:
            ask(f"GUARD LIMIT (H0): this command is longer than {_MAX_CHECKED_CHARS:,} characters (heredoc bodies "
                f"excluded), too long to check within the hook's time budget. Approve only if you know what it does.")
        hit = governor_write_target(sh, str(data.get("cwd") or ""))
        if hit:
            ask(f"GOVERNOR WRITE (H4a): this command modifies {hit}, part of the enforcement layer "
                f"(hooks, their settings, the canonical kit, or the policy data they read). The "
                f"founder approves each such write. Approve if this is a sanctioned landing or "
                f"configuration change; deny otherwise.")
            break


def main() -> None:
    # H0 (PLAN_hooks_outcome_tamper_loopbound_2026-10-05 §3): until 2026-10-05 every error path here
    # was `sys.exit(0)` -- a guard that crashed allowed the command it could not check (reviewer A S4-25).
    # Now an unreadable payload or a crashing matcher ASKS the founder. Never a hard deny: a lockout
    # stays impossible (the founder can always approve), but a dead guard is no longer silent.
    try:
        data = json.load(sys.stdin)
        if not isinstance(data, dict):
            raise ValueError("payload is not a JSON object")
    except Exception as exc:
        _log_error("pretool_guard.payload", exc)
        ask(f"GUARD ERROR (H0, fail-closed-to-ask): the hook payload could not be read "
            f"({type(exc).__name__}), so the DENY guards could not check this command. "
            f"Approve only if you know what the command does.")
    global _H9_DATA
    try:
        if isinstance(data.get("tool_input"), dict) and "command" in data["tool_input"]:
            _H9_DATA = data
        _guard(data)
    except SystemExit:
        raise
    except Exception as exc:
        _log_error("pretool_guard.matchers", exc)
        ask(f"GUARD ERROR (H0, fail-closed-to-ask): the DENY guard crashed "
            f"({type(exc).__name__}: {str(exc)[:80]}) while checking this command. "
            f"Approve only if you know what the command does.")
    # H9 -- Art. 14 loop bound. ADVISORY class: its own errors fail OPEN (logged), never ask.
    try:
        ti = data.get("tool_input", {}) or {}
        _H9_DATA = None
        lb = _sibling("loop_bound") if "command" in ti else None
        if lb is not None:
            reason = _h9_check(lb, data)
            if reason:
                ask(reason)
    except SystemExit:
        raise
    except Exception as exc:
        _log_error("loop_bound", exc)
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except BaseException as _exc:             # Seat A F16: last resort, outside main's own handlers
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse", "permissionDecision": "ask",
            "permissionDecisionReason": f"GUARD ERROR (H0, last resort): {type(_exc).__name__}. "
                                        f"Approve only if you know what the command does."}}))
        sys.exit(0)

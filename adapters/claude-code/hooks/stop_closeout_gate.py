#!/usr/bin/env python3
"""stop_closeout_gate.py — CLOSEOUT-GATE policy (POLICY_CORE.md), Claude Code adapter.

Stop hook. Parses the session transcript (JSONL) and blocks the turn-end ONCE
when closeout duties are visibly unmet:
  1. Code files were edited but no verification command ran — a Bash call
     matching VERIFY_MARKERS (pytest / py_compile / bash -n / osacompile /
     test invocation), OR a Browser-pane MCP call that actually inspects the
     rendered result (console/network/JS-eval/page-read/screenshot — see
     BROWSER_VERIFY_TOOLS). A tool_use whose own tool_result came back
     is_error=true is never counted as an edit in the first place (2026-09-14:
     a mistyped, erroring Edit call kept re-appearing in the block message for
     a path that was never actually written).
  2. The working project's NEOCORTEX/JOURNAL.md exceeds its 64 KB bound
     (NEOCORTEX_SPEC §2 rotation rule).

It ALSO runs a project-scoped continuity sweep AND the staleness linter
(`closeout_lint.py`, M-9) as NON-BLOCKING ADVISORIES
(founder ruling 2026-07-27): findings are recorded to governance/pilot_telemetry.log
with a CLOSEOUT-ADVISORY marker and echoed to stderr. They never enter `problems`
and can never block a turn — 11 of 25 projects fail today, so a blocking wire
would jam turn-end fleet-wide. Project-scoped costs ~0.34 s (measured); the
fleet-wide sweep costs ~8.7 s and is deliberately NOT run here.

Loop safety: if stop_hook_active is set (we already blocked once this turn),
exit silently — a gate that loops is worse than no gate.
"""
import os as _os0, sys as _sys0
# B4-10 / B5-01 (reviewer A rounds 4-5): Python puts this script's directory first on sys.path, so a file in .claude/hooks/
# named like a stdlib module (json.py, __future__.py, ...) would run inside this hook before any check. Drop that
# entry FIRST: no import may precede this block (not even `from __future__`, which is itself an import).
if __name__ == "__main__" and _sys0.path and \
        _os0.path.realpath(_sys0.path[0] or ".") == _os0.path.dirname(_os0.path.realpath(__file__)):
    del _sys0.path[0]

import datetime
import hashlib
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import time
import sys
import time
from pathlib import Path

def _resolve_root(start: Path) -> Path:
    """Workspace root, by marker or env — not by counting ".." hops.

    `parent.parent.parent` is right for hooks at <root>/.claude/hooks/ and wrong everywhere
    else; in the published kit (adapters/claude-code/hooks/) it lands on adapters/, so the
    continuity sweep and the C3 gate could never be found and both checks were silently
    born dead. Fourth file in this kit with the same defect (§5.5 panel, 2026-08-25).
    A hook must never raise, so every branch here falls back rather than failing.
    """
    import os
    env = os.environ.get("GOVERNANCE_WORKSPACE")
    if env:
        cand = Path(env).expanduser()
        if cand.is_dir():
            return cand.resolve()          # S4-48 (final): the trusted anchor is the RESOLVED root
    here = start.parent                      # start is the FILE; walk from its directory
    for candidate in (here, *here.parents):
        if (candidate / ".claude" / "settings.json").is_file() \
                and (candidate / ".claude" / "hooks").is_dir():
            return candidate
        # Either instrument this gate consults is also a valid root marker. The version-drift
        # fixture builds a workspace with _skills/ but no settings.json, and it is a legitimate
        # root: resolving it positively beats relying on the ..-hop fallback.
        if (candidate / "_skills" / "build_index.py").is_file():
            return candidate
        if (candidate / "governance" / "scripts" / "continuity_sweep.py").is_file():
            return candidate
    return start.parent.parent.parent


ROOT = _resolve_root(Path(__file__).resolve())


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


def _append_ledger(proj_root: Path, findings: list, ledger_path: 'Path | None' = None) -> None:
    """Append one JSONL record per finding to governance/precision_ledger.jsonl — raw material
    for the precision ledger STATUS item 3 named and left unbuilt (2026-08-28 ruling: log now,
    build the reader/gate later, because there was no traffic to build them ON). Deliberately
    just this: no disposition, no aggregation, no gate, no consumer yet.

    Each occurrence gets its OWN line (append-only, like every other durable log in this
    project) rather than a single mutated row, so a later reader can see recurrence over time,
    not just current state. The id is a hash of (check, path, detail) — NOT line: a finding
    that shifts a few lines because of an unrelated edit above it must keep its identity, or a
    later disposition ("this got fixed on 09-02") could never be matched back to it.

    `ledger_path`, injectable for exactly one reason: this file is SHARED, real, and — proven
    live while this very function was first tested — actively appended to by OTHER concurrent
    sessions. A test that let its synthetic fixture findings fall into the default path would
    contaminate the one thing this instrument is for: telling real findings from noise. Three
    such lines already landed before this parameter existed (2026-08-28); left in place rather
    than risk a lost-update race rewriting a file another session was appending to at the same
    moment — noted here, not silently scrubbed.

    Unbounded growth is a known, stated limit, not a silently accepted one — this item's scope
    was the logging half only; rotation/pruning is the ledger-reader's problem, whenever that
    gets built.
    """
    ts = datetime.datetime.now().isoformat(timespec="seconds")
    lines = []
    for f in findings:
        check = str(f.get("check", "?"))
        path = str(f.get("path", "?"))
        detail = str(f.get("detail", ""))[:200]
        fid = hashlib.sha256(f"{check}|{path}|{detail}".encode()).hexdigest()[:16]
        rec = {"id": fid, "ts": ts, "proj": proj_root.name, "check": check,
              "path": path, "line": f.get("line", 0), "detail": detail}
        lines.append(json.dumps(rec))
    if not lines:
        return
    if ledger_path is not None:
        try:
            with open(ledger_path, "a", encoding="utf-8") as fh:
                fh.write("\n".join(lines) + "\n")
        except OSError:
            pass
        return
    try:
        _safe_append(ROOT, ("governance", "precision_ledger.jsonl"), "\n".join(lines) + "\n")
    except OSError:
        pass


CODE_SUFFIXES = (".py", ".sh", ".applescript", ".js", ".ts", ".html", ".css",
                 # S4-22 (final, 2026-10-05): the common source types were outside the gate.
                 ".tsx", ".jsx", ".mjs", ".cjs", ".vue", ".svelte", ".scss", ".go", ".rs", ".swift", ".java",
                 ".kt", ".c", ".cc", ".cpp", ".h", ".hpp", ".m", ".mm", ".rb", ".php", ".sql", ".zsh", ".bash",
                 ".ipynb", ".lua", ".pl", ".r", ".scala", ".cs")
WEB_SUFFIXES = (".html", ".css", ".scss", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".vue", ".svelte")
# B2-20 (reviewer A round 2b): a browser inspection clears only UI files. `.js`/`.ts` may be backend code (a page
# read is no test of `backend/auth.ts`), so they are no longer cleared by it.
BROWSER_CLEARS = (".html", ".css", ".scss", ".vue", ".svelte", ".jsx", ".tsx")
VERIFY_MARKERS = ("pytest", "py_compile", "bash -n", "osacompile",
                  "npm test", "python3 -m unittest", "test_", "--dry-run")

# A project with no pytest/compile step (a static HTML/CSS/JS page, e.g.) is verified by
# actually looking at the rendered result, not by a Bash test runner. Before this list
# existed, that kind of verification was invisible to the gate: real edits, followed by a
# real render check (console errors read, DOM/JS queried, a screenshot taken), still left
# `edited_since_verify` non-empty because NOTHING in the transcript ever matched a Bash
# VERIFY_MARKERS substring — a false CLOSEOUT-GATE block on every subsequent turn even
# though the work had genuinely been checked. Found live, 2026-09-14 (example-project):
# scrolled/screenshotted/queried the rendered page after each edit, all session, and the
# gate kept citing edits from turns earlier as still-unverified.
# `computer` is deliberately NOT listed here in full — only a screenshot actually inspects
# something; a click or type is an interaction, not verification (checked at the call site).
BROWSER_VERIFY_TOOLS = (
    "mcp__Claude_Browser__read_console_messages",
    "mcp__Claude_Browser__javascript_tool",
    "mcp__Claude_Browser__read_network_requests",
    "mcp__Claude_Browser__get_page_text",
    "mcp__Claude_Browser__read_page",
)

# Paths that are NOT project deliverables and therefore carry no closeout duty.
# Scratchpad/temp artifacts (a throwaway .html rendered to look at, a temp .sh) hit
# CODE_SUFFIXES exactly like real source does, but nothing here is ever shipped,
# reviewed or committed — and the only verification the gate can SEE is a Bash
# VERIFY_MARKERS substring, so a scratchpad artifact verified by looking at it in a
# browser (or by any non-Bash tool) is structurally unclearable. That combination
# produced a recurring FALSE BLOCK on turn-end.
#
# DENY-list, deliberately, not an allow-list of project roots: an allow-list would
# have to enumerate every governed tree and would silently exempt anything it forgot
# — real fleet-skill code under ~/.claude/skills/, a project cloned to a new path.
# A fixed-prefix deny-list can only ever exempt these four; everything else still
# gates UNLESS it also matches _is_scratch_path below, the second, segment-based
# exemption this same rationale extends to (see its own docstring).
# (/tmp and /var/folders are listed alongside their /private/… realpaths because a
# tool may report either form on macOS.)
NON_CLOSEOUT_PREFIXES = ("/private/tmp/", "/tmp/",
                         "/private/var/folders/", "/var/folders/")


def _is_scratch_path(fp: str) -> bool:
    """True if `fp` has a `scratch` path component anywhere — a governed project's OWN
    scratch/ directory (workspace convention, e.g. `<project>/scratch/`), exempted for
    the identical reason NON_CLOSEOUT_PREFIXES exempts /tmp/ above: this gate can only
    see a VERIFY_MARKERS substring in a Bash call, so a scratch artifact checked by any
    non-Bash means (the Browser pane, a screenshot, direct inspection) is structurally
    unclearable, producing the same recurring false block NON_CLOSEOUT_PREFIXES was
    built to stop — just one directory short of covering it, since a project's own
    scratch/ sits at a different absolute prefix per project and NON_CLOSEOUT_PREFIXES
    can only list fixed strings.

    Found live, 2026-09-09 (PROJECT-A): `scratch/svg_to_png.html`, written then `rm -f`'d
    many turns earlier, re-blocked FOUR consecutive turns with identical text — the
    /tmp/ exemption did not apply because the file lived under the project's own
    scratch/, not a session temp dir.

    Segment-matched via Path(...).parts, not a substring match, so an unrelated name
    that merely CONTAINS "scratch" (e.g. a "scratchpad-notes" directory) does not
    false-exempt — only an exact `scratch` path component does.
    """
    try:
        return "scratch" in Path(fp).parts
    except Exception:
        return False


def _is_non_closeout_path(fp: str) -> bool:
    """True if `fp` carries no closeout duty at all: under a fixed NON_CLOSEOUT_PREFIXES
    root, or under any project's own scratch/ (see _is_scratch_path). Single call site
    for both checks so the two exemption mechanisms stay visibly paired rather than
    drifting apart at separate call sites."""
    return fp.startswith(NON_CLOSEOUT_PREFIXES) or _is_scratch_path(fp)

# --- Bash-edit detection (M-13, founder ruling 2026-09-04: widen the gate now) ---
# CRUMBLES.md 2026-08-26 named the gap this closes: a session editing exclusively through
# Bash (heredoc/sed/cat>/tee, the harness's own "auto mode" default) left `edited_code`
# empty all session, so this gate never fired for it — the same blind spot also silently
# disabled the ARMED verify-gate path below, which is keyed on the same list.
#
# Reuses pretool_guard.py's l0_write_target() technique token-for-token (shlex(posix=False)
# so a quoted path with spaces survives; segment-split on ;|&&|| so each pipeline stage
# gets its own verb; explicit tables for tee/truncate/dd/cp/mv/rsync/install) rather than a
# plain regex — measured there at 28/29 on a naive regex, the one failure being exactly the
# false-deny a regex can't avoid: it cannot tell a redirect operator from a `>` inside a
# quoted string or grep pattern. Generalized here from "is this L0" to "return every write
# target", so CODE_SUFFIXES/NON_CLOSEOUT_PREFIXES filtering stays the caller's job, same
# division of labor the Edit/Write/NotebookEdit branch already uses.
#
# KNOWN LIMITATION, stated not hidden: no heredoc-body awareness. A multi-line Bash command
# whose HEREDOC BODY happens to contain text that reads like a write statement (e.g. prose
# describing a `sed -i` example, being written INTO a doc) can add a spurious extra path to
# the window. Failure direction is over-inclusion — asking for verification that wasn't
# strictly needed — never under-detection of a real edit, the safer of the two failure
# modes for a gate whose whole purpose is not missing edits. Documented and exercised by
# test_stop_closeout_gate.py::TestBashEditDetection.test_heredoc_body_false_positive_is_the_known_tradeoff.
_DEST_VERBS = {"cp", "mv", "rsync", "install"}
_NAMED_WRITE_VERBS = {"tee", "truncate"}


def _unquote(tok: str) -> str:
    if len(tok) >= 2 and tok[0] == tok[-1] and tok[0] in "\"'":
        return tok[1:-1]
    return tok


_UNRESOLVED = "<unresolved-tilde>/"     # a relative marker: _qualify keeps it relative, so the placement rule reads a resolution failure


def _posix_unquote(tok: str) -> str:
    """Shell quote and escape removal of ONE word (the tokenizer keeps quote characters); a malformed word keeps `_unquote`."""
    if not tok:
        return tok
    try:
        parts = shlex.split(tok, posix=True)
    except ValueError:
        return _unquote(tok)
    return parts[0] if len(parts) == 1 else _unquote(tok)


def _tgt(tok: str) -> str:
    """A write/rm target as the shell reads the word (s241 F-1, s242 F-1). The tilde-prefix is the part up to the first `/`; it is
    eligible only when it holds no quote or backslash. `~` and `~user` expand (`expanduser`); `~+`, `~-` and a user that does not
    exist are UNRESOLVED: the word becomes `_UNRESOLVED + word`, a relative path that `_qualify` leaves relative, which the
    placement rule reads as a resolution failure (blocks), never a folder under the cwd. Then quotes and escapes are removed from the
    whole word. This is the only place a `~` is expanded: `_qualify` is the plain join the receipt CLI uses."""
    if tok[:1] == "~":
        import os
        if set(tok) & set("\\$`"):                    # s243 F-1: a backslash, `$` or backtick anywhere: not resolved here (Bash differs from shlex)
            return _UNRESOLVED + tok
        cut = tok.find("/")
        prefix, rest = (tok, "") if cut < 0 else (tok[:cut], tok[cut:])
        if not (set(prefix) & set("\"'\\")):
            home = os.path.expanduser(prefix) if prefix not in ("~+", "~-") else prefix
            if home == prefix:
                return _UNRESOLVED + _posix_unquote(tok)
            return home + _posix_unquote(rest)
    return _posix_unquote(tok)


_ASSIGN_RE = None


def _record_assignments(words: list, amap: dict) -> bool:
    """B5-18 (reviewer A round 5): True when this simple command only assigns shell variables (`D=src`, `export D=src`,
    `declare`/`local`/`readonly D=src`); the values (with earlier variables substituted) are recorded in `amap`
    IN ORDER, so a write resolves with the value it actually sees, and text such as `echo D=/tmp` or a comment
    is never an assignment."""
    import re
    w = [x for x in words]
    if w and w[0] in ("export", "declare", "local", "readonly", "typeset"):
        w = [x for x in w[1:] if not x.startswith("-")]
    if not w or not all(re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", x) for x in w):
        return False
    for x in w:
        name, val = x.split("=", 1)
        val = _unquote(val)
        if "$" in val:
            val = re.sub(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?", lambda m: amap.get(m.group(1), "\x00"), val)
        amap[name] = val
    return True


def _resolve_vars(targets: list, cmd: str, amap: dict = None) -> list:
    """(e) 2026-10-06, first field report after P7: write targets spelled through a shell variable (`$SP/x.py`,
    `${D}/y.swift`) are resolved from assignments in the same command, then from the environment; a target that
    still holds an unresolved variable is DROPPED (its location is unknown: the same documented limit as the
    guard's variable indirection). A `~` is NOT expanded here: the parser expands an unquoted one (`_tgt`)."""
    import os
    import re
    global _ASSIGN_RE
    if _ASSIGN_RE is None:
        _ASSIGN_RE = re.compile(r"(?:^|[;&|\s(])([A-Za-z_][A-Za-z0-9_]*)=(\"[^\"]*\"|'[^']*'|[^\s;&|()]*)")
    if amap is not None:
        assigns = amap                         # B5-18: the ordered values seen before this command
    else:
        # no segment order known: a variable assigned more than once (or in a string) is ambiguous -> unresolved
        seen = {}
        for m in _ASSIGN_RE.finditer(cmd):
            seen.setdefault(m.group(1), set()).add(_unquote(m.group(2)))
        assigns = {k: next(iter(v)) for k, v in seen.items() if len(v) == 1}
    out = []
    for t in targets:
        if t.startswith(_UNRESOLVED):          # s243 F-1: a marked word is a resolution failure, kept (never dropped as a variable)
            out.append(t)
            continue
        if "$" in t:
            def sub(m):
                v = assigns.get(m.group(1), None if amap is not None and m.group(1) in amap else
                                os.environ.get(m.group(1)))
                return v if v and "\x00" not in v else "\x00"
            t = re.sub(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?", sub, t)
            if "\x00" in t or "$" in t:
                continue
        out.append(t)
    return out


def _bash_write_targets(cmd: str) -> list:
    """Every file path `cmd` writes, overwrites, or truncates — best-effort, not a parser.

    Reads return nothing by construction: a path counts only as a redirect target, an
    in-place-edit/named-write verb's operand, or a copy/move destination — never merely an
    argument (so `cat x.py`, `grep foo x.py`, `python3 x.py` are all silent).
    """
    try:
        lx = shlex.shlex(cmd, posix=False, punctuation_chars=True)
        lx.whitespace_split = True
        toks = list(lx)
    except ValueError:
        return []

    segments, cur = [], []
    for tok in toks:
        if tok in (";", "|", "||", "&&", "&"):
            segments.append(cur)
            cur = []
        else:
            cur.append(tok)
    segments.append(cur)

    targets = []
    for seg in segments:
        for i, tok in enumerate(seg):
            # B2-18: every file-redirect spelling (`>|`, `&>`, `&>>`, and `>&` when its word is not an fd)
            if tok in (">", ">>", ">|", "&>", "&>>", ">&") and i + 1 < len(seg):
                tgt = _tgt(seg[i + 1])
                if tok == ">&" and (tgt.isdigit() or tgt == "-"):
                    continue
                targets.append(tgt)
        words, skip = [], False
        for w in seg:
            if skip:
                skip = False
                continue
            if w in (">", ">>", ">|", "&>", "&>>", ">&", "<", "2>", "2>>", "<<", "<<<"):
                skip = True                      # the operator's word is not an argument
                if words and words[-1].isdigit():
                    words.pop()
                continue
            words.append(w)
        if _vr is not None:
            words = _vr._strip_wrappers(words)[0]   # B2-18: `env cp …`, `sudo -u x cp …`, `nice -n 5 tee …`
        if not words:
            continue
        verb = _unquote(words[0]).split("/")[-1].lower()
        args = words[1:]
        fw = _vr.formatter_writes(verb, args) if _vr is not None else None
        if fw is not None:
            if fw:                                   # B2-18: a formatter in write mode edits its operands
                targets.extend(_tgt(a) for a in _vr._nonflags(args) if "." in a.split("/")[-1])
            continue
        if verb == "sed" and any(re.match(r"^(-[A-Za-z]*i|--in-place)", _unquote(a)) for a in args):   # B5-09
            targets.extend(_tgt(a) for a in args if not a.startswith("-"))
        elif verb in _NAMED_WRITE_VERBS:
            targets.extend(_tgt(a) for a in args if not a.startswith("-"))
        elif verb == "dd":
            for a in args:
                if a.lower().startswith("of="):
                    targets.append(_unquote(a[3:]))
        elif verb in _DEST_VERBS:
            operands = [a for a in args if not a.startswith("-")]
            if operands:
                targets.append(_tgt(operands[-1]))
    return targets


def _bash_rm_targets(cmd: str) -> list:
    """Every file path `cmd` removes via `rm` — same best-effort tokenization as
    `_bash_write_targets`, kept as its OWN small function rather than refactored to
    share code with it, so a change here cannot alter that function's already-tested
    behavior.

    A path returned here is treated as CLEARED from the pending set (see main()), not
    merely un-added: before this function existed, `rm` was invisible to this gate —
    a file, once written, could never leave `edited_since_verify` no matter what
    happened to it afterward, because nothing in this file's write-detection or its
    VERIFY_MARKERS reset had any notion of deletion. A file created and then deleted
    within the same session leaves nothing left to verify or attest to.

    Deliberately narrow: only the `rm` verb itself (not `rmdir`, `unlink`, `find
    -delete`, or a Python/Node one-liner that deletes a file) — those are real,
    documented gaps, left for a future pass rather than guessed at now, matching this
    file's own convention of stating limitations rather than hiding them.
    """
    try:
        lx = shlex.shlex(cmd, posix=False, punctuation_chars=True)
        lx.whitespace_split = True
        toks = list(lx)
    except ValueError:
        return []

    segments, cur = [], []
    for tok in toks:
        if tok in (";", "|", "||", "&&", "&"):
            segments.append(cur)
            cur = []
        else:
            cur.append(tok)
    segments.append(cur)

    targets = []
    for seg in segments:
        words = [w for w in seg if w not in (">", ">>", "<", "2>", "&>")]
        if not words:
            continue
        verb = _unquote(words[0]).split("/")[-1].lower()
        if verb != "rm":
            continue
        targets.extend(_tgt(a) for a in words[1:] if not a.startswith("-"))
    return targets


def _capped(name: str, default: int) -> int:
    """A cap from the environment can only LOWER the default (tests only; floor 1000 bytes)."""
    try:
        v = int(os.environ.get(name, ""))
        if 1000 <= v <= default:
            return v
    except ValueError:
        pass
    return default


# v0.8 (PLAN R-3 Input v0.8, S-2): ONE read of the whole transcript into one ledger. FILE_CAP bounds the file (a larger one is read over
# its last FILE_CAP bytes and the ledger is incomplete); LINE_CAP bounds one record (a longer one is never loaded, the ledger is
# incomplete). An incomplete ledger BLOCKS the gate (S-4): uncertainty is never read as "nothing happened".
FILE_CAP = _capped("CLOSEOUT_TRANSCRIPT_CAP", 1 << 30)
LINE_CAP = _capped("CLOSEOUT_LINE_CAP", 64 << 20)
RETAIN_CAP = _capped("CLOSEOUT_RETAIN_CAP", 128 << 20)   # v0.8.1 (s282 F-3): the tool-call text one ledger may keep in memory
TORN_RETRY_S = 0.3                                       # v0.8.1 (s282 F-2): the one wait before a torn last line is re-read
_TOOL_INPUT_KEYS = ("file_path", "notebook_path", "command", "run_in_background", "action")


def _fmt_bytes(n: int) -> str:
    for unit, name in ((1 << 30, "GiB"), (1 << 20, "MiB")):
        if n % unit == 0:
            return "%d %s" % (n // unit, name)
    return "%d bytes" % n


def _safe_open_transcript(tpath: str):
    """Defensively open the externally-supplied transcript path (F7): O_NOFOLLOW (refuse a symlinked final component) + O_NONBLOCK +
    regular-file-only (no /dev/* or FIFOs, so a special-file transcript can't make the read never end). Returns (binary stream, size
    taken once by fstat) or None: an unreadable/odd transcript is the F7 outcome (exit 0, no assertion)."""
    if not isinstance(tpath, str) or not tpath:
        return None   # F7: non-string/empty transcript_path (e.g. JSON null) -> fail-open, no crash
    try:
        fd = os.open(tpath, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            os.close(fd)
            return None
        return os.fdopen(fd, "rb"), st.st_size
    except OSError:
        try:
            os.close(fd)
        except OSError:
            pass
        return None


class _Ledger:
    """What one read of the transcript found. `events`: the records that carry tool calls, in file order, each
    {"line", "blob", "cwd", "blocks"} with blocks reduced to id, name and the input fields the walk uses. `results`: real
    tool_use id -> (is_error, text, truncated). `incomplete`: reasons the read was not whole (empty when whole)."""

    def __init__(self):
        self.events = []
        self.last_text, self.last_text_line, self.last_prompt_line = "", -1, -1
        self.results = {}
        self.real_use_ids = set()
        self.blob_result_ids = set()
        self.incomplete = []


def _read_ledger(tpath: str, opener=None):
    """The only code that opens the transcript (S-1). `opener(tpath)` returns (binary stream, size) or None (tests count reads and
    append bytes after the open through it). Returns a _Ledger, or None when the transcript cannot be opened (F7)."""
    opened = (opener or _safe_open_transcript)(tpath)
    if opened is None:
        return None
    fh, size = opened
    led = _Ledger()
    pending = {}      # blob tool_use id -> its block, kept once and only until a real twin is known
    names = {}        # real tool_use id -> tool name
    file_cap, line_cap = FILE_CAP, LINE_CAP
    retained = 0

    def parse(chunk):
        # (entry, its message, bad). `bad` is True only for a record that does not PARSE (v0.8.1, s282 F-2: no byte marker can tell
        # what a damaged record held). A record that parses but is no object, or whose message is no object, holds no tool block.
        try:
            entry = json.loads(chunk.decode("utf-8", "replace"))
        except Exception:
            return None, None, True
        if not isinstance(entry, dict):
            return {}, {}, False
        raw_msg = entry.get("message")
        return entry, raw_msg if isinstance(raw_msg, dict) else {}, False

    def note(reason):
        if reason not in led.incomplete:
            led.incomplete.append(reason)

    def skip_rest(chunk, pos):
        # the remainder of a record that is not kept: bounded reads, never loaded whole
        while not chunk.endswith(b"\n") and pos < size:
            chunk = fh.readline(min(line_cap + 1, size - pos))
            if not chunk:
                break
            pos += len(chunk)
        return pos

    try:
        pos, line_no = 0, -1
        if size > file_cap:
            pos = size - file_cap
            fh.seek(pos)
            note("transcript over " + _fmt_bytes(file_cap))
            first = fh.readline(min(line_cap + 1, size - pos))
            pos = skip_rest(first, pos + len(first))              # drop the partial first line
        while pos < size:
            chunk = fh.readline(min(line_cap + 1, size - pos))
            if not chunk:
                break
            pos += len(chunk)
            line_no += 1
            has_nl = chunk.endswith(b"\n")
            if len(chunk) - (1 if has_nl else 0) > line_cap:
                note("record over " + _fmt_bytes(line_cap))
                pos = skip_rest(chunk, pos)
                continue
            if not chunk.strip():
                continue                    # a blank line holds no record (internal review 2026-11-12)
            entry, msg, bad = parse(chunk)
            torn = bad and not has_nl and pos >= size      # only the file's last line can be torn, so the re-read runs at most once
            if torn:
                # v0.8.1 (s282 F-2): the last record may be mid-write. ONE wait, ONE re-read of that record alone (to its newline,
                # bounded like any record); bytes after it stay unread (S-2: one snapshot).
                try:
                    time.sleep(TORN_RETRY_S)
                    fh.seek(pos - len(chunk))
                    again = fh.readline(line_cap + 1)
                    if again.endswith(b"\n") and len(again) - 1 <= line_cap:
                        entry, msg, bad = parse(again)
                        torn = False
                except Exception:
                    pass
            if bad:
                note("record being written" if torn else "unreadable record")
                continue
            blob = entry.get("type") == "api-request-blob"
            content = msg.get("content")
            blocks = [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []
            if not blob:
                if _is_human_prompt(entry):
                    led.last_prompt_line = line_no
                if entry.get("type") == "assistant":
                    for blk in blocks:
                        if blk.get("type") == "text" and isinstance(blk.get("text"), str) and blk["text"]:
                            led.last_text, led.last_text_line = blk["text"], line_no
            kept = []
            for blk in blocks:
                kind = blk.get("type")
                if kind == "tool_use":
                    tid = blk.get("id") if isinstance(blk.get("id"), str) and blk.get("id") else None
                    inp = blk.get("input") if isinstance(blk.get("input"), dict) else {}
                    red = {"id": tid, "name": blk.get("name", ""), "input": {k: inp[k] for k in _TOOL_INPUT_KEYS if k in inp}}
                    cost = sum(len(v) for v in red["input"].values() if isinstance(v, str))
                    if not blob:
                        if tid:
                            led.real_use_ids.add(tid)
                            names[tid] = red["name"]
                            if tid in pending:
                                pending.pop(tid)["dead"] = True         # a real twin is now known: the copy is dropped
                        kept.append(red)
                        retained += cost
                    elif not (tid and (tid in led.real_use_ids or tid in pending)):
                        if tid:
                            pending[tid] = red
                        kept.append(red)
                        retained += cost
                elif kind == "tool_result":
                    tid = blk.get("tool_use_id")
                    if not (isinstance(tid, str) and tid):
                        continue
                    if blob:
                        led.blob_result_ids.add(tid)
                        continue
                    try:
                        text = _text_of(blk.get("content")) if names.get(tid, "Bash") == "Bash" else ""   # a known non-Bash call: no text kept
                    except Exception:
                        text = ""
                    truncated = len(text) > 8192             # Seat B B-14: a cut output never proves a pass
                    if truncated:
                        text = text[:4096] + "\n...\n" + text[-4096:]
                    led.results[tid] = (blk.get("is_error") is True, text, truncated)
                    retained += len(text)
            if kept:
                led.events.append({"line": line_no, "blob": blob, "cwd": "" if blob else str(entry.get("cwd") or ""), "blocks": kept})
            if retained > RETAIN_CAP:
                # v0.8.1 (s282 F-3): past the budget nothing more is kept and nothing kept is judged: the incomplete block is the
                # outcome (S-4), reached without walking what was read
                note("tool-call text over " + _fmt_bytes(RETAIN_CAP))
                led.events, led.results = [], {}
                break
    except OSError:
        note("read error")
    finally:
        try:
            fh.close()
        except OSError:
            pass
    for ev in led.events:
        ev["blocks"] = [b for b in ev["blocks"] if not b.get("dead")]
    led.events = [ev for ev in led.events if ev["blocks"]]
    for tid, (err, text, trunc) in list(led.results.items()):
        if names.get(tid) != "Bash":
            led.results[tid] = (err, "", False)               # only a Bash result's text is ever read
    return led


def _verify_project_root(edited_paths, mods):
    """The ONE project-root rule lives in receipt.py (receipt-v2 R-3, GF-2): this is only the delegation, no `CLAUDE.md`
    test and no ancestor walk of its own. Returns (root, reason)."""
    return mods["receipt"].project_root(edited_paths)


def _rr_walk(path):
    """Resolve an absolute, unnormalised `path` component by component with lstat/readlink only, as the kernel does (`..` applies
    after a link; at most 40 links). ("ok", resolved) | ("absent", lexical placement from the nearest existing ancestor: ENOENT
    or ENOTDIR on a component means the rest does not exist) | ("fail", None): ELOOP, EACCES, any other error, over 40 links."""
    cur, links = [], 0
    todo = [c for c in path.split("/") if c][::-1]
    while todo:
        c = todo.pop()
        if c == ".":
            continue
        if c == "..":
            if cur:
                cur.pop()
            continue
        p = "/" + "/".join(cur + [c])
        try:
            st = os.lstat(p)
        except (FileNotFoundError, NotADirectoryError):
            return "absent", os.path.normpath("/" + "/".join(cur + [c] + todo[::-1]))
        except OSError:
            return "fail", None
        if stat.S_ISLNK(st.st_mode):
            links += 1
            if links > 40:
                return "fail", None
            try:
                target = os.readlink(p)
            except OSError:
                return "fail", None
            if not target:
                return "fail", None
            if target.startswith("/"):
                cur = []
            todo.extend([x for x in target.split("/") if x][::-1])
        else:
            cur.append(c)
    return "ok", "/" + "/".join(cur)


def _rr_test_dir(d):
    """Test one ancestor `d`: lstat of BOTH `d/NEOCORTEX` and `d/.verify`, always both. "absent" when either is ENOENT/ENOTDIR (`d` is
    not a candidate; nothing is followed); "unreadable" when neither is proved absent and an error prevents deciding, or when a
    candidate is malformed (a wrong type of NEOCORTEX, .verify or armed, a dangling or looping link, any error but ENOENT);
    "unarmed" for a well-formed candidate with no `armed` (passed over); "armed" for NEOCORTEX folder + .verify folder + armed file."""
    gone = err = False
    for entry in ("NEOCORTEX", ".verify"):
        try:
            os.lstat(os.path.join(d, entry))
        except (FileNotFoundError, NotADirectoryError):
            gone = True
        except OSError:
            err = True
    if gone:
        return "absent"
    if err:
        return "unreadable"
    try:
        neo = os.stat(os.path.join(d, "NEOCORTEX")).st_mode
        ver = os.stat(os.path.join(d, ".verify")).st_mode
    except OSError:
        return "unreadable"
    if not (stat.S_ISDIR(neo) and stat.S_ISDIR(ver)):
        return "unreadable"
    marker = os.path.join(d, ".verify", "armed")
    try:
        os.lstat(marker)
    except FileNotFoundError:
        return "unarmed"
    except OSError:
        return "unreadable"
    try:
        return "armed" if stat.S_ISREG(os.stat(marker).st_mode) else "unreadable"
    except OSError:
        return "unreadable"


def _rr_ancestors(start):
    """From `start` up to `/`: ("unreadable", None) at the first unreadable candidate, ("armed", d) at the first armed root,
    (None, None) when neither is met."""
    d = start
    while True:
        state = _rr_test_dir(d)
        if state == "unreadable":
            return "unreadable", None
        if state == "armed":
            return "armed", d
        parent = os.path.dirname(d)
        if parent == d:
            return None, None
        d = parent


def _rr_place(path):
    """Place ONE path twice, lexically (normpath, `..` folded) and resolved (_rr_walk). Returns (kind, root, key) with kind in
    "unreadable" | "ambiguous" | "root" | "none". In this order: an unreadable candidate met on either placement is unreadable;
    an armed root from either placement is that root (one root spelled two ways, same device and inode, is one root; two
    different ones are ambiguous); a resolution failure and no armed root found lexically is unreadable; otherwise "none".
    A path that is not an absolute string is a resolution failure."""
    if not isinstance(path, str) or not path.startswith("/") or "\0" in path:
        return "unreadable", None, None
    lexical = _rr_ancestors(os.path.dirname(os.path.normpath(path)))
    how, placed = _rr_walk(path)
    resolved = _rr_ancestors(os.path.dirname(placed)) if how != "fail" else (None, None)
    if "unreadable" in (lexical[0], resolved[0]):
        return "unreadable", None, None
    roots = []
    for state, d in (resolved, lexical):
        if state == "armed":
            try:
                s = os.stat(d)
            except OSError:
                return "unreadable", None, None
            if (s.st_dev, s.st_ino) not in [r[0] for r in roots]:
                roots.append(((s.st_dev, s.st_ino), d))
    if len(roots) > 1:
        return "ambiguous", None, None
    if roots:
        return "root", roots[0][1], roots[0][0]
    if how == "fail":
        return "unreadable", None, None
    return "none", None, None


def _rr_scan(paths):
    """Across paths: unreadable > ambiguous > root > none; two different roots among paths are ambiguous. Returns (kind, root)."""
    try:
        items = list(paths or [])
    except Exception:
        return "unreadable", None
    roots, ambiguous = [], False
    for fp in items:
        try:
            kind, d, key = _rr_place(fp)
        except Exception:
            return "unreadable", None
        if kind == "unreadable":
            return "unreadable", None
        if kind == "ambiguous":
            ambiguous = True
        elif kind == "root" and key not in [r[0] for r in roots]:
            roots.append((key, d))
    if ambiguous or len(roots) > 1:
        return "ambiguous", None
    if roots:
        return "root", roots[0][1]
    return "none", None


def _probe_armed_local(edited_paths) -> bool:
    """Module-free armed-ness probe for when hook_support cannot be loaded (GF-1(d) rule 2): the same answers as
    hook_support.vg_probe, from the same placement text (`_rr_scan`; a parity test holds the three copies equal): armed or not
    armed only, raising OSError where project_root says "armed state unreadable". Stdlib only."""
    kind, _root = _rr_scan(edited_paths)
    if kind == "unreadable":
        raise OSError("armed state unreadable")
    return kind in ("root", "ambiguous")


def _safe_text(exc, limit=200) -> str:
    """`str(exc)` that cannot raise: an exception whose __str__ raises is named by its type (s232 F-1)."""
    try:
        return str(exc)[:limit]
    except BaseException:
        try:
            return "<%s>" % type(exc).__name__
        except BaseException:
            return "<exception>"


def _verify_state(edited_paths):
    """GF-1(d) / GF-2: what the VERIFY-GATE does for this turn. Returns a dict:
      armed  - the turn is under an armed project (a `.verify/armed` marker at the resolved root);
      block  - None, or the text the turn blocks with whatever the receipt says (ambiguous root, a script that is not
               approved or failed, an unreadable armed state);
      root   - the resolved project root (Path) or None;
      mods   - the loaded verify_gate modules or None.
    The WHOLE interaction with hook_support and the loaded set (vg_prepare, indexing its result, project_root, vg_failure,
    the marker check) is under one boundary (s232 F-1): nothing raised there leaves this function. On any unexpected
    exception the module-free probe answers armed or not armed only (D4): armed blocks with "verify_gate script failed: ...",
    a probe error is row 8. When hook_support cannot be loaded at all the same probe answers (rule 2)."""
    st = {"armed": False, "block": None, "root": None, "mods": None}
    fail = None
    if _hs is None:     # hook_support (loader and probe) unavailable: the gate's own stdlib-only probe answers (rule 2)
        why = f"{type(_HS_ERR).__name__}: {_safe_text(_HS_ERR)}" if _HS_ERR is not None else "not loaded (bytes differ from the approved list)"
        fail = (7, f"verify_gate script failed: hook_support: {why}"[:240])
    else:
        try:
            prep = _hs.vg_prepare(ROOT)
            if prep["ok"]:
                root, reason = _verify_project_root(edited_paths, prep["mods"])
                if root is not None:
                    # a root that project_root returns IS armed: no second marker read (s233 D-5, s238 F-7)
                    st.update(armed=True, root=Path(root), mods=prep["mods"])
                    return st
                if reason in ("ambiguous project root", "armed state unreadable"):
                    st.update(armed=True, block=reason, mods=prep["mods"])     # no receipt can satisfy either
                    return st
                if reason == "no project root":
                    st["mods"] = prep["mods"]
                    return st
                fail = (7, "verify_gate script failed: receipt: unexpected project_root reason %s" % _safe_text(repr(reason)))
            else:
                fail = (prep["row"], prep["text"])
        except BaseException as exc:
            if isinstance(exc, KeyboardInterrupt):
                raise
            try:
                fail = _hs.vg_failure(exc, ROOT)
            except BaseException as exc2:
                if isinstance(exc2, KeyboardInterrupt):
                    raise
                fail = (7, "verify_gate script failed: receipt: %s: %s"
                        % (type(exc).__name__, _safe_text(exc)))
            st.update(armed=False, block=None, root=None, mods=None)
    st["mods"] = None
    try:
        found = _probe_armed_local(edited_paths) if _hs is None else _hs.vg_probe(edited_paths)
    except BaseException as exc:
        if isinstance(exc, KeyboardInterrupt):
            raise
        try:
            _log_error("stop_closeout_gate.vg_probe", exc)
        except BaseException as exc2:
            if isinstance(exc2, KeyboardInterrupt):
                raise
        st["armed"], st["block"] = True, "verify_gate armed state unreadable"
        return st
    if found:
        try:
            text = str(fail[1])[:240]
        except BaseException as exc3:      # a failure text whose __str__ raises must not leave the boundary (s235 E-10, s241 F-3:
            if isinstance(exc3, KeyboardInterrupt):      # SystemExit too, as every other helper here; KeyboardInterrupt excepted)
                raise
            text = "verify_gate script failed (armed project; the failure text is unavailable)"
        st["armed"], st["block"] = True, text
    return st


def _verify_armed(edited_paths) -> bool:
    """A project opts into BLOCKING independent-verification by placing a `.verify/armed` marker AT ITS OWN ROOT
    (receipt.project_root, from the files edited this turn: never `Path.cwd()`). A project without the marker is not
    armed: the gate does not block it on verification."""
    return bool(_verify_state(edited_paths)["armed"])      # R-3: no `except OSError: return False` (uncertainty is never "not armed")


def _has_valid_receipt(edited_paths, changed_paths: list, state=None):
    """(ok, reason). ok iff a valid, change-set-bound, v2 VERIFY-RECEIPT exists for exactly these files at the edited
    project's own `.verify/` (never `Path.cwd()`). `reason` is validate_receipt's reason when a receipt file exists and fails,
    "" when none exists. The fabricable `echo test_` substring does NOT satisfy this."""
    try:
        st = state if state is not None else _verify_state(edited_paths)
        root, mods = st["root"], st["mods"]
        if root is None or mods is None:
            return False, ""
        rpath = root / ".verify" / "receipt.json"
        if not rpath.is_file():
            return False, ""
        rec = json.loads(rpath.read_text(encoding="utf-8"))
        ledger = str(root / ".verify" / "_consumed_nonces.json")
        ok, why = mods["receipt"].validate_receipt(rec, changed_paths=changed_paths, ledger_path=ledger)
        return bool(ok), str(why)
    except BaseException as exc:
        if isinstance(exc, KeyboardInterrupt):
            raise
        return False, "verify_gate script failed: receipt: %s: %s" % (type(exc).__name__, _safe_text(exc))


def _python312():
    """Resolve the provisioned interpreter. NEVER sys.executable: the interpreter running
    hooks may be an older system Python (workspace CLAUDE.md, Python interpreter discipline), which
    fails the sweep spuriously. Return None (=> advisory skipped) if 3.12 is absent."""
    try:
        return shutil.which("python3.12") or (
            "/usr/local/bin/python3.12"
            if Path("/usr/local/bin/python3.12").is_file() else None)
    except Exception:
        return None


def _continuity_advisory(proj_root, timeout: float = 8) -> str:
    """Project-scoped continuity sweep, ADVISORY ONLY. Returns a one-line summary, or ""
    when clean/unavailable. Fail-open on every path: any error, missing script, missing
    interpreter or timeout yields "" — this must never block, warn wrongly, or raise."""
    try:
        sweep = ROOT / "governance" / "scripts" / "continuity_sweep.py"
        py = _python312()
        if py is None or not sweep.is_file():
            # Announce, exactly as the C3 gate does. This half stayed silent while its
            # sibling spoke -- one asymmetry is all it takes for "disabled" to read as
            # "clean" (§5.5 re-review).
            why = "python3.12 not found" if py is None else f"{sweep} not found"
            print(f"CLOSEOUT-ADVISORY continuity check DISABLED: {why}", file=sys.stderr)
            return ""
        r = subprocess.run([py, str(sweep), "--project", str(proj_root)],
                           capture_output=True, text=True, timeout=timeout)
        if r.returncode != 1:      # 0 = clean, 2 = usage error => stay silent
            return ""
        last = [ln for ln in r.stdout.splitlines() if ln.startswith("RESULT:")]
        return last[-1].strip() if last else "continuity findings present"
    except Exception:
        return ""


def _staleness_advisory(proj_root, ledger_path: 'Path | None' = None, timeout: float = 20) -> str:
    """Project-scoped `closeout_lint`, ADVISORY ONLY. Same contract as the continuity sweep.

    This is M-9's missing half. The §3 gate of 2026-08-26 found the linter shipped with ZERO
    callers anywhere -- "a linter a human must remember to run", which is the defect class the
    mechanism PLAN exists to kill, recurring inside the tool written to kill it.

    Why ADVISORY and why PROJECT-SCOPED, both deliberate:
      - Advisory needs no ruling. Which checks may BLOCK is a founder decision, still owed;
        being CONNECTED is not, and connected-but-advisory is what closes the gate's finding.
      - Project-scoped costs ~0.4 s for `governance` (fleet-wide ~9.4 s; the once-published
        "3.7 s" is NOT reproducible -- the same committed 8-check code measures 8.4 s today,
        so the figure moved with the fleet, not with the code), and, more
        importantly, it cannot report another project's backlog at this project's close-out:
        47 of the fleet's 53 `status-enum` findings sit in ONE other project's MANIFEST, a
        file a concurrent session had written 13 minutes before this was measured.

    Fail-open on every path, exactly like its sibling: missing script, missing interpreter,
    error or timeout yields "" -- but a DISABLED state announces itself, because one silent
    half is all it takes for "disabled" to read as "clean".

    FEEDS THE PRECISION LEDGER (2026-08-28, founder-ruled: log now, gate later). Every finding
    this call sees is also appended to governance/precision_ledger.jsonl via `--json`, which is
    the raw material STATUS item 3 named and left unbuilt. This function still returns only a
    summary string -- the ledger write is a side effect, not a behavior change for its caller.
    """
    try:
        lint = ROOT / "governance" / "scripts" / "closeout_lint.py"
        py = _python312()
        if py is None or not lint.is_file():
            why = "python3.12 not found" if py is None else f"{lint} not found"
            print(f"CLOSEOUT-ADVISORY staleness check DISABLED: {why}", file=sys.stderr)
            return ""
        r = subprocess.run([py, str(lint), "--path", str(proj_root), "--json"],
                           capture_output=True, text=True, timeout=timeout)
        if r.returncode not in (0, 1):      # 2 = usage error; --json never returns anything else
            return ""
        findings = []
        for line in r.stdout.splitlines():
            try:
                findings.append(json.loads(line))
            except (ValueError, TypeError):
                continue          # one malformed line must not blind the whole advisory
        if not findings:
            return ""
        _append_ledger(proj_root, findings, ledger_path=ledger_path)
        # PER-CHECK ATTRIBUTION (2026-08-27). Until now this returned ONLY the total, so 35
        # logged firings across the fleet could say how MANY findings there were and never
        # WHICH CHECK produced them. That is the exact data needed to decide which checks may
        # BLOCK: the founder's 2026-08-26 ruling admitted six git-grounded checks on the
        # strength of them being at zero findings on a clean tree, and one of them
        # (`series-count`, 18% measured precision) has since produced a finding on this very
        # project that was a filing CONVENTION, not a defect — it would have refused a correct
        # commit had the wire been live. A blocking set assembled from a one-day snapshot needs
        # real traffic to confirm it, and traffic that cannot be attributed measures nothing.
        # Still advisory, still non-blocking; this adds a suffix and changes no decision.
        by: dict = {}
        for f in findings:
            k = f.get("check", "?")
            by[k] = by.get(k, 0) + 1
        summary = f"{len(findings)} finding(s). Advisory -- pass --strict to block."
        if by:
            summary += "  [" + " ".join(f"{k}={v}" for k, v in sorted(by.items())) + "]"
        return summary
    except Exception:
        return ""


def _journal_project_root():
    """Resolve the governed project root whose NEOCORTEX/JOURNAL.md the 64 KB bound
    applies to. Pre-2026-07-27 the check hardcoded Path.cwd()/"NEOCORTEX", so any
    session whose cwd was the workspace root or a parent silently exempted every
    journal. Resolution order:
      1. $CLAUDE_PROJECT_DIR (set by the harness — settings.json already relies on
         it), when that directory contains a NEOCORTEX/;
      2. otherwise walk up from cwd to the first directory holding both CLAUDE.md
         and NEOCORTEX/ (covers sessions started in a project subdirectory).
    Fail-open on ANY error or no match: return None and the check is skipped —
    this hook must never block a turn because of its own bug."""
    import os
    try:
        env = os.environ.get("CLAUDE_PROJECT_DIR", "")
        if env:
            p = Path(env)
            if (p / "NEOCORTEX").is_dir():
                return p
        cwd = Path.cwd()
        for cand in (cwd, *cwd.parents):
            if (cand / "CLAUDE.md").is_file() and (cand / "NEOCORTEX").is_dir():
                return cand
    except Exception:
        pass
    return None


def _advisory_roots(edited_paths, fallback):
    """Governed projects TOUCHED this turn — the scope the non-blocking advisories run over.

    WHY THIS EXISTS, and it is not a refinement. `_journal_project_root()` resolves by
    `$CLAUDE_PROJECT_DIR` or by walking up from cwd for a directory holding BOTH `CLAUDE.md`
    and `NEOCORTEX/`. A session rooted at the WORKSPACE ROOT — the documented primary working
    directory, and where governance work happens — matches neither: the root has `CLAUDE.md`
    but no `NEOCORTEX/`, and the harness leaves `CLAUDE_PROJECT_DIR` unset. So the resolver
    returns None and BOTH advisories are skipped in silence.

    Measured 2026-08-26 by running this hook with a real payload rather than trusting its
    tests: exit 0, no output, nothing logged. `governance` last reached the advisory log at
    15:30 that day, from a session whose cwd was inside `governance/`. This is the fifth dead
    escalation channel found in this workstream, and it would have shipped as "connected".

    Resolving from the EDITED FILES fixes it and generalises: the advisory that matters at
    turn-end is about the project you actually changed, wherever the session is rooted.

    Deliberately NOT used by the JOURNAL bound check, which BLOCKS. Widening a blocking
    check's reach could start failing turns that pass today; this path can only ever print.
    """
    roots, seen = [], set()
    for fp in edited_paths or []:
        try:
            cand = Path(fp).resolve().parent
        except Exception:
            continue
        try:
            for d in (cand, *cand.parents):
                if (d / "CLAUDE.md").is_file() and (d / "NEOCORTEX").is_dir():
                    if str(d) not in seen:
                        seen.add(str(d))
                        roots.append(d)
                    break
        except Exception:                   # EACCES on a component: this advisory scope must not crash the turn (a crash here
            continue                        # dropped the armed block already collected: observed with T17 (l), 2026-11-10)
        if len(roots) >= 3:                 # bound the turn-end cost; 3 projects is already
            break                           # an unusual turn, and each costs ~0.3-1 s
    if roots:
        return roots
    return [fallback] if fallback is not None else []


def _skill_version_drift():
    """B5-20 (reviewer A round 5): `_skills/build_index.py` is foreign code (not in the hook set, not inventoried): it runs
    in a CHILD process with a 3 s timeout, so a `sys.exit`, `os._exit` or hang there can never suppress this gate's
    H1 or H4c output. Fail-open, and said so."""
    import subprocess as _sp
    try:
        r = _sp.run([sys.executable, "-I", str(Path(__file__).resolve()), "--skill-drift-only"], capture_output=True,
                    text=True, timeout=3, cwd=str(ROOT))
        out = json.loads(r.stdout) if r.returncode == 0 and r.stdout.strip() else []
        return [str(x) for x in out] if isinstance(out, list) else []
    except Exception as exc:
        print(f"C3 skill-version-drift gate SKIPPED ({type(exc).__name__})", file=sys.stderr)
        return []


def _skill_version_drift_inproc():
    """C3 version-drift guard: skills whose version sources disagree. `[]` when clean.

    WHY THIS GATE EXISTS. The check itself already existed — `build_index.py` compares
    frontmatter / title-line / metadata.json and `--strict` exits 1 — and it printed drift
    to stderr on EVERY run. But nothing consumed that output: the only automated caller
    (`skill_index_regen.py`) runs LENIENT by contract and always exits 0. So the warning
    was real, correct, and unread, and `example_scraper` sat split across FOUR sources
    (frontmatter 2.3.0 / title v2.1 / metadata.json 2.2.0 / scripts/__init__.py 1.0.0)
    until 2026-07-28. An unread warning is not a gate; this is the missing consumer.

    WHY NOT JUST SHELL OUT TO `build_index.py --strict`. MEASURED 30.9 s — it greps the
    whole workspace to count consumers. This file already declines the fleet-wide
    continuity sweep at 8.7 s for exactly that reason, so 31 s on every turn-end is out.
    Instead we import build_index and reuse its OWN comparison helpers (Art. 7: never
    re-implement a skill's logic) while SKIPPING `collect_skills()`. Reading two small
    files per skill costs ~10 ms, and the comparison semantics stay in one place — if
    `_version_mismatches_for` changes, this gate inherits it.

    SCOPE: fleet-wide, not scoped to skills this session touched. The drift that motivated
    this was PRE-EXISTING — nobody had edited those manifests recently — so a
    session-scoped trigger would have missed it entirely. Safe to run unconditionally
    because the fleet is clean as of 2026-07-28 (`--strict` exits 0 across all 27 skills),
    so this cannot jam turn-end fleet-wide the way the continuity sweep would.

    FAIL-OPEN on any error: a broken guard must never wedge turn-end.
    """
    try:
        import importlib.util
        bi_path = ROOT / "_skills" / "build_index.py"
        if not bi_path.is_file():
            # SAY SO. Failing open is correct here -- a broken guard must never wedge
            # turn-end -- but failing open SILENTLY makes "gate disabled" and "gate passed"
            # indistinguishable, which is the exact "an unread warning is not a gate"
            # defect this file was written to fix. On any clone of the published kit
            # build_index.py was absent, so C3 was permanently and invisibly off
            # (§5.5 panel, 2026-08-25).
            print(f"C3 skill-version-drift gate DISABLED: {bi_path} not found",
                  file=sys.stderr)
            return []
        spec = importlib.util.spec_from_file_location("_bi_gate", bi_path)
        if spec is None or spec.loader is None:
            return []
        bi = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bi)   # constants + regexes only; main() is __main__-guarded

        out: list[str] = []
        for skill_md in sorted((ROOT / "_skills").glob("*/SKILL.md")):
            try:
                text = skill_md.read_text(encoding="utf-8", errors="replace")
                fm = bi._parse_frontmatter(text)
                if not fm:
                    continue          # no frontmatter → nothing authoritative; not our gate
                meta_path = skill_md.parent / "metadata.json"
                meta_version = bi._NO_META
                if meta_path.is_file():
                    try:
                        meta_version = json.loads(meta_path.read_text(encoding="utf-8")).get("version")
                    except (ValueError, OSError):
                        meta_version = bi._NO_META   # unparseable metadata is a different defect
                for msg in bi._version_mismatches_for(fm.get("version"),
                                                      bi._extract_title_version(text),
                                                      meta_version):
                    out.append(f"{skill_md.parent.name}: {msg}")
            except OSError:
                continue              # one unreadable skill must not blind the whole sweep
        return out
    except Exception:
        return []                     # fail-open


# --- PLAN_hooks_outcome_tamper_loopbound_2026-10-05: H1 (outcome), H2 (test tamper), H9 (BLOCKED
# safe exit), H4c (governor integrity). Sibling modules are optional: if verifier_recognizer cannot be
# imported the gate falls back to its pre-2026-10-05 substring behaviour (VERIFY_MARKERS) and records
# the import error, so a broken deploy degrades to the old gate, never to no gate.
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


def _approved_digest_check(rel: str):
    """Independent H4c check of ONE file against governance/hooks_approved.sha256, without importing
    hook_support (reviewer A B3-10: a broken comparator must not hide its own modification). None = matches or
    no list; else a discrepancy line."""
    import hashlib
    root = Path(__file__).resolve().parent.parent.parent
    try:
        lst = (root / "governance" / "hooks_approved.sha256").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    want = next((ln.split(None, 1)[0] for ln in lst.splitlines() if ln.strip().endswith("  " + rel)
                 or ln.split(None, 1)[1:2] == [rel]), None)
    if want is None:
        return None
    p = root / rel
    import os as _o
    import stat as _st
    try:
        st = _o.lstat(p)
        if not _st.S_ISREG(st.st_mode) or st.st_size > 4_000_000:
            have = "SPECIAL"                       # B5-02: a FIFO / device / link / huge file is never opened
        else:
            fd = _o.open(p, _o.O_RDONLY | getattr(_o, "O_NOFOLLOW", 0) | getattr(_o, "O_NONBLOCK", 0))
            try:
                data = _o.read(fd, 4_000_001)
            finally:
                _o.close(fd)
            have = "{}:{:o}".format(hashlib.sha256(data).hexdigest(), st.st_mode & 0o7777)
    except OSError:
        have = "MISSING"
    return None if have == want else f"{rel}: approved {want[:12]}, live {have[:12]} (independent check)"


try:
    _vr = _load_sibling("verifier_recognizer")
    _VR_ERR = None
except BaseException as _exc:                  # incl. SystemExit raised at import: never a silent exit
    if isinstance(_exc, KeyboardInterrupt):
        raise
    _vr, _VR_ERR = None, _exc
_HS_ERR = None
try:
    _hs = _load_sibling("hook_support")
except BaseException as _exc:
    if isinstance(_exc, KeyboardInterrupt):
        raise
    _hs, _HS_ERR = None, _exc


def _log_error(where: str, exc: BaseException) -> None:
    if _hs is not None:
        _hs.log_error(where, exc, ROOT)


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content if isinstance(b, dict))
    return ""


def _write_target(fp: str, cwd: str):
    """The collector's one filter + qualification for a write target: None for an empty target (BSD `sed -i ''`) or a non-closeout
    path, else the path qualified like R-3 (absolute as spelled; relative joined to its event's cwd, or left relative)."""
    if not fp or _is_non_closeout_path(fp):
        return None
    return _qualify(fp, cwd)


def _is_human_prompt(entry: dict) -> bool:
    if entry.get("type") != "user" or entry.get("isMeta") or entry.get("isSidechain"):   # Seat A F15
        return False
    c = (entry.get("message") or {}).get("content")
    if isinstance(c, str):
        return bool(c.strip())
    if isinstance(c, list):
        return any(isinstance(b, dict) and b.get("type") == "text" for b in c) and \
            not any(isinstance(b, dict) and b.get("type") == "tool_result" for b in c)
    return False


def _qualify(fp: str, cwd: str) -> str:
    """R-2 / R-3 path qualification, the one rule: an absolute path is kept as spelled; a relative one is joined to the cwd of the
    event that wrote it with a plain separator join (no normpath, no realpath: `./` and `..` survive into the placement and the
    hash). A `~` is a plain name here, as in receipt.qualify_path (s241 F-1; the Bash parser expands an unquoted one before this). A relative path whose event has no usable cwd (empty or not
    absolute) stays relative: the placement rule reads a relative path as a resolution failure; the hook's own cwd is never used."""
    import os
    if os.path.isabs(fp) or fp.startswith(_UNRESOLVED) or not cwd or not os.path.isabs(cwd):
        return fp
    return os.path.join(cwd, fp)


def _abs(fp: str, cwd: str) -> str:
    import os
    fp = os.path.expanduser(fp)
    return os.path.normpath(fp if os.path.isabs(fp) or not cwd else os.path.join(cwd, fp))


_ROOT_CACHE: dict = {}


def _project_root_of(fp: str):
    """Nearest ancestor holding NEOCORTEX/ or .git (the unit a test run can speak for), or None."""
    try:
        d = Path(fp).parent
        key = str(d)
        if key in _ROOT_CACHE:
            return _ROOT_CACHE[key]
        found = None
        for cand in (d, *d.parents):
            if (cand / "NEOCORTEX").is_dir() or (cand / ".git").exists():
                found = str(cand)
                break
        _ROOT_CACHE[key] = found
        return found
    except Exception:
        return None


def _related(a, b) -> bool:
    """Same project, or one contains the other. Unknown roots count as related (never false-block)."""
    import os
    if a is None or b is None:
        return True
    return a == b or a.startswith(b.rstrip(os.sep) + os.sep) or b.startswith(a.rstrip(os.sep) + os.sep)


def _declared_commands(cwd: str, paths) -> tuple:
    roots = {r for r in [_project_root_of(_abs("x", cwd)) if cwd else None] + [_project_root_of(p) for p in paths] if r}
    return _vr.load_declared(sorted(roots)) if _vr is not None else ()


def _blocked_report(text: str) -> tuple:
    """(declared, missing_fields) for an Art. 14 `BLOCKED:` closeout in the turn's last message."""
    import re
    if not text or not re.search(r"(?mi)^[\s>*_`#-]*BLOCKED\s*:", text):
        return False, []
    fields = (("blocker", r"blocker"), ("diagnosis", r"diagnos"),
              ("what was tried", r"\btried\b|\battempt"), ("recommendation", r"recommend"))
    return True, [label for label, rx in fields if not re.search(rx, text, re.I)]


def _tamper_details(test_paths: list) -> tuple:
    """(kept_test_paths, details) — git diff -U0 counts for the test paths (bounded, 4 s total).
    pyproject.toml / package.json are kept only when their diff touches test configuration."""
    import re
    import time
    # Seat A F7/F18: `git diff HEAD` (staged + unstaged, not only unstaged), absolute paths from the
    # caller, 0.5 s per file inside a 1.5 s total — this runs BEFORE the decision is printed.
    deadline = time.monotonic() + 1.5
    removed_asserts = added_skips = timeout_lines = 0
    kept = []
    for fp in test_paths[:8]:
        diff = ""
        left = min(0.5, deadline - time.monotonic())
        if left > 0.1:
            try:
                r = subprocess.run(["git", "-C", str(Path(fp).parent), "diff", "HEAD", "-U0", "--no-color", "--", Path(fp).name],
                                   capture_output=True, text=True, timeout=left)
                diff = r.stdout if r.returncode == 0 else ""
            except Exception:
                diff = ""
        cond = _vr.TEST_CONFIG_IF_DIFF.get(Path(fp).name) if _vr is not None else None
        if cond is not None and not cond.search(diff):
            continue
        kept.append(fp)
        for ln in diff.splitlines():
            if ln.startswith("-") and not ln.startswith("---") and re.search(r"\bassert\b|\.assert[A-Z]\w*\(|\bexpect\(", ln):
                removed_asserts += 1
            if ln.startswith("+") and not ln.startswith("+++") and re.search(
                    r"pytest\.mark\.(skip|xfail)|@unittest\.skip|pytest\.skip\(|\.skip\(|\bxit\(|\bxdescribe\(|\.only\(", ln):
                added_skips += 1
            # 2026-10-28: removed lines only, as assertions are. A changed timeout shows as a removed line, so
            # a change or a deletion still counts; a pure addition (a new test that sets its own timeout) does not.
            if ln.startswith("-") and not ln.startswith("---") and re.search(r"timeout\D{0,12}\d", ln, re.I):
                timeout_lines += 1
    bits = []
    if removed_asserts:
        bits.append(f"{removed_asserts} assertion line(s) removed")
    if added_skips:
        bits.append(f"{added_skips} skip/xfail/only marker(s) added")
    if timeout_lines:
        bits.append(f"{timeout_lines} timeout line(s) removed or changed")
    return kept, ("; diff: " + ", ".join(bits)) if bits else ""



# H4c once-per-session de-duplication (founder-approved 2026-10-28; evidence: governance/pilot_telemetry.log held
# 247 GOVERNOR MODIFIED notes out of 310 advisory notes since 2026-10-06, 37 of them on 2026-10-28). The first report
# of an issue set in a session is the full text; the same set on a later turn of the SAME session is one short line
# that still names the count and the files. NEVER silent: any change of the set, and any other session, prints in full.
# State: governance/.h4c_seen.json, {"v": 1, "sessions": {"<key>": {"fp", "n", "ts"}}}, the 50 most recent sessions.
# It is only a hint: if it cannot be read or written, the full text is printed (fail-open toward MORE output).
_H4C_STATE = ("governance", ".h4c_seen.json")
_H4C_STATE_MAX_SESSIONS = 50
_H4C_STATE_MAX_BYTES = 64 * 1024
_H4C_SHORT_NAMES = 5


def _h4c_session_key(data: dict) -> str:
    """The Stop input's session_id, else a hash of its transcript_path; "" = no usable key (no de-duplication)."""
    try:
        sid = data.get("session_id")
        if isinstance(sid, str) and sid.strip():
            return "s:" + sid.strip()[:128]
        tp = data.get("transcript_path")
        if isinstance(tp, str) and tp:
            return "t:" + hashlib.sha256(tp.encode("utf-8", "replace")).hexdigest()[:32]
    except Exception:
        pass
    return ""


def _nofollow_dirfd(root: Path, dirs) -> int:
    """fd of root/<dirs...>, every component opened with O_NOFOLLOW (same walk as _safe_append)."""
    import os
    dfd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for d in dirs:
            nfd = os.open(d, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=dfd)
            os.close(dfd)
            dfd = nfd
    except BaseException:
        os.close(dfd)
        raise
    return dfd


def _safe_read_text(root: Path, rel_parts: tuple, limit: int) -> str:
    """Read root/<rel_parts...> with the _safe_append guards: no symlink at any component, a regular file only,
    not hardlinked, at most `limit` bytes. Raises OSError otherwise (a symlink raises ELOOP)."""
    import os
    import stat
    *dirs, name = rel_parts
    dfd = _nofollow_dirfd(root, dirs)
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dfd)
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1 or st.st_size > limit:
                raise OSError("not a plain regular file of bounded size")
            chunks = []
            while True:
                b = os.read(fd, 65536)
                if not b:
                    break
                chunks.append(b)
            return b"".join(chunks).decode("utf-8", "replace")
        finally:
            os.close(fd)
    finally:
        os.close(dfd)


def _safe_replace_text(root: Path, rel_parts: tuple, text: str) -> None:
    """Replace the content of root/<rel_parts...> with the _safe_append guards: opened O_NOFOLLOW|O_NONBLOCK and
    WITHOUT O_TRUNC, refused (nothing touched) if it is not a regular file or is hardlinked, truncated only after
    that check. Raises OSError when refused; the caller swallows it."""
    import os
    import stat
    *dirs, name = rel_parts
    dfd = _nofollow_dirfd(root, dirs)
    try:
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=dfd)
        try:
            st = os.fstat(fd)
            if not (stat.S_ISREG(st.st_mode) and st.st_nlink == 1):
                raise OSError("refusing a non-regular or hardlinked state file")
            os.ftruncate(fd, 0)
            raw, done = text.encode(), 0
            while done < len(raw):
                done += os.write(fd, raw[done:])
        finally:
            os.close(fd)
    finally:
        os.close(dfd)


def _h4c_load_sessions() -> dict:
    """key -> {"fp", "n", "ts"}; {} when the state is absent, unreadable, symlinked or malformed."""
    try:
        obj = json.loads(_safe_read_text(ROOT, _H4C_STATE, _H4C_STATE_MAX_BYTES))
        raw = obj.get("sessions") if isinstance(obj, dict) else None
        if not isinstance(raw, dict):
            return {}
        return {k: v for k, v in raw.items() if isinstance(k, str) and isinstance(v, dict)}
    except Exception:
        return {}


def _h4c_store_sessions(sessions: dict) -> None:
    while len(sessions) > _H4C_STATE_MAX_SESSIONS:
        sessions.pop(next(iter(sessions)))                     # oldest first: insertion order = recency
    try:
        _safe_replace_text(ROOT, _H4C_STATE, json.dumps({"v": 1, "sessions": sessions}))
    except Exception:
        pass                                                   # fail-open: the next turn prints in full


def _h4c_report(detail: list, full: list, session_key: str) -> list:
    """The lines for this turn's issue set: `full` the first time (or whenever the set differs from the one last
    reported in this session, or the state cannot be trusted), one short line when it is the same set again.
    `detail` = every issue string (the fingerprint covers all of them, not the 3 the full text shows)."""
    if not session_key:
        return full
    fp = hashlib.sha256("\n".join(sorted(set(detail))).encode("utf-8", "replace")).hexdigest() if detail else ""
    sessions = _h4c_load_sessions()
    prev = sessions.get(session_key)
    if not detail:
        if session_key in sessions:                           # resolved: forget it, so a recurrence prints in full
            del sessions[session_key]
            _h4c_store_sessions(sessions)
        return full
    names = []
    for issue in detail:
        n = issue.split(":", 1)[0].strip()
        if n and n not in names:
            names.append(n)
    if isinstance(prev, dict) and prev.get("fp") == fp:
        shown = ", ".join(names[:_H4C_SHORT_NAMES])
        if len(names) > _H4C_SHORT_NAMES:
            shown += f", +{len(names) - _H4C_SHORT_NAMES} more"
        return [f"GOVERNOR (H4c): {len(names)} governed file(s) still differ from the approved list ({shown}): "
                f"ask the founder to re-approve or restore"]
    sessions.pop(session_key, None)
    sessions[session_key] = {"fp": fp, "n": len(names), "ts": datetime.datetime.now().isoformat(timespec="seconds")}
    _h4c_store_sessions(sessions)
    return full


def _h4c_advisories(session_key: str = "") -> list:
    """H4c lines for this turn (shared by the normal path and the stop_hook_active path, B4-38).
    `session_key` ("" = none) turns on the once-per-session de-duplication of the issue lines below; the
    _PRELOAD_ISSUES and GOVERNOR CHECK UNAVAILABLE lines are always printed in full."""
    out = []
    for issue in _PRELOAD_ISSUES:                      # B4-07 / B5-30: siblings refused before execution
        out.append(f"GOVERNOR MODIFIED (H4c): {issue}")
    # B3-10 (reviewer A round 3): "cannot check" is reported, never silent; the comparator's own bytes are
    # checked independently of it.
    own = _approved_digest_check(".claude/hooks/hook_support.py")
    detail, full = [], []                              # the de-duplicated issue lines: every issue, and today's text
    if _hs is None:
        out.append(f"GOVERNOR CHECK UNAVAILABLE (H4c): .claude/hooks/hook_support.py could not be "
                          f"loaded ({type(_HS_ERR).__name__}), so the governor cannot be compared"
                          + (f"; GOVERNOR MODIFIED (H4c): {own}" if own else ""))
    elif own:
        detail.append(own)
        full.append(f"GOVERNOR MODIFIED (H4c): {own}")
    if _hs is not None:
        # Seat B B-27: a MISSING approved list is reported too, wherever a governance/ tree exists
        # (a deleted baseline must be visible within one turn); kits without governance/ stay silent.
        issues = [i for i in _hs.integrity_report(ROOT)
                  if not i.startswith("governance/hooks_approved.sha256 is missing")
                  # B2-32: a moved/removed governance/ must not silence the alarm; the root settings
                  # file marks an installed governor as surely as governance/ does.
                  or (ROOT / "governance").is_dir() or (ROOT / ".claude" / "settings.json").exists()]
        if issues:
            detail.extend(issues)
            full.append("GOVERNOR MODIFIED (H4c): " + "; ".join(issues[:3])
                              + (f" (+{len(issues) - 3} more)" if len(issues) > 3 else "")
                              + " — re-approve at a founder-visible landing or restore the approved files")
        out.extend(_h4c_report(detail, full, session_key))
    return out


class _Walk:
    """What the walk of a ledger decided: the change-set and the windows main() judges."""

    def __init__(self, edited_code, edited_since_verify, window_all, tamper_events, last_note, outside_notes, path_cwd):
        self.edited_code, self.edited_since_verify, self.window_all = edited_code, edited_since_verify, window_all
        self.tamper_events, self.last_note, self.outside_notes, self.path_cwd = tamper_events, last_note, outside_notes, path_cwd


def _walk(ledger) -> "_Walk":
    # `edited_code` is the FULL session change-set (needed as-is by the VERIFY-armed receipt path
    # below). `edited_since_verify` is the window this gate judges: it accumulates on each qualifying
    # edit and is cleared by a verification whose OUTCOME is visible and good (H1, 2026-10-05) --
    # no longer by a substring. Scoping history: see the 2026-09 notes in this file's git log.
    edited_code, edited_since_verify = [], []
    path_cwd = {}              # path -> cwd of the entry that wrote it (relative Bash targets)
    window_all = []            # H2: every written path, any suffix, since the last clearing verifier
    tamper_events = []         # H2: (line_no, test_paths, code_paths)
    last_note = ""             # H1: why the last verifier seen did NOT clear the window
    outside_notes = []         # H1e (advisory): verifications that only named another project's tests
    results = ledger.results
    line_no = -1

    def _real_ok(tid) -> bool:
        # v0.8 S-3: a call clears (an `rm`, a browser check) only on a REAL result that is not an error; no result, or a result
        # only in a blob copy, clears nothing
        return tid in results and not results[tid][0]

    def _wrote(fp: str, cwd: str) -> None:
        fp = _write_target(fp, cwd)    # 2026-11-10 hot-fix: an empty target is never a write target; R-3: qualified by its own event's cwd
        if fp is None:
            return
        path_cwd[fp] = cwd
        window_all.append(fp)
        if fp.endswith(CODE_SUFFIXES):
            edited_code.append(fp)
            edited_since_verify.append(fp)

    def _clear(keep) -> None:
        nonlocal window_all, edited_since_verify, tamper_events
        cleared = [p for p in window_all if not keep(p)]
        if _vr is not None:
            tests = [p for p in cleared if _vr.is_test_path(p) or Path(p).name in _vr.TEST_CONFIG_IF_DIFF]
            code = [p for p in cleared if p.endswith(CODE_SUFFIXES) and p not in tests]
            if tests and code:
                tests_abs = sorted({_abs(p, path_cwd.get(p, "")) for p in tests})   # Seat A F7(d)
                tamper_events.append((line_no, tests_abs, sorted(set(code))))
        window_all = [p for p in window_all if keep(p)]
        edited_since_verify = [p for p in edited_since_verify if keep(p)]

    for ev in ledger.events:
        line_no = ev["line"]
        uncertain, cwd = ev["blob"], ev["cwd"]
        # v0.7.2: a record whose type is "api-request-blob" repeats earlier tool_use blocks and carries no cwd. No cwd is ever
        # inferred: a real record with none keeps v0.7's rule (a relative path in it is a resolution failure).
        # v0.7.3 / v0.8: the ledger already dropped every blob call whose real twin is in the SAME ledger; one left (`uncertain`) has
        # no real twin: its write targets are recorded with NO cwd (an absolute path is placed normally; a relative one stays
        # relative, a resolution failure, so it blocks: both fail closed) and it never clears, never verifies.
        for blk in ev["blocks"]:
            tid = blk["id"]
            failed = tid in results and results[tid][0]
            if uncertain and ledger.incomplete:
                continue                    # v0.8 S-4: the read was not whole; the incomplete block is the outcome, nothing is classified
            name, ti = blk["name"], blk["input"]
            if uncertain:
                # no real twin: record the possible write (no cwd) EVEN IF a result says error (v0.7.4 F-1: a result for an
                # uncertain call is itself uncertain, so every write target is kept: fail closed), clear nothing, verify nothing
                if name in ("Edit", "Write", "NotebookEdit", "MultiEdit"):
                    _wrote(str(ti.get("file_path") or ti.get("notebook_path") or ""), "")   # v0.7.4 F-1: an error result still records it
                elif name == "Bash":
                    ucmd = str(ti.get("command", ""))
                    try:
                        try:
                            usegs = _vr.segments_full(ucmd) if _vr is not None else None
                        except ValueError:
                            usegs = None
                        if usegs:
                            # v0.8.1 (s282 F-1): the value a write sees is the one assigned BEFORE it (the real branch's ordered
                            # map); a later re-assignment no longer makes the target ambiguous and dropped
                            uamap = {}
                            for useg in usegs:
                                if _record_assignments(useg[2], uamap):
                                    continue
                                for fp in _resolve_vars(_bash_write_targets(" ".join(useg[2])), ucmd, uamap):
                                    _wrote(fp, "")
                        else:
                            for fp in _resolve_vars(_bash_write_targets(ucmd), ucmd):
                                _wrote(fp, "")
                    except Exception:
                        pass
                continue
            if name in ("Edit", "Write", "NotebookEdit", "MultiEdit"):   # A3 F-02: MultiEdit edits too
                if failed:
                    continue   # the tool call itself failed — nothing was actually written
                _wrote(str(ti.get("file_path") or ti.get("notebook_path") or ""), cwd)   # S4-44
            elif name == "Bash":
                cmd = str(ti.get("command", ""))
                # M-13: a Bash command that WRITES a file counts like an Edit — unless the call
                # errored (tested decision, TestErroredToolCallNotCountedAsEdit). A3 F-02 / N32: when
                # an errored call contains a verifier, the writes in the segments BEFORE its first
                # verifier still count (`cat > x.py <<EOF … && pytest` whose pytest fails did write
                # x.py); segments from that verifier on may not have run, so they are not counted.
                # Seat B B-16: writes are read PER SEGMENT (newlines split), and a write placed
                # AFTER the last verifier of the command is re-armed after the clear below.
                declared = _declared_commands(cwd, edited_since_verify[-5:])
                runs = _vr.analyse(cmd, declared) if _vr is not None else []
                # §3a (PLAN v0.7, founder ruling 2026-10-06, option A): only a PLAIN verifier clears.
                plain, plain_why = _vr.plain_verifier(cmd, declared) if _vr is not None else (None, "")
                if plain is not None and ti.get("run_in_background"):
                    # B3-06 (reviewer A round 3): a background launch's result is an acknowledgement, not the outcome
                    plain, plain_why = None, "run in the background (its outcome is not in this result)"
                before_w, after_w = [], []
                if not failed or runs:
                    try:
                        segs = _vr.segments_full(cmd) if _vr is not None else None
                    except ValueError:
                        segs = None
                    if segs:
                        last_idx = max((r["index"] for r in runs), default=-1)
                        first_idx = min((r["index"] for r in runs), default=-1)
                        amap = {}                  # B5-18: assignments in ORDER, real statements only
                        for k, seg in enumerate(segs):
                            if failed and k >= first_idx:
                                break              # N32: from the first verifier on, the errored call may not have run
                            if _record_assignments(seg[2], amap):
                                continue
                            tw = _resolve_vars(_bash_write_targets(" ".join(seg[2])), cmd, amap)
                            (after_w if runs and k > last_idx else before_w).extend(tw)
                    elif not failed:
                        before_w = _resolve_vars(_bash_write_targets(cmd), cmd)
                for fp in before_w:
                    _wrote(fp, cwd)
                # 2026-09-09 live incident: `rm` targets CLEAR a path from the pending sets.
                rm_targets = {_qualify(p, cwd) for p in _resolve_vars(_bash_rm_targets(cmd), cmd)} if _real_ok(tid) else set()   # Seat A F4; s239 H-2: qualified like the writes; v0.8 S-3: only a real, non-error result clears
                rm_targets = {p for p in rm_targets if os.path.isabs(p)}   # v0.7.3 (s259 F-1): an rm still relative (no cwd) clears nothing
                if rm_targets:
                    edited_code = [p for p in edited_code if p not in rm_targets]
                    edited_since_verify = [p for p in edited_since_verify if p not in rm_targets]
                    window_all = [p for p in window_all if p not in rm_targets]
                if _vr is None:
                    # B5-31 (reviewer A round 5): no degraded clearing. The recognizer was refused (modified, not
                    # approved, not a regular file) or failed to load: nothing can show that a run passed, so
                    # nothing clears until the hooks are re-approved; the block says why.
                    last_note = ("the H1 recognizer is unavailable (" + (type(_VR_ERR).__name__ if _VR_ERR else "?")
                                 + "), so no verification can clear until the hooks are re-approved at a "
                                   "founder-visible landing (.claude/hooks/hook_support.py --write-approved)")
                    continue
                if plain is not None:
                    absent = tid not in results
                    is_err, text, truncated = results.get(tid, (False, "", False))
                    # Seat A F17 (declined, tested): a call with NO recorded result clears like
                    # before; §3a keeps that contract for the plain form only.
                    # v0.7.3: a missing result stays a pass, EXCEPT when a result for this id exists only in a blob copy:
                    # that is a result we cannot trust, not an absent one (a blob-only result is not evidence of success)
                    verdict = ("unknown" if (absent and tid in ledger.blob_result_ids)
                               else "pass" if absent else _vr.plain_verdict(plain["kind"], text))
                    if (verdict == "pass" and truncated and plain["kind"].split(":")[-1] not in _vr.SUMMARY_KINDS
                            and plain["kind"] not in _vr.SYNTAX_KINDS):
                        # B3-05: a repo script clears on "no failure line"; a truncated result cannot show that
                        verdict = "unknown"
                    if is_err or verdict == "fail":
                        last_note = "the last verification run FAILED"
                    elif verdict == "empty":
                        last_note = "the last verification run collected/ran NO tests"
                    elif verdict != "pass":
                        last_note = (f"the last verification ({plain['kind']}) showed no complete runner "
                                     f"summary of tests that ran"
                                     f"{' (its output was truncated)' if truncated else ''}")
                    else:
                        paths = [_abs(p, cwd) for p in plain["paths"]]
                        # Project relatedness (H1e) is ADVISORY: a run whose explicit paths all lie in
                        # another project still clears, but is reported (P6 replay: precision unproven).
                        if paths and edited_since_verify:
                            troots = [_project_root_of(p) for p in paths]
                            unrelated = [p for p in edited_since_verify
                                         if not any(_related(_project_root_of(_abs(p, path_cwd.get(p, ""))), t) for t in troots)]
                            if unrelated and len(unrelated) == len(edited_since_verify):
                                outside_notes.append((line_no, ", ".join(sorted(set(unrelated))[:2])))
                        _clear(lambda p: False)
                        last_note = ""
                elif runs:
                    last_note = (f"the last verification was not a plain verifier command ({plain_why}); "
                                 f"run your tests on their own")
                for fp in after_w:
                    _wrote(fp, cwd)
            elif name in BROWSER_VERIFY_TOOLS:
                if _real_ok(tid):                  # Seat B B-17: only a SUCCESSFUL inspection clears
                    # S4-43 (final): and only WEB assets — a page read is no test of backend code.
                    _clear(lambda p: not p.lower().endswith(BROWSER_CLEARS))
            elif name == "mcp__Claude_Browser__computer" and ti.get("action") == "screenshot":
                if _real_ok(tid):
                    _clear(lambda p: not p.lower().endswith(BROWSER_CLEARS))   # a screenshot is inspection; a click/type is not
    return _Walk(edited_code, edited_since_verify, window_all, tamper_events, last_note, outside_notes, path_cwd)



def main() -> None:
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)
    if data.get("stop_hook_active"):
        # B4-38 (reviewer A round 4): no second H1 block, but a governor change made during closeout recovery is still
        # reported within the turn (bounded: the same H4c advisory as below).
        try:
            msgs = _h4c_advisories(_h4c_session_key(data))
            if msgs:
                print(json.dumps({"systemMessage": " | ".join(msgs)}))
        except Exception as exc:
            _log_error("stop_closeout_gate.h4c", exc)
        sys.exit(0)
    if _VR_ERR is not None:
        _log_error("stop_closeout_gate.import.verifier_recognizer", _VR_ERR)

    tpath = data.get("transcript_path", "")
    ledger = _read_ledger(tpath)
    if ledger is None:
        sys.exit(0)   # F7: can't safely read the transcript -> no closeout assertion (fail-open)
    walk = _walk(ledger)
    edited_code, edited_since_verify, window_all = walk.edited_code, walk.edited_since_verify, walk.window_all
    tamper_events, last_note, outside_notes = walk.tamper_events, walk.last_note, walk.outside_notes
    last_prompt_line, last_text, last_text_line = ledger.last_prompt_line, ledger.last_text, ledger.last_text_line


    blocked, missing = (_blocked_report(last_text) if last_text_line > last_prompt_line else (False, []))
    problems, advisories = [], []
    if ledger.incomplete:
        # v0.8 S-4 (decision D-8.1): a read that was not whole is never read as "nothing happened". It blocks, first, whatever else was
        # found in what was read (those problems follow it).
        problems.append(f"TRANSCRIPT READ INCOMPLETE ({'; '.join(ledger.incomplete)}): the gate cannot tell which edits were made or "
                        f"verified; continue in a new session (write the handoff first)")
    if edited_code:
        vstate = _verify_state(edited_code)
        if vstate["armed"]:
            # VERIFY-armed: require an un-fabricable independent-verification receipt for the
            # WHOLE session change-set. Keyed on `edited_code` (full session), not the rolling
            # window: a receipt attests the entire change-set at once. A project is armed by its own
            # `.verify/armed` marker (receipt.project_root); one without the marker gets the ordinary gate.
            sample = ", ".join(sorted(set(edited_code))[:3])
            if vstate["block"]:
                problems.append(f"VERIFY-GATE (armed): code was edited ({sample}…); {vstate['block']}")
            else:
                r_ok, r_why = _has_valid_receipt(edited_code, sorted(set(edited_code)), vstate)
                if not r_ok:
                    problems.append(
                        f"VERIFY-GATE (armed): code was edited ({sample}…) with no valid independent-"
                        f"verification receipt for this change-set — run the verify gate "
                        f"(_skills/verify_gate), or state why verification is impossible (Art. 1)"
                        + (f"; receipt: {r_why}" if r_why else ""))
        elif edited_since_verify:
            sample = ", ".join(sorted(set(edited_since_verify))[:3])
            if blocked and not missing:
                advisories.append(
                    f"BLOCKED declared (Art. 14): {len(set(edited_since_verify))} edited file(s) left "
                    f"unverified ({sample}…); the blocker report stands in for verification this turn")
            else:
                why = f" ({last_note})" if last_note else ""
                msg = (f"code was edited ({sample}…) since the last verification command{why} — run "
                       f"the relevant tests/checks on their own (one plain command: `cd <dir> && pytest -q`; "
                       f"no `| tail`, `&& echo`, `;`, `|| true` or redirect), or state explicitly why verification is impossible in a BLOCKED: "
                       f"report (blocker, diagnosis, what was tried, recommendation) (Art. 1, Art. 14)")
                if blocked and missing:
                    msg += f"; the BLOCKED: report is missing: {', '.join(missing)}"
                problems.append(msg)
    drift = _skill_version_drift()
    if drift:
        shown = "; ".join(drift[:3])
        more = f" (+{len(drift) - 3} more)" if len(drift) > 3 else ""
        problems.append(
            f"skill version drift — {shown}{more}. The SKILL.md frontmatter is the SSOT "
            f"(UNIVERSAL_SKILL_SPEC R-SHARED-4): align the other sources to it, then "
            f"`python3.12 _skills/build_index.py --strict` must exit 0")

    journal_root = _journal_project_root()
    proj_root = journal_root
    # S4-56 (final): the bound also covers the projects EDITED this session, so a session rooted at
    # the workspace root (no NEOCORTEX/ there, CLAUDE_PROJECT_DIR unset) no longer skips it.
    jroots = []
    for r in ([journal_root] if journal_root is not None else []) + list(_advisory_roots(edited_code, None) or []):
        if r is not None and r not in jroots:
            jroots.append(r)
    for jr in jroots:
        journal = jr / "NEOCORTEX" / "JOURNAL.md"
        try:
            if journal.is_file() and journal.stat().st_size > 64 * 1024:
                problems.append(
                    f"{journal} is {journal.stat().st_size // 1024} KB "
                    f"(bound: 64 KB) — rotate per NEOCORTEX_SPEC §2")
        except OSError:
            pass

    # H2 — test-tamper flag (ADVISORY; PLAN decision D3 may make it blocking after telemetry).
    # Fires only on WEAKENING evidence in the test diff (assertions removed, skip/xfail/only added,
    # timeouts changed). P6 replay 2026-10-05: a bare "tests and code changed together" rule would
    # have fired on 741 of 15,062 turns — ordinary test-first work — and trained everyone to ignore it.
    try:
        for ln_no, tests, code in tamper_events:
            if ln_no <= last_prompt_line:
                continue                       # an earlier turn's event: already reported then
            kept, details = _tamper_details(tests)
            if kept and details:
                advisories.append(
                    f"TEST-TAMPER CHECK (H2): tests or test configuration changed in the same window "
                    f"as code, then the verification passed — {', '.join(kept[:3])} with "
                    f"{', '.join(code[:3])}{details}. In the closeout, list each test edit and its "
                    f"reason (fixing a wrong test is legitimate; weakening one to pass is not)")
    except Exception as exc:
        _log_error("stop_closeout_gate.h2", exc)

    current = [note for ln_no, note in outside_notes if ln_no > last_prompt_line]
    if current:
        advisories.append(f"VERIFY SCOPE (H1e, advisory): a verification named only another project's "
                          f"tests while {current[-1]}… was pending — check that the right suite ran")

    # H4c — governor tamper evidence. Silent while no approved list exists (the session anchor
    # reports that case once per session); after a landing, any mismatch is surfaced every turn.
    try:
        advisories.extend(_h4c_advisories(_h4c_session_key(data)))
    except Exception as exc:
        _log_error("stop_closeout_gate.h4c", exc)

    # S4-06: the decision is printed BEFORE the slow advisories below, so their subprocess
    # timeouts can never eat a block.
    out = {}
    if problems:
        try:
            _safe_append(ROOT, ("governance", "pilot_telemetry.log"),
                         f"{datetime.datetime.now().isoformat(timespec='seconds')} CLOSEOUT-BLOCK {problems[0][:60]}\n")
        except OSError:
            pass
        out = {"decision": "block", "reason": "CLOSEOUT-GATE: " + "; ".join(problems) + "."}
    if advisories:
        for a in advisories:
            try:
                _safe_append(ROOT, ("governance", "pilot_telemetry.log"),
                             f"{datetime.datetime.now().isoformat(timespec='seconds')} CLOSEOUT-NOTE {a[:70]}\n")
            except OSError:
                pass
        out["systemMessage"] = "CLOSEOUT-GATE advisories: " + " | ".join(advisories)
    if out:
        print(json.dumps(out))
        sys.stdout.flush()

    # NON-BLOCKING advisories — deliberately kept out of `problems` (founder ruling
    # 2026-07-27: advisory-first, not blocking). Durable in the telemetry log so they survive
    # the turn; echoed to stderr so they are not silently swallowed. Scoped to the projects
    # EDITED this turn: resolving from cwd skips them entirely whenever the session is rooted
    # at the workspace root, which is where governance work happens.
    # ONE wall-clock budget for the whole loop (all roots together), founder ruling 2026-11-11:
    # slot 153 measured 28.7 s of side checks against this hook's 20 s limit. Each child's
    # timeout is the remainder (min 0.5 s); once spent, the rest are skipped and said so.
    budget_s, t_start, skipped = 5.0, time.monotonic(), 0
    for proj_root in _advisory_roots(edited_code, journal_root):
        for marker, check, howto in (
            ("CLOSEOUT-ADVISORY", _continuity_advisory,
             "governance/scripts/continuity_sweep.py --project"),
            ("CLOSEOUT-STALENESS", _staleness_advisory,
             "governance/scripts/closeout_lint.py --path"),
        ):
            remaining = budget_s - (time.monotonic() - t_start)
            if remaining <= 0:
                skipped += 1
                continue
            text = check(proj_root, timeout=max(0.5, remaining))
            if not text:
                continue
            try:
                _safe_append(ROOT, ("governance", "pilot_telemetry.log"),
                             f"{datetime.datetime.now().isoformat(timespec='seconds')} "
                             f"{marker} {proj_root.name}: {text[:80]}\n")
            except OSError:
                pass
            print(f"{marker} (non-blocking) {proj_root.name}: {text} — "
                  f"run {howto} '{proj_root}' for detail", file=sys.stderr)
    if skipped:
        line = f"CLOSEOUT-ADVISORY skipped: side-check budget 5 s spent ({skipped} skipped)"
        try:
            _safe_append(ROOT, ("governance", "pilot_telemetry.log"),
                         f"{datetime.datetime.now().isoformat(timespec='seconds')} {line}\n")
        except OSError:
            pass
        print(line, file=sys.stderr)
    sys.exit(0)


if __name__ == "__main__":
    if sys.argv[1:] == ["--skill-drift-only"]:       # B5-20: the child process of _skill_version_drift()
        print(json.dumps(_skill_version_drift_inproc()))
        sys.exit(0)
    try:
        main()
    except SystemExit:
        raise
    except Exception as _exc:              # fail-open, but no longer silent (H0)
        _log_error("stop_closeout_gate.main", _exc)
        sys.exit(0)

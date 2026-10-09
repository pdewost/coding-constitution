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
import re
import shlex
import shutil
import subprocess
import sys
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
    guard's variable indirection). `~` is expanded."""
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
        if "$" in t:
            def sub(m):
                v = assigns.get(m.group(1), None if amap is not None and m.group(1) in amap else
                                os.environ.get(m.group(1)))
                return v if v and "\x00" not in v else "\x00"
            t = re.sub(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?", sub, t)
            if "\x00" in t or "$" in t:
                continue
        out.append(os.path.expanduser(t))
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
                tgt = _unquote(seg[i + 1])
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
                targets.extend(a for a in _vr._nonflags(args) if "." in a.split("/")[-1])
            continue
        if verb == "sed" and any(re.match(r"^(-[A-Za-z]*i|--in-place)", _unquote(a)) for a in args):   # B5-09
            targets.extend(_unquote(a) for a in args if not a.startswith("-"))
        elif verb in _NAMED_WRITE_VERBS:
            targets.extend(_unquote(a) for a in args if not a.startswith("-"))
        elif verb == "dd":
            for a in args:
                if a.lower().startswith("of="):
                    targets.append(_unquote(a[3:]))
        elif verb in _DEST_VERBS:
            operands = [a for a in args if not a.startswith("-")]
            if operands:
                targets.append(_unquote(operands[-1]))
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
        targets.extend(_unquote(a) for a in words[1:] if not a.startswith("-"))
    return targets


def _transcript_cap() -> int:
    import os
    try:
        v = int(os.environ.get("CLOSEOUT_TRANSCRIPT_CAP", ""))
        if 1000 <= v <= 64 * 1024 * 1024:
            return v                                        # tests only; bounded
    except ValueError:
        pass
    return 64 * 1024 * 1024


TRANSCRIPT_CAP = _transcript_cap()
_TRUNCATED = [False]


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
    fh = os.fdopen(fd, encoding="utf-8", errors="replace")
    # S4-55 (final): past the cap, read the TAIL (the recent turns) instead of silently dropping it.
    try:
        size = os.fstat(fd).st_size
        if size > TRANSCRIPT_CAP:
            fh.seek(size - TRANSCRIPT_CAP)
            fh.readline()                                   # drop the partial first line
            _TRUNCATED[0] = True
    except (OSError, ValueError):
        pass
    return fh


def _collect_error_tool_use_ids(tpath: str) -> set:
    """Pre-pass: every tool_use `id` whose paired tool_result came back is_error=true.

    A tool_use block exists in the transcript the moment the call is MADE — it says
    nothing about whether the call actually succeeded. A failed Edit (bad path, a no-op
    "old_string == new_string" rejection, a permissions error) still produces a normal
    tool_use block, so without this check it counted as a real edit, exactly like a
    successful one. Found live, 2026-09-14: a mistyped Edit path
    (/srv/example/workspace/example-project/…, missing a path segment, directory doesn't
    exist) errored with no write, yet kept showing up in the block message turns later
    as still-unverified — there was never anything to verify.

    Best-effort and fail-open: any parse hiccup just omits that id from the set (worst
    case, one more edit gets treated as real and asks for verification that technically
    wasn't needed — the same over-inclusion bias as the rest of this gate, never the
    reverse). Second pass over the same file the main loop reads; same 64 MB cap.
    """
    ids = set()
    fh = _safe_open_transcript(tpath)
    if fh is None:
        return ids
    try:
        read_bytes = 0
        for line in fh:
            try:
                entry = json.loads(line)
            except Exception:
                continue
            msg = entry.get("message") or {}
            for blk in msg.get("content") or []:
                if not isinstance(blk, dict) or blk.get("type") != "tool_result":
                    continue
                if blk.get("is_error") is True and blk.get("tool_use_id"):
                    ids.add(blk["tool_use_id"])
    except OSError:
        return ids
    finally:
        fh.close()
    return ids


def _verify_project_root(edited_paths):
    """Resolve the ONE governed project `.verify/` belongs to, from the files EDITED THIS TURN
    — never from `Path.cwd()`. Found 2026-08-28, proving the mechanism live rather than reading
    the code: `Path.cwd()/.verify/...` reads valid only when the session's actual shell cwd is
    the project itself, and reads ABSENT from the workspace root — this session's own documented
    "primary working directory," and where most governed work here actually happens. Placing
    `.verify/armed` at a project root would leave a workspace-root-cwd session silently UNARMED
    despite being armed; placing it at the workspace root instead would silently govern the
    entire fleet. Neither is "arm this project." Same fix shape `_advisory_roots()` already uses
    for the non-blocking advisories, one turn's edited files rather than cwd.

    NOT shared code with `_advisory_roots()` / `_journal_project_root()` — each already has a
    different contract (up-to-3 roots with a fallback; single root from cwd/env) and this file's
    own convention is separate, purpose-built resolvers, not a forced-shared abstraction.

    SAFE to widen, unlike the JOURNAL bound check `_advisory_roots()`'s own docstring warns off:
    that check's predicate (an oversized JOURNAL.md) is real and widespread today, so fixing its
    resolution could start blocking turns that pass by accident. verify_gate's predicate (a
    `.verify/armed` marker) exists NOWHERE in the fleet as of this fix — confirmed by a live
    `find` sweep — so correcting the resolution changes zero live behavior today; it only matters
    once/if a project is actually armed.

    Bounded to the FIRST governed root found (deterministic order), not up to 3 like the
    advisories — a `.verify/` state is per-project, singular, and a turn touching multiple
    armed projects at once is an edge case worth naming, not engineering for blind.
    """
    for fp in edited_paths or []:
        try:
            cand = Path(fp).resolve().parent
        except Exception:
            continue
        for d in (cand, *cand.parents):
            if (d / "CLAUDE.md").is_file() and (d / "NEOCORTEX").is_dir():
                return d
    return None


def _verify_armed(edited_paths) -> bool:
    """A project opts into BLOCKING independent-verification by placing a `.verify/armed`
    marker AT ITS OWN ROOT (resolved from edited files, see `_verify_project_root`). Until then
    the VERIFY-GATE is advisory (see verify_advisory.py) — this gate never blocks on it. No
    project is armed today, so this path is dormant (zero behavior change)."""
    try:
        root = _verify_project_root(edited_paths)
        if root is None:
            return False
        return (root / ".verify" / "armed").is_file()
    except OSError:
        return False


def _has_valid_receipt(edited_paths, changed_paths: list) -> bool:
    """True iff a valid, change-set-bound VERIFY-RECEIPT exists for exactly these files, at the
    edited project's own `.verify/` (never `Path.cwd()`). The fabricable `echo test_` substring
    does NOT satisfy this — it needs a real attested verdict."""
    try:
        root = _verify_project_root(edited_paths)
        if root is None:
            return False
        import sys as _sys
        _sys.path.insert(0, str(ROOT / "_skills" / "verify_gate" / "scripts"))
        import receipt as _r  # type: ignore
        rpath = root / ".verify" / "receipt.json"
        if not rpath.is_file():
            return False
        rec = json.loads(rpath.read_text(encoding="utf-8"))
        ledger = str(root / ".verify" / "_consumed_nonces.json")
        ok, _ = _r.validate_receipt(rec, changed_paths=changed_paths, ledger_path=ledger)
        return bool(ok)
    except Exception:
        return False


def _python312():
    """Resolve the provisioned interpreter. NEVER sys.executable: the interpreter running
    hooks may be Xcode's 3.9.6 (workspace CLAUDE.md, Python interpreter discipline), which
    fails the sweep spuriously. Return None (=> advisory skipped) if 3.12 is absent."""
    try:
        return shutil.which("python3.12") or (
            "/usr/local/bin/python3.12"
            if Path("/usr/local/bin/python3.12").is_file() else None)
    except Exception:
        return None


def _continuity_advisory(proj_root) -> str:
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
                           capture_output=True, text=True, timeout=8)
        if r.returncode != 1:      # 0 = clean, 2 = usage error => stay silent
            return ""
        last = [ln for ln in r.stdout.splitlines() if ln.startswith("RESULT:")]
        return last[-1].strip() if last else "continuity findings present"
    except Exception:
        return ""


def _staleness_advisory(proj_root, ledger_path: 'Path | None' = None) -> str:
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
                           capture_output=True, text=True, timeout=20)
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
        for d in (cand, *cand.parents):
            if (d / "CLAUDE.md").is_file() and (d / "NEOCORTEX").is_dir():
                if str(d) not in seen:
                    seen.add(str(d))
                    roots.append(d)
                break
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
    was real, correct, and unread, and `linkedin_scraper` sat split across FOUR sources
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


def _collect_results(tpath: str) -> dict:
    """tool_use_id -> (is_error, text) for every tool_result (text = first 4 KB + last 4 KB).
    Same bounded, fail-open pass as _collect_error_tool_use_ids, which it supersedes in main()."""
    out = {}
    fh = _safe_open_transcript(tpath)
    if fh is None:
        return out
    try:
        read_bytes = 0
        for line in fh:
            try:
                entry = json.loads(line)
            except Exception:
                continue
            for blk in (entry.get("message") or {}).get("content") or []:
                if isinstance(blk, dict) and blk.get("type") == "tool_result" and blk.get("tool_use_id"):
                    t = _text_of(blk.get("content"))
                    truncated = len(t) > 8192             # Seat B B-14: a cut output never proves a pass
                    if truncated:
                        t = t[:4096] + "\n...\n" + t[-4096:]
                    out[blk["tool_use_id"]] = (blk.get("is_error") is True, t, truncated)
    except OSError:
        return out
    finally:
        fh.close()
    return out


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
            if ln[:1] in "+-" and ln[:3] not in ("+++", "---") and re.search(r"timeout\D{0,12}\d", ln, re.I):
                timeout_lines += 1
    bits = []
    if removed_asserts:
        bits.append(f"{removed_asserts} assertion line(s) removed")
    if added_skips:
        bits.append(f"{added_skips} skip/xfail/only marker(s) added")
    if timeout_lines:
        bits.append(f"{timeout_lines} timeout line(s) changed")
    return kept, ("; diff: " + ", ".join(bits)) if bits else ""



def _h4c_advisories() -> list:
    """H4c lines for this turn (shared by the normal path and the stop_hook_active path, B4-38)."""
    out = []
    for issue in _PRELOAD_ISSUES:                      # B4-07 / B5-30: siblings refused before execution
        out.append(f"GOVERNOR MODIFIED (H4c): {issue}")
    # B3-10 (reviewer A round 3): "cannot check" is reported, never silent; the comparator's own bytes are
    # checked independently of it.
    own = _approved_digest_check(".claude/hooks/hook_support.py")
    if _hs is None:
        out.append(f"GOVERNOR CHECK UNAVAILABLE (H4c): .claude/hooks/hook_support.py could not be "
                          f"loaded ({type(_HS_ERR).__name__}), so the governor cannot be compared"
                          + (f"; GOVERNOR MODIFIED (H4c): {own}" if own else ""))
    elif own:
        out.append(f"GOVERNOR MODIFIED (H4c): {own}")
    if _hs is not None:
        # Seat B B-27: a MISSING approved list is reported too, wherever a governance/ tree exists
        # (a deleted baseline must be visible within one turn); kits without governance/ stay silent.
        issues = [i for i in _hs.integrity_report(ROOT)
                  if not i.startswith("governance/hooks_approved.sha256 is missing")
                  # B2-32: a moved/removed governance/ must not silence the alarm; the root settings
                  # file marks an installed governor as surely as governance/ does.
                  or (ROOT / "governance").is_dir() or (ROOT / ".claude" / "settings.json").exists()]
        if issues:
            out.append("GOVERNOR MODIFIED (H4c): " + "; ".join(issues[:3])
                              + (f" (+{len(issues) - 3} more)" if len(issues) > 3 else "")
                              + " — re-approve at a founder-visible landing or restore the approved files")
    return out


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)
    if data.get("stop_hook_active"):
        # B4-38 (reviewer A round 4): no second H1 block, but a governor change made during closeout recovery is still
        # reported within the turn (bounded: the same H4c advisory as below).
        try:
            msgs = _h4c_advisories()
            if msgs:
                print(json.dumps({"systemMessage": " | ".join(msgs)}))
        except Exception as exc:
            _log_error("stop_closeout_gate.h4c", exc)
        sys.exit(0)
    if _VR_ERR is not None:
        _log_error("stop_closeout_gate.import.verifier_recognizer", _VR_ERR)

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
    last_prompt_line = -1
    last_text, last_text_line = "", -1
    tpath = data.get("transcript_path", "")
    results = _collect_results(tpath)
    error_tool_use_ids = {k for k, v in results.items() if v[0]}
    fh = _safe_open_transcript(tpath)
    if fh is None:
        sys.exit(0)   # F7: can't safely read the transcript -> no closeout assertion (fail-open)

    def _wrote(fp: str, cwd: str) -> None:
        if _is_non_closeout_path(fp):
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

    try:
        import re
        read_bytes = 0
        line_no = -1
        for line in fh:
            line_no += 1
            try:
                entry = json.loads(line)
            except Exception:
                continue
            if _is_human_prompt(entry):
                last_prompt_line = line_no
            msg = entry.get("message") or {}
            cwd = str(entry.get("cwd") or "")
            if entry.get("type") == "assistant":
                for blk in msg.get("content") or []:
                    if isinstance(blk, dict) and blk.get("type") == "text" and blk.get("text"):
                        last_text, last_text_line = blk["text"], line_no
            for blk in msg.get("content") or []:
                if not isinstance(blk, dict) or blk.get("type") != "tool_use":
                    continue
                tid = blk.get("id")
                failed = tid in error_tool_use_ids
                name, ti = blk.get("name", ""), blk.get("input", {}) or {}
                if name in ("Edit", "Write", "NotebookEdit"):
                    if failed:
                        continue   # the tool call itself failed — nothing was actually written
                    _wrote(str(ti.get("file_path") or ti.get("notebook_path") or ""), cwd)   # S4-44
                elif name == "Bash":
                    cmd = str(ti.get("command", ""))
                    # M-13: a Bash command that WRITES a file counts like an Edit — unless the call
                    # errored (tested decision, TestErroredToolCallNotCountedAsEdit). Known gap, not
                    # touched here: a compound `cat > x.py <<EOF … && pytest` whose pytest fails
                    # did write x.py, yet is skipped by that rule.
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
                    if not failed:
                        try:
                            segs = _vr.segments_full(cmd) if _vr is not None else None
                        except ValueError:
                            segs = None
                        if segs:
                            last_idx = max((r["index"] for r in runs), default=-1)
                            amap = {}                  # B5-18: assignments in ORDER, real statements only
                            for k, seg in enumerate(segs):
                                if _record_assignments(seg[2], amap):
                                    continue
                                tw = _resolve_vars(_bash_write_targets(" ".join(seg[2])), cmd, amap)
                                (after_w if runs and k > last_idx else before_w).extend(tw)
                        else:
                            before_w = _resolve_vars(_bash_write_targets(cmd), cmd)
                    for fp in before_w:
                        _wrote(fp, cwd)
                    # 2026-09-09 live incident: `rm` targets CLEAR a path from the pending sets.
                    rm_targets = set(_resolve_vars(_bash_rm_targets(cmd), cmd)) if not failed else set()   # Seat A F4
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
                        verdict = "pass" if absent else _vr.plain_verdict(plain["kind"], text)
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
                    if not failed:                    # Seat B B-17: only a SUCCESSFUL inspection clears
                        # S4-43 (final): and only WEB assets — a page read is no test of backend code.
                        _clear(lambda p: not p.lower().endswith(BROWSER_CLEARS))
                elif name == "mcp__Claude_Browser__computer" and ti.get("action") == "screenshot":
                    if not failed:
                        _clear(lambda p: not p.lower().endswith(BROWSER_CLEARS))   # a screenshot is inspection; a click/type is not
    except OSError:
        sys.exit(0)
    finally:
        fh.close()

    blocked, missing = (_blocked_report(last_text) if last_text_line > last_prompt_line else (False, []))
    problems, advisories = [], []
    if edited_code:
        if _verify_armed(edited_code):
            # VERIFY-armed: require an un-fabricable independent-verification receipt for the
            # WHOLE session change-set. Keyed on `edited_code` (full session), not the rolling
            # window: a receipt attests the entire change-set at once. (Dormant: no project armed.)
            sample = ", ".join(sorted(set(edited_code))[:3])
            if not _has_valid_receipt(edited_code, sorted(set(edited_code))):
                problems.append(
                    f"VERIFY-GATE (armed): code was edited ({sample}…) with no valid independent-"
                    f"verification receipt for this change-set — run the verify gate "
                    f"(_skills/verify_gate), or state why verification is impossible (Art. 1)")
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

    if _TRUNCATED[0]:
        advisories.append(f"TRANSCRIPT TRUNCATED (S4-55): only the last {TRANSCRIPT_CAP // (1024 * 1024) or 1} MB of the "
                          f"session were read; edits older than that are not judged")
    current = [note for ln_no, note in outside_notes if ln_no > last_prompt_line]
    if current:
        advisories.append(f"VERIFY SCOPE (H1e, advisory): a verification named only another project's "
                          f"tests while {current[-1]}… was pending — check that the right suite ran")

    # H4c — governor tamper evidence. Silent while no approved list exists (the session anchor
    # reports that case once per session); after a landing, any mismatch is surfaced every turn.
    try:
        advisories.extend(_h4c_advisories())
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
    for proj_root in _advisory_roots(edited_code, journal_root):
        for marker, text, howto in (
            ("CLOSEOUT-ADVISORY", _continuity_advisory(proj_root),
             "governance/scripts/continuity_sweep.py --project"),
            ("CLOSEOUT-STALENESS", _staleness_advisory(proj_root),
             "governance/scripts/closeout_lint.py --path"),
        ):
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

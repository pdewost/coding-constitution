#!/usr/bin/env python3
"""
governance/scripts/continuity_sweep.py — mechanical end-of-sprint continuity +
staleness sweep across every NEOCORTEX in the workspace.

Source of requirements: governance/AUDIT_gate_effectiveness_2026-07-27.md §5 (P5)
and REFLECTION_operator_failure_modes_2026-07-27.md §6 (classes 1-3, 5).

Invocation (manual, or from a sprint_closeout pass — this script does NOT
self-schedule and nothing is wired to invoke it automatically as of 2026-07-27):

    python3.12 governance/scripts/continuity_sweep.py [--root <workspace>]
                                                      [--project <path>] [--json]

Exit codes: 0 = clean, 1 = at least one finding (heuristic prompts included),
2 = usage error.

Checks
  1 bounds        STATUS.md <= 150 lines; JOURNAL.md < 64 KB; active-side
                  PLAN_*.md <= 300 lines. NOTE on "active": the spec mandates a
                  line-1 `Status: ACTIVE|DONE|SUPERSEDED-BY|ARCHIVED` header, but
                  live plans deviate (bold markers, ad-hoc vocab like DRAFT /
                  VALIDATED / IN EXECUTION, status on line 3). A literal
                  line-1-ACTIVE match fires on zero of the three plans the audit
                  already knows exceed the bound, so this check treats a plan as
                  active-side unless its status marker contains DONE, SUPERSEDED
                  or ARCHIVED, and reports the marker text it read.
  2 manifest      MANIFEST.json parity per NEOCORTEX_SPEC §2: required fields,
                  files[] entries are objects with file/genre/status, genre one
                  of the six, every root *.md (except STATUS/JOURNAL) listed,
                  every listed file present on disk. Mandatory-file presence
                  (MANIFEST/STATUS/JOURNAL) reports here too.
  3 status_truth  LOW-CONFIDENCE HEURISTIC, a prompt for human adjudication and
                  never an assertion: a doc whose status marker says ACTIVE or
                  DRAFT, or whose body claims "no code written" / "not yet
                  implemented" / "pending gate" / "not yet deployed", while the
                  project's git subtree shows commits strictly AFTER the doc's
                  date stamp (filename date, else file mtime). False positives
                  are expected; each finding names its date source and commit
                  count so a human can adjudicate.
  4 tracking      In a project whose NEOCORTEX has tracked files, list files
                  inside the NEOCORTEX that git ignores (the PROJECT-B rotated-
                  JOURNAL defect class). Reports the inconsistency; which side
                  is wrong is a policy call this script does not make.
                  Filters out .DS_Store / __pycache__ / *.pyc noise.
  5 dangling_refs Paths cited in STATUS.md / active-side plans that exist at
                  none of: project root, NEOCORTEX/, NEOCORTEX/_archive/,
                  workspace root (absolute paths checked as-is, ~ expanded).
                  DROPPED by the extraction filter (documented, so silence is
                  interpretable — calibrated on the 2026-07-27 first fleet run,
                  which showed the raw extractor drowning ~200 real findings in
                  ~1,000 command lines and route fragments): URLs; any token
                  containing whitespace (shell command lines; also loses real
                  space-containing macOS paths — accepted cost); any token
                  containing ':' (pytest node-ids, 'site:' operators, HTTP
                  verbs); glob/placeholder/shell chars (* ? < > { } $ | ;);
                  ellipses; tokens starting with '-'; absolute paths outside
                  /Users, /Volumes, /Applications, /opt, /usr/local (routes
                  like 'POST /close', ephemeral /tmp //var); template date
                  placeholders; bare names with no slash at all (basename
                  shorthand for files that usually exist deeper in the tree);
                  extension-less relative paths whose FIRST segment exists
                  nowhere (junk fragments) — with an existing first segment
                  they are reported (a real tree, a missing leaf).
  6 instruments   The workspace's own tooling, fleet mode only: every *.py/*.sh
                  under governance/ (excluding .publish_staging — a published
                  COPY, not the live instrument) and .claude/hooks/ (excluding
                  test_*). Claims are read from the file head: (a) a hook file
                  must be referenced by .claude/settings.json or
                  settings.local.json; (b) a cron/launchd/schedule claim must
                  appear in `crontab -l`, `launchctl list`, or a
                  ~/Library/LaunchAgents plist (lines saying "not scheduled" /
                  "Schedule: none" are negations, not claims); (c) an output
                  artifact named with a <YYYY-MM>/<YYYY-MM-DD> pattern must
                  exist for the current period (the newest actual sibling is
                  reported for context); (d) an "invoked/called/run by X" claim
                  must have at least one referencer of this script's basename
                  under governance/, .claude/ or _skills/ (a bounded, Python-
                  walked search — pathlib does not consult .gitignore, so this
                  is gitignore-proof within that scope; a wiring that lives
                  elsewhere would be missed and is called out in the report).
  7 invariants    LOW-CONFIDENCE ADVISORY: STATUS.md `## Invariants` entries
                  phrased as a remedy ("always add X", "remember to Y", "don't
                  forget") with no checkable predicate (no threshold, bound,
                  number, or "no X may ..." form). Counted for human rewrite,
                  never rewritten here.

NOT mechanized here, deliberately (named per the audit's own discipline):
  - AUDIT P5 item 4 (stale numbers re-queried against the DB): the figures live
    in project-specific prose and the authoritative queries live in project-
    specific schemas; a generic fleet script has no schema knowledge to re-derive
    them. Belongs in each project's own close-out harness.
  - REFLECTION §6.4 (checks written in one language on a multilingual product):
    a product-copy locale-parity scan, meaningful only inside the one
    multilingual project (PROJECT-A) and its test suite, not against NEOCORTEX
    state files.

Read-only over projects: this script writes nothing anywhere.
Stdlib only. Python 3.12. Space-safe: pathlib + list-form subprocess throughout.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import subprocess
import sys
from pathlib import Path

# ── constants ─────────────────────────────────────────────────────────────────

STATUS_MAX_LINES = 150
JOURNAL_MAX_BYTES = 64 * 1024
PLAN_MAX_LINES = 300

VALID_GENRES = {"PLAN", "AUDIT", "INCIDENT", "DECISION", "NOTE", "PROTOCOL"}
EXEMPT_MD = {"STATUS.md", "JOURNAL.md"}
REQUIRED_TOP = {"spec", "project", "updated", "archive", "files"}
REQUIRED_ARCHIVE = {"exists", "span", "contents", "note"}
REQUIRED_FILE_FIELDS = {"file", "genre", "status"}

CLOSED_STATUS_RE = re.compile(r"\b(DONE|SUPERSEDED|ARCHIVED)\b", re.IGNORECASE)
STATUS_MARKER_RE = re.compile(r"(?i)\*{0,2}\s*status\s*:?\s*\*{0,2}\s*:?\s*(.{0,110})")
STATUS_HEAD_LINES = 5  # how deep we look for a status marker

STALE_PHRASES = ("no code written", "not yet implemented", "pending gate",
                 "not yet deployed")

PRUNE_DIR_NAMES = {"node_modules", "__pycache__", "venv", "env", "data",
                   "worktrees", "Library"}
# every dot-directory is pruned during discovery (covers .git/.venv/.claude —
# the one NEOCORTEX under .claude/worktrees/ is excluded by design anyway).
# NOTE the `worktrees` entry here is belt-and-braces only: the authoritative
# worktree exclusion is _is_linked_worktree(), which reads the `.git` POINTER
# FILE and therefore catches a worktree whatever its directory is called.

TRACKING_NOISE = {".DS_Store"}
TRACKING_NOISE_SUFFIX = (".pyc",)

REMEDY_RE = re.compile(
    r"(?i)(\b(?:remember to|don'?t forget|do not forget|be sure to|"
    r"make sure (?:to|you)|always (?:add|run|use|include|check|set|verify|"
    r"call|keep|update|re-?run|confirm))\b|always\s+`)")
# 'always `func()`' (imperative with a backticked remedy) counts; a declarative
# 'is always X' does not — the verb whitelist plus the backtick form keeps
# "the DB is always WAL-mode"-style observable claims out.
TESTABLE_RE = re.compile(
    r"(?i)(may not exceed|must not exceed|no .{1,60} may\b|never exceeds?|"
    r"[<>≤≥]=?\s*\d|\b\d+\s*(kb|mb|gb|lines?|files?|days?|s\b|sec|ms)\b|"
    r"exit\s+\d|==|!=)")

SCHEDULE_NEGATION_RE = re.compile(
    r"(?i)(not (?:currently )?scheduled|unscheduled|schedule:\s*none|"
    r"does not self-schedule|not self-scheduling|"
    r"no (?:crontab|cron job|launchd job|schedule))")
# A schedule CLAIM is a labeled field or an explicit cadence statement — NOT a
# bare keyword: prose that merely mentions cron/launchd (e.g. a script
# describing how it checks OTHER scripts' schedules) must not self-flag.
# First fleet run 2026-07-27 produced exactly that false positive.
SCHEDULE_CLAIM_RE = re.compile(
    r"(?i)(^\s*#?\s*schedule\s*:|croncreate|"
    r"runs (?:at|every|daily|weekly|monthly|nightly))")
LABEL_RE = re.compile(r"\b(com\.[a-z0-9_-]+(?:\.[a-z0-9_-]+)+)\b")
HOOK_CLAIM_RE = re.compile(
    r"(?i)\b(stop hook|session-?close hook|sessionstart|userpromptsubmit|"
    r"pretooluse|posttooluse|stop-hook)\b")
INVOKED_CLAIM_RE = re.compile(
    r"(?i)\b(?:invoked|called|used|executed)\s+(?:by|from)\b")
DATE_ARTIFACT_RE = re.compile(r"([\w./-]*<YYYY-MM(?:-DD)?>[\w./-]*)")

URL_RE = re.compile(r"https?://\S+|\b\w+://\S+")
BACKTICK_RE = re.compile(r"`([^`\n]{2,200})`")
BARE_PATH_RE = re.compile(
    r"(?<![\w/`.])((?:[A-Za-z0-9_.À-ɏ-]+/)+"
    r"[A-Za-z0-9_.À-ɏ-]+\.[A-Za-z0-9]{1,12})")
KNOWN_SUFFIXES = (
    ".md", ".py", ".json", ".sqlite", ".db", ".sh", ".js", ".ts", ".yaml",
    ".yml", ".txt", ".csv", ".html", ".css", ".scpt", ".applescript", ".log",
    ".plist", ".xlsx", ".spec.js")
DROP_CHARS = set("*?<>{}$|;\"'")
ABS_ALLOWED_PREFIXES = ("/Users/", "/Volumes/", "/Applications/", "/opt/",
                        "/usr/local/", "~")


# ── small helpers ─────────────────────────────────────────────────────────────

def _read_text(p: Path) -> str | None:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _git(project: Path, *args: str) -> tuple[int, str]:
    """Run git with list-form args (space-safe), cwd=project. (rc, out+err)."""
    try:
        r = subprocess.run(["git", "-C", str(project), *args],
                           capture_output=True, text=True, timeout=60)
        return r.returncode, (r.stdout + r.stderr)
    except (OSError, subprocess.SubprocessError) as e:
        return 999, f"(git unavailable: {e})"


def _finding(check: str, severity: str, confidence: str, message: str,
             detail: str = "") -> dict:
    return {"check": check, "severity": severity, "confidence": confidence,
            "message": message, "detail": detail}


def _status_marker(text: str) -> tuple[str | None, int]:
    """Return (marker_text, line_no 1-based) from the first STATUS_HEAD_LINES
    lines, or (None, 0). See module docstring for why not literal line-1."""
    for i, line in enumerate(text.splitlines()[:STATUS_HEAD_LINES], 1):
        m = STATUS_MARKER_RE.search(line)
        if m:
            return m.group(1).strip(), i
    return None, 0


def _is_active_side(marker: str | None) -> bool:
    """Active-side unless the marker names a closed state."""
    if marker is None:
        return True          # spec-mandated header absent -> treat as active-side
    return not CLOSED_STATUS_RE.search(marker)


def _doc_date(path: Path) -> tuple[str, str]:
    """(ISO date, source) — first YYYY-MM-DD anywhere in the filename (some docs
    carry it mid-name: DESIGN_2026-06-05_clip_json.md), else file mtime."""
    m = re.search(r"(\d{4}-\d{2}-\d{2})", path.name)
    if m:
        return m.group(1), "filename"
    try:
        return _dt.date.fromtimestamp(path.stat().st_mtime).isoformat(), "mtime"
    except OSError:
        return _dt.date.today().isoformat(), "fallback-today"


# ── discovery ─────────────────────────────────────────────────────────────────

def _is_linked_worktree(d: Path) -> bool:
    """True when `d` is the root of a git LINKED WORKTREE.

    THE SIGNAL IS THE FILE TYPE, NOT THE NAME. `git worktree add` writes a `.git`
    **file** holding a single `gitdir: <path>` line, where a normal clone has a
    `.git` **directory**. That is a git-internal invariant, so it identifies every
    linked worktree regardless of where the checkout lives or what its folder is
    called — which a name-based denylist cannot.

    2026-07-31: `PROJECT-A-telemetry-wt/` at the workspace root is a PROJECT-A worktree
    (`.git` = 141 bytes, `gitdir: …/Project PROJECT-A …/.git/worktrees/PROJECT-A-telemetry-wt`,
    branch `feature/enterprise-telemetry`, frozen 2026-07-22). The previous exclusion
    tested only for a literal `worktrees` PATH COMPONENT, which this directory does
    not have, so 49 findings that are a stale duplicate of PROJECT-A's own state were
    counted into the fleet total. `drift_baseline.py`'s `STALE_FORKS` name denylist
    was the downstream workaround for exactly this; the general signal replaces the
    need for it.

    Also true of a submodule (git writes the same pointer shape, into
    `.git/modules/…`). No submodule exists in this workspace today — all five `.git`
    files are worktree pointers — and excluding a nested checkout from a FLEET STATE
    sweep is the same call either way: its NEOCORTEX belongs to the superproject.
    Fail-safe: any OSError or a `.git` we cannot read returns False (swept, i.e. the
    old behaviour), because a missed exclusion is noise while a wrong exclusion is a
    silently unswept project."""
    try:
        gitfile = d / ".git"
        if not gitfile.is_file():
            return False        # a directory (normal repo) or absent -> not a worktree
        with gitfile.open("r", encoding="utf-8", errors="replace") as fh:
            head = fh.read(512)
    except OSError:
        return False
    return head.lstrip().startswith("gitdir:")


def discover_neocortexes(root: Path) -> list[Path]:
    """All NEOCORTEX dirs under root, excluding any git linked worktree (detected by
    its `.git` pointer FILE — see _is_linked_worktree) and, belt-and-braces, any path
    carrying a literal `worktrees` component.
    Pure os.walk + pathlib (space-safe, gitignore-blind by construction)."""
    found: list[Path] = []
    for dirpath, dirnames, _ in os.walk(root):
        d = Path(dirpath)
        # A linked worktree is pruned WHOLE, before its NEOCORTEX is even looked at.
        # `d != root` so that an explicit --root INTO a worktree still sweeps it: the
        # exclusion is about not double-counting a checkout the fleet walk stumbles
        # over, not about refusing to look when asked directly.
        if d != root and _is_linked_worktree(d):
            dirnames[:] = []
            continue
        if "NEOCORTEX" in dirnames:
            nc = d / "NEOCORTEX"
            if "worktrees" not in nc.parts:
                found.append(nc)
            dirnames.remove("NEOCORTEX")   # never descend into a NEOCORTEX
        dirnames[:] = [n for n in dirnames
                       if n not in PRUNE_DIR_NAMES and not n.startswith(".")]
    return sorted(found)


# ── check 1: bounds ───────────────────────────────────────────────────────────

def check_bounds(nc: Path) -> list[dict]:
    out = []
    status = nc / "STATUS.md"
    text = _read_text(status)
    if text is not None:
        n = len(text.splitlines())
        if n > STATUS_MAX_LINES:
            out.append(_finding("bounds", "MED", "verified",
                                f"STATUS.md has {n} lines (bound {STATUS_MAX_LINES})"))
    journal = nc / "JOURNAL.md"
    try:
        if journal.is_file():
            size = journal.stat().st_size
            if size >= JOURNAL_MAX_BYTES:
                out.append(_finding(
                    "bounds", "MED", "verified",
                    f"JOURNAL.md is {size:,} bytes ({size // 1024} KB; bound < 64 KB)"))
    except OSError:
        pass
    for plan in sorted(nc.glob("PLAN_*.md")):
        text = _read_text(plan)
        if text is None:
            continue
        marker, mline = _status_marker(text)
        if not _is_active_side(marker):
            continue
        n = len(text.splitlines())
        if n > PLAN_MAX_LINES:
            shown = (marker[:60] + "…") if marker and len(marker) > 60 else (marker or "(no Status header found)")
            out.append(_finding(
                "bounds", "MED", "verified",
                f"{plan.name} has {n} lines (bound {PLAN_MAX_LINES} for active-side plans)",
                f"status marker read from line {mline or '-'}: {shown!r}"))
    return out


# ── check 2: manifest parity ──────────────────────────────────────────────────

def check_manifest(nc: Path) -> list[dict]:
    out = []
    for mandatory in ("MANIFEST.json", "STATUS.md", "JOURNAL.md"):
        if not (nc / mandatory).is_file():
            out.append(_finding("manifest", "MED", "verified",
                                f"mandatory file {mandatory} is missing"))
    mpath = nc / "MANIFEST.json"
    text = _read_text(mpath)
    if text is None:
        return out
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        out.append(_finding("manifest", "MED", "verified",
                            f"MANIFEST.json invalid JSON: {e}"))
        return out
    if not isinstance(data, dict):
        out.append(_finding("manifest", "MED", "verified",
                            "MANIFEST.json top level is not an object"))
        return out
    for f in sorted(REQUIRED_TOP - set(data)):
        out.append(_finding("manifest", "MED", "verified",
                            f"MANIFEST.json missing required field '{f}'"))
    archive = data.get("archive")
    if isinstance(archive, dict):
        for f in sorted(REQUIRED_ARCHIVE - set(archive)):
            out.append(_finding("manifest", "MED", "verified",
                                f"MANIFEST.json archive missing field '{f}'"))
    declared: set[str] = set()
    files_list = data.get("files")
    if isinstance(files_list, list):
        for i, entry in enumerate(files_list):
            if not isinstance(entry, dict):
                out.append(_finding("manifest", "MED", "verified",
                                    f"files[{i}] is not an object: {str(entry)[:80]!r}"))
                continue
            for f in sorted(REQUIRED_FILE_FIELDS - set(entry)):
                out.append(_finding("manifest", "MED", "verified",
                                    f"files[{i}] ({entry.get('file', '?')}) missing field '{f}'"))
            genre = entry.get("genre", "")
            if genre and genre not in VALID_GENRES:
                out.append(_finding(
                    "manifest", "MED", "verified",
                    f"files[{i}] ({entry.get('file', '?')}) invalid genre {genre!r} "
                    f"(must be one of {sorted(VALID_GENRES)})"))
            fname = entry.get("file", "")
            if fname:
                declared.add(fname)
                if not (nc / fname).exists():
                    out.append(_finding("manifest", "MED", "verified",
                                        f"files[] lists {fname!r} but it is absent on disk"))
    elif files_list is not None:
        out.append(_finding("manifest", "MED", "verified", "'files' is not an array"))
    try:
        on_disk = {p.name for p in nc.iterdir()
                   if p.is_file() and p.suffix.lower() == ".md"}
    except OSError:
        on_disk = set()
    for f in sorted((on_disk - EXEMPT_MD) - declared):
        out.append(_finding("manifest", "MED", "verified",
                            f"{f} exists on disk but is not listed in files[]"))
    return out


# ── check 3: status-line truth (heuristic) ────────────────────────────────────

def check_status_truth(nc: Path, project: Path) -> list[dict]:
    out = []
    rc, top = _git(project, "rev-parse", "--show-toplevel")
    if rc != 0:
        return out       # not under git — nothing to compare against (documented)
    toplevel = Path(top.strip())
    try:
        rel = project.resolve().relative_to(toplevel.resolve())
        subtree = str(rel) if str(rel) != "." else "."
    except (ValueError, OSError):
        subtree = "."
    docs = []
    try:
        docs = [p for p in sorted(nc.iterdir())
                if p.is_file() and p.suffix.lower() == ".md"]
    except OSError:
        pass
    for doc in docs:
        text = _read_text(doc)
        if text is None:
            continue
        marker, _ = _status_marker(text)
        triggers = []
        if marker and re.search(r"(?i)\b(ACTIVE|DRAFT)\b", marker) \
                and not CLOSED_STATUS_RE.search(marker):
            triggers.append(f"status marker {marker[:40]!r}")
        low = text.lower()
        for ph in STALE_PHRASES:
            if ph in low:
                triggers.append(f"body phrase {ph!r}")
        if not triggers:
            continue
        date, source = _doc_date(doc)
        rc, log = _git(project, "log", "--oneline",
                       f"--since={date} 23:59:59", "--", subtree)
        if rc != 0:
            continue
        commits = [l for l in log.splitlines() if l.strip()]
        if not commits:
            continue
        rc2, last = _git(project, "log", "-1", "--format=%cs", "--", subtree)
        last_date = last.strip() if rc2 == 0 else "?"
        out.append(_finding(
            "status_truth", "LOW", "heuristic",
            f"{doc.name}: claims [{'; '.join(triggers)}] dated {date} ({source}), "
            f"but the project subtree has {len(commits)} commit(s) after that date "
            f"(latest {last_date}) — is the claim still true? "
            f"PROMPT FOR HUMAN ADJUDICATION, not an assertion.",
            f"first commits after: {'; '.join(c[:60] for c in commits[:3])}"))
    return out


# ── check 4: tracking ─────────────────────────────────────────────────────────

def check_tracking(nc: Path, project: Path) -> list[dict]:
    out = []
    # Anchor git at the repo that actually governs the NEOCORTEX: some projects
    # keep a LOCAL-ONLY repo rooted at the NEOCORTEX itself (PROJECT-B, per founder
    # decision 2026-07-17) — asking the PROJECT's repo misses it entirely
    # (first fleet run 2026-07-27 missed the known defect-13 case that way).
    rc, top = _git(nc, "rev-parse", "--show-toplevel")
    if rc != 0:
        return out
    try:
        nc_rooted = Path(top.strip()).resolve() == nc.resolve()
    except OSError:
        nc_rooted = False
    base = nc if nc_rooted else project
    pathspec = "." if nc_rooted else "NEOCORTEX"
    rc, tracked_out = _git(base, "ls-files", "--", pathspec)
    if rc != 0:
        return out
    tracked = [l for l in tracked_out.splitlines() if l.strip()]
    if not tracked:
        return out       # fully untracked NEOCORTEX = the standard regime; consistent
    all_files = []
    for p in nc.rglob("*"):
        if not p.is_file():
            continue
        if p.name in TRACKING_NOISE or p.suffix in TRACKING_NOISE_SUFFIX \
                or "__pycache__" in p.parts or ".git" in p.parts:
            continue
        try:
            all_files.append(str(p.relative_to(base)))
        except ValueError:
            continue
    if not all_files:
        return out
    try:
        r = subprocess.run(["git", "-C", str(base), "check-ignore", "--stdin"],
                           input="\n".join(all_files), capture_output=True,
                           text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return out
    ignored = [l for l in r.stdout.splitlines() if l.strip()]
    if ignored:
        sample = "; ".join(ignored[:6]) + ("; …" if len(ignored) > 6 else "")
        out.append(_finding(
            "tracking", "MED", "verified",
            f"NEOCORTEX has {len(tracked)} tracked file(s) but {len(ignored)} "
            f"file(s) inside it are gitignored: {sample} — the ignore rule and "
            f"the tracking are inconsistent; which side is wrong is a policy "
            f"decision this sweep does not make."))
    return out


# ── check 5: dangling references ──────────────────────────────────────────────

def _extract_path_tokens(text: str) -> list[tuple[str, int]]:
    """(token, line_no) candidates after the documented drop-filter."""
    tokens: list[tuple[str, int]] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        stripped_line = URL_RE.sub(" ", line)                # drop URLs
        for m in BACKTICK_RE.finditer(stripped_line):
            tok = m.group(1).strip()
            if "/" in tok or tok.endswith(KNOWN_SUFFIXES):
                tokens.append((tok, lineno))
        no_ticks = re.sub(r"`[^`\n]*`", " ", stripped_line)  # avoid double-count
        for m in BARE_PATH_RE.finditer(no_ticks):
            tokens.append((m.group(1), lineno))
    cleaned = []
    for tok, lineno in tokens:
        tok = tok.strip().strip("()").rstrip(".,:;)»").rstrip("/")
        while tok.startswith(("…/", ".../")):
            tok = tok.split("/", 1)[1]
        if not tok or len(tok) < 3:
            continue
        if any(ch.isspace() for ch in tok):
            continue          # shell command lines / prose (documented cost:
                              # also drops real space-containing macOS paths)
        if ":" in tok or "…" in tok or "..." in tok:
            continue          # pytest node-ids, 'site:' operators, ellipses
        if any(c in DROP_CHARS for c in tok):
            continue          # globs/placeholders/shell
        if tok.startswith("-") or "://" in tok:
            continue
        if tok.startswith("/") and not tok.startswith(ABS_ALLOWED_PREFIXES):
            continue          # HTTP routes, /tmp //var ephemera, system paths
        if re.search(r"<Y{2,4}|YYYY[-_]MM|<date|<slug|<n>|<file", tok, re.IGNORECASE):
            continue          # template placeholders
        if "/" not in tok and not tok.startswith("~"):
            continue          # bare basename shorthand — dropped (documented)
        cleaned.append((tok, lineno))
    seen = set()
    uniq = []
    for tok, lineno in cleaned:
        if tok not in seen:
            seen.add(tok)
            uniq.append((tok, lineno))
    return uniq


def check_dangling(nc: Path, project: Path, workspace: Path) -> list[dict]:
    out = []
    sources = []
    status = nc / "STATUS.md"
    if status.is_file():
        sources.append(status)
    for plan in sorted(nc.glob("PLAN_*.md")):
        text = _read_text(plan)
        if text is None:
            continue
        marker, _ = _status_marker(text)
        if _is_active_side(marker):
            sources.append(plan)
    for doc in sources:
        text = _read_text(doc)
        if text is None:
            continue
        missing = []
        for tok, lineno in _extract_path_tokens(text):
            p = Path(os.path.expanduser(tok))
            if p.is_absolute():
                if not p.exists():
                    missing.append((tok, lineno))
                continue
            bases = (project, nc, nc / "_archive", workspace)
            if any((b / tok).exists() for b in bases):
                continue
            if not tok.endswith(KNOWN_SUFFIXES):
                # extension-less: report only when the FIRST segment is a real
                # directory at some base (a real tree, a missing leaf); a junk
                # fragment whose first segment exists nowhere is dropped
                first = tok.split("/", 1)[0]
                if not any((b / first).exists() for b in bases):
                    continue
            missing.append((tok, lineno))
        for tok, lineno in missing[:15]:
            out.append(_finding(
                "dangling_refs", "LOW", "heuristic",
                f"{doc.name}:{lineno} cites {tok!r} — not found at project root, "
                f"NEOCORTEX/, NEOCORTEX/_archive/, or workspace root",
                "extraction is heuristic; the non-existence itself was verified "
                "at all four bases"))
        if len(missing) > 15:
            out.append(_finding(
                "dangling_refs", "LOW", "heuristic",
                f"{doc.name}: {len(missing) - 15} further unresolved citation(s) "
                f"suppressed (first 15 shown)"))
    return out


# ── check 6: dead instruments (fleet mode only) ───────────────────────────────

def _instrument_scripts(root: Path) -> list[Path]:
    scripts: list[Path] = []
    gov = root / "governance"
    if gov.is_dir():
        for p in sorted(gov.rglob("*")):
            if p.suffix in (".py", ".sh") and p.is_file():
                if ".publish_staging" in p.parts or "__pycache__" in p.parts:
                    continue
                scripts.append(p)
    hooks = root / ".claude" / "hooks"
    if hooks.is_dir():
        for p in sorted(hooks.iterdir()):
            if p.suffix in (".py", ".sh") and p.is_file() \
                    and not p.name.startswith("test_"):
                scripts.append(p)
    return [p for p in scripts if not p.name.startswith("test_")]


def _bounded_referencers(root: Path, basename: str, self_path: Path) -> list[str]:
    """Files under governance/, .claude/, _skills/ referencing basename.
    Python-walked (gitignore-blind by construction), bounded scope."""
    hits = []
    for scope in (root / "governance", root / ".claude", root / "_skills"):
        if not scope.is_dir():
            continue
        for p in scope.rglob("*"):
            if not p.is_file() or p == self_path:
                continue
            if p.suffix not in (".py", ".sh", ".json", ".md", ".yaml", ".yml"):
                continue
            if "__pycache__" in p.parts or ".publish_staging" in p.parts:
                continue
            text = _read_text(p)
            if text and basename in text:
                try:
                    hits.append(str(p.relative_to(root)))
                except ValueError:
                    hits.append(str(p))
    return hits


def check_instruments(root: Path) -> tuple[list[dict], list[dict]]:
    """Returns (findings, claim_records). Every claim -> CONFIRMED or UNSUPPORTED."""
    findings: list[dict] = []
    records: list[dict] = []

    settings_text = ""
    for sname in ("settings.json", "settings.local.json"):
        t = _read_text(root / ".claude" / sname)
        if t:
            settings_text += t

    try:
        r = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=15)
        crontab_text = r.stdout if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        crontab_text = ""
    try:
        r = subprocess.run(["launchctl", "list"], capture_output=True, text=True, timeout=15)
        launchctl_text = r.stdout if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        launchctl_text = ""
    agents_text = ""
    agents_dir = Path.home() / "Library" / "LaunchAgents"
    if agents_dir.is_dir():
        for pl in agents_dir.glob("*.plist"):
            t = _read_text(pl)
            if t:
                agents_text += t

    hooks_dir = root / ".claude" / "hooks"
    today = _dt.date.today()

    for script in _instrument_scripts(root):
        if script.name == Path(__file__).name and script.resolve() == Path(__file__).resolve():
            pass  # the sweep inspects itself like any other instrument
        text = _read_text(script)
        if text is None:
            continue
        head = "\n".join(text.splitlines()[:80])
        rel = str(script.relative_to(root))

        # (a) hook files must be wired in settings
        if script.parent == hooks_dir:
            wired = script.name in settings_text
            records.append({"script": rel, "claim": "resides in .claude/hooks/ (implicit hook)",
                            "verdict": "CONFIRMED" if wired else "UNSUPPORTED",
                            "evidence": ".claude/settings*.json references it"
                            if wired else "no settings*.json reference"})
            if not wired:
                findings.append(_finding(
                    "instruments", "MED", "verified",
                    f"{rel}: lives in .claude/hooks/ but no settings*.json "
                    f"entry invokes it — a hook nothing fires"))

        # (b) schedule claims
        claim_lines = [l for l in head.splitlines()
                       if SCHEDULE_CLAIM_RE.search(l)
                       and not SCHEDULE_NEGATION_RE.search(l)]
        if claim_lines:
            labels = set()
            for l in claim_lines:
                labels.update(LABEL_RE.findall(l))
            hit_where = []
            for hay, name in ((crontab_text, "crontab"),
                              (launchctl_text, "launchctl"),
                              (agents_text, "LaunchAgents plists")):
                if script.name in hay or any(lb in hay for lb in labels):
                    hit_where.append(name)
            verdict = "CONFIRMED" if hit_where else "UNSUPPORTED"
            records.append({"script": rel,
                            "claim": f"schedule: {claim_lines[0].strip()[:90]}",
                            "verdict": verdict,
                            "evidence": ", ".join(hit_where) or
                            "absent from crontab, launchctl list, and LaunchAgents plists"})
            if verdict == "UNSUPPORTED":
                findings.append(_finding(
                    "instruments", "MED", "verified",
                    f"{rel}: claims a schedule ({claim_lines[0].strip()[:70]!r}) "
                    f"but no crontab entry, launchd job, or LaunchAgent references "
                    f"it (labels checked: {sorted(labels) or 'none stated'})"))

        # (c) dated output artifacts
        for m in DATE_ARTIFACT_RE.finditer(head):
            pattern = m.group(1)
            if pattern.startswith("<"):        # bare placeholder, no path around it
                continue
            period = today.strftime("%Y-%m-%d") if "<YYYY-MM-DD>" in pattern \
                else today.strftime("%Y-%m")
            expected_rel = pattern.replace("<YYYY-MM-DD>", period).replace("<YYYY-MM>", period)
            expected = root / expected_rel
            glob_pat = Path(pattern.replace("<YYYY-MM-DD>", "*").replace("<YYYY-MM>", "*"))
            newest = None
            try:
                candidates = sorted((root / glob_pat.parent).glob(glob_pat.name))
                newest = candidates[-1].name if candidates else None
            except OSError:
                pass
            if expected.exists():
                records.append({"script": rel, "claim": f"artifact {pattern}",
                                "verdict": "CONFIRMED",
                                "evidence": f"{expected_rel} exists"})
            else:
                records.append({"script": rel, "claim": f"artifact {pattern}",
                                "verdict": "UNSUPPORTED",
                                "evidence": f"{expected_rel} missing; newest actual: {newest}"})
                findings.append(_finding(
                    "instruments", "MED", "verified",
                    f"{rel}: documents output {pattern!r} but the current-period "
                    f"artifact {expected_rel} does not exist (newest actual: {newest}) "
                    f"— the instrument has not run this period"))

        # (d) generic invoked-by claims (skip hook files — covered by (a))
        if script.parent != hooks_dir and INVOKED_CLAIM_RE.search(head):
            refs = _bounded_referencers(root, script.name, script)
            verdict = "CONFIRMED" if refs else "UNSUPPORTED"
            records.append({"script": rel, "claim": "claims to be invoked/called by something",
                            "verdict": verdict,
                            "evidence": "; ".join(refs[:4]) or
                            "no referencer under governance/, .claude/, _skills/"})
            if verdict == "UNSUPPORTED":
                findings.append(_finding(
                    "instruments", "MED", "verified",
                    f"{rel}: claims to be invoked by something, but nothing under "
                    f"governance/, .claude/ or _skills/ references its name "
                    f"(bounded search — a wiring elsewhere would be missed)"))

        # (e) a hook wired in settings.json must be EXECUTABLE.
        # Claude Code runs a "command" hook by path; without the +x bit the shell returns
        # 126 and the hook never runs. Hooks fail open, so nothing whatsoever surfaces —
        # no error, no log line, no degraded behaviour a human would notice.
        # 2026-07-27: verify_advisory.py was committed mode 100644 by b980bdb on
        # 2026-06-24 and had not executed once in the 33 days since. Its telemetry log
        # looked exactly like a broken writer, and an investigation concluded the write
        # path was at fault; the writer was fine and still is. `ls -l` settled it.
        if script.parent == hooks_dir and script.name in settings_text:
            try:
                mode = oct(script.stat().st_mode)[-3:]
            except OSError:
                mode = "???"
            executable = os.access(script, os.X_OK)
            records.append({"script": rel, "claim": "wired as a hook in settings.json",
                            "verdict": "CONFIRMED" if executable else "UNSUPPORTED",
                            "evidence": f"mode {mode}, "
                                        f"{'executable' if executable else 'NOT EXECUTABLE'}"})
            if not executable:
                findings.append(_finding(
                    "instruments", "HIGH", "verified",
                    f"{rel}: wired as a hook in .claude/settings.json but is NOT "
                    f"executable (mode {mode}) — the shell returns 126 and the hook has "
                    f"never run. Hooks fail open, so this is completely silent. "
                    f"Fix: chmod +x, and `git update-index --chmod=+x` so the mode "
                    f"survives a fresh clone"))
    return findings, records


# ── check 7: remedy-not-observable invariants ─────────────────────────────────

def check_invariants(nc: Path) -> list[dict]:
    out = []
    text = _read_text(nc / "STATUS.md")
    if text is None:
        return out
    lines = text.splitlines()
    in_section = False
    entries: list[str] = []
    for line in lines:
        if re.match(r"^##\s+Invariants", line, re.IGNORECASE):
            in_section = True
            continue
        if in_section and re.match(r"^##\s+", line):
            break
        if in_section:
            if re.match(r"^\s*\d+\.\s", line):
                entries.append(line.strip())
            elif entries and line.strip() and not line.startswith("#"):
                entries[-1] += " " + line.strip()
    remedy_only = []
    for e in entries:
        if REMEDY_RE.search(e) and not TESTABLE_RE.search(e):
            remedy_only.append(e)
    if remedy_only:
        sample = " | ".join(e[:70] for e in remedy_only[:3])
        out.append(_finding(
            "invariants", "LOW", "heuristic",
            f"STATUS.md Invariants: {len(remedy_only)} of {len(entries)} entr(y/ies) "
            f"are remedy-phrased with no checkable predicate — candidates for a "
            f"testable rewrite (advisory; not rewritten here). e.g. {sample}"))
    return out


# ── orchestration ─────────────────────────────────────────────────────────────

NOT_MECHANIZED = [
    {"item": "AUDIT P5 item 4 — stale numbers re-queried against the DB",
     "reason": "figures live in project-specific prose and the authoritative "
               "queries live in project-specific schemas; a generic fleet script "
               "has no schema knowledge to re-derive them — belongs in each "
               "project's own close-out harness"},
    {"item": "REFLECTION §6.4 — checks written in one language on a multilingual product",
     "reason": "a product-copy locale-parity scan, meaningful only inside the one "
               "multilingual project (PROJECT-A) and its test suite, not against "
               "NEOCORTEX state files"},
]


def sweep_project(nc: Path, workspace: Path) -> dict:
    project = nc.parent
    findings: list[dict] = []
    findings += check_bounds(nc)
    findings += check_manifest(nc)
    findings += check_status_truth(nc, project)
    findings += check_tracking(nc, project)
    findings += check_dangling(nc, project, workspace)
    findings += check_invariants(nc)
    return {"project": str(project), "neocortex": str(nc), "findings": findings}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Mechanical continuity + staleness sweep over NEOCORTEXes "
                    "(AUDIT_gate_effectiveness_2026-07-27 §5 P5).")
    parser.add_argument("--root", help="workspace root (default: this script's "
                                       "grandparent directory)")
    parser.add_argument("--project", help="sweep a single project directory "
                                          "(its NEOCORTEX/); skips the "
                                          "workspace-instruments check")
    parser.add_argument("--json", action="store_true", dest="as_json",
                        help="machine-readable output")
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        return 2 if e.code not in (0,) else 0

    root = Path(args.root).expanduser().resolve() if args.root \
        else Path(__file__).resolve().parents[2]
    if not root.is_dir():
        print(f"ERROR: root is not a directory: {root}", file=sys.stderr)
        return 2

    instruments_run = False
    instrument_findings: list[dict] = []
    instrument_records: list[dict] = []

    if args.project:
        proj = Path(args.project).expanduser().resolve()
        nc = proj / "NEOCORTEX"
        if not nc.is_dir():
            print(f"ERROR: no NEOCORTEX/ under {proj}", file=sys.stderr)
            return 2
        targets = [nc]
    else:
        targets = discover_neocortexes(root)
        instruments_run = True
        instrument_findings, instrument_records = check_instruments(root)

    results = [sweep_project(nc, root) for nc in targets]
    total = sum(len(r["findings"]) for r in results) + len(instrument_findings)

    if args.as_json:
        print(json.dumps({
            "root": str(root),
            "swept_neocortexes": len(targets),
            "total_findings": total,
            "projects": results,
            "instruments": {"run": instruments_run,
                            "findings": instrument_findings,
                            "claims": instrument_records},
            "not_mechanized": NOT_MECHANIZED,
        }, indent=2, ensure_ascii=False))
        return 1 if total else 0

    print(f"CONTINUITY SWEEP — root: {root}")
    print(f"NEOCORTEXes swept: {len(targets)} (git linked worktrees excluded — "
          f"detected by their `.git` pointer file, not by directory name)")
    print()
    for r in results:
        rel = os.path.relpath(r["project"], root)
        if not r["findings"]:
            print(f"  CLEAN  {rel}")
            continue
        print(f"  {len(r['findings']):3d}    {rel}")
        for f in r["findings"]:
            print(f"         [{f['check']}] {f['severity']}/{f['confidence']} — {f['message']}")
            if f["detail"]:
                print(f"                 ↳ {f['detail']}")
    if instruments_run:
        print()
        print("  WORKSPACE INSTRUMENTS (governance/ + .claude/hooks/):")
        for rec in instrument_records:
            print(f"         {rec['verdict']:<11} {rec['script']} — {rec['claim']}")
            print(f"                     evidence: {rec['evidence']}")
        for f in instrument_findings:
            print(f"         [{f['check']}] {f['severity']}/{f['confidence']} — {f['message']}")
    print()
    print("  NOT MECHANIZED (deliberately, with reasons):")
    for nm in NOT_MECHANIZED:
        print(f"         - {nm['item']}: {nm['reason']}")
    print()
    print(f"RESULT: {total} finding(s) → exit {1 if total else 0}")
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())

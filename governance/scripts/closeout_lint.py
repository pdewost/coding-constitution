#!/usr/bin/env python3.12
"""closeout_lint.py — the staleness linter (M-9).

WHAT THIS IS FOR
----------------
Every assertion below is stated as an OBSERVABLE, not a remedy, and every one is traced to a
real 2026 incident in this workspace. That is deliberate: the workspace's own documented-
invariant discipline says a rule naming a REMEDY ("remember to update STATUS") is folklore the
moment its author leaves, while a rule naming an OBSERVABLE ("no file may claim X while git
says Y") is something a script can test.

THE INCIDENTS, because a check whose provenance is lost gets deleted by the next reader:

  #1  A standing surface asserted state that git contradicts.
      2026-08-25, three times in one day: STATUS said the evidence store was "uncommitted"
      hours after it was committed; a commit message said "M-3 DONE" while STATUS still listed
      M-3 as pending; the instrument table called a tool "DANGEROUS AS SHIPPED" two commits
      after it was gated. All three were caught by a human reading, none by a mechanism.

  #2  A stated series count did not match the files on disk.
      "seven rounds" while eight AUDIT files existed. The eighth had run, been persisted, and
      was never absorbed -- roughly a third of the next review's spend went on re-finding what
      it had already reported.

  #3  A persisted review verdict carried no disposition record.
      _rev8 sat MANIFEST-listed with no drafter disposition, alone in a series of nine.

  #4  A ruling partitioned a closed set and lost a member.
      A Constitutional Review's ruled and deferred lists together accounted for 13 of 14
      articles. The missing one governed the amendment process itself.

  #5  A docstring claimed a schedule that a LaunchAgent contradicts.
      monthly_audit.py says "Schedule: none" while com.PROJECT-B.monthly-audit.plist runs it
      monthly. Found twice by independent reviewers before anyone acted.

  #6  MANIFEST status tokens drifted outside their enum. 38 on 2026-08-25 morning; 68 the
      same evening, mostly from artifacts created that day.

  #7  A class-to-model binding table with no expiry.
      A budget posture bound task classes to named models and stayed correct only because its
      author volunteered an expiry date. Nothing required one.

  #8  A published file diverged from its source with the divergence undeclared.
      6 of 8 allowlisted files had silently drifted; the record described the constitution's
      own divergence as "scrub-only" when two of four hunks were substantive rewrites.

  #9  A PLAN item was tagged DONE while the code it names was never committed.
      2026-08-26: M-5 carried a DONE tag, named real files and a real passing gate count --
      satisfying every word of the PLAN's own shipping rule -- while the producer sat
      uncommitted in the governance repo and the consumer in a SECOND repo, one `git stash`
      from gone. All three panel reviewers found it; assertion #1 ran in the mirror-image
      direction (it fires on a false claim of BEING uncommitted) and could not. The gap was
      never "is it built" -- it was that nothing could tell BUILT from SHIPPED.

  #10 A standing next-action outlived the work that satisfied it.
      2026-08-26: STATUS's single-next-action block asked for M-6/M-10/M-11 to be specified.
      They WERE specified, that same evening, by commit 4e828a7 -- whose own message reads
      "specify M-6/M-10/M-11, and close the session" -- and that commit did not touch
      STATUS.md. The block stood for a day and the next cold start read it as ground truth.
      Measured over the 25 most recent commits touching this JOURNAL: 7 did not touch STATUS
      (28%). The next-action is STRUCTURALLY the most perishable line in a NEOCORTEX, because
      it names precisely what the session is working to invalidate; everything else decays
      passively. The failure is not forgetfulness, it is ORDERING -- STATUS gets rebased
      mid-session and is then outlived by the work that follows it. Recorded three more times
      inside the single turn that diagnosed it.

EXIT: 0 = clean. 1 = findings. 2 = usage error.
Findings are ADVISORY by default and BLOCKING with --strict, so wiring it into a closeout gate
is a separate, deliberate decision rather than a side effect of it existing.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import plistlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
NEOCORTEX_GLOB = "**/NEOCORTEX/*.md"

# Claims of uncommitted-ness that git can contradict (#1).
UNCOMMITTED_CLAIM = re.compile(
    r"(is|are|remains?|stays?)\s+\*{0,2}uncommitted\*{0,2}"
    r"|exists?\s+nowhere\s+else"
    r"|not\s+(yet\s+)?committed",
    re.I,
)
SERIES_CLAIM = re.compile(r"\b(?:(\w+)|(\d+))\s+rounds?\b", re.I)
# A blocked-work register asserting work is still outstanding (#4). `class: gate-blocked` is
# CRUMBLES' own vocabulary; OPEN_WORK.md is a prose table and says it in words.
OPEN_CLAIM = re.compile(
    r"gate-blocked"
    r"|still\s+owed"
    r"|\bnot\s+closed\b"
    r"|\bstill\s+(?:open|blocked|outstanding)\b",
    re.I,
)
# "§3 gate not yet run", "gate STILL NOT RUN", "gate has not been run" — one claim, many words.
GATE_UNRUN_CLAIM = re.compile(r"gate\s+(?:\w+\s+){0,3}?not\s+(?:\w+\s+){0,2}?run\b", re.I)
SHIPPED_CONTEXT = re.compile(r"\bshipped\b|\bDONE\b|✅|~~", re.I)
GATE_RUN_STATUS = re.compile(r"GATE\s+RUN\b|gate\s+(?:PASSED|CLEARED)\b", re.I)
# Ways a docstring denies having a schedule (#5). Deliberately excludes the bare word
# "unscheduled": the corrected `monthly_audit.py` header and this module both use it while
# DESCRIBING the rule, and a check that fires on its own documentation is uninhabitable.
UNSCHEDULED_CLAIM = re.compile(
    r"schedule\s*:\s*(?:none|no\b|tbd|manual|not\b)"
    r"|\b(?:is|are|it'?s)\s+not\s+(?:currently\s+|presently\s+|yet\s+)?scheduled\b"
    r"|\bnot\s+currently\s+scheduled\b"
    r"|\bno\s+(?:launchagent|launchd\s+job|cron(?:tab|\s+entry)?|plist)\b"
    r"|\bwhether\b[^.\n]{0,60}?\bschedule\b[^.\n]{0,60}?\b(?:open|undecided|not\s+(?:yet\s+)?decided|tbd)\b"
    r"|\bscheduling\b[^.\n]{0,60}?\b(?:is\s+an?\s+open|undecided|not\s+(?:yet\s+)?decided)\b",
    re.I,
)
WORD_NUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
            "seven": 7, "eight": 8, "nine": 9, "ten": 10}
# The enum is derived from ACTUAL fleet usage, not invented. A first cut listing only ten
# tokens produced 162 findings, ~150 of which were legitimate lifecycle states the enum simply
# omitted (DONE alone accounts for 72). A linter that cries wolf 150 times is switched off on
# its first run, so the enum was widened to what projects genuinely use — and the residue is
# then meaningful: GENRE words leaking into the status field (AUDIT, PLAN, REFLECTION,
# PROPOSAL are genres, not states) and truncated fragments (PARTIALLY).
STATUS_ENUM = {
    # lifecycle
    "ACTIVE", "DRAFT", "PROPOSED", "VALIDATED", "ADOPTED", "BUILT", "SHIPPED",
    "DONE", "COMPLETE", "COMPLETED", "RESOLVED", "ABSORBED", "CLOSED",
    # stopped / parked
    "DEFERRED", "BLOCKED", "PAUSED", "STOPPED", "ABANDONED", "SUPERSEDED", "ARCHIVED",
    # verdicts a review artifact legitimately carries
    "REFUTED", "HOLDS", "PENDING",
}


class Finding:
    def __init__(self, check: str, path: str, line: int, detail: str):
        self.check, self.path, self.line, self.detail = check, path, line, detail

    def __str__(self) -> str:
        where = f"{self.path}:{self.line}" if self.line else self.path
        return f"  [{self.check}] {where}\n      {self.detail}"


def _git(*args: str) -> str:
    try:
        r = subprocess.run(["git", "-C", str(ROOT), *args],
                           capture_output=True, text=True, timeout=60)
        return r.stdout if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def _rel(p: pathlib.Path) -> str:
    """Display path, relative to the workspace when it is inside it.

    `p.relative_to(ROOT)` RAISES for anything outside the workspace, which made every check
    crash under `--path /elsewhere` — the tool's own scoping flag was broken for any external
    scope, and it took writing the tests to find out. Falls back to the absolute path.
    """
    try:
        return str(p.relative_to(ROOT))
    except ValueError:
        return str(p)


# Directories a governance linter has no business descending into, and one it must not.
# `_interaction_log/` is the conversation archive — 3,435 transcripts / 2.2 GB, declared
# QUERY-NEVER-INGEST by the root CLAUDE.md (Art. 6). Walking it was pure cost: it holds no
# NEOCORTEX file and no scheduled script, and skipping it is also the posture the archive rule
# already asks for. The rest are caches and vendored trees.
PRUNE_DIRS = {
    ".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    "node_modules", ".venv", "venv", "site-packages", ".next", ".cache",
    "_interaction_log",
    # Build output: 148k files in one project alone, and no governance artifact lives there.
    "dist", "build", ".build", "DerivedData", "Pods",
}
# One pruned walk per scope, reused by every check. Before this, every check globbed the whole
# workspace independently: EVERY check paid a ~7.3 s floor, including `binding-no-expiry`, which
# runs no subprocess at all — the cost was the walk, not the work. Whole-run wall clock was 68 s,
# which is disqualifying for any hook or close-out caller, i.e. the linter could not be CONNECTED
# no matter what policy it was given. Cache is per-process; the tool is single-shot by design.
_WALK: dict[str, list[pathlib.Path]] = {}


def _files(scope: pathlib.Path) -> list[pathlib.Path]:
    """The files any check can match: direct children of a `NEOCORTEX/` dir, plus `OPEN_WORK.md`.

    Collecting EVERY file was 465,553 Paths for the ~3,000 a check can act on. The two rules
    below are not heuristics — they are the glob semantics the checks already had:
    `**/NEOCORTEX/*.md` never matched below `NEOCORTEX/`, so nothing is newly excluded, and
    `PROJECT-B/NEOCORTEX` alone holds 13,758 files under subdirectories that could never match.
    """
    key = str(scope)
    hit = _WALK.get(key)
    if hit is not None:
        return hit
    # os.scandir, NOT Path.iterdir: a DirEntry carries the type from the directory read, so
    # this costs one syscall per entry instead of three stats. On this workspace that is the
    # difference between an 18 s walk and a sub-second one — and 18 s is still unwireable.
    out: list[pathlib.Path] = []
    stack = [(str(scope), False)]
    while stack:
        d, in_nc = stack.pop()
        try:
            with os.scandir(d) as it:
                for e in it:
                    try:
                        if e.is_symlink():
                            continue        # never follow: a loop would hang the linter
                        if e.is_dir(follow_symlinks=False):
                            # Never descend BELOW a NEOCORTEX dir: only direct children can
                            # match, which is what the original globs already meant.
                            if e.name not in PRUNE_DIRS and not in_nc:
                                stack.append((e.path, e.name == "NEOCORTEX"))
                        elif e.is_file(follow_symlinks=False):
                            if in_nc or e.name == "OPEN_WORK.md":
                                out.append(pathlib.Path(e.path))
                    except OSError:
                        continue
        except OSError:
            continue                        # unreadable dir must not abort the sweep
    out.sort()
    _WALK[key] = out
    return out


def _family(name: str) -> str:
    """A series' identity with its revision marker and trailing date removed.

    `AUDIT_adversarial_drift_control_plan_rev2_2026-07-29.md` and
    `AUDIT_adversarial_drift_control_plan_2026-07-29.md` are the same series written two ways;
    so are `..._2026-08-24.md` and `..._2026-08-24_rev8.md`. Comparing families instead of
    literal filenames is what lets one rule cover both conventions.
    """
    n = re.sub(r"\.md$", "", name)
    n = re.sub(r"_(?:rev|v)\d+", "", n)
    return re.sub(r"_\d{4}-\d{2}-\d{2}$", "", n)


VERSION_MARK = re.compile(r"_(?:rev|v)(\d+)")


def _superseded_by_sibling(f: pathlib.Path, siblings: list[str]) -> bool:
    """A PLAN revision that a higher-numbered sibling replaces is a DATED RECORD, not standing.

    Same doctrine the check already applies to AUDITs, extended to the case that produced a
    false positive on the real tree: `PLAN_phase3_scaling_ch2_11_v3` says "Trajectory across
    three rounds" and is marked `REFUTED … DO NOT EXECUTE` with a `_v4` sitting beside it.
    Three rounds WAS the count when v3 was written. Policing a superseded revision pressures
    a reader to rewrite history to satisfy a linter — the exact objection that rescoped this
    check the first time.
    """
    mine = VERSION_MARK.search(f.name)
    if not mine:
        return False
    fam = _family(f.name)
    for n in siblings:
        if n == f.name or _family(n) != fam:
            continue
        theirs = VERSION_MARK.search(n)
        if theirs and int(theirs.group(1)) > int(mine.group(1)):
            return True
    return False


_ARCHIVED: dict[str, frozenset[str]] = {}


def _archived_names(nc_dir: pathlib.Path) -> frozenset[str]:
    """Filenames under `<NEOCORTEX>/_archive/`, read on demand and cached.

    The shared walk deliberately stops at a NEOCORTEX dir, so archived files are invisible to
    it — correct for every other check (an archived record is frozen and out of scope) and
    wrong for exactly one question: "does the revision this surface names still exist?" A
    revision that was archived DOES exist; saying otherwise is the check inventing a defect.
    """
    key = str(nc_dir)
    hit = _ARCHIVED.get(key)
    if hit is None:
        names: set[str] = set()
        stack = [str(nc_dir / "_archive")]
        while stack:                        # RECURSIVE: `_archive/` has subdirectories by
            try:                            # design — dated review-record dirs, and the
                with os.scandir(stack.pop()) as it:   # `2026-08-25-oversized/` folder holding
                    for e in it:            # records relocated intact for exceeding the 64 KB
                        try:                # NEOCORTEX-root data rule. A flat scan missed them.
                            if e.is_symlink():
                                continue
                            if e.is_dir(follow_symlinks=False):
                                stack.append(e.path)
                            elif e.is_file(follow_symlinks=False):
                                names.add(e.name)
                        except OSError:
                            continue
            except OSError:
                continue
        hit = frozenset(names)
        _ARCHIVED[key] = hit
    return hit


def _in_neocortex(p: pathlib.Path) -> bool:
    return p.parent.name == "NEOCORTEX"


def _neocortex_files(scope: pathlib.Path) -> list[pathlib.Path]:
    return [p for p in _files(scope) if _in_neocortex(p) and p.suffix == ".md"]


def _neocortex_files_excluding_reviews(scope: pathlib.Path) -> list[pathlib.Path]:
    """`_neocortex_files`, minus review records (`AUDIT_adversarial_*.md`).

    THE GREP-CORPUS EXPOSURE (verified 2026-08-27, built 2026-08-28). A check that scans free
    TEXT for a claim-phrase cannot tell a live instance of the phrase from a PLAN or AUDIT
    QUOTING that same old wording while explaining a correction — already hit for real once, on
    a scheduled script's own corrected docstring. Fix shape recorded verbatim at the time:
    exclude review records ONLY.

    NEVER WIDEN THIS to PLAN, STATUS, JOURNAL, MANIFEST or a handoff NOTE. Those are exactly the
    live-state surfaces these checks exist to police; blinding them to fix a corpus problem
    would cure the disease by killing the patient. `check_audit_dispositions` deliberately does
    NOT call this function — it needs to see the review records it is checking.
    """
    return [p for p in _neocortex_files(scope) if not p.name.startswith("AUDIT_adversarial_")]


def _registers(scope: pathlib.Path) -> list[pathlib.Path]:
    """Every register of blocked-or-owed work, NOT just CRUMBLES.

    The first cut globbed `**/NEOCORTEX/CRUMBLES.*` only. `OPEN_WORK.md` lives one directory
    UP from NEOCORTEX, so the linter built to catch a stale blocked-work register could not
    see the fleet's largest one — which listed four shipped mechanisms and an already-run gate
    as "Still owed and NOT closed" while this check reported clean. Found by a reviewer.
    """
    found = [p for p in _files(scope)
             if (_in_neocortex(p) and p.stem == "CRUMBLES") or p.name == "OPEN_WORK.md"]
    return [p for p in found if "_archive" not in p.parts]


# ── #1 ────────────────────────────────────────────────────────────────────────────────────
def check_uncommitted_claims(scope: pathlib.Path) -> list[Finding]:
    """No file may claim something is uncommitted while git reports it clean."""
    out: list[Finding] = []
    dirty = {ln[3:].strip() for ln in _git("status", "--porcelain").splitlines() if ln[3:].strip()}
    for f in _neocortex_files_excluding_reviews(scope):
        for i, line in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if not UNCOMMITTED_CLAIM.search(line):
                continue
            # Only fire when the line names a path that git says is CLEAN.
            for m in re.finditer(r"`([\w./\-]+\.(?:json|py|md|sh|yaml))`", line):
                cand = m.group(1)
                if not any(cand in d for d in dirty):
                    tracked = _git("ls-files", "--error-unmatch", cand)
                    if tracked.strip():
                        out.append(Finding("uncommitted-claim", _rel(f), i,
                                           f"claims `{cand}` is uncommitted; git reports it "
                                           f"tracked and clean"))
    return out


# ── #2 ────────────────────────────────────────────────────────────────────────────────────
SERIES_REF = re.compile(r"(AUDIT_adversarial_[\w\-]+?)(?:_rev\d+)?\.md")
PLAN_REF = re.compile(r"PLAN_([\w\-]+?)\.md")
REV_REF = re.compile(r"_rev(\d+)")
GATE_MARK = re.compile(r"§\s*3\b|section\s+3\b", re.I)
# A new logical block starts at a blank line, a heading, a list item or a table row; everything
# else is a continuation. The live defect this exists to catch is split across two physical
# lines ("(all nine" / "rounds carry a disposition record"), so a line-at-a-time scan cannot
# see it — the check missed the very instance sitting on its own project's STATUS.
BLOCK_START = re.compile(r"^\s*(?:#{1,6}\s|[-*+]\s|\d+\.\s|\||>)")
# A block must stay a PARAGRAPH. Omitting `>` let an entire blockquoted section merge into one
# block, which picked up a §3 marker from one sentence and a round-count from another sixty
# lines away — the attribution defect this rewrite exists to kill, re-created at block scale.
MAX_BLOCK_LINES = 8


def _blocks(text: str) -> list[tuple[int, str]]:
    """(first physical line number, joined text) for each logical block."""
    out: list[tuple[int, str]] = []
    cur: list[str] = []
    first = 1
    for i, ln in enumerate(text.splitlines(), 1):
        if not ln.strip():
            if cur:
                out.append((first, " ".join(cur)))
                cur = []
            continue
        if cur and (BLOCK_START.match(ln) or len(cur) >= MAX_BLOCK_LINES):
            out.append((first, " ".join(cur)))
            cur = []
        if not cur:
            first = i
        cur.append(ln.strip())
    if cur:
        out.append((first, " ".join(cur)))
    return out


def _quoted(block: str, pos: int) -> bool:
    """Is this match inside quotes? A quoted count is a CITATION, not an assertion.

    The check's own PLAN documents it with the line `"seven rounds" when 8 audits existed`, and
    the first cut flagged that — a check firing on its own documentation is uninhabitable, the
    same constraint that shaped `schedule-claim`.
    """
    return (block.count('"', 0, pos) + block.count("\u201c", 0, pos)) % 2 == 1


def check_series_counts(scope: pathlib.Path) -> list[Finding]:
    """A STANDING surface's §3 round-count and revision range must match the files on disk.

    Scoped to standing surfaces only. A first cut linted every NEOCORTEX file and flagged
    `_rev6` for saying "five rounds" — which was TRUE when rev6 was written. An AUDIT is a
    DATED RECORD, not a standing surface: it states what was so on its date and must not be
    rewritten later, or the series stops being evidence.

    NARROWED 2026-08-26, after measuring it: **2 of 11 findings on the real tree were real** —
    18% precision, worse than the 150-false-positive first run this check was supposedly
    calibrated away from. "N rounds" in prose almost never means "the series has N files": the
    nine false ones counted FINDINGS ("Two round-2 audit findings are absorbed"), REVIEWERS
    ("two of three round-9 reviewers"), other people's PASSES ("Fugu has now reviewed two
    rounds"), and partitive subsets ("the next two rounds", "Four rounds of prose prescription").
    Both true ones were the file's own §3 series summary. So the claim is now checked only where
    it can mean what the check assumes:

      - the block carries a §3 marker, or names a series file outright (the original incident's
        shape: "Gate state, seven rounds. See `AUDIT_adversarial_thing.md`"); AND
      - the series is resolvable — named in the block, or the file's own (`PLAN_X` ↔
        `AUDIT_adversarial_X`); AND
      - the count is not inside quotes.

    Plus one assertion that needs no heuristic at all: **a `_revN` named for a resolvable series
    must exist on disk.** That is a filename claim against the filesystem, and it is what
    actually caught the live instance — `STATUS.md` cited `{,_rev2…_rev9}` and "all nine rounds"
    for a series with EIGHT files and no `_rev9`.
    """
    out: list[Finding] = []
    for f in _neocortex_files(scope):
        if "_archive" in f.parts:
            continue                        # archived = deliberately frozen
        if not (f.name == "STATUS.md" or f.name.startswith("PLAN_")):
            continue                        # dated records are not standing surfaces
        siblings = [g.name for g in _neocortex_files(scope) if g.parent == f.parent]
        archived = _archived_names(f.parent)
        if _superseded_by_sibling(f, siblings):
            continue                        # nor is a PLAN revision that a later one replaced
        own = ("AUDIT_adversarial_" + f.name[len("PLAN_"):-3]
               if f.name.startswith("PLAN_") else None)

        def count(series: str) -> int:
            """Files in the series, matched by FAMILY.

            Prefix matching undercounts every series that versions in the middle of the name
            (`AUDIT_..._ch2_11_v2_2026-08-04.md` … `_v7_…`): each revision looked like a series
            of one, so a truthful "seven rounds" would have been reported as a 6-file error.

            ARCHIVED ROUNDS COUNT. Excluding them made the check report "claims 9 rounds; 8
            files exist" against `STATUS.md` — and the ninth round's audit was on disk the
            whole time, relocated to `_archive/2026-08-25-oversized/` for exceeding the 64 KB
            root rule. A round does not stop having happened because its record was moved, and
            a check that sends a reader to "correct" a true sentence is worse than no check.
            """
            fam = _family(series)
            return sum(1 for n in {*siblings, *archived}
                       if n.endswith(".md") and _family(n) == fam)

        for lineno, block in _blocks(f.read_text(encoding="utf-8", errors="replace")):
            named = [m.group(1) for m in SERIES_REF.finditer(block)]
            named += ["AUDIT_adversarial_" + m.group(1) for m in PLAN_REF.finditer(block)]
            named = [n for n in named if count(n)]
            series = named[0] if named else own

            # (a) exact: every revision this block names must exist SOMEWHERE it could live.
            #     Two false positives shaped this, both from the first run: superseded
            #     revisions are moved to `_archive/` by design (the walk does not descend
            #     below a NEOCORTEX dir, so they must be read separately), and a `_revN` in a
            #     PLAN usually refers to a revision OF THAT PLAN, not of its audit series.
            #     Only assert against a series that actually exists — a mis-derived name with
            #     zero files on disk would otherwise condemn every revision it mentions.
            if series and count(series):
                fams = {_family(series), _family(f.name)}
                for m in REV_REF.finditer(block):
                    n = m.group(1)
                    # Match on FAMILY, not on a rigid `{series}_rev{N}.md` suffix: the fleet
                    # writes both `..._2026-08-24_rev8.md` and `..._plan_rev2_2026-07-29.md`,
                    # and the first cut condemned every series using the second form.
                    if any(f"_rev{n}" in c and _family(c) in fams
                           for c in (*siblings, *archived)):
                        continue
                    out.append(Finding("series-count", _rel(f), lineno,
                                       f"names `_rev{n}`, which exists neither in NEOCORTEX/ "
                                       f"nor _archive/ for `{series}` ({count(series)} in the "
                                       f"series) or for this file"))
            # (b) heuristic, and only where the words can mean what the check assumes.
            if not series or not (named or GATE_MARK.search(block)):
                continue
            actual = count(series)
            if not actual:
                continue
            for m in SERIES_CLAIM.finditer(block):
                if _quoted(block, m.start()):
                    continue
                claimed = WORD_NUM.get((m.group(1) or "").lower()) or (
                    int(m.group(2)) if m.group(2) else None)
                if claimed and claimed != actual and claimed < 20:
                    out.append(Finding("series-count", _rel(f), lineno,
                                       f"claims {claimed} rounds; {actual} files exist for "
                                       f"this series"))
                    break
    return out


# ── #3 ────────────────────────────────────────────────────────────────────────────────────
def check_audit_dispositions(scope: pathlib.Path) -> list[Finding]:
    """Where a project USES the append-a-disposition convention, every AUDIT must follow it.

    SELF-CALIBRATING, on purpose. Appending a disposition table to each round's AUDIT is a
    governance-local practice, not a fleet rule; a first cut applied it everywhere and flagged
    21 audits in projects that never adopted it. Nothing erodes a linter faster than telling
    people their working conventions are violations. So: a directory is only held to the
    convention once at least one audit in it demonstrably follows the convention. An outlier
    among compliant siblings is a real finding — that is exactly how _rev8 was missed.
    """
    out: list[Finding] = []
    by_dir: dict[pathlib.Path, list[pathlib.Path]] = {}
    for f in _neocortex_files(scope):
        if f.name.startswith("AUDIT_adversarial_") and "_archive" not in f.parts:
            by_dir.setdefault(f.parent, []).append(f)
    for d, files in by_dir.items():
        has_disp = {f: "disposition" in f.read_text(encoding="utf-8", errors="replace").lower()
                    for f in files}
        if not any(has_disp.values()):
            continue                        # this project does not use the convention
        for f, ok in sorted(has_disp.items()):
            if not ok:
                out.append(Finding("audit-undispositioned", _rel(f), 0,
                                   f"no disposition record, while {sum(has_disp.values())} of "
                                   f"{len(files)} audits in this directory have one"))
    return out


# ── #5 ────────────────────────────────────────────────────────────────────────────────────
def check_schedule_claims(scope: pathlib.Path) -> list[Finding]:
    """No script may claim it is unscheduled — or that scheduling it is still undecided —
    while a loaded LaunchAgent names its path.

    The first cut matched two literals, `schedule: none` and `not currently scheduled`. On
    2026-08-26 `--only schedule-claim` returned CLEAN on a docstring that contradicted itself
    four lines apart: line 5 named the plist and its 09:17 run time, line 19 said "Whether (and
    how) to schedule it is an open founder decision." A reviewer found it; the check written for
    exactly this could not. A claim of not-being-scheduled is not one phrase, and an UNDECIDED
    claim is the same defect as a NONE claim — both assert no schedule exists.

    The population is tiny by construction (only scripts a LOADED agent already names), so the
    pattern can be generous — but not so generous that it fires on a docstring DESCRIBING the
    rule, which is what this file's own corrected header now does.
    """
    out: list[Finding] = []
    agents = pathlib.Path.home() / "Library" / "LaunchAgents"
    scheduled: set[pathlib.Path] = set()
    if agents.is_dir():
        for pl in agents.glob("*.plist"):
            try:
                d = plistlib.loads(pl.read_bytes())
            except Exception:                                   # noqa: BLE001
                continue
            for arg in d.get("ProgramArguments", []) or []:
                # The plist names the FULL PATH of the script it runs, so take it. The first
                # cut matched on BASENAME against every `*.py` in the workspace: it searched
                # 465k files to answer a question the plist had already answered, and it would
                # have flagged an unrelated copy of `monthly_audit.py` sitting anywhere in the
                # tree while the agent ran a different file entirely.
                if isinstance(arg, str) and arg.endswith(".py"):
                    scheduled.add(pathlib.Path(arg))
    for py in sorted(scheduled):
        if not py.is_file():
            continue
        try:
            py.resolve().relative_to(scope.resolve())
        except ValueError:
            continue                        # scheduled, but outside the scope being linted
        head = "\n".join(py.read_text(encoding="utf-8", errors="replace").splitlines()[:40])
        m = UNSCHEDULED_CLAIM.search(head)
        if m:
            out.append(Finding("schedule-claim", _rel(py), 0,
                               f"claims {m.group(0).strip()!r}; a loaded LaunchAgent runs it"))
    return out


# ── #6 ────────────────────────────────────────────────────────────────────────────────────
def check_manifest_status_enum(scope: pathlib.Path) -> list[Finding]:
    """Every MANIFEST status token must be in the enum."""
    out: list[Finding] = []
    for mf in [p for p in _files(scope) if _in_neocortex(p) and p.name == "MANIFEST.json"]:
        try:
            data = json.loads(mf.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        for e in data.get("files", []):
            tok = str(e.get("status", "")).split()[0].strip("*—-:").upper() if e.get("status") else ""
            if tok and tok not in STATUS_ENUM:
                out.append(Finding("status-enum", _rel(mf), 0,
                                   f"`{e.get('file')}` has status token {tok!r}, not in the enum"))
    return out


# ── #7 ────────────────────────────────────────────────────────────────────────────────────
def check_binding_table_expiry(scope: pathlib.Path) -> list[Finding]:
    """A table binding task classes to named models must carry an expiry."""
    out: list[Finding] = []
    for f in _neocortex_files_excluding_reviews(scope):
        text = f.read_text(encoding="utf-8", errors="replace")
        if not re.search(r"\|\s*`?(?:reasoning|implementation|destructive|mechanical)`?\s+"
                         r"(?:frontier|mid|cheapest)@", text):
            continue
        if not re.search(r"expir\w+|re-?bind|valid until", text, re.I):
            out.append(Finding("binding-no-expiry", _rel(f), 0,
                               "binds task classes to named models with no expiry; model "
                               "bindings rot faster than documents are re-read"))
    return out


def _blocked_tags(lines: list[str], i: int) -> set[str]:
    """The M-tags a register row actually claims are BLOCKED, deduplicated.

    Two false positives this exists to kill, both produced on the first real run against
    `governance/NEOCORTEX/CRUMBLES.md`:

    1. A flat 6-line lookback reached ABOVE `crumbles:` into the file's RESOLVED header — the
       comment block whose whole purpose is to record that M-5 shipped. Firing there tells a
       correctly-reconciled register that its reconciliation is a violation. The window now
       starts at the row's own `- item:` and never crosses it.
    2. The row itself said *"NINE of twelve items have now shipped ahead of it (M-1/2/…)"* —
       an enumeration of SHIPPED work inside a row blocked on something else (the gate). A tag
       in a sentence that declares it shipped is not a claim that it is blocked, so that
       sentence is skipped. The row's actual stale claim is caught by (b) below instead.
    """
    start = i - 1
    for j in range(i - 1, max(-1, i - 8), -1):
        if re.match(r"\s*-\s+item:", lines[j]):
            start = j
            break
    block = "\n".join(lines[start:i])
    tags: set[str] = set()
    for sentence in re.split(r"(?<=[.;])\s+|\n", block):
        if SHIPPED_CONTEXT.search(sentence):
            continue
        # NOT a possessive. "M-9's wiring is owed" is a claim about a SUB-PART of M-9, and
        # M-9 itself shipped — firing there tells a register that correctly distinguishes the
        # two that it is stale. Produced live the moment `OPEN_WORK.md` was reconciled.
        tags |= set(re.findall(r"\b(M-\d+)\b(?!['’]s)", sentence))
    return tags


# ── #4, in the only tractable form ────────────────────────────────────────────────────────
def check_crumbles_shipped(scope: pathlib.Path) -> list[Finding]:
    """No CRUMBLES row may call work `gate-blocked` when git shows it shipped.

    #4's general form — "a ruling that partitions a closed set must account for every member"
    — is not mechanizable without knowing which set is being partitioned, so it stays a human
    discipline with a named arithmetic check (assert ruled union deferred == the whole set).
    What IS mechanizable is the recurrence it produced: a blocked-work register outliving the
    work. On 2026-08-25/26 four CRUMBLES rows claimed gate-blocked status for M-1, M-3, M-4
    and a restraint that had all shipped hours earlier; a parallel session found them, not a
    mechanism. This is assertion #1's shape in a different file.
    """
    out: list[Finding] = []
    log = _git("log", "--oneline", "-80")
    for cf in _registers(scope):
        text = cf.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        for i, line in enumerate(lines, 1):
            # (a) an M-tag this row calls blocked that git has already shipped.
            if OPEN_CLAIM.search(line):
                for tag in sorted(_blocked_tags(lines, i)):
                    # The tag must OWN the commit subject — at its head (`M-7: ...`,
                    # `M-5 (…): …`) or as a conventional-commit scope (`governance(M-8): …`).
                    # An incidental mention mid-sentence is not evidence that work shipped.
                    shipped = rf"^\w+ (?:{re.escape(tag)}\b|[\w.\-]+\({re.escape(tag)}\))"
                    if log and re.search(shipped, log, re.M):
                        out.append(Finding("crumble-shipped", _rel(cf), i,
                                           f"row is class gate-blocked but git has a commit for "
                                           f"{tag}; blocked-work registers outlive the work"))
            # (b) a §3-gate claim about a NAMED PLAN whose own status line records the gate as
            #     RUN. Independent of (a), and deliberately NOT gated on an OPEN_CLAIM marker:
            #     "the gate on X is not run" is falsifiable on its own, and in CRUMBLES the
            #     claim sits on the `item:` line while `class: gate-blocked` sits two lines
            #     below. OPEN_WORK.md carries no M-tags at all, so (a) alone could never see
            #     the largest live instance — the row that named this PLAN and four shipped
            #     mechanisms as "Still owed and NOT closed."
            if not GATE_UNRUN_CLAIM.search(line):
                continue
            for m in re.finditer(r"`?([\w./\-]*PLAN_[\w.\-]+\.md)`?", line):
                named = m.group(1)
                target = next((t for t in (cf.parent / named, ROOT / named)
                               if t.is_file()), None)
                if target is None:
                    continue
                status_line = target.read_text(encoding="utf-8", errors="replace").split("\n")[0]
                if GATE_RUN_STATUS.search(status_line):
                    out.append(Finding("crumble-shipped", _rel(cf), i,
                                       f"claims the §3 gate on `{pathlib.Path(named).name}` is "
                                       f"unrun; that PLAN's own status line records it as RUN"))
    return out


# ── #9 ────────────────────────────────────────────────────────────────────────────────────
# A DONE item header, in the convention this fleet's PLANs actually use:
#     ### M-5 — Mirror freshness with an owner — **DONE 2026-08-26**
DONE_HEADER = re.compile(r"^#{2,6}\s+.*[—–-]\s*\*{0,2}DONE\b", re.M)
HEADER_ANY = re.compile(r"^#{1,6}\s")
# Paths a DONE item names. Extensions restricted to things a repo tracks; a `.plist` under
# ~/Library or a `.json` under a gitignored store resolves to no repo (or to an ignored path)
# and is dropped below, which is why the pattern can afford to be generous here.
ITEM_PATH = re.compile(r"`([\w./\-]+\.(?:py|md|json|sh|yaml|yml|toml|cfg|txt))`")


def _repo_of(path: pathlib.Path) -> pathlib.Path | None:
    """The git repo that GOVERNS this path — not the workspace repo by assumption.

    M-5's producer was uncommitted in `governance/` and its consumer in the PROJECT-B control-centre
    repo, a SEPARATE repository the workspace repo ignores entirely. A check that asked only the
    workspace repo would have reported the consumer as "not tracked here" and had no idea it was
    tracked-and-dirty one directory tree over, or missed it as ignored. Walk up for the `.git`.
    """
    for d in [path if path.is_dir() else path.parent, *path.parents]:
        if (d / ".git").exists():
            return d
    return None


def _git_at(repo: pathlib.Path, *args: str) -> tuple[int, str]:
    """Like `_git`, but in a NAMED repo and returning the exit code.

    The exit code matters here: `ls-files --error-unmatch` and `check-ignore` both signal their
    answer through it, and `_git`'s swallow-stdout-on-failure contract cannot express that.
    """
    try:
        r = subprocess.run(["git", "-C", str(repo), *args],
                           capture_output=True, text=True, timeout=60)
        return r.returncode, r.stdout
    except (OSError, subprocess.SubprocessError):
        return 1, ""


def _done_items(text: str) -> list[tuple[int, str, str]]:
    """(line number, header, body) for every DONE-tagged item. Body runs to the next header."""
    lines = text.splitlines()
    starts = [i for i, ln in enumerate(lines) if DONE_HEADER.match(ln)]
    out = []
    for st in starts:
        end = st + 1
        while end < len(lines) and not HEADER_ANY.match(lines[end]):
            end += 1
        out.append((st + 1, lines[st], "\n".join(lines[st:end])))
    return out


def check_done_claims_committed(scope: pathlib.Path) -> list[Finding]:
    """No PLAN item may be tagged DONE while a file it names is untracked or dirty.

    Assertion #1 runs in the mirror-image direction: it catches a file claiming something IS
    uncommitted when git says otherwise. Nothing caught the direction that actually cost a
    review round — a DONE tag over code git has never seen. This is the commit boundary itself,
    and it is the difference between BUILT and SHIPPED.

    Three deliberate exclusions, each one a false positive this check would otherwise produce
    by the dozen:
      - a named path that does not exist on disk (prose, illustration, a file since deleted);
      - a path no git repo governs (`~/Library/LaunchAgents/...`, anything outside a checkout);
      - a path its own repo IGNORES by design (the interaction-log store, build output). An
        ignored file is not a broken shipping claim, and the root `.gitignore` here is `/*`.
    """
    out: list[Finding] = []
    for f in _neocortex_files_excluding_reviews(scope):
        if not f.name.startswith("PLAN_") or "_archive" in f.parts:
            continue                        # archived = deliberately frozen
        text = f.read_text(encoding="utf-8", errors="replace")
        for lineno, header, body in _done_items(text):
            tag = header.lstrip("# ").split("—")[0].strip() or "item"
            seen: set[str] = set()
            for m in ITEM_PATH.finditer(body):
                cand = m.group(1)
                if cand in seen:
                    continue
                seen.add(cand)
                # Resolve against the workspace and against the PLAN's own project, in that
                # order — PLANs cite both `governance/scripts/x.py` and `NEOCORTEX/y.md`.
                project = f.parent.parent
                target = next((t for t in (ROOT / cand, project / cand, f.parent / cand)
                               if t.is_file()), None)
                if target is None:
                    continue
                repo = _repo_of(target)
                if repo is None:
                    continue
                rel = str(target.resolve().relative_to(repo.resolve()))
                where = _rel(repo)
                where = "the workspace repo" if where in (".", "") else f"repo {where}"
                if _git_at(repo, "check-ignore", "-q", "--", rel)[0] == 0:
                    continue                # ignored by design, not a shipping claim
                if _git_at(repo, "ls-files", "--error-unmatch", "--", rel)[0] != 0:
                    out.append(Finding("done-claim-uncommitted", _rel(f), lineno,
                                       f"{tag} is tagged DONE but `{cand}` is UNTRACKED in "
                                       f"{where}; built is not shipped"))
                    continue
                st = _git_at(repo, "status", "--porcelain", "--", rel)[1].strip()
                if st:
                    out.append(Finding("done-claim-uncommitted", _rel(f), lineno,
                                       f"{tag} is tagged DONE but `{cand}` has uncommitted "
                                       f"changes in {where}; built is not shipped"))
    return out


def _neocortex_dirs(scope: pathlib.Path) -> list[pathlib.Path]:
    """Every live NEOCORTEX directory under `scope`, `_archive/` excluded (frozen history)."""
    seen, out = set(), []
    for f in _neocortex_files(scope):
        d = f.parent
        # No `_archive` test here: `_in_neocortex` already returns False for anything below a
        # NEOCORTEX subdirectory, so rotated volumes never reach this loop. Verified by mutation
        # -- a variant that deletes an `_archive` guard from THIS function changes no test
        # outcome, because the guard was doing nothing. Dead defensive code is worse than none:
        # the next reader credits it with protection that lives somewhere else entirely.
        if d.name != "NEOCORTEX":
            continue
        if d not in seen:
            seen.add(d)
            out.append(d)
    return sorted(out)


def check_status_behind_journal(scope: pathlib.Path) -> list[Finding]:
    """STATUS.md must not be committed STRICTLY EARLIER than JOURNAL.md (#10).

    The observable, stated so a script can test it rather than a human remember it: the last
    commit touching `STATUS.md` may not be a strict ancestor of the last commit touching
    `JOURNAL.md`. A JOURNAL that has moved on without STATUS is a session that recorded what it
    did and left the standing next-action asserting the opposite.

    Ancestry, not dates. Both files are usually written the same DAY -- the incident that
    produced this check had both stamped 2026-08-26 -- so a date comparison cannot see it.
    Commit ancestry can, and it is exact.

    THREE DELIBERATE SILENCES, each measured rather than assumed:
      - EITHER FILE UNTRACKED -> silent. 19 of 23 NEOCORTEX directories in this fleet have
        untracked docs today, so this check is blind to them. That is a COVERAGE limit and it
        is stated here rather than mistaken for a clean bill.
      - STATUS DIRTY IN THE WORKING TREE -> silent. A modified STATUS is a rebase in progress;
        firing at the moment the author is fixing the very thing would be the worst possible
        time to be right. (The uncommitted half is #9's job, not this one's.)
      - NO GOVERNING REPO -> silent. Same rule as #9: ask the repo that governs the path.

    WHAT IT CANNOT CATCH, and this is not a bug to be fixed later by widening it: a session
    that DID rewrite STATUS but left the next-action stale inside it passes here. Catching that
    needs token-matching between the next-action and the JOURNAL's DONE claims -- the same
    shape as `series-count`, which measures 18% precision. The residual case is closed by
    ORDERING (rebase STATUS last), not by a noisier assertion.
    """
    out: list[Finding] = []
    for nc in _neocortex_dirs(scope):
        status, journal = nc / "STATUS.md", nc / "JOURNAL.md"
        if not (status.is_file() and journal.is_file()):
            continue
        repo = _repo_of(status)
        if repo is None:
            continue
        try:
            rel_s = str(status.resolve().relative_to(repo.resolve()))
            rel_j = str(journal.resolve().relative_to(repo.resolve()))
        except (ValueError, OSError):
            continue
        sha_s = _git_at(repo, "log", "-1", "--format=%H", "--", rel_s)[1].strip()
        sha_j = _git_at(repo, "log", "-1", "--format=%H", "--", rel_j)[1].strip()
        if not sha_s or not sha_j or sha_s == sha_j:
            continue                       # untracked, or committed together: nothing to say
        if _git_at(repo, "status", "--porcelain", "--", rel_s)[1].strip():
            continue                       # rebase in progress -- do not fire mid-fix
        if _git_at(repo, "merge-base", "--is-ancestor", sha_s, sha_j)[0] != 0:
            continue                       # STATUS is level or ahead
        n = _git_at(repo, "rev-list", "--count", f"{sha_s}..{sha_j}")[1].strip() or "?"
        subj = _git_at(repo, "log", "-1", "--format=%s", sha_j)[1].strip()[:72]
        out.append(Finding("status-behind-journal", _rel(status), 0,
                           f"STATUS.md was last committed {n} commit(s) BEFORE JOURNAL.md "
                           f"({sha_s[:7]} vs {sha_j[:7]}: \"{subj}\") — the session recorded "
                           f"what it did and left the standing next-action asserting otherwise"))
    return out


CHECKS = {
    "uncommitted-claim":    check_uncommitted_claims,
    "series-count":         check_series_counts,
    "audit-undispositioned": check_audit_dispositions,
    "schedule-claim":       check_schedule_claims,
    "status-enum":          check_manifest_status_enum,
    "binding-no-expiry":    check_binding_table_expiry,
    "crumble-shipped":      check_crumbles_shipped,
    "done-claim-uncommitted": check_done_claims_committed,
    "status-behind-journal": check_status_behind_journal,
}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--path", default=str(ROOT), help="scope to lint (default: workspace root)")
    ap.add_argument("--strict", action="store_true",
                    help="exit non-zero on findings (default: advisory, exit 0)")
    ap.add_argument("--only", help="run one check by name")
    ap.add_argument("--json", action="store_true",
                    help="emit each finding as one JSON line (check/path/line/detail); "
                         "suppresses the human-readable report, for machine consumers "
                         "(the precision ledger) rather than a person at a terminal")
    a = ap.parse_args(argv)

    scope = pathlib.Path(a.path).expanduser().resolve()
    if not scope.is_dir():
        print(f"not a directory: {scope}", file=sys.stderr)
        return 2

    checks = CHECKS if not a.only else {a.only: CHECKS[a.only]} if a.only in CHECKS else None
    if checks is None:
        print(f"unknown check {a.only!r}; known: {', '.join(CHECKS)}", file=sys.stderr)
        return 2

    findings: list[Finding] = []
    for fn in checks.values():
        try:
            findings.extend(fn(scope))
        except Exception as e:                                  # noqa: BLE001
            # Fail open per check: one broken assertion must never suppress the others, and a
            # linter that crashes is a linter that gets removed.
            print(f"  [check-error] {fn.__name__}: {type(e).__name__}: {e}", file=sys.stderr)

    if a.json:
        # No 20-per-check cap here — that cap exists to keep a terminal report readable, and
        # a machine consumer needs every finding to build a stable identity for it later.
        for f in findings:
            print(json.dumps({"check": f.check, "path": f.path, "line": f.line,
                              "detail": f.detail}))
        return 1 if (a.strict and findings) else 0

    # Report the reached scope, not just the path. The walk prunes (build output, vendored
    # trees, the interaction-log archive) and never descends below a NEOCORTEX dir; a linter
    # that bounds its own coverage must say what it bounded, or "clean" is unreadable.
    reached = _files(scope)
    ncd = len({f.parent for f in reached if _in_neocortex(f)})
    print(f"closeout_lint: {len(checks)} check(s) over {scope}\n"
          f"  scope: {len(reached)} file(s) in {ncd} NEOCORTEX dir(s) "
          f"(pruned: {', '.join(sorted(PRUNE_DIRS))})")
    if not findings:
        print("  clean")
        return 0
    by = {}
    for f in findings:
        by.setdefault(f.check, []).append(f)
    for name, group in sorted(by.items()):
        print(f"\n{name} — {len(group)} finding(s)")
        for f in group[:20]:
            print(f)
        if len(group) > 20:
            print(f"      … and {len(group) - 20} more")
    print(f"\n{len(findings)} finding(s). "
          f"{'BLOCKING (--strict)' if a.strict else 'Advisory — pass --strict to block.'}")
    return 1 if a.strict else 0


if __name__ == "__main__":
    raise SystemExit(main())

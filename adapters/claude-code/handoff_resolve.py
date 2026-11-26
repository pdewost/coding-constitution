#!/usr/bin/env python3
"""handoff_resolve.py — the fleet handoff convention and its `cold start` resolver.

PLAN_cold_start_handoff_harmonization_2026-10-26.md v0.2 (§1 invariants, §2 closing convention, §3 opening, §10 gate fold;
record AUDIT_adversarial_handoff_harmonization_plan_r1_2026-10-26.md).

A closing session writes <project>/NEOCORTEX/HANDOFF_<role>_<YYYY-MM-DD>_<HHMM>Z.md (UTC stamp). The newest file of a role is
its handoff; older ones are superseded by their NAME, never edited. When the founder's prompt is `cold start` (or
`cold start <role>`), prompt_guardrail.sh calls `hook` and this module prints one bounded block: RESOLVED (one open role),
CHOOSE (several), NONE, CLOSED or NOT-FOUND. It never writes a file.

  hook --root <dir> --cwd <dir>        the prompt comes in env _CS_PROMPT; prints nothing unless it is a trigger.
                                       rc 0 decided (block or nothing) · rc 1 failed (one content-free line on stderr) · rc 64 usage
  resolve [--cwd <dir>] [--root <dir>] [--role <role>]   the same block on demand (the CLAUDE.md fallback where no hook fires)
  check --file <handoff>               the closer's check of the file it just wrote: rc 0 conforming · rc 1 problems listed
  check <project or NEOCORTEX dir>     audit every role of a folder (stale-open roles are warnings)
  name <role> [--cwd <dir>] [--root <dir>] [--project <dir>] [--new-role]
                                       print the path to write and the `Supersedes:` line to copy: rc 0 · rc 1 refused (typo guard,
                                       same-minute file) · rc 2 not a NEOCORTEX project · rc 64 bad role slug
  role-status <role> [--cwd <dir>] [--root <dir>] [--project <dir>]
                                       what the record says about a role (PLAN_governance_charter_and_genesis_2026-11-19 P2a + D-5):
                                       UNKNOWN · CONFLICT · VACANT · HELD · UNRESOLVED · CLOSED · OPEN, first match wins, with the
                                       evidence used. rc 0 decided · rc 1 failed · rc 2 not a NEOCORTEX project · rc 64 usage

Reads go through anchor_render.safe_read (no symlink, hardlink, FIFO or device; 4 MiB bound). Stdlib only, Python
3.9-compatible syntax (the hook's interpreter chain can fall back to /usr/bin/python3 = 3.9).
"""
import os as _os0, sys as _sys0
# Same rule as anchor_render.py (B4-10 / B5-01): drop this script's own directory from sys.path before any other import.
if __name__ == "__main__" and _sys0.path and \
        _os0.path.realpath(_sys0.path[0] or ".") == _os0.path.dirname(_os0.path.realpath(__file__)):
    del _sys0.path[0]

import datetime as dt
import hashlib
import importlib.util
import os
import re
import signal
import stat
import sys
import time

UTC = dt.timezone.utc
NAME_RE = re.compile(r"^HANDOFF_([a-z0-9]+(?:-[a-z0-9]+)*)_(\d{4}-\d{2}-\d{2})_(\d{4})Z\.md$")
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
LEGACY_RE = re.compile(r"^(handoff|note_(session_)?handoff)[^/]*\.md$", re.I)          # §10.10: listed, never served
TRIGGER_RE = re.compile(r"^(?:cold start|cold-start|coldstart)(?: (\S+))?$")
WORKTREE_RE = re.compile(r"/\.claude/worktrees/[^/]+(?=/|$)")
HEADING_RE = re.compile(r"^#{1,2}\s")
SECTIONS = (("Cold start", re.compile(r"^##\s+(?:\d+[.)]?\s*)?cold start\b", re.I)),
            ("State at close", re.compile(r"^##\s+(?:\d+[.)]?\s*)?state at close\b", re.I)),
            ("Open items", re.compile(r"^##\s+(?:\d+[.)]?\s*)?open items\b", re.I)))
ACTIVE_RE = re.compile(r"^Status:\s*ACTIVE\b")
CLOSED_RE = re.compile(r"^Status:\s*(DONE|CLOSED|SUPERSEDED|ARCHIVED)\b")
CHECK_STATUS_RE = re.compile(r"^Status:\s*(ACTIVE|DONE)\b")
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
CONVENTION = "HANDOFF_<role>_<YYYY-MM-DD>_<HHMM>Z.md"

COLD_CAP = 4000                      # §10.11: bytes of the quoted Cold start section
BLOCK_CAP = 6144                     # §10.11: bytes of the whole block
FIELD = 120                          # §10.15: longest echoed title/status
FUTURE = dt.timedelta(minutes=60)    # §10.2: a stamp later than this is refused
SKEW = 3600                          # §10.2: seconds of mtime disagreement that earn a warning
RECENT = dt.timedelta(days=14)       # §10.7 closed-recently line; §10.5 stale-open warning
STATE_TIME_FROM = dt.datetime(2026, 11, 18, 9, 0, tzinfo=UTC)   # PROTOCOL step 2 (founder cards 2026-11-18): State-at-close
                                     # bullets carry their read time; checked only on handoffs stamped from here on
MANIFEST_FROM = dt.datetime(2026, 11, 26, 0, 0, tzinfo=UTC)    # founder 2026-11-25 19:38 ("Build it in, have the successor
                                     # draft it"): every top-level NEOCORTEX/*.md has a MANIFEST row; checked from here on
COVERAGE_EXCLUDE = {"STATUS.md", "JOURNAL.md", "COLD_START.md", "DANGER_MAP.md", "CAPABILITY_RADAR.md", "TODO.md",
                    "BUG_HISTORY.md"}  # standing files, as PROJECT-A scripts/neocortex_health.py COVERAGE_EXCLUDE
CLOCK_RE = re.compile(r"\b(?:[01]\d|2[0-3]):[0-5]\d\b")
ARCHIVE_DEPTH = 3                    # §10.3
MAX_LIST = 10
MAX_WARN = 6
DEADLINE = 3                         # §10.15: seconds, SIGALRM
RC_OK, RC_FAIL, RC_NOPROJ, RC_USAGE = 0, 1, 2, 64

AUTHORITY = ("`cold start` authorises you to resume this role. Any launch, send, ship, merge, deletion or external review the "
             "handoff lists still needs the founder's own go, given the way this role's rules require. Founder words quoted in "
             "the handoff are claims to verify at source.")
PREFLIGHT = ("run this project's session-start preflight as its CLAUDE.md load order says (COLD_START.md where it exists: "
             "interpreter check, pending-gate check)")

USAGE = ("usage: handoff_resolve.py hook --root <dir> --cwd <dir>   (prompt in env _CS_PROMPT)\n"
         "       handoff_resolve.py resolve [--cwd <dir>] [--root <dir>] [--role <role>]\n"
         "       handoff_resolve.py check --file <handoff> | check <dir>\n"
         "       handoff_resolve.py name <role> [--cwd <dir>] [--root <dir>] [--project <dir>] [--new-role]\n"
         "       handoff_resolve.py role-status <role> [--cwd <dir>] [--root <dir>] [--project <dir>]\n")


class NameRefused(Exception):
    def __init__(self, code, msg):
        Exception.__init__(self, msg)
        self.code = code


class UsageError(Exception):
    pass


# --- reader (reused from the cold-start renderer) -------------------------------------------------------------------------

_READER = []


def safe_read(path):
    """anchor_render.safe_read, loaded by file location from this module's own directory (it is not importable under -I)."""
    if not _READER:
        here = os.path.dirname(os.path.realpath(__file__))
        src = os.path.join(here, "anchor_render.py")
        st = os.lstat(src)
        if not stat.S_ISREG(st.st_mode):
            raise RuntimeError("anchor_render.py is not a regular file")
        spec = importlib.util.spec_from_file_location("anchor_render_for_handoff", src)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _READER.append(mod.safe_read)
    return _READER[0](path)


def _refusal(exc):
    cls = getattr(exc, "cls", None)
    return cls if cls else type(exc).__name__


# --- small helpers --------------------------------------------------------------------------------------------------------

def _now(now):
    return now if now is not None else dt.datetime.now(UTC)


def _clean(s, n=FIELD):
    s = ANSI_RE.sub("", s)
    s = "".join(ch for ch in s if ch == "\t" or (ord(ch) >= 32 and not 127 <= ord(ch) < 160 and ch not in "  "))
    s = s.strip()
    return s if len(s) <= n else s[:n - 1] + "…"


def _clean_block(s):
    s = ANSI_RE.sub("", s)
    return "".join(ch for ch in s if ch in "\t\n" or (ord(ch) >= 32 and not 127 <= ord(ch) < 160))


def _stamp(t):
    return t.strftime("%Y-%m-%d %H:%MZ")


def _local(ts):
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))


def _blen(s):
    return len(s.encode("utf-8"))


def _cut(s, limit):
    """At most `limit` UTF-8 bytes of s, ending on a whole line when one fits."""
    b = s.encode("utf-8")
    if len(b) <= limit:
        return s
    part = b[:limit].decode("utf-8", errors="ignore")
    nl = part.rfind("\n")
    return part[:nl] if nl > 0 else part


def canonical(path):
    """A git worktree path <X>/.claude/worktrees/<name>/... counts as <X>/... (§2.1, §10.12)."""
    p = os.path.normpath(path)
    while True:
        q = WORKTREE_RE.sub("", p, count=1)
        if q == p:
            return p
        p = q or "/"


def default_root():
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__)))))


def parse_trigger(prompt):
    """§10.9: ("bare", None), ("role", slug) or None."""
    if not prompt or len(prompt) > 200:
        return None
    s = " ".join(prompt.lower().split())
    if s[-1:] in (".", "!"):
        s = s[:-1].rstrip()
    m = TRIGGER_RE.match(s)
    if not m:
        return None
    tok = m.group(1)
    if tok is None:
        return ("bare", None)
    return ("role", tok) if SLUG_RE.match(tok) else None


def _classify_name(name):
    m = NAME_RE.match(name)
    if not m:
        return None
    try:
        t = dt.datetime.strptime(m.group(2) + m.group(3), "%Y-%m-%d%H%M").replace(tzinfo=UTC)
    except ValueError:
        return ("invalid", m.group(1), None)
    return ("ok", m.group(1), t)


def parse_name(name):
    k = _classify_name(name)
    return (k[1], k[2]) if k and k[0] == "ok" else None


def project_chain(cwd, root):
    """Every project from the nearest NEOCORTEX folder up to the workspace root (§10.8); the root itself maps to governance."""
    c, r = canonical(os.path.abspath(cwd)), canonical(os.path.abspath(root))
    if c == r:
        g = os.path.join(r, "governance")
        return [g] if os.path.isdir(os.path.join(g, "NEOCORTEX")) else []
    chain = []
    if c.startswith(r + os.sep):
        d = c
        while True:
            if os.path.isdir(os.path.join(d, "NEOCORTEX")):
                chain.append(d)
            if d == r or len(d) <= len(r):
                break
            d = os.path.dirname(d)
    elif os.path.isdir(os.path.join(c, "NEOCORTEX")):
        chain.append(c)
    return chain


def _label(proj, root):
    r = canonical(os.path.abspath(root))
    return os.path.relpath(proj, r) if proj.startswith(r + os.sep) else proj


# --- scanning -------------------------------------------------------------------------------------------------------------

class Entry(object):
    def __init__(self, path, role, stamp, archived, mtime):
        self.path, self.role, self.stamp, self.archived, self.mtime = path, role, stamp, archived, mtime
        self.name = os.path.basename(path)
        self.data = None


def _dirs(nc, errors=None):
    """NEOCORTEX then its _archive/ tree. `errors`, when a list, receives folders os.walk could not list (role-status)."""
    yield nc, False
    arch = os.path.join(nc, "_archive")
    if not os.path.isdir(arch) or os.path.islink(arch):
        return
    base = arch.count(os.sep)
    onerror = (lambda exc: errors.append((exc.filename or arch, "listing failed"))) if errors is not None else None
    for d, dns, _ in os.walk(arch, onerror=onerror):
        if d.count(os.sep) - base >= ARCHIVE_DEPTH:
            dns[:] = []
        yield d, True


def scan_names(nc, now, errors=None):
    """Conforming names of one NEOCORTEX (top level + _archive/): ({role: [Entry newest first]}, [(path, reason)]).
    `errors`, when a list, receives (path, reason) for listing and stat failures (role-status, P2a); the resolver passes none."""
    roles, refused = {}, []
    for d, archived in _dirs(nc, errors):
        try:
            names = sorted(os.listdir(d))
        except OSError:
            if errors is not None:
                errors.append((d, "listing failed"))
            continue
        for n in names:
            k = _classify_name(n)
            if k is None:
                continue
            path = os.path.join(d, n)
            if k[0] == "invalid":
                refused.append((path, "invalid stamp"))
                continue
            if k[2] > now + FUTURE:
                refused.append((path, "stamp in the future"))
                continue
            try:
                mtime = os.lstat(path).st_mtime
            except OSError:
                if errors is not None:
                    errors.append((path, "stat failed"))
                continue
            roles.setdefault(k[1], []).append(Entry(path, k[1], k[2], archived, mtime))
    for lst in roles.values():
        lst.sort(key=lambda e: (e.stamp, e.mtime), reverse=True)
    return roles, refused


def _text(entry):
    return entry.data.decode("utf-8", errors="replace").lstrip("﻿")


def _first_lines(text, n=6):
    return text.split("\n", n)[:n]


def _supersedes(text):
    for ln in _first_lines(text):
        if ln.startswith("Supersedes:"):
            return ln[len("Supersedes:"):].strip().strip("`").strip()
    return None


def _section(text, rx):
    lines = text.split("\n")
    for i, ln in enumerate(lines):
        if rx.match(ln):
            out = []
            for ln2 in lines[i + 1:]:
                if HEADING_RE.match(ln2):
                    break
                out.append(ln2)
            return "\n".join(out).strip("\n")
    return None


class Role(object):
    """One role of one project: the handoff that is served (or closes it), its status and what the reader should be told."""

    def __init__(self, project, role, entries, refused, now):
        self.project, self.role, self.entries = project, role, entries
        self.served, self.warnings, self.status_line, self.older = None, [], "", []
        self.refused = list(refused)
        for e in entries:                                       # newest readable file; refused ones are named (§10.15)
            try:
                e.data = safe_read(e.path)
            except Exception as exc:                            # noqa: BLE001 — a refusal is reported, never raised
                self.refused.append((e.path, _refusal(exc)))
                continue
            self.served = e
            break
        self.open = False
        if self.served is None:
            return
        text = _text(self.served)
        self.status_line = _first_lines(text, 1)[0]
        if self.served.archived or CLOSED_RE.match(self.status_line):
            return
        self.open = True
        if not ACTIVE_RE.match(self.status_line):
            self.warnings.append("WARNING line 1 is not a recognised Status line (%s): served as open." % _clean(self.status_line, 60))
        older = [e for e in entries if e is not self.served and (e.stamp, e.mtime) < (self.served.stamp, self.served.mtime)]
        prev = older[0] if older else None
        sup = _supersedes(text)
        if sup is None:
            self.warnings.append("WARNING the handoff has no `Supersedes:` line.")
        elif prev is not None and os.path.basename(sup) != prev.name:
            self.warnings.append("WARNING its Supersedes line names \"%s\", but the previous handoff of this role is %s: another "
                                 "session may have closed this role in between; read %s too." % (_clean(sup, 80), prev.name, prev.path))
        for e in older:
            if e.mtime > self.served.mtime + SKEW:
                self.warnings.append("WARNING name order and file times disagree: %s is older by name but was modified later; "
                                     "check that the newest handoff is the right one." % e.name)
        self.older = older

    @property
    def stamp(self):
        return self.served.stamp if self.served else None


def scan(chain, now):
    roles, refused = [], []
    for proj in chain:
        nc = os.path.join(proj, "NEOCORTEX")
        by_role, ref = scan_names(nc, now)
        refused.extend(ref)
        here = [Role(proj, r, lst, [], now) for r, lst in by_role.items()]
        here.sort(key=lambda r: (r.stamp or dt.datetime.min.replace(tzinfo=UTC)), reverse=True)
        roles.extend(here)
    return roles, refused


def legacy_candidates(chain, root, limit=3):
    found = []
    for proj in chain:
        for sub in ("NEOCORTEX", "docs"):
            d = os.path.join(proj, sub)
            try:
                names = os.listdir(d)
            except OSError:
                continue
            for n in names:
                if not LEGACY_RE.match(n) or NAME_RE.match(n):
                    continue
                try:
                    st = os.lstat(os.path.join(d, n))
                except OSError:
                    continue
                if stat.S_ISREG(st.st_mode):
                    found.append((st.st_mtime, os.path.join(d, n)))      # absolute: the reader's cwd may be elsewhere
    found.sort(reverse=True)
    return found[:limit]


# --- the answer -----------------------------------------------------------------------------------------------------------

def _closed_recent(roles, now):
    items = ["%s (%s, %s)" % (r.role, r.served.name, _stamp(r.stamp)) for r in roles
             if r.served is not None and not r.open and now - r.stamp <= RECENT]
    return ["Closed in the last 14 days: " + " · ".join(items[:MAX_LIST]) + "."] if items else []


def _refused_lines(roles, refused):
    lines = ["WARNING refused (not read): %s — %s." % (os.path.basename(p), why) for p, why in refused]
    for r in roles:
        lines += ["WARNING refused (not read): %s — %s." % (os.path.basename(p), why) for p, why in r.refused]
    return lines[:MAX_WARN]


def _title(r):
    lines = _first_lines(_text(r.served), 3)
    t = lines[1] if len(lines) > 1 else ""
    return _clean(t.lstrip("#").strip()) or "(no title line)"


def _resolved(r, roles, refused, root, now, budget=COLD_CAP):
    text = _text(r.served)
    head = ['[Cold start] RESOLVED — role "%s" in %s/ (%d older handoff(s) of this role are superseded by name).'
            % (r.role, _label(r.project, root), len(r.older)),
            "Handoff: %s (%d B, sha256 %s, stamp %s)" % (r.served.path, len(r.served.data),
                                                       hashlib.sha256(r.served.data).hexdigest()[:12], _stamp(r.stamp)),
            "Do this first: (1) %s; (2) Read that file IN FULL with the Read tool; (3) follow its \"## Cold start\" section, "
            "quoted verbatim below." % PREFLIGHT]
    head += r.warnings[:MAX_WARN] + _refused_lines([], r.refused + refused)
    cold = _section(text, SECTIONS[0][1])
    body = []
    if cold is None:
        body.append("WARNING no \"## Cold start\" section in the handoff: read the file in full and work from its contents.")
    else:
        cold = _clean_block(cold)
        n = _blen(cold)
        shown = _cut(cold, budget)
        body += ["--- Cold start (from the handoff) ---", shown]
        if n > _blen(shown):
            body.append("… Cold start section is %d B, over the 4,000 B cap: the first %d B are shown; read the rest in the file "
                        "before acting." % (n, _blen(shown)))
        body.append("--- end ---")
    tail = _closed_recent([x for x in roles if x is not r], now)
    tail += ["You are the successor for this role. If another live session already holds this role, stop and ask the founder.",
             AUTHORITY]
    return "\n".join(head + body + tail)


def _choose(opened, roles, refused, root, now):
    lines = ["[Cold start] CHOOSE — %d open handoffs here (%s). Do not pick one yourself."
             % (len(opened), ", ".join(sorted(set(_label(r.project, root) for r in opened))))]
    for i, r in enumerate(opened[:MAX_LIST], 1):
        lines.append("  %d. %s — %s — stamp %s — %s [%s]" % (i, r.role, r.served.name, _stamp(r.stamp), _title(r),
                                                             _label(r.project, root)))
    if len(opened) > MAX_LIST:
        lines.append("  … and %d more open roles." % (len(opened) - MAX_LIST))
    lines.append("Ask the founder which role this session takes (one question, newest first; the founder may also type "
                 "\"cold start <role>\"). Then %s, Read that file in full and follow its \"## Cold start\" section." % PREFLIGHT)
    lines += _closed_recent(roles, now) + _refused_lines(roles, refused) + [AUTHORITY]
    return "\n".join(lines)


def _none(chain, roles, refused, cwd, root, now):
    if not chain:
        return ("[Cold start] NONE — this folder is not a NEOCORTEX project (no NEOCORTEX/ at or above %s within the "
                "workspace). Ask the founder what this session is for; handoffs live in each project's NEOCORTEX/." % _clean(cwd, 200))
    labels = ", ".join(_label(p, root) + "/NEOCORTEX" for p in chain)
    n = sum(len(r.entries) for r in roles)
    lines = ["[Cold start] NONE — no open handoff in the convention under %s (%s: %d file%s%s)."
             % (labels, CONVENTION, n, "" if n == 1 else "s", ", 0 open" if n else "")]
    cands = legacy_candidates(chain, root)
    if cands:
        lines.append("Older-format handoffs (not in the convention; newest first; never served): "
                     + " · ".join("%s (%s)" % (p, _local(t)) for t, p in cands) + ".")
        lines.append("Tell the founder that no handoff in the convention is open here, show these, and ask which one to read. "
                     "Do not choose one yourself.")
    else:
        lines.append("No older-format handoff file either. Tell the founder no handoff exists here and ask what this session is for.")
    lines += _closed_recent(roles, now) + _refused_lines(roles, refused)
    return "\n".join(lines)


def _closed(r, root):
    return ('[Cold start] CLOSED — role "%s" in %s/ was closed by %s (line 1: %s). Nothing to resume. Ask the founder what '
            "this session is for." % (r.role, _label(r.project, root), r.served.path, _clean(r.status_line) or "under _archive/"))


def _not_found(token, chain, roles, root):
    if not chain:
        return ('[Cold start] NOT-FOUND — no role "%s": this folder is not a NEOCORTEX project. If the founder meant a cold '
                "start, ask the founder which project and role; otherwise treat the prompt as ordinary." % token)
    known = ", ".join("%s (%s)" % (r.role, "open" if r.open else "closed") for r in roles[:MAX_LIST]) or "none"
    return ('[Cold start] NOT-FOUND — no role "%s" in %s. Roles here: %s. If the founder meant a cold start, ask the founder which role; '
            "otherwise treat the prompt as ordinary." % (token, ", ".join(_label(p, root) for p in chain), known))


def _bounded(render, *args):
    """§10.11: the block never exceeds BLOCK_CAP bytes; the Cold start quote is what shrinks."""
    budget = COLD_CAP
    text = render(*args, budget=budget)
    while _blen(text) > BLOCK_CAP and budget > 200:
        budget -= _blen(text) - BLOCK_CAP + 64
        text = render(*args, budget=max(budget, 200))
    return _cut(text, BLOCK_CAP)


def answer(cwd, root, trigger, now=None):
    """(kind, text) for a trigger in folder `cwd` of workspace `root` (I2)."""
    now = _now(now)
    chain = project_chain(cwd, root)
    roles, refused = scan(chain, now)
    for r in roles:
        if r.served is None:                                    # every file of the role was refused: report, do not serve
            refused.extend(r.refused)
    roles = [r for r in roles if r.served is not None]
    if trigger[0] == "role":
        match = [r for r in roles if r.role == trigger[1]]
        if not match:
            return "NOT-FOUND", _not_found(trigger[1], chain, roles, root)
        opened = [r for r in match if r.open]
        if len(opened) == 1:
            return "RESOLVED", _bounded(_resolved, opened[0], roles, refused, root, now)
        if opened:
            return "CHOOSE", _choose(opened, roles, refused, root, now)
        return "CLOSED", _closed(match[0], root)
    opened = [r for r in roles if r.open]
    if len(opened) == 1:
        return "RESOLVED", _bounded(_resolved, opened[0], roles, refused, root, now)
    if opened:
        return "CHOOSE", _cut(_choose(opened, roles, refused, root, now), BLOCK_CAP)
    return "NONE", _cut(_none(chain, roles, refused, cwd, root, now), BLOCK_CAP)


# --- closing side: check and name ---------------------------------------------------------------------------------------

class ManifestError(Exception):
    """A MANIFEST file that exists but cannot be used; the message names the file and the fault."""


def _manifest_rows(nc):
    """Top-level names covered by a row: a `file` value with no directory part (s508 F1: `sub/X.md` does not cover X.md), in
    MANIFEST.json files[] / archive.rows[] and _archive/MANIFEST_ARCHIVE.json rows[]. Raises FileNotFoundError when MANIFEST.json
    is absent, ManifestError (naming the file) when either file is unreadable or a row list is not a list (s508 F2, F7)."""
    import json
    names = set()
    for rel, keys in (("MANIFEST.json", (("files",), ("archive", "rows"))), (os.path.join("_archive", "MANIFEST_ARCHIVE.json"), (("rows",),))):
        p = os.path.join(nc, rel)
        if not os.path.lexists(p):
            if rel == "MANIFEST.json":
                raise FileNotFoundError(p)
            continue
        try:
            doc = json.loads(safe_read(p).decode("utf-8", errors="replace").lstrip("\ufeff"))
        except Exception as exc:                                # noqa: BLE001 — symlink refused, bad JSON, vanished, too deep
            raise ManifestError("%s unreadable (%s)" % (rel, _refusal(exc)))
        if not isinstance(doc, dict):
            raise ManifestError("%s is not a JSON object" % rel)
        for path_keys in keys:
            o = doc
            for k in path_keys:
                o = o.get(k) if isinstance(o, dict) else None
            if o is None:
                continue
            if not isinstance(o, list):
                raise ManifestError("%s %s[] is not a list" % (rel, ".".join(path_keys)))
            for row in o:
                if isinstance(row, dict) and isinstance(row.get("file"), str) and "/" not in row["file"] and "\\" not in row["file"]:
                    names.add(row["file"])
    return names


def manifest_gaps(nc):
    """Problems: each top-level NEOCORTEX/*.md (standing files excepted) with no MANIFEST row, or the MANIFEST unreadable.
    Names are compared exactly (case-sensitive, as on disk; s508 F6). A symlinked .md that resolves to a file counts like a file:
    it sits in the folder and a reader meets it there (s508 F5)."""
    try:
        rows = _manifest_rows(nc)
    except FileNotFoundError:
        return ["no MANIFEST.json in %s: create it (NEOCORTEX_SPEC §2), with one files[] row per top-level .md" % nc]
    except ManifestError as exc:
        return ["%s: fix it, then run check again" % exc]
    out = []
    for n in sorted(os.listdir(nc)):
        if n.endswith(".md") and n not in COVERAGE_EXCLUDE and n not in rows and os.path.isfile(os.path.join(nc, n)):
            out.append("no MANIFEST row for %s: add a files[] row {\"file\", \"genre\", \"status\", \"hook\"} to "
                       "NEOCORTEX/MANIFEST.json" % n)
    return out


def check_file(path, now=None):
    """§10.5: problems with the handoff the closer just wrote ([] = conforming)."""
    now = _now(now)
    name = os.path.basename(path)
    k = _classify_name(name)
    if k is None:
        return ["name does not follow %s (role: lowercase letters, digits, single hyphens; stamp in UTC)" % CONVENTION]
    if k[0] == "invalid":
        return ["invalid stamp in the name"]
    role, stamp = k[1], k[2]
    if stamp > now + FUTURE:
        return ["stamp in the future (read the clock: `date -u +%Y-%m-%d_%H%M`)"]
    problems = []
    nc = os.path.dirname(os.path.abspath(path))
    if os.path.basename(nc) != "NEOCORTEX":
        problems.append("not at the top level of a NEOCORTEX/ folder")
    try:
        data = safe_read(path)
    except Exception as exc:                                    # noqa: BLE001
        return problems + ["unreadable: %s" % _refusal(exc)]
    text = data.decode("utf-8", errors="replace").lstrip("﻿")
    lines = _first_lines(text, 3) + ["", ""]
    if not CHECK_STATUS_RE.match(lines[0]):
        problems.append("line 1 must be `Status: ACTIVE` or `Status: DONE`")
    if not lines[1].startswith("# HANDOFF"):
        problems.append("line 2 must be the `# HANDOFF — <role> — \"<session title>\" [<id>] — <local time>` title")
    if os.path.basename(nc) == "NEOCORTEX":
        by_role, _ = scan_names(nc, now)
        same = by_role.get(role, [])
        newer = [e for e in same if e.stamp > stamp]
        older = [e for e in same if e.stamp < stamp]
        if newer:
            problems.append("not the role's newest handoff: %s is newer" % newer[0].name)
        want = older[0].name if older else "none"
        sup = _supersedes(text)
        if sup is None:
            problems.append("no `Supersedes:` line (line 3; `name` prints it)")
        elif os.path.basename(sup) != want:
            problems.append("Supersedes names \"%s\", but the role's previous handoff is %s: another session closed this role in "
                            "between; merge its open items, then fix line 3" % (_clean(sup, 80), want))
    m = CHECK_STATUS_RE.match(lines[0])
    if m and m.group(1) == "ACTIVE":
        for title, rx in SECTIONS:
            if _section(text, rx) is None:
                problems.append("missing section: ## %s" % title)
        cold = _section(text, SECTIONS[0][1])
        if cold is not None and _blen(cold) > COLD_CAP:
            problems.append("Cold start section is %d B, over the 4,000 B cap" % _blen(cold))
        state = _section(text, SECTIONS[1][1])
        if state is not None and stamp >= STATE_TIME_FROM:
            for ln in state.split("\n"):
                if ln.startswith(("- ", "* ")) and not CLOCK_RE.search(ln):
                    problems.append("State at close bullet with no read time (HH:MM): %s" % _clean(ln, 80))
    if os.path.basename(nc) == "NEOCORTEX" and stamp >= MANIFEST_FROM:
        problems += manifest_gaps(nc)
    return problems


def check_dir(folder, now=None):
    """Audit every role of a project (or NEOCORTEX) folder: (problems, warnings)."""
    now = _now(now)
    nc = folder if os.path.basename(os.path.normpath(folder)) == "NEOCORTEX" else os.path.join(folder, "NEOCORTEX")
    by_role, refused = scan_names(nc, now)
    problems = ["%s: %s" % (os.path.basename(p), why) for p, why in refused]
    warnings = []
    for role, lst in sorted(by_role.items()):
        newest = lst[0]
        if newest.archived:
            continue
        problems += ["%s: %s" % (newest.name, m) for m in check_file(newest.path, now=now)]
        try:
            first = safe_read(newest.path).decode("utf-8", errors="replace").lstrip("﻿").split("\n", 1)[0]
        except Exception:                                       # noqa: BLE001 — already reported by check_file
            continue
        if not CLOSED_RE.match(first) and now - newest.stamp > RECENT:
            warnings.append("STALE-OPEN role %s: its newest handoff %s is %d days old; if the role has ended, close it with a "
                            "final `Status: DONE` handoff." % (role, newest.name, (now - newest.stamp).days))
    return problems, warnings


def make_name(role, cwd, root, project=None, new_role=False, now=None):
    """§2.2 / §10.7: (path to write, Supersedes value) for a closing session."""
    now = _now(now)
    if not SLUG_RE.match(role or ""):
        raise NameRefused(RC_USAGE, "role must be lowercase letters and digits with single hyphens, e.g. governance, finance-console")
    if project:
        proj = canonical(os.path.abspath(project))
        if not os.path.isdir(os.path.join(proj, "NEOCORTEX")):
            raise NameRefused(RC_NOPROJ, "%s has no NEOCORTEX/ folder" % proj)
    else:
        chain = project_chain(cwd, root)
        if not chain:
            raise NameRefused(RC_NOPROJ, "not a NEOCORTEX project here (%s): write the handoff in the project that owns this work; "
                                         "pass --project <dir>" % canonical(os.path.abspath(cwd)))
        proj = chain[0]
    nc = os.path.join(proj, "NEOCORTEX")
    by_role, _ = scan_names(nc, now)
    same = by_role.get(role, [])
    if not same and not new_role:
        known = ", ".join(sorted(by_role)) or "none yet"
        raise NameRefused(RC_FAIL, "no handoff for role '%s' yet in %s; existing roles: %s. Pass --new-role to start a new role."
                          % (role, nc, known))
    path = os.path.join(nc, "HANDOFF_%s_%sZ.md" % (role, now.strftime("%Y-%m-%d_%H%M")))
    if os.path.lexists(path):
        raise NameRefused(RC_FAIL, "%s already exists (same minute): wait a minute and run `name` again" % path)
    return path, (same[0].name if same else "none")


# --- role-status (PLAN_governance_charter_and_genesis_2026-11-19 P2a, amendment D-5) ----------------------------------------
# Authority comes from recorded events (handoff -> TAKEN OVER), activity is advisory. A takeover event is a JOURNAL line with
# TAKEN OVER, COLD START or "took over" that names one of the role's conforming handoffs (or "TAKEN OVER <role> from none" for a
# first holder); "TAKEN OVER received/sent" lines relay another session's takeover and are skipped. Its title is the writer field.
# Honest limit: an impersonated line is not detected; the founder's card is the enforcement (charter §8).

DRAFT_RE = re.compile(r"^DRAFT_HANDOFF_([a-z0-9]+(?:-[a-z0-9]+)*)_(\d{4}-\d{2}-\d{2})_(\d{4})Z?[^/]*\.md$")
JOURNAL_RE = re.compile(r"^JOURNAL(?:_[^/]*)?\.md$")
TAKE_RE = re.compile(r"TAKEN OVER|COLD START|\btook over\b|\bcold start (?:of|from|on)\b")   # case-sensitive: "Cold start 1.6 KB"
RELAY_RE = re.compile(r"TAKEN OVER\W{0,3}\s*(?:received|sent)\b", re.I)                      # is not an event
# VERIFY-GATE F1: an event is the writer's own act. Negated or future wording just before the keyword, or between it and the
# handoff name, makes the line a mention, not an event; the handoff must follow the keyword within NEAR characters.
NEG_BEFORE = re.compile(r"\b(?:not|no|nobody|no one|never|will|would|shall|should|say|says|said|asked|whether|wants?|expected|"
                        r"successor|next session|relayed|until|await(?:ed|ing)?)\b", re.I)
NEG_BETWEEN = re.compile(r"\b(?:expected|will|not|pending|until|await(?:ed|ing)?)\b", re.I)
NEAR = 120
# VERIFY-GATE round 2 (N1): positive conditions, not only a blocklist. (a) the keyword opens the writer's statement: between
# the title (or the line's stamp, for quoted-title forms) and the keyword only [..], (..), ":" and "*" may stand; (b) between
# the keyword and the handoff, once quotes, brackets, parentheses, backticks and holder titles are removed, only TAKE_WORDS
# remain; (c) no "?" or retraction word in the 30 characters after the handoff name.
TAKE_WORDS = set("by from on of role the a an resumed resumes resume holds held cold start handoff founder founder's at desktop "
                 "card in this chat session".split())
GROUPS_RE = re.compile(r"\[[^\[\]]*\]|\([^()]*\)|\"[^\"]*\"|\u201c[^\u201d]*\u201d|'[^']*'|`[^`]*`")
STAMP_RE = re.compile(r"^[-*#\s]*(?:\d{1,2}:\d{2}\s*(?:CET|CEST|EDT|EST|UTC)?\s*\(\d{4}-\d{2}-\d{2}\)\s*"   # PROJECT-A "- HH:MM CET (date)"
                      r"|(?:\d[\d:./-]*\s*)*(?:CET|CEST|EDT|EST|UTC|Z)?\s*)(?:[\u2014\u2013-]{1,2}\s*)?(?:\((?:after close|closed)\)\s*)?[*\s]*$")
NEG_AFTER = re.compile(r"^[^\n]{0,30}?(?:\?|\b(?:refused|failed|cancel\w*|denied|blocked|rejected|happened|undone|reverted)\b)", re.I)


def _strip_groups(s):
    prev = None
    while prev != s:
        prev, s = s, GROUPS_RE.sub(" ", s)
    return s


def _is_own_takeover(ln, k0, m, w, trx, role):
    """(a)-(c) above; True only for a line where the writer states its own takeover of the handoff m names."""
    bm = BY_RE.search(ln)
    if bm and bm.start() == k0.start():                          # quoted-title form: only a stamp may precede the keyword
        if not STAMP_RE.match(re.sub(r"\[[^\[\]]*\]", " ", ln[:k0.start()])):     # a stamp, maybe a [ref] tag
            return False
    else:
        i = ln.find(w)
        if i < 0 or i > k0.start():
            return False
        if re.sub(r"[\s:*(\[]", "", _strip_groups(ln[i + len(w):k0.start()])):
            return False
    between = _strip_groups(ln[k0.end():m.start()])
    words = [x for x in re.split(r"[\s:;,.*\u2014\u2013(\[-]+", trx.sub(" ", re.sub(_title_any(role), " ", between))) if x]
    if any(x.lower() not in TAKE_WORDS and x.lower() not in role.split("-") for x in words):
        return False
    rest = ln[m.end():]
    rest = rest[3:] if rest.startswith(".md") else rest
    return not NEG_AFTER.match(rest)


def _title_any(role):
    return r"(?i)\b%s \d{1,2} [a-z]{3}(?: - \d+)?\b" % r"[- ]".join(re.escape(x) for x in role.split("-"))
ENTRY_RE = re.compile(r"^(?:[-*]|#{1,4})\s|^\d{1,4}[-:]\d")             # JOURNAL entries (bullet, heading or leading stamp) only
# a quoted title right after the keyword ("TAKEN OVER by "X"", "TAKEN OVER governance: 'X'", "COLD START "X""), never after "from"
BY_RE = re.compile(r"(?:TAKEN OVER|COLD START)(?:(?!from)[^\"“'\n]){0,25}[\"“']([^\"”'\n]{1,80})[\"”']")
DASH_RE = re.compile(r"\s[—–-]{1,2}\s")
NOTE_DRAFT = "draft present; a cold-start successor still stops and asks (PROTOCOL 99-102)"


def _writer(line):
    """The session title that wrote a JOURNAL line, or None."""
    m = BY_RE.search(line)
    if m:
        return _clean(m.group(1), 80)
    k = TAKE_RE.search(line)
    head = line[:k.start()] if k else line
    d = DASH_RE.search(head)
    if not d:
        return None
    w = head[d.end():]
    w = re.sub(r"^\((?:after close|closed)\)\s*", "", w.strip())
    for stop in (" [", ":", " (", " — "):
        i = w.find(stop)
        if i > 0:
            w = w[:i]
    w = w.strip().strip("\"'“”").strip()
    if not w or len(w) > 80 or TAKE_RE.match(w) or w.upper() == w:     # "FOUNDER", "SESSION CLOSE": not a session title
        return None
    return _clean(w, 80)


def _role_files(nc, role, errors):
    """(drafts [(stamp, path)], legacy [path]) of one role, top level + _archive/."""
    drafts, legacy = [], []
    pre = "HANDOFF_%s_" % role
    for d, _ in _dirs(nc):
        try:
            names = sorted(os.listdir(d))
        except OSError:
            errors.append((d, "listing failed"))
            continue
        for n in names:
            m = DRAFT_RE.match(n)
            if m and m.group(1) == role:
                try:
                    s = dt.datetime.strptime(m.group(2) + m.group(3), "%Y-%m-%d%H%M").replace(tzinfo=UTC)
                except ValueError:
                    s = None
                drafts.append((s, os.path.join(d, n)))
            elif n.startswith(pre) and n.endswith(".md") and not NAME_RE.match(n):
                legacy.append(os.path.join(d, n))
    return drafts, legacy


def _journal_lines(nc, errors):
    """Every JOURNAL volume (archived volumes by name, then the live file); a line seen in two volumes counts once."""
    vols = []
    for d, archived in _dirs(nc):
        try:
            names = sorted(os.listdir(d))
        except OSError:
            errors.append((d, "listing failed"))
            continue
        vols += [(0 if archived else 1, os.path.join(d, n)) for n in names if JOURNAL_RE.match(n)]
    vols.sort()
    seen, out = set(), []
    for _, path in vols:
        try:
            text = safe_read(path).decode("utf-8", errors="replace")
        except Exception as exc:                                # noqa: BLE001 — unreadable evidence is UNKNOWN, never skipped
            errors.append((path, _refusal(exc)))
            continue
        for ln in text.split("\n"):
            s = ln.strip()
            if s and s not in seen:
                seen.add(s)
                out.append(s)
    return out, len(vols)


def _title_rx(role):
    """VERIFY-GATE F2: a holder's title has the PROTOCOL shape `<Role> <d mon>[ - N]` (e.g. "Governance 19 nov - 3")."""
    return re.compile(r"^%s \d{1,2} [a-z]{3}\b" % r"[- ]".join(re.escape(w) for w in role.split("-")), re.I)


def _events(lines, role):
    """({handoff stem: [titles in order]}, [first-holder titles], [takeover-looking lines not counted], role mentions).
    A mention is any entry naming one of the role's handoffs or written under one of its titles (SUCCESSOR and next-session
    lines included, F6); mentions and uncounted lines keep a role from reading VACANT (F4)."""
    hrx = re.compile(r"(?<![A-Za-z_])HANDOFF_%s_\d{4}-\d{2}-\d{2}_\d{4}Z" % re.escape(role))   # never inside DRAFT_HANDOFF_
    nrx = re.compile(r"TAKEN OVER\s+(?:role\s+)?%s\s+from none\b|TAKEN OVER from none\b.*\b%s\b" % (re.escape(role), re.escape(role)))
    trx = _title_rx(role)
    took, first, blind, mentions = {}, [], [], 0
    for ln in lines:
        if not ENTRY_RE.match(ln):
            continue
        w = _writer(ln)
        titled = bool(w and trx.match(w))
        if hrx.search(ln) or titled:
            mentions += 1
        k0 = TAKE_RE.search(ln)
        if not k0 or RELAY_RE.match(ln, k0.start()):                     # the line's event is a relay of someone else's
            continue
        if NEG_BEFORE.search(ln[max(0, k0.start() - 40):k0.start()]):
            blind.append(ln)
            continue
        if nrx.search(ln):
            (first if titled else blind).append(w if titled else ln)
            continue
        m = hrx.search(ln, k0.end())
        if not m:
            continue
        if (m.start() - k0.end() > NEAR or NEG_BETWEEN.search(ln[k0.end():m.start()]) or not titled
                or not _is_own_takeover(ln, k0, m, w, trx, role)):
            blind.append(ln)
            continue
        lst = took.setdefault(m.group(0), [])
        if w not in lst:
            lst.append(w)
    return took, first, blind, mentions


def _uniq(xs):
    out = []
    for x in xs:
        if x not in out:
            out.append(x)
    return out


def role_status(role, cwd, root, project=None, now=None):
    """The state of one role from the record: dict(state, title, rule, project, newest, lines). Never writes."""
    now = _now(now)
    if not SLUG_RE.match(role or ""):
        raise NameRefused(RC_USAGE, "role must be lowercase letters and digits with single hyphens")
    if project:
        proj = canonical(os.path.abspath(project))
        if not os.path.isdir(os.path.join(proj, "NEOCORTEX")):
            raise NameRefused(RC_NOPROJ, "%s has no NEOCORTEX/ folder" % proj)
    else:
        chain = project_chain(cwd, root)
        if not chain:
            raise NameRefused(RC_NOPROJ, "not a NEOCORTEX project here (%s); pass --project <dir>" % canonical(os.path.abspath(cwd)))
        proj = chain[0]
    nc = os.path.join(proj, "NEOCORTEX")
    errors = []
    by_role, refused = scan_names(nc, now, errors)
    H = by_role.get(role, [])
    mine = [(p, why) for p, why in refused if os.path.basename(p).startswith("HANDOFF_%s_" % role)]
    drafts, legacy = _role_files(nc, role, errors)
    lines, nvol = _journal_lines(nc, errors)
    took, first, blind, mentions = _events(lines, role)
    names = set(e.name[:-3] for e in H)
    r = {"project": proj, "role": role, "state": None, "title": None, "rule": None, "newest": None, "notes": []}
    ev = ["evidence: %d handoff(s), %d legacy-named, %d draft(s), %d JOURNAL volume(s), %d takeover event(s)%s"
          % (len(H), len(legacy), len(drafts), nvol, sum(len(v) for v in took.values()) + len(first),
             ", %d unattributed" % len(blind) if blind else "")]

    def done(state, rule, title=None, why=None):
        r.update(state=state, rule=rule, title=title)
        if why:
            r["notes"].insert(0, why)
        r["notes"] += ev
        return r

    newest, status = (H[0] if H else None), ""
    if errors:
        return done("UNKNOWN", 1, why="unreadable evidence: " + "; ".join("%s (%s)" % (os.path.basename(p) or p, w)
                                                                          for p, w in errors[:MAX_WARN]))
    if newest is not None:
        try:
            newest.data = safe_read(newest.path)
        except Exception as exc:                                # noqa: BLE001 — never fall back to an older file
            return done("UNKNOWN", 1, why="newest handoff %s refused (%s)" % (newest.name, _refusal(exc)))
        r["newest"] = newest.path
        status = _first_lines(_text(newest), 1)[0]
        r["notes"].append("newest handoff: %s (%s)" % (newest.name, _clean(status, 60)))
    titles = _uniq(took.get(newest.name[:-3], [])) if newest else []
    dated = [s for s, _ in drafts if s is not None]
    newer_draft = bool(newest and dated and max(dated) > newest.stamp)
    if drafts and not H:
        return done("CONFLICT", 2, why="draft handoff with no handoff: its writer may hold the role (PROTOCOL 99-102)")
    if newer_draft and not titles:
        return done("CONFLICT", 2, why="a draft is newer than %s and no takeover of it is recorded (PROTOCOL 99-102)" % newest.name)
    if len(titles) >= 2:
        return done("CONFLICT", 2, why="two titles took over %s: %s (PROTOCOL 91)" % (newest.name, ", ".join(titles)))
    fr = _uniq(first)
    if not H and not legacy and not drafts and not mine:
        if not took and not fr and not blind and not mentions:
            return done("VACANT", 3, why="no handoff, draft, legacy file, takeover or JOURNAL mention of role %s here (a typo in "
                                         "the role or --project also gives this)" % role)
        if len(fr) >= 2 and not took:                           # D-5 note: two first holders = CONFLICT (as PROTOCOL 91)
            return done("CONFLICT", 2, why="two first holders recorded: %s" % ", ".join(fr))
        if len(fr) == 1 and not took:
            return done("HELD", 4, fr[0], why="first holder (TAKEN OVER from none)")
    if not H:
        return done("UNRESOLVED", 5, why="no conforming handoff, but %s" % ", ".join(
            x for x in ("legacy-named files" if legacy else "",
                        "journal events" if (took or fr or blind or mentions) else "",
                        "refused handoff names" if mine else "") if x))
    stray = sorted(k for k in took if k not in names)
    if stray:
        return done("UNRESOLVED", 5, why="a takeover names a handoff that is not on disk: %s" % ", ".join(stray[:3]))
    if mine:
        return done("UNRESOLVED", 5, why="refused handoff name(s): %s" % "; ".join("%s (%s)" % (os.path.basename(p), w) for p, w in mine[:3]))
    if len(H) > 1 and H[0].stamp == H[1].stamp:
        return done("UNRESOLVED", 5, why="the two newest handoffs carry the same stamp")
    if not CLOSED_RE.match(status) and not ACTIVE_RE.match(status):         # F3: archived or not
        return done("UNRESOLVED", 5, why="the newest handoff's Status line is not recognised")
    m = CLOSED_RE.match(status)
    if m and m.group(1) in ("SUPERSEDED", "ARCHIVED"):
        return done("UNRESOLVED", 5, why="the newest handoff is %s" % m.group(1))
    if newest.archived or m:                                    # archived + ACTIVE = CLOSED, as the resolver (D-5 note)
        return done("CLOSED", 6, why="newest handoff %s" % ("archived" if newest.archived else m.group(1)))
    if titles:
        if newer_draft:
            r["notes"].append(NOTE_DRAFT)
        seen = [ln for ln in lines if _writer(ln) == titles[0]]
        if seen:
            r["notes"].append("last seen (information only): " + _clean(seen[-1], 160))
        return done("HELD", 7, titles[0], why="%s took over %s" % (titles[0], newest.name))
    return done("OPEN", 8, why="no takeover of %s recorded; a holder that took over without recording it writes its TAKEN OVER "
                               "line" % newest.name)


def render_status(r, root):
    head = "ROLE-STATUS %s: %s" % (r["role"], r["state"]) + (' by "%s"' % r["title"] if r["title"] else "")
    out = [head, "project: %s/  (rule %d)" % (_label(r["project"], root), r["rule"])]
    out += ["- " + _clean(n, 240) for n in r["notes"]]
    if r["state"] != "HELD":
        out.append("A duty of role %s while it is not HELD: ask the founder (governance/GOVERNANCE_CHARTER.md §6)." % r["role"])
    return "\n".join(out)


# --- CLI ------------------------------------------------------------------------------------------------------------------

def _opts(argv, names, flags=()):
    out, pos, i = {}, [], 0
    while i < len(argv):
        a = argv[i]
        if a in flags:
            out[a] = True
            i += 1
        elif a in names:
            if i + 1 >= len(argv):
                raise UsageError(a)
            out[a] = argv[i + 1]
            i += 2
        elif a.startswith("--"):
            raise UsageError(a)
        else:
            pos.append(a)
            i += 1
    return out, pos


def _timeout(signum, frame):
    raise TimeoutError("deadline")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        sys.stderr.write(USAGE)
        return RC_USAGE
    cmd, rest = argv[0], argv[1:]
    try:
        if cmd in ("hook", "resolve"):
            o, pos = _opts(rest, ("--root", "--cwd", "--role"))
            if pos:
                raise UsageError("positional")
            root = o.get("--root") or default_root()
            cwd = o.get("--cwd") or os.getcwd()
            if cmd == "hook":
                trigger = parse_trigger(os.environ.get("_CS_PROMPT", ""))
                if trigger is None:
                    return RC_OK
            else:
                role = o.get("--role")
                trigger = ("role", role) if role else ("bare", None)
            if hasattr(signal, "SIGALRM"):
                signal.signal(signal.SIGALRM, _timeout)
                signal.alarm(DEADLINE)
            try:
                _, text = answer(cwd, root, trigger)
            finally:
                if hasattr(signal, "SIGALRM"):
                    signal.alarm(0)
            sys.stdout.write(text + "\n")
            return RC_OK
        if cmd == "check":
            o, pos = _opts(rest, ("--file",))
            if "--file" in o and not pos:
                problems = check_file(o["--file"])
                for m in problems:
                    print("FAIL %s: %s" % (o["--file"], m))
                if not problems:
                    print("OK %s" % o["--file"])
                return RC_FAIL if problems else RC_OK
            if len(pos) == 1 and "--file" not in o:
                problems, warnings = check_dir(pos[0])
                for m in problems:
                    print("FAIL " + m)
                for w in warnings:
                    print(w)
                if not problems:
                    print("OK %s" % pos[0])
                return RC_FAIL if problems else RC_OK
            raise UsageError("check")
        if cmd == "name":
            o, pos = _opts(rest, ("--root", "--cwd", "--project"), flags=("--new-role",))
            if len(pos) != 1:
                raise UsageError("name")
            try:
                path, sup = make_name(pos[0], o.get("--cwd") or os.getcwd(), o.get("--root") or default_root(),
                                      project=o.get("--project"), new_role=bool(o.get("--new-role")))
            except NameRefused as exc:
                sys.stderr.write("handoff_resolve name: %s\n" % exc)
                return exc.code
            print(path)
            print("Supersedes: %s" % sup)
            return RC_OK
        if cmd == "role-status":
            o, pos = _opts(rest, ("--root", "--cwd", "--project"))
            if len(pos) != 1:
                raise UsageError("role-status")
            root = o.get("--root") or default_root()
            try:
                r = role_status(pos[0], o.get("--cwd") or os.getcwd(), root, project=o.get("--project"))
            except NameRefused as exc:
                sys.stderr.write("handoff_resolve role-status: %s\n" % exc)
                return exc.code
            print(render_status(r, root))
            return RC_OK
        raise UsageError(cmd)
    except UsageError:
        sys.stderr.write(USAGE)
        return RC_USAGE
    except BaseException as exc:                                # noqa: BLE001 — fail loud, leak no content
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        sys.stderr.write("handoff_resolve %s failed: %s\n" % (cmd, type(exc).__name__))
        return RC_FAIL


if __name__ == "__main__":
    sys.exit(main())

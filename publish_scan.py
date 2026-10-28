#!/usr/bin/env python3.12
"""publish_scan.py — the §4 pre-publish hard gate, as code.

PROTOCOL_publish_regime §4 requires a PII/secrets scan before ANY public push, with
ZERO hits, "because public history is forever". Until 2026-08-25 that gate lived only
as a paragraph telling a human to run some greps — the exact prose-without-mechanism
shape this regime spent nine review rounds learning not to trust. This is the gate.

It scans only what git would actually publish (`git ls-files -co --exclude-standard`),
because the previous by-hand run inflated its own result by reading two gitignored
worktrees and reported 17 hits where the publish set had 3.

DECLARED EXCEPTIONS are the point of `EXCEPTIONS` below: a hard gate with no way to
record an intentional, reviewed hit gets switched off the first time it is inconvenient.
Each exception names the file, the pattern, and why — and anything NOT listed fails.

DECLARED LIMITS (stated, not hidden; round 3, 2026-10-09). The gate does not see: a private term that matches only the Latin-1
reading of a file that is valid UTF-8 (a mojibake term); a filename that is not valid UTF-8 (the working tree of a macOS volume
cannot hold one; a hand-built history can); an identity-class string placed on the same source line as, but outside, the closing
brace of its own PATTERNS / EXCEPTIONS table. Each was found by an external look and left open on the founder's word (option C).

Exit 0 = clean (or only declared exceptions). Exit 1 = blocked — including whenever some
part of the publish set could not be read: "skipped" and "clean" must never look the same.
"""
from __future__ import annotations
import ast, bisect, os, re, stat, subprocess, sys, pathlib

PATTERNS = {
    # --- identity / location ---
    "absolute-home-path":  r"/Users/|/home/[a-z]",
    "owner-name":          r"Dewost",
    # The author's contact handle, private folder name and phone prefix are NOT listed here (2026-10-28, external
    # review MED-2): a published scanner must not carry the strings it protects. They live in the untracked terms
    # file with the codenames (see _codename_pattern below), which also feeds the identity and message checks.
    # Project-specific codenames are NOT hardcoded here. See _codename_pattern() below:
    # a published leak-scanner whose denylist contains the very strings it protects is
    # itself the leak, and this tool excludes its own file from scanning, so it could not
    # see itself doing it. Terms live in an untracked local file instead.
    # Some terms must be matched case-SENSITIVELY: when a codename's lowercase form is
    # ordinary vocabulary or a real identifier, case-folding it cries wolf on legitimate
    # code and prose. Mark those with the 'cs:' prefix in the terms file.
    "local-database":      r"antigravity\.db|PROJECT-B_bill|\.sqlite\b",

    # --- CREDENTIALS. Added 2026-08-25 after the §5.5 panel defeated this gate.
    # The original seven patterns covered paths, names and codenames and NOTHING else:
    # an injected AWS key, an sk-ant- key, a GitHub token, an RSA PRIVATE KEY block and a
    # MongoDB URI with an embedded password all passed, and the tool printed
    # "CLEAN — §4 gate passed. Safe to push." A gate tested only against the class its
    # author had in mind is not a gate; it is a rehearsal.
    "aws-key":             r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b",
    "anthropic-key":       r"\bsk-ant-[A-Za-z0-9_\-]{8,}",
    "openai-key":          r"(?<![A-Za-z0-9])sk-[A-Za-z0-9]{20,}",
    # Stripe uses UNDERSCORES (sk_live_/sk_test_), which \b-anchored patterns skip entirely.
    "stripe-key":          r"(?<![A-Za-z0-9])sk_(?:live|test)_[A-Za-z0-9]{16,}",
    "npm-token":           r"(?<![A-Za-z0-9])npm_[A-Za-z0-9]{20,}",
    "jwt":                 r"(?<![A-Za-z0-9])eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}",
    "aws-secret-key":      r"(?i)aws[_\-]?secret[_\-]?access[_\-]?key\s*[:=]\s*[\"\']?[A-Za-z0-9/+=]{40}",
    "azure-account-key":   r"AccountKey=[A-Za-z0-9/+=]{40,}",
    "github-token":        r"\bgh[pousr]_[A-Za-z0-9]{20,}\b",
    "slack-token":         r"\bxox[abprs]-[A-Za-z0-9\-]{10,}",
    "google-key":          r"\bAIza[0-9A-Za-z_\-]{35}\b",
    "private-key-block":   r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----",
    "credentialed-uri":    r"\b[a-zA-Z][a-zA-Z0-9+.\-]*://[^/\s:@]+:[^/\s@]+@",
    # NOT \b-anchored on the keyword. \b after an underscore is a no-op, and real .env names
    # are underscore-joined (STRIPE_SECRET=, DB_PASSWORD=, MY_API_KEY=) — a reviewer walked
    # every one of those past the first version of this pattern.
    "generic-secret-assign": r"(?i)(?:api[_\-]?key|secret|passwd|password|token|bearer)"
                             r"\s*[:=]\s*[\"\']?[A-Za-z0-9/+_\-]{16,}",
}

# (path, pattern-name, exact-matched-line, why) — reviewed, intentional, not a leak.
#
# Keyed on (file, pattern, exact line text) — NOT on a line number, which would break on
# every edit above it. The honest limit, stated rather than glossed: if the identical line
# appears twice in the same file, one review licenses both. Reviewed 2026-08-25: no current
# exception text repeats. The line text is part of the key ON PURPOSE. Scoping to (file, pattern)
# alone waves through EVERY hit of that pattern in that file forever: the §5.5 panel
# appended a second, unrelated "Dewost" string to LICENSE and it passed silently under
# the copyright exception. An exception must license one reviewed occurrence, not a
# standing amnesty for a filename.
EXCEPTIONS = [
    ("LICENSE", "owner-name", "Copyright (c) 2026 Philippe Dewost",
     "A copyright notice must name its holder. Removing it would not protect anything "
     "and would make the licence unenforceable."),
    ("governance/scripts/continuity_sweep.py", "absolute-home-path",
     'ABS_ALLOWED_PREFIXES = ("/Users/", "/Volumes/", "/Applications/", "/opt/",',
     "Not a path to anywhere: it is the prefix list the sweep uses to RECOGNISE absolute "
     "paths. Same class as this scanner matching its own PATTERNS table."),
    # --- Already-published history. These four live in commits that predate this gate and
    # are reviewed, benign, and unfixable by editing a file. Two are RULES that name the very
    # patterns they forbid; two are the copyright notice in its older form.
    ("ANTIGRAVITY.md", "absolute-home-path",
     "- **Path Assumptions**: Never hardcode paths. Discover the project root, available "
     "tools, and writeable directories before executing. On Claude Code, respect "
     "mount-point restrictions (`/mnt/user-data/uploads` is read-only; work in "
     "`/home/claude`; deliver to `/mnt/user-data/outputs`).",
     "A rule FORBIDDING hardcoded paths, which necessarily cites the sandbox paths it "
     "describes. Generic to the harness, not to any machine."),
    ("ANTIGRAVITY.md", "local-database",
     "1. **`.gitignore` enforcement**: Every project with personal data MUST block at "
     "minimum: `storage/`, `data/`, `*.db`, `*.sqlite`, `BRAIN/`, `.env`, `logs/`, and any "
     "extract/cache directories. The `.gitignore` is part of Code and MUST be committed.",
     "A rule REQUIRING these extensions be gitignored. Naming them is the point."),
    ("LICENSE", "owner-name", "Copyright 2026 Philippe Dewost",
     "The copyright notice in its pre-2026-08 form, in already-published history."),
    ("NOTICE", "owner-name", "Copyright 2026 Philippe Dewost",
     "As above, in NOTICE."),
    ("governance/scripts/continuity_sweep.py", "local-database",
     '".md", ".py", ".json", ".sqlite", ".db", ".sh", ".js", ".ts", ".yaml",',
     "A list of file extensions the sweep recognises, not a database filename."),
    # v0.13 (2026-10-27): a synthetic canary in a test proving that hostile files never leak their content.
    ("adapters/claude-code/test_anchor_render.py", "generic-secret-assign", 'secret = "SECRET-' 'TOKEN-5f1c9d2e"',   # split so this table line is not itself a hit
     "A made-up token the test plants and then asserts never appears in output. It opens nothing."),
]

# Identity/message pattern classes that are public by declaration (2026-10-28): the author's name, as the copyright
# holder named in LICENSE. Every other class found in a commit identity or message blocks.
ID_ALLOWED_CLASSES = frozenset({"owner-name"})

# The scanner's own path, repo-relative and EXACT. It is exempt from its PATTERNS (every
# pattern is, by construction, an example of what it hunts) and from nothing else. Not by
# basename (any `sub/publish_scan.py` would ride along) and not by resolve() equality (a
# symlink alias of the scanner would): S4 re-checks, 2026-10-08 and 2026-10-09.
SELF = "publish_scan.py"

# Round 2 (2026-10-09, after the s4h look). The scanner's own file used to be exempt from EVERY credential and identity pattern:
# a key appended to it passed. Now it is exempt from the IDENTITY-class patterns only, and only on lines that belong to its own
# PATTERNS / EXCEPTIONS tables (see _self_exempt_lines); credentials, private terms and identity strings anywhere else in it block.
IDENTITY_CLASS = frozenset({"absolute-home-path", "workspace-path", "owner-name", "contact-handle", "phone", "local-database"})
SELF_TABLES = ("PATTERNS", "EXCEPTIONS")

# Git objects are read as they are, never through `refs/replace/`: a replacement can show the scanner a clean blob where the
# commit being published holds a key (F3). Both the flag and the environment variable, so a config cannot undo it.
GIT = ["git", "--no-replace-objects"]
GIT_ENV = {**os.environ, "GIT_NO_REPLACE_OBJECTS": "1"}


def publish_set(root: pathlib.Path) -> list[str]:
    """Repo-relative paths git would publish. `-z`: without it git QUOTES any path with a
    non-ASCII or control character, and `root / '"caf\\303\\251.md"'` names no file — the
    entry was skipped unread (hardening, 2026-10-09)."""
    out = subprocess.run([*GIT, "ls-files", "-z", "-co", "--exclude-standard"],
                         cwd=root, capture_output=True, check=True, env=GIT_ENV).stdout
    return [os.fsdecode(p) for p in out.split(b"\0") if p]


def _views(data: bytes) -> list[str]:
    """Every reading of `data` the scan must clear — a hit in ANY of them is a hit.

    The old reader took the first encoding that did not raise: utf-8, then utf-16, then
    latin-1. But utf-16 'succeeds' on almost any even-length input, so one Latin-1 byte
    turned a file's ASCII keys into noise and the file read as clean. Wide encodings get
    their own views (and a NUL-stripped one, which reads ASCII in UTF-16 or UTF-32 alike)."""
    try:
        views = [data.decode("utf-8")]
    except UnicodeDecodeError:
        views = [data.decode("utf-8", "replace"), data.decode("latin-1")]
    # UTF-16 in both byte orders and at both alignments, ALWAYS: a BOM-less non-ASCII term has no NUL and no BOM, so a
    # heuristic that waits for either never reads it (round 2, F2).
    for off in (0, 1):
        views += [data[off:].decode("utf-16-le", "replace"), data[off:].decode("utf-16-be", "replace")]
    for off in (0, 1, 2, 3):                       # UTF-32: a non-ASCII term leaves no ASCII once NULs are stripped (round 3, L2)
        views += [data[off:].decode("utf-32-le", "replace"), data[off:].decode("utf-32-be", "replace")]
    if b"\x00" in data:
        views.append(data.replace(b"\x00", b"").decode("utf-8", "replace"))
    return views


def _self_exempt_lines(text: str) -> set[int]:
    """Line numbers (1-based) of the scanner's own PATTERNS and EXCEPTIONS tables, plus the comment lines directly above each.
    A source that does not parse exempts nothing: the gate then reports every pattern example in it, and blocks."""
    try:
        tree = ast.parse(text[1:] if text.startswith("\ufeff") else text)   # a BOM changes no line number
    except (SyntaxError, ValueError):
        return set()
    lines, out = text.split("\n"), set()
    for node in tree.body:
        targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, ast.AnnAssign) else []
        if any(isinstance(t, ast.Name) and t.id in SELF_TABLES for t in targets):
            first = node.lineno
            while first > 1 and lines[first - 2].lstrip().startswith("#"):
                first -= 1
            out.update(range(first, node.end_lineno + 1))
    return out


def _scan_text(rel: str, text: str, patterns: dict, allowed: set,
               self_lines: set | None = None) -> list[tuple[int, str, str]]:
    """(line, pattern, snippet) for every undeclared hit in `text`, which belongs to `rel`.

    A LINE is what lies between two '\\n' — git's notion, and the only one used here, for
    the match position and for the text an exception is compared with alike. The old code
    counted '\\n' to place a match but read the line back with splitlines(), which also
    breaks on U+2028, U+2029, form feed, \\x1c-\\x1e, \\x85: after one of those a second key
    sat on 'line 1' and borrowed line 1's declared exception (S4s, 2026-10-09). A match that
    runs over a line end is compared with ALL the lines it touches, so an exception for one
    line cannot license a key that only matches together with the next one."""
    lines = text.split("\n")
    starts = [0]
    for ln in lines[:-1]:
        starts.append(starts[-1] + len(ln) + 1)
    out = []
    for name, pat in patterns.items():
        for m in re.finditer(pat, text):
            i = bisect.bisect_right(starts, m.start()) - 1
            j = bisect.bisect_right(starts, max(m.end() - 1, m.start())) - 1
            content = "\n".join(lines[i:j + 1]).strip()
            if (rel, name, content) in allowed:
                continue
            if self_lines and name in IDENTITY_CLASS and all(n in self_lines for n in range(i + 1, j + 2)):
                continue                           # the scanner's own tables name what they hunt
            out.append((i + 1, name, content[:90]))
    return out


def _scan_name(rel: str, patterns: dict, allowed: set) -> list[str]:
    """Pattern names that hit the PATH itself (F4: the path is content too)."""
    return [n for n, pat in patterns.items() if re.search(pat, rel) and (rel, n, rel) not in allowed]



TERMS_FILE = ".publish_scan_terms"


def _codename_pattern(root: pathlib.Path):
    """Build the private-codename patterns from an UNTRACKED local terms file.

    Why not a literal list in this file: this scanner is published, and it skips itself when
    scanning (its PATTERNS table is, by construction, full of examples of what it hunts). Put
    codenames in it and they ship — invisibly, because the one file that would have caught
    them is the one file it does not read. Found 2026-08-25 while preparing a history rewrite
    to remove a third party's surname, which was sitting in this tool's own denylist.

    Format, one per line, '#' comments allowed:
        ACMEPROJ             case-insensitive by default
        cs:SOMENAME          'cs:' prefix = case-SENSITIVE — use it when the term's
                             lowercase form is ordinary vocabulary or a real identifier,
                             or the scan will rewrite/flag legitimate code

    Absent file = generic patterns only, and main() then BLOCKS unless PUBLISH_SCAN_NO_PRIVATE_TERMS=1
    says there are none (an adopter: your codenames are not ours). A mislaid file must not pass for an
    empty one (external review, 2026-10-28, N-3). Keep the file untracked; .gitignore already lists it.
    """
    f = root / TERMS_FILE
    try:
        os.lstat(f)
    except FileNotFoundError:
        return {}                                  # genuinely absent: generic patterns only, as documented
    except OSError as e:
        raise Unreadable(f"{TERMS_FILE} cannot be examined ({e.strerror}): the private terms cannot be applied")
    try:
        if not f.is_file():
            raise Unreadable(f"{TERMS_FILE} exists but is not a regular file (a directory, or a dangling link?): "
                             "the private terms cannot be applied")
        text = f.read_text(encoding="utf-8-sig")       # an editor's BOM must not become part of the first term (round 3, L4)
    except (OSError, UnicodeDecodeError) as e:
        raise Unreadable(f"{TERMS_FILE} cannot be read as UTF-8 text ({type(e).__name__}): the private terms "
                         "cannot be applied")
    ci, cs = [], []
    for line in text.splitlines():
        line = line.split("#")[0].strip()
        if not line:
            continue
        (cs if line.startswith("cs:") else ci).append(re.escape(line[3:] if line.startswith("cs:") else line))
    pats = {}
    if ci:
        pats["private-codename"] = r"(?i)(?<![A-Za-z])(?:" + "|".join(ci) + r")(?![A-Za-z])"
    if cs:
        pats["private-codename-cs"] = r"(?<![A-Za-z])(?:" + "|".join(cs) + r")(?![A-Za-z])"
    return pats


def _allowed() -> set:
    """The declared-exception key set, shared by the tree scan and the history scan."""
    return {(f, p, line.strip()) for f, p, line, _ in EXCEPTIONS}


def _git(root: pathlib.Path, *args: str) -> str:
    """Run git, returning stdout; empty string on any failure (offline, no upstream, …)."""
    try:
        r = subprocess.run([*GIT, *args], cwd=root, capture_output=True, text=True, env=GIT_ENV)
        return r.stdout if r.returncode == 0 else ""
    except OSError:
        return ""


def _is_shallow(root: pathlib.Path) -> bool:
    """True when the repository lacks the ancestors of some commit (a depth-limited clone)."""
    if _git(root, "rev-parse", "--is-shallow-repository").strip() == "true":
        return True
    shallow = _git(root, "rev-parse", "--git-path", "shallow").strip()
    return bool(shallow) and (root / shallow).exists()


def _require_unborn(root: pathlib.Path) -> None:
    """HEAD does not resolve. That is an empty history ONLY for a branch that has no ref at all; a ref that exists (loose or
    packed) but will not resolve is damage, and an empty scan of it would read CLEAN (round 2, F5)."""
    ref = _git(root, "symbolic-ref", "-q", "HEAD").strip()
    if not ref:
        raise Unreadable("HEAD names no branch and resolves to no commit")
    loose = _git(root, "rev-parse", "--git-path", ref).strip()
    packed = _git(root, "rev-parse", "--git-path", "packed-refs").strip()
    if loose and os.path.lexists(root / loose):      # lexists: a DANGLING symlink is damage, not absence (round 3, L5)
        raise Unreadable(f"{ref} exists but does not resolve to a commit")
    if packed and (root / packed).is_file() and ref in (root / packed).read_text(errors="replace").split():
        raise Unreadable(f"{ref} is listed in packed-refs but does not resolve to a commit")


def _require_no_grafts(root: pathlib.Path) -> None:
    """Legacy grafts (`.git/info/grafts`, or the file `core.graftsFile` names) rewrite the parents git walks: a graft can make a
    reachable ancestor invisible, and `--no-replace-objects` does not switch it off (round 3, L1). Refuse to scan under one."""
    cfg = _git(root, "config", "--get", "core.graftsFile").strip()
    path = _git(root, "rev-parse", "--git-path", "info/grafts").strip()
    if cfg or (path and os.path.lexists(root / path)):
        raise Unreadable("this repository uses a grafts file (legacy parent rewriting): history cannot be shown complete. "
                         "Remove it (git replace --convert-graft-file, then delete the file) and run the gate again.")


def _gitlinks(root: pathlib.Path) -> set[str]:
    """Paths the index records as submodules (mode 160000): a directory there is expected, not a stray nested repository."""
    out = _git_bytes(root, "ls-files", "-z", "-s")
    links = set()
    for rec in out.split(b"\0"):
        meta, _, path = rec.partition(b"\t")
        if meta.startswith(b"160000"):
            links.add(os.fsdecode(path))
    return links


class Unreadable(RuntimeError):
    """Some part of what a push would publish could not be read: the gate cannot say CLEAN."""


def _git_bytes(root: pathlib.Path, *args: str, stdin: bytes | None = None) -> bytes:
    """Run git and return raw stdout. Unlike _git(), a failure RAISES: a history pass that
    quietly read nothing is the one result this gate must never turn into CLEAN."""
    r = subprocess.run([*GIT, *args], cwd=root, input=stdin, capture_output=True, env=GIT_ENV)
    if r.returncode != 0:
        raise Unreadable(f"git {args[0]} failed: {r.stderr.decode(errors='replace').strip()[:200]}")
    return r.stdout


def _history_entries(root: pathlib.Path, rng: list[str]) -> dict[tuple[str, str, str], str]:
    """{(path, mode, object id): first commit} for every entry of every commit's TREE in range.

    Why trees, not `git log -p` (hardening, 2026-10-09). The patch parser read the push as
    text, and text can lie: a content line starting with '++' printed as '+++ …' and was
    skipped as a file header; '++ b/publish_scan.py' WAS a file header, and borrowed the
    scanner's self-exemption for every line after it; splitlines() on the patch cut a line at
    U+2028 / CR / FF and the rest lost its '+'; a blob with a NUL printed only 'Binary files
    differ'; and a header carries a path but nobody checked it, so a deleted file's private
    NAME passed. Every snapshot a push publishes is a commit tree: read those, from plumbing
    that config cannot reshape, NUL-separated, and there is no text to be fooled by."""
    if rng == ["HEAD"] and not _git(root, "rev-parse", "--verify", "-q", "HEAD").strip():
        _require_unborn(root)
        return {}                                  # a branch with no commit yet: a push publishes nothing
    out = _git_bytes(root, "rev-list", "--reverse", "--format=%T", *rng).decode()
    trees: dict[str, str] = {}                     # root tree -> first commit carrying it
    commit = "?"
    for line in out.splitlines():
        if line.startswith("commit "):
            commit = line.split()[1]
        elif line:
            trees.setdefault(line.strip(), commit)
    entries: dict[tuple[str, str, str], str] = {}
    for tree, commit in trees.items():
        # -t: also the TREE entries, so an EMPTY directory (no file below it) is listed and its name is checked (F6)
        for rec in _git_bytes(root, "ls-tree", "-r", "-t", "-z", "--full-tree", tree).split(b"\0"):
            if not rec:
                continue
            meta, _, path = rec.partition(b"\t")
            mode, _type, oid = meta.decode().split()
            entries.setdefault((os.fsdecode(path), mode, oid), commit)
    return entries


def _read_blobs(root: pathlib.Path, oids: list[str]) -> dict[str, bytes]:
    """Content of each blob, through ONE `git cat-file --batch`. A missing object raises."""
    if not oids:
        return {}
    out = _git_bytes(root, "cat-file", "--batch", stdin="".join(o + "\n" for o in oids).encode())
    blobs, pos = {}, 0
    for oid in oids:
        nl = out.index(b"\n", pos)
        head = out[pos:nl].decode().split()
        if len(head) != 3 or head[1] != "blob":
            raise Unreadable(f"history object {oid} cannot be read: {' '.join(head)}")
        size = int(head[2])
        blobs[oid] = out[nl + 1:nl + 1 + size]
        pos = nl + 1 + size + 1                    # content, then a trailing '\n'
    return blobs


def scan_history(root: pathlib.Path, full: bool, patterns: dict,
                 allowed: set) -> list[tuple[str, str, str]]:
    """Scan commit CONTENT and commit PATHS, not just the working tree.

    A push publishes commits, not a snapshot. A secret added in one commit and edited out
    in a later commit within the same push is published permanently while a tree-only scan
    reports CLEAN — and this tool's own docstring justified itself with "public history is
    forever" while doing exactly that. Caught by the §5.5 panel, twice, independently.

    Default range is every commit reachable from HEAD. `--full-history` walks every
    reachable commit, which is what you want before a repository's first publish, or when
    auditing what is already out there. `own` = the patterns the scanner's own file is
    held to (the local private terms); `patterns` = everything, for every other path.
    """
    _require_no_grafts(root)
    if _is_shallow(root):                          # the commits below the boundary are not there to read (round 2, F4)
        raise Unreadable("this is a shallow repository: its older commits cannot be read, so history cannot be shown "
                         "clean. Fetch the full history (git fetch --unshallow) and run the gate again.")
    # DEFAULT = every commit reachable from HEAD, i.e. exactly what a push makes the remote
    # contain. NOT `--all`: that sweeps in remote-tracking refs, which are a local CACHE of
    # what is ALREADY published, not something this push creates. After a history rewrite the
    # old refs still sit there and made the gate block a demonstrably clean push — a gate that
    # cries wolf about a state you cannot change from here is a gate people learn to bypass.
    # NOT `upstream..HEAD` either: after a rewrite the histories are unrelated, and a
    # force-push republishes all of it, so the whole of HEAD is the honest scope.
    if full:
        rng, label = ["--all"], "all reachable commits (INCLUDING refs you are not pushing)"
    else:
        rng, label = ["HEAD"], "every commit reachable from HEAD (what a push publishes)"
    entries = _history_entries(root, rng)
    hits: list[tuple[str, str, str]] = []
    seen_names: set[str] = set()
    for (path, _mode, _oid), commit in entries.items():
        if path not in seen_names:
            seen_names.add(path)
            for name in _scan_name(path, patterns, allowed):
                hits.append((commit[:9], name, f"in the FILENAME: {path}"[:100]))
    # 100644/100755 = file, 120000 = symlink (the blob IS the target path, which is what a
    # symlink publishes), 160000 = submodule (a foreign commit id: nothing here to read).
    readable = {k: c for k, c in entries.items() if k[1] in ("100644", "100755", "120000")}
    for (path, mode, oid), commit in entries.items():
        if (path, mode, oid) not in readable and mode not in ("160000", "040000"):
            hits.append((commit[:9], "UNREADABLE", f"{path}: tree entry mode {mode}"[:100]))
    blobs = _read_blobs(root, sorted({k[2] for k in readable}))
    for (path, mode, oid), commit in readable.items():
        found = set()
        for k, v in enumerate(_views(blobs[oid])):
            ex = _self_exempt_lines(v) if (path == SELF and mode != "120000" and k == 0) else None
            found |= {(n, s) for _ln, n, s in _scan_text(path, v, patterns, allowed, ex)}
        for name, snippet in sorted(found):
            hits.append((commit[:9], name, f"{path}: {snippet}"[:100]))
    print(f"  history range scanned: {label}, {len(entries)} tree entr(ies)")
    return hits


def scan_tree(root: pathlib.Path, patterns: dict,
              allowed: set) -> tuple[list[tuple[str, int, str, str]], int]:
    """The working tree as git would publish it: every listed path is read, or the gate fails."""
    findings, scanned = [], 0
    gitlinks = _gitlinks(root)
    for rel in publish_set(root):
        # The NAME first, before any exemption or read: it is published whatever the content.
        for name in _scan_name(rel, patterns, allowed):
            findings.append((rel, 0, name, f"in the FILENAME: {rel}"))
        path = root / rel
        try:
            st = os.lstat(path)
            if stat.S_ISLNK(st.st_mode):
                # A symlink publishes its TARGET PATH, never the target's content: scan the
                # path. (is_file() followed it — a dangling link to a private path was
                # skipped, a link to a file outside the repo was read as if it shipped.)
                data = os.fsencode(os.readlink(path))
            elif stat.S_ISREG(st.st_mode):
                data = path.read_bytes()
            elif stat.S_ISDIR(st.st_mode) and rel in gitlinks:
                continue                           # a registered submodule: a foreign commit id, nothing here to read (F8)
            else:
                findings.append((rel, 0, "UNREADABLE", "not a regular file or a symlink "
                                 "(a nested repository?): it cannot be shown clean"))
                continue
        except FileNotFoundError:
            findings.append((rel, 0, "UNREADABLE", "listed by git but missing from the working "
                             "tree: commit the deletion or restore the file"))
            continue
        except OSError as e:
            findings.append((rel, 0, "UNREADABLE", f"cannot be read ({e.strerror}): it cannot "
                             "be shown clean"))
            continue
        scanned += 1
        found = set()
        for k, v in enumerate(_views(data)):
            ex = _self_exempt_lines(v) if (rel == SELF and stat.S_ISREG(st.st_mode) and k == 0) else None
            found |= set(_scan_text(rel, v, patterns, allowed, ex))
        findings += [(rel, ln, n, s) for ln, n, s in sorted(found)]
    return findings, scanned


def main() -> int:
    root = pathlib.Path(__file__).resolve().parent
    full_history = "--full-history" in sys.argv
    if _git(root, "rev-parse", "--show-prefix").strip():
        # ls-files lists only what lies under its cwd: from a subdirectory, the rest of the
        # repository would never be read.
        print("BLOCKED — publish_scan.py must sit at the root of the repository it scans.")
        return 1
    try:
        extra = _codename_pattern(root)
    except Unreadable as e:
        print(f"BLOCKED — {e}")
        return 1
    if not os.path.lexists(root / TERMS_FILE) and os.environ.get("PUBLISH_SCAN_NO_PRIVATE_TERMS") != "1":
        print(f"BLOCKED — no {TERMS_FILE}: the private terms (codenames, contact handle) are not applied, so CLEAN would"
              " prove nothing about them. Restore the file, or set PUBLISH_SCAN_NO_PRIVATE_TERMS=1 if you have none.")
        return 1
    PATTERNS.update(extra)
    print(f"publish_scan: {len(extra)} project-codename pattern(s) loaded from "
          f"{TERMS_FILE}" if extra else
          f"publish_scan: no {TERMS_FILE} found — generic patterns only")
    allowed = _allowed()
    # The scanner is exempt from nothing but the identity examples inside its own tables (cf. _self_exempt_lines);
    # a private term or a key in it would ship through the one file nobody read (cf. _codename_pattern).
    try:
        findings, scanned = scan_tree(root, PATTERNS, allowed)
    except subprocess.CalledProcessError as e:
        print(f"\nBLOCKED — the publish set cannot be listed (git exit {e.returncode}).")
        return 1
    except Unreadable as e:
        print(f"\nBLOCKED — the index cannot be read: {e}")
        return 1

    print(f"publish_scan: {scanned} file(s) in the publish set, "
          f"{len(PATTERNS)} pattern(s), {len(EXCEPTIONS)} declared exception(s)")
    for f, p, _line, why in EXCEPTIONS:
        print(f"  allowed: {f} [{p}] — {why}")
    # F9: commit AUTHOR/COMMITTER metadata is published too, and no content scan sees it. Same range as the history
    # scan (N-4, 2026-10-28): HEAD by default, --all only with --full-history. With --all, a local restore branch or
    # the old remote-tracking ref blocked the very push that rewrites them away.
    rng = ["--all"] if full_history else ["HEAD"]
    ident = _git(root, "log", *rng, "--pretty=format:%an <%ae>%n%cn <%ce>")
    idents = sorted({l.strip() for l in ident.splitlines() if l.strip()})
    id_hits = [(i, n) for i in idents for n, pat in PATTERNS.items() if re.search(pat, i)]
    # 2026-10-28 (external review HIGH-1/MED-2): identity hits BLOCK, except the classes declared public here. The
    # author's NAME is the declared copyright holder (see the LICENSE exception); any other identity hit blocks.
    id_block = [(i, n) for i, n in id_hits if n not in ID_ALLOWED_CLASSES]
    # Commit MESSAGES are published too and no tree scan reads them.
    msgs = _git(root, "log", *rng, "--format=%H%x00%B%x1e")
    msg_hits = []
    for rec in msgs.split("\x1e"):
        if "\x00" not in rec:
            continue
        sha, body = rec.strip("\n").split("\x00", 1)
        for n, pat in PATTERNS.items():
            m = re.search(pat, body)
            if m and n not in ID_ALLOWED_CLASSES:
                msg_hits.append((sha[:7], n, body[max(0, m.start() - 20):m.end() + 20].replace("\n", " ")))
    if idents:
        print(f"  commit identities in history: {len(idents)}")
        for i, n in id_hits:
            print(f"    [{n}] {i}   (metadata — a content scan never sees this)")

    try:
        hist = scan_history(root, full_history, PATTERNS, allowed)
    except (Unreadable, ValueError) as e:      # ValueError: a git reply that does not parse
        print(f"\nBLOCKED — history cannot be read, so it cannot be shown clean: {e}")
        return 1
    # History hits in ALREADY-PUSHED commits cannot be fixed by editing a file; they are
    # reported separately so they are never mistaken for something this run can clear.
    if hist:
        print(f"\nHISTORY — {len(hist)} hit(s) in commit content:")
        for commit, name, snippet in hist[:40]:
            print(f"  {commit}  [{name}]  {snippet}")
        if len(hist) > 40:
            print(f"  … and {len(hist) - 40} more")

    if id_block or msg_hits:
        for i, n in id_block:
            print(f"  BLOCKING identity [{n}] {i}")
        for sha, n, snip in msg_hits:
            print(f"  BLOCKING message {sha} [{n}] {snip}")
        print("\nBLOCKED — commit identities or messages carry undeclared hits. Re-author or reword the commits"
              " (a new candidate), or declare the class in ID_ALLOWED_CLASSES with a reason. Do not push.")
        return 1
    if not findings and not hist:
        print("\nCLEAN — §4 gate passed. Safe to push.")
        return 0
    if not findings:
        print("\nBLOCKED on history. Commits carry hits the working tree does not.")
        print("Editing a file cannot fix this: rewrite the history or accept and DECLARE"
              " the exposure in redaction_map.yaml.")
        return 1
    print(f"\nBLOCKED — {len(findings)} undeclared hit(s):")
    for rel, line, name, snippet in findings:
        print(f"  {rel}:{line}  [{name}]  {snippet}")
    print("\nFix the file, or add a reviewed entry to EXCEPTIONS with a reason. Do not push.")
    return 1

if __name__ == "__main__":
    sys.exit(main())

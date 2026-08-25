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

Exit 0 = clean (or only declared exceptions). Exit 1 = blocked.
"""
from __future__ import annotations
import re, subprocess, sys, pathlib

PATTERNS = {
    # --- identity / location ---
    "absolute-home-path":  r"/Users/|/home/[a-z]",
    "workspace-path":      r"Documents/Personnel",
    "owner-name":          r"Dewost",
    "contact-handle":      r"@phileos",
    "phone":               r"\+33\s?[0-9]",
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
]

def publish_set(root: pathlib.Path) -> list[pathlib.Path]:
    out = subprocess.run(["git", "ls-files", "-co", "--exclude-standard"],
                         cwd=root, capture_output=True, text=True, check=True).stdout
    return [root / line for line in out.splitlines() if line]



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

    Absent file = generic patterns only, which is the correct default for an adopter: your
    codenames are not ours. Keep the file untracked; .gitignore already lists it.
    """
    f = root / TERMS_FILE
    if not f.is_file():
        return {}
    ci, cs = [], []
    for line in f.read_text(encoding="utf-8").splitlines():
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
        r = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)
        return r.stdout if r.returncode == 0 else ""
    except OSError:
        return ""


def scan_history(root: pathlib.Path, full: bool) -> list[tuple[str, str, str]]:
    """Scan commit CONTENT, not just the working tree.

    A push publishes commits, not a snapshot. A secret added in one commit and edited out
    in a later commit within the same push is published permanently while a tree-only scan
    reports CLEAN — and this tool's own docstring justified itself with "public history is
    forever" while doing exactly that. Caught by the §5.5 panel, twice, independently.

    Default range is what THIS push would add (upstream..HEAD). `--full-history` walks every
    reachable commit, which is what you want before a repository's first publish, or when
    auditing what is already out there.
    """
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
    patch = _git(root, "log", "-p", "--no-color", *rng)
    hits: list[tuple[str, str, str]] = []
    if not patch:
        return hits
    # Track which file each hunk belongs to so this scanner can skip ITSELF. Without it the
    # tool matches its own PATTERNS table — every pattern is, by construction, an example of
    # the thing it looks for — and reports permanent false hits nobody can ever clear.
    self_name = pathlib.Path(__file__).name
    commit, current_file = "?", ""
    for line in patch.splitlines():
        if line.startswith("commit "):
            commit = line.split()[1][:9]
        elif line.startswith("+++ b/"):
            current_file = line[6:].strip()
        elif line.startswith("+") and not line.startswith("+++"):
            if pathlib.Path(current_file).name == self_name:
                continue
            body = line[1:]
            for name, pat in PATTERNS.items():
                if re.search(pat, body):
                    # Honour the SAME declared exceptions as the tree scan. A reviewed,
                    # documented non-leak must not be a leak merely because it is being
                    # read out of a commit instead of a file — two verdicts for one fact
                    # is how a gate teaches people to ignore it.
                    if (current_file, name, body.strip()) in _allowed():
                        continue
                    hits.append((commit, name, f"{current_file}: {body.strip()}"[:100]))
    print(f"  history range scanned: {label}")
    return hits

def main() -> int:
    root = pathlib.Path(__file__).resolve().parent
    full_history = "--full-history" in sys.argv
    extra = _codename_pattern(root)
    PATTERNS.update(extra)
    print(f"publish_scan: {len(extra)} project-codename pattern(s) loaded from "
          f"{TERMS_FILE}" if extra else
          f"publish_scan: no {TERMS_FILE} found — generic patterns only")
    allowed = _allowed()
    findings, scanned = [], 0
    for path in publish_set(root):
        if not path.is_file() or path.name == pathlib.Path(__file__).name:
            continue
        text = None
        for enc in ("utf-8", "utf-16", "latin-1"):
            try:
                text = path.read_text(encoding=enc)
                break
            except UnicodeDecodeError:
                continue                  # try the next encoding before giving up
            except OSError:
                break
        if text is None:
            # Genuinely binary. Note it rather than pass over it in silence: "skipped" and
            # "clean" must never look the same. A UTF-16 file full of keys reads perfectly
            # well to anyone who opens it with the right encoding (§5.5 re-review).
            print(f"  NOT SCANNED (undecodable): {path.relative_to(root)}")
            continue
        scanned += 1
        rel = str(path.relative_to(root))
        for name, pat in PATTERNS.items():      # F4: the PATH itself is content too
            if re.search(pat, rel) and (rel, name, rel) not in allowed:
                findings.append((rel, 0, name, f"in the FILENAME: {rel}"))
        for name, pat in PATTERNS.items():
            for m in re.finditer(pat, text):
                line = text[:m.start()].count("\n") + 1
                content = text.splitlines()[line - 1].strip()
                if (rel, name, content) in allowed:
                    continue
                findings.append((rel, line, name, content[:90]))

    print(f"publish_scan: {scanned} file(s) in the publish set, "
          f"{len(PATTERNS)} pattern(s), {len(EXCEPTIONS)} declared exception(s)")
    for f, p, _line, why in EXCEPTIONS:
        print(f"  allowed: {f} [{p}] — {why}")
    # F9: commit AUTHOR/COMMITTER metadata is published too, and no content scan sees it.
    ident = _git(root, "log", "--all", "--pretty=format:%an <%ae>%n%cn <%ce>")
    idents = sorted({l.strip() for l in ident.splitlines() if l.strip()})
    id_hits = [(i, n) for i in idents for n, pat in PATTERNS.items() if re.search(pat, i)]
    if idents:
        print(f"  commit identities in history: {len(idents)}")
        for i, n in id_hits:
            print(f"    [{n}] {i}   (metadata — a content scan never sees this)")

    hist = scan_history(root, full_history)
    # History hits in ALREADY-PUSHED commits cannot be fixed by editing a file; they are
    # reported separately so they are never mistaken for something this run can clear.
    if hist:
        print(f"\nHISTORY — {len(hist)} hit(s) in commit content:")
        for commit, name, snippet in hist[:40]:
            print(f"  {commit}  [{name}]  {snippet}")
        if len(hist) > 40:
            print(f"  … and {len(hist) - 40} more")

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

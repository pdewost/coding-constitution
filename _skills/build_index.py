#!/usr/bin/env python3
"""
_skills/build_index.py — skills discoverability index generator.

Walks _skills/*/SKILL.md, parses YAML frontmatter, counts real consumers
(multi-signal, false-positive-safe; see _build_consumer_map), and emits:
  _skills/index.json
  _skills/SKILLS_INDEX.md  (≤40 lines; archived skills excluded by default)

Consumer = a distinct file OUTSIDE the skill's OWN dir that ACTUALLY USES the skill
(deduped by absolute path). Three usage signals:
  1. python import   — (from|import) _skills.<name>   in *.py
  2. path / CLI      — _skills/<name>(word-boundary)   in *.py *.sh *.js *.applescript ONLY
                       (covers script/CLI invocations + the old "# from _skills/<name>" form;
                        *.md / *.json excluded — a path in a doc is a mention, not usage)
  3. skill dependency— another skill whose frontmatter `requires:` lists <name>
                       (that skill's SKILL.md is the consumer file; self-requires ignored)
Only the skill's OWN directory is excluded (NOT all of _skills/), so skill→skill
consumers (imports + requires) are counted. Deliberately NOT counted: bare `\bname\b`
prose (kills the "notify"-in-prose false positive) and backticked doc MENTIONS (a skill
named in a planning/governance/audit doc is a mention, not a consumer — a doc that truly
uses a skill also imports or invokes it).

§6 lifecycle lint (advisory; --lint): flags status/consumer mismatches.

Usage:
  python3 _skills/build_index.py            # lenient (default) — output schema-identical
  python3 _skills/build_index.py --lint     # also print §6 lifecycle advisories
  python3 _skills/build_index.py --strict   # exit 1 if any skill lacks frontmatter
  python3 _skills/build_index.py --lint --strict   # also exit 1 on lint violations

Implements §4 + §6 of UNIVERSAL_SKILL_SPEC v2.0.
"""

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Version-mismatch guard (C3) — the spec §4 PROMISES the index "fails loudly on
# version in frontmatter ≠ version in title line"; historically it never checked.
# Two comparisons: frontmatter↔title (all skills whose H1 carries a "(vX.Y[.Z])")
# and frontmatter↔metadata.json (ONLY skills that ship a metadata.json — e.g.
# llm_registry has none and is skipped, never a false "missing" mismatch).
# ---------------------------------------------------------------------------
# Title version lives in the first markdown H1: `# SKILL: <name> (v2.1)` — but also the
# no-"SKILL:" forms like `# Antigravity UI Skill (v1.0)`. Optional; a title with no
# "(v…)" (Unified_Messaging_Bridge, verify_gate, …) is simply not title-checked.
_RE_TITLE_VERSION = re.compile(r"\(v([0-9]+(?:\.[0-9]+)*)\)")


def _version_tuple(v: str) -> tuple[int, ...]:
    """Numeric-component tuple of a version string ('2.1.0' → (2,1,0)); non-digits ignored."""
    return tuple(int(x) for x in re.findall(r"\d+", str(v)))


def _title_version_matches_fm(fm_version, title_version) -> bool:
    """True iff the TITLE version agrees with the frontmatter version, comparing only the
    components the title specifies. So an ABBREVIATED title ('3.8' vs frontmatter '3.8.1',
    'v2.3' vs '2.3.0') is NOT a mismatch, but a genuine divergence ('v2.1' vs '2.3.0',
    linkedin_scraper's live split) IS. This is what stops every 2-component title in the
    fleet from false-flagging against its 3-component semver frontmatter."""
    ft, tt = _version_tuple(fm_version), _version_tuple(title_version)
    if not tt:
        return True   # title carried no parseable version → nothing to contradict
    return ft[: len(tt)] == tt


def _extract_title_version(text: str) -> str | None:
    """The version in the first markdown H1 (`# … (vX.Y[.Z])`), or None if the title
    has no `(v…)` token. Only the first H1 is consulted (per §4 'the title line')."""
    first_h1 = next((ln for ln in text.splitlines() if ln.startswith("# ")), "")
    m = _RE_TITLE_VERSION.search(first_h1)
    return m.group(1) if m else None


# Sentinel: metadata.json ABSENT (skip parity) — distinct from present-but-null-version.
_NO_META = object()


def _version_mismatches_for(fm_version, title_version, meta_version) -> list[str]:
    """C3 guard: return human-readable mismatch strings, each NAMING the offending field
    (plan acceptance: 'exit non-zero AND name the offending field'). Empty when all PRESENT
    sources agree with the frontmatter.

    - frontmatter↔title: component-truncated compare (an abbreviated title is not a mismatch).
    - frontmatter↔metadata.json: full numeric compare, but ONLY when a metadata.json exists
      (meta_version is _NO_META otherwise → skipped, never a false 'missing' mismatch)."""
    out: list[str] = []
    if fm_version is None:
        return out   # nothing authoritative to compare against
    if title_version is not None and not _title_version_matches_fm(fm_version, title_version):
        out.append(f"frontmatter version={fm_version} != title-line version=v{title_version}")
    if meta_version is not _NO_META and meta_version is not None:
        if _version_tuple(fm_version) != _version_tuple(meta_version):
            out.append(f"frontmatter version={fm_version} != metadata.json version={meta_version}")
    return out

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
SKILLS_DIR = Path(__file__).parent          # _skills/
WORKSPACE_ROOT = SKILLS_DIR.parent          # Developpement/
INDEX_JSON = SKILLS_DIR / "index.json"
INDEX_MD = SKILLS_DIR / "SKILLS_INDEX.md"

# ---------------------------------------------------------------------------
# YAML frontmatter parser (stdlib only — no PyYAML required)  [UNCHANGED]
# ---------------------------------------------------------------------------
_FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _parse_frontmatter(text: str) -> dict | None:
    """Return parsed frontmatter dict, or None if absent/unparseable."""
    m = _FM_RE.match(text)
    if not m:
        return None
    fm: dict = {}
    current_key = None
    current_list: list | None = None
    for raw_line in m.group(1).splitlines():
        line = raw_line.rstrip()
        if line.startswith("  - ") and current_list is not None:
            current_list.append(line[4:].strip().strip('"'))
            continue
        kv = re.match(r"^(\w+)\s*:\s*(.*)", line)
        if kv:
            current_key = kv.group(1)
            val = kv.group(2).strip().strip('"')
            if val == "" or val == "[]":
                current_list = []
                fm[current_key] = current_list
            elif val.startswith("[") and val.endswith("]"):
                inner = val[1:-1]
                items = [i.strip().strip('"') for i in inner.split(",") if i.strip()]
                fm[current_key] = items
                current_list = None
            else:
                fm[current_key] = val
                current_list = None
        else:
            current_list = None
    return fm


# ---------------------------------------------------------------------------
# Consumer counting — multi-signal, false-positive-safe, deduped by file.
# ---------------------------------------------------------------------------
_GREP_EXCL = [
    "--exclude-dir=.git", "--exclude-dir=node_modules", "--exclude-dir=.pytest_cache",
    "--exclude-dir=_archive", "--exclude-dir=chroma", "--exclude-dir=storage",
    "--exclude-dir=data", "--exclude-dir=__pycache__",
    "--exclude-dir=venv", "--exclude-dir=.venv", "--exclude-dir=site-packages",
]

_RE_IMPORT = re.compile(r"(?:from|import)\s+_skills\.([A-Za-z_]\w*)")
_RE_PATH = re.compile(r"_skills/([A-Za-z_]\w*)")
# Signal 1b — the sys.path.insert(<_skills root>) + BARE import form. Consumers that
# splice the _skills/ dir onto sys.path import the skill by its *bare* package name
# (`from linkedin_scraper.scripts.x import y`, `import PROJECT-C_analytics.PROJECT-C_analytics`)
# — NOT `_skills.<name>`, so _RE_IMPORT misses them (PROJECT-B dashboard_app.py, PROJECT-C_analytics's
# PROJECT-C consumer, applescript_bridge's callers). Anchored at import-statement position
# (start-of-line, optional indent — these imports live inside if-blocks / functions) and the
# captured leading identifier is filtered through `add()` against the known skill-name set, so
# only a real skill dir-name that equals the imported top package counts. The name must be
# followed by `.`, whitespace, or EOL (word-boundary) — this is why `alpha_extra` can't match
# `alpha` (prefix-collision test) and a bare English `notify` in prose can't match (no import kw).
_RE_BARE_IMPORT = re.compile(r"^\s*(?:from|import)\s+([A-Za-z_]\w*)(?=[.\s]|$)")


def _grep(pattern: str, includes: list[str]) -> list[str]:
    """grep -rEn over the workspace. Returns 'path:lineno:content' lines (empty on error)."""
    cmd = ["grep", "-rEn", *includes, *_GREP_EXCL, "-e", pattern, str(WORKSPACE_ROOT)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
        return r.stdout.splitlines()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []


def _parse_grep_line(line: str) -> tuple[str, str] | None:
    """'path:lineno:content' → (path, content). None if unparseable."""
    parts = line.split(":", 2)
    if len(parts) < 3:
        return None
    return parts[0], parts[2]


def _build_consumer_map(skill_names: set[str],
                        requires_by_skill: dict[str, list[str]]) -> dict[str, set[str]]:
    """Map each skill name → set of distinct consumer file paths (signals 1–4)."""
    cmap: dict[str, set[str]] = {n: set() for n in skill_names}

    def add(name: str, filepath: str) -> None:
        if name not in skill_names or not filepath:
            return
        own = f"{SKILLS_DIR}{os.sep}{name}{os.sep}"   # exclude ONLY the skill's own dir
        if filepath.startswith(own):
            return
        cmap[name].add(filepath)

    # Signal 1 — python imports (*.py)
    for line in _grep(r"(from|import)[[:space:]]+_skills\.[A-Za-z_]", ["--include=*.py"]):
        pc = _parse_grep_line(line)
        if not pc:
            continue
        fp, content = pc
        for m in _RE_IMPORT.finditer(content):
            add(m.group(1), fp)

    # Signal 1b — bare import after a sys.path.insert(<_skills root>) (see _RE_BARE_IMPORT).
    # grep the import keywords; the regex re-anchors at statement position and `add()` keeps
    # only names that are real skill dirs — so an unrelated `import json` never counts, and a
    # bare skill-named import that ISN'T on sys.path simply won't run in that consumer but is
    # still an authored dependency on the skill (the only consumers using this form DO insert
    # the _skills root first — verified: PROJECT-B dashboard, PROJECT-C tests, applescript_bridge callers).
    for line in _grep(r"^[[:space:]]*(from|import)[[:space:]]+[A-Za-z_]", ["--include=*.py"]):
        pc = _parse_grep_line(line)
        if not pc:
            continue
        fp, content = pc
        m = _RE_BARE_IMPORT.match(content)
        if m:
            add(m.group(1), fp)

    # Signal 2 — path / CLI / snippet-comment in EXECUTABLE files only.
    # Deliberately excludes *.md and *.json: a `_skills/<name>` path written in a
    # PROJECT_BRIEF / JOURNAL / STATUS / PLAN / AUDIT / OPEN_WORK doc (or index.json
    # itself) is a MENTION, not usage. Real script/CLI invocations live in code.
    for line in _grep(r"_skills/[A-Za-z_]",
                      ["--include=*.py", "--include=*.sh", "--include=*.js",
                       "--include=*.applescript"]):
        pc = _parse_grep_line(line)
        if not pc:
            continue
        fp, content = pc
        for m in _RE_PATH.finditer(content):
            add(m.group(1), fp)

    # (Doc-backtick mentions are intentionally NOT a signal: a skill named in a
    #  planning/governance/audit doc — or listed as a "future candidate" — is a
    #  MENTION, not a consumer. Real usage is import (1) / path-CLI (2) / dependency
    #  (3); a doc that truly uses a skill also imports or invokes it, so nothing is
    #  lost. This keeps the count = "things that actually use the skill".)

    # Signal 3 — skill→skill `requires:` (the requiring skill's SKILL.md is the consumer).
    for skill, deps in requires_by_skill.items():
        consumer_file = f"{SKILLS_DIR}{os.sep}{skill}{os.sep}SKILL.md"
        for dep in deps:
            if dep == skill:
                continue   # ignore self-requires
            add(dep, consumer_file)

    return cmap


# ---------------------------------------------------------------------------
# Consumer-count rendering — kind-aware (C2)
# ---------------------------------------------------------------------------
# The `consumers` integer measures CODE usage (import / script-path / requires), which is
# the usage mechanism for `library` skills ONLY. `procedure` skills are INVOKED via the
# harness (not imported) and `snippets` are copy-pasted — so a code-consumer count of 0 for
# them is expected and says nothing about adoption. Rendering a bare `0` there produced the
# "frontmatter-says-used / table-says-0" self-contradiction (e.g. github_commit_audit).
# For these kinds we render 0 as an explicit n/a; a NON-zero count (a requires-edge or a rare
# direct import of a procedure) is still shown as the number, since that IS real code usage.
# `library` is always the bare number. Any future non-library/non-snippets kind (e.g. a
# "slash" kind) is treated like procedure — invoked, not imported.
_LIBRARY_KINDS = {"library"}
_CONSUMER_NA_LABEL = "n/a (invoked, not imported)"


def render_consumer_count(kind, consumers: int) -> str:
    """Human display for the Consumers column. Bare int for library; for invoked/copied
    kinds a 0 becomes an explicit n/a, while a real (>0) code-consumer count is still shown."""
    if kind in _LIBRARY_KINDS:
        return str(consumers)
    return _CONSUMER_NA_LABEL if not consumers else str(consumers)


# ---------------------------------------------------------------------------
# §6 lifecycle lint (advisory)
# ---------------------------------------------------------------------------

def lint_lifecycle(records: list[dict]) -> list[str]:
    """Return §6 status/consumer mismatch advisories (does NOT mutate status).

    KIND-AWARE: the consumer count measures CODE usage (imports / script-path / requires),
    which is the usage mechanism for `library` skills only. `procedure` skills are invoked
    (not imported) and `snippets` are copy-pasted — a 0 code-consumer count for them is
    expected, NOT a problem — so the ≥2/promote thresholds apply to `library` only. The
    archived-but-still-referenced check applies to ALL kinds (any live import/requires of an
    archived skill is worth surfacing regardless of kind).
    """
    out: list[str] = []
    for r in records:
        if not r.get("_has_frontmatter"):
            continue
        st, c, n, kind = r.get("status"), r.get("consumers", 0), r["name"], r.get("kind")
        if st == "archived" and c > 0:
            out.append(f"  archived {n}: {c} code-consumer(s) still reference it — verify before keeping archived.")
            continue
        if kind != "library":
            continue   # procedure/snippets: code-consumer count is not a lifecycle signal
        if st == "active" and c == 0:
            out.append(f"  active   {n} (library): 0 consumers — §6 wants ≥2 (or tests+production+1). Review.")
        elif st == "incubating" and c >= 2:
            out.append(f"  incubat. {n} (library): {c} consumers — §6 says PROMOTE to active.")
    return out


# ---------------------------------------------------------------------------
# Collect
# ---------------------------------------------------------------------------

def _as_list(v) -> list:
    """Coerce a frontmatter field to a list. A malformed scalar (e.g. `requires: dep`
    instead of `requires: [dep]`) → [] rather than a string (prevents schema drift and
    a `list('dep')`→char-explosion in the requires tally). Session-critical robustness."""
    return v if isinstance(v, list) else []


def collect_skills() -> list[dict]:
    """Two-phase: parse all frontmatter first (names + requires), then count consumers.

    Also records, per skill, the title-line version (§4) and metadata.json version so the
    caller can surface version-mismatch guards (C3). Detection only — never mutates the file.
    """
    # (name, fm, raw_dirname, title_version, metadata_version_or_sentinel)
    parsed: list[tuple[str, dict | None, str, str | None, object]] = []
    for skill_dir in sorted(SKILLS_DIR.iterdir()):
        if not skill_dir.is_dir():
            continue
        if skill_dir.name.startswith(".") or skill_dir.name == "__pycache__":
            continue
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            continue
        text = skill_md.read_text(encoding="utf-8", errors="replace")
        fm = _parse_frontmatter(text)
        name = (fm.get("name") if fm else None) or skill_dir.name
        title_version = _extract_title_version(text)
        # metadata.json parity ONLY for skills that HAVE a metadata.json (plan C3: skip those
        # without — e.g. llm_registry has none). A malformed metadata.json is not a version
        # mismatch: leave parity un-checked rather than crash the whole index build.
        meta_path = skill_dir / "metadata.json"
        meta_version: object = _NO_META
        if meta_path.exists():
            try:
                meta_version = json.loads(meta_path.read_text(encoding="utf-8")).get("version")
            except (ValueError, OSError):
                meta_version = _NO_META
        parsed.append((name, fm, skill_dir.name, title_version, meta_version))

    skill_names = {name for name, _, _, _, _ in parsed}
    requires_by_skill = {
        name: _as_list(fm.get("requires"))
        for name, fm, _, _, _ in parsed if fm
    }
    cmap = _build_consumer_map(skill_names, requires_by_skill)

    records: list[dict] = []
    for name, fm, _dirname, title_version, meta_version in parsed:
        if fm is None:
            records.append({
                "name": name, "version": None, "kind": None,
                "status": "needs-frontmatter",
                "description": "(no frontmatter — run frontmatter retrofit)",
                "interface": [], "env": [], "requires": [],
                "consumers": len(cmap.get(name, set())),
                "_has_frontmatter": False,
                "_version_mismatches": [],   # no frontmatter → nothing to compare against
            })
        else:
            fm_version = fm.get("version")
            mismatches = _version_mismatches_for(fm_version, title_version, meta_version)
            records.append({
                "name": name,
                "version": fm_version,
                "kind": fm.get("kind", "library"),
                "status": fm.get("status", "active"),
                "description": fm.get("description", ""),
                "interface": _as_list(fm.get("interface")),
                "env": _as_list(fm.get("env")),
                "requires": _as_list(fm.get("requires")),
                "consumers": len(cmap.get(name, set())),
                "_has_frontmatter": True,
                "_version_mismatches": mismatches,   # C3 (private; excluded from index.json)
            })
    return records


# ---------------------------------------------------------------------------
# Emit  [UNCHANGED schema]
# ---------------------------------------------------------------------------

def emit_json(records: list[dict], path: Path) -> None:
    clean = [{k: v for k, v in r.items() if not k.startswith("_")} for r in records]
    path.write_text(json.dumps(clean, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def emit_markdown(records: list[dict], path: Path) -> None:
    visible = [r for r in records if r.get("status") != "archived"]
    lines: list[str] = []
    lines.append("# Skills Index")
    lines.append("")
    lines.append(f"_Generated by `_skills/build_index.py` · {len(visible)} skills (archived excluded)_")
    lines.append("")
    lines.append("| Name | Version | Kind | Status | Consumers | Description |")
    lines.append("|---|---|---|---|---|---|")
    for r in visible:
        version = r.get("version") or "—"
        kind = r.get("kind") or "—"
        status = r.get("status") or "—"
        consumers = render_consumer_count(r.get("kind"), r.get("consumers", 0))  # C2: kind-aware
        desc = r.get("description", "")
        if len(desc) > 80:
            desc = desc[:77] + "..."
        lines.append(f"| `{r['name']}` | {version} | {kind} | {status} | {consumers} | {desc} |")
    lines.append("")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Build _skills index.json + SKILLS_INDEX.md")
    parser.add_argument("--strict", action="store_true",
                        help="Exit nonzero if any skill lacks frontmatter (and, with --lint, on lint violations)")
    parser.add_argument("--lint", action="store_true",
                        help="Print §6 lifecycle (status vs consumer) advisories")
    args = parser.parse_args()

    records = collect_skills()
    missing_fm = [r for r in records if not r.get("_has_frontmatter")]

    # C3 version-mismatch guard (spec §4 "fails loudly on ... version in frontmatter ≠ version
    # in title line"). ALWAYS emit to stderr so the drift is visible on every run; exit non-zero
    # only under --strict (a single mismatched skill must not silently block a lenient rebuild,
    # mirroring §4's "one broken skill never blocks fleet-wide registration").
    version_offenders = [r for r in records if r.get("_version_mismatches")]
    if version_offenders:
        n = sum(len(r["_version_mismatches"]) for r in version_offenders)
        print(f"ERROR: {n} version mismatch(es) across {len(version_offenders)} skill(s):",
              file=sys.stderr)
        for r in version_offenders:
            for msg in r["_version_mismatches"]:
                print(f"  - {r['name']}: {msg}", file=sys.stderr)

    if args.strict and (missing_fm or version_offenders):
        if missing_fm:
            print(f"ERROR: {len(missing_fm)} skill(s) lack frontmatter:", file=sys.stderr)
            for r in missing_fm:
                print(f"  - {r['name']}", file=sys.stderr)
        sys.exit(1)

    emit_json(records, INDEX_JSON)
    emit_markdown(records, INDEX_MD)

    total = len(records)
    with_fm = sum(1 for r in records if r.get("_has_frontmatter"))
    print(f"Index built: {total} skills ({with_fm} with frontmatter, "
          f"{len(missing_fm)} needs-frontmatter)")
    print(f"  -> {INDEX_JSON}")
    print(f"  -> {INDEX_MD}")
    if missing_fm:
        print(f"Skills without frontmatter ({len(missing_fm)}):")
        for r in missing_fm:
            print(f"  - {r['name']}")

    if args.lint:
        advisories = lint_lifecycle(records)
        if advisories:
            print(f"\n§6 lifecycle advisories ({len(advisories)}):", file=sys.stderr)
            for a in advisories:
                print(a, file=sys.stderr)
            if args.strict:
                sys.exit(1)
        else:
            print("\n§6 lifecycle: all statuses consistent with consumer counts ✓", file=sys.stderr)


if __name__ == "__main__":
    main()

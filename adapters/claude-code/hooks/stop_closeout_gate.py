#!/usr/bin/env python3
"""stop_closeout_gate.py — CLOSEOUT-GATE policy (POLICY_CORE.md), Claude Code adapter.

Stop hook. Parses the session transcript (JSONL) and blocks the turn-end ONCE
when closeout duties are visibly unmet:
  1. Code files were edited but no verification command ran (no pytest /
     py_compile / bash -n / osacompile / test invocation in any Bash call).
  2. The working project's NEOCORTEX/JOURNAL.md exceeds its 64 KB bound
     (NEOCORTEX_SPEC §2 rotation rule).

It ALSO runs a project-scoped continuity sweep as a NON-BLOCKING ADVISORY
(founder ruling 2026-07-27): findings are recorded to governance/pilot_telemetry.log
with a CLOSEOUT-ADVISORY marker and echoed to stderr. They never enter `problems`
and can never block a turn — 11 of 25 projects fail today, so a blocking wire
would jam turn-end fleet-wide. Project-scoped costs ~0.34 s (measured); the
fleet-wide sweep costs ~8.7 s and is deliberately NOT run here.

Loop safety: if stop_hook_active is set (we already blocked once this turn),
exit silently — a gate that loops is worse than no gate.
"""
import datetime
import json
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
            return cand
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


CODE_SUFFIXES = (".py", ".sh", ".applescript", ".js", ".ts", ".html", ".css")
VERIFY_MARKERS = ("pytest", "py_compile", "bash -n", "osacompile",
                  "npm test", "python3 -m unittest", "test_", "--dry-run")

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
# A deny-list can only ever exempt these four prefixes; everything else still gates.
# (/tmp and /var/folders are listed alongside their /private/… realpaths because a
# tool may report either form on macOS.)
NON_CLOSEOUT_PREFIXES = ("/private/tmp/", "/tmp/",
                         "/private/var/folders/", "/var/folders/")


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
    return os.fdopen(fd, encoding="utf-8", errors="replace")


def _verify_armed() -> bool:
    """A project opts into BLOCKING independent-verification by placing a `.verify/armed`
    marker. Until then the VERIFY-GATE is advisory (see verify_advisory.py) — this gate never
    blocks on it. No project is armed today, so this path is dormant (zero behavior change)."""
    try:
        return (Path.cwd() / ".verify" / "armed").is_file()
    except OSError:
        return False


def _has_valid_receipt(changed_paths: list) -> bool:
    """True iff a valid, change-set-bound VERIFY-RECEIPT exists for exactly these files. The
    fabricable `echo test_` substring does NOT satisfy this — it needs a real attested verdict."""
    try:
        import sys as _sys
        _sys.path.insert(0, str(ROOT / "_skills" / "verify_gate" / "scripts"))
        import receipt as _r  # type: ignore
        rpath = Path.cwd() / ".verify" / "receipt.json"
        if not rpath.is_file():
            return False
        rec = json.loads(rpath.read_text(encoding="utf-8"))
        ledger = str(Path.cwd() / ".verify" / "_consumed_nonces.json")
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


def _skill_version_drift() -> list[str]:
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


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)
    if data.get("stop_hook_active"):
        sys.exit(0)

    # `edited_code` is the FULL session change-set (needed as-is by the VERIFY-armed
    # receipt path below, which attests the whole change-set, not a rolling window).
    # `edited_since_verify` is the per-edit-relative-to-verification window this gate
    # actually judges: it accumulates on each qualifying edit and RESETS every time a
    # verification command is seen, so a verification command clears everything edited
    # before it. This replaces the old two sticky booleans (edited_code non-empty,
    # `verified` OR'd across the WHOLE session) — that pairing meant a single unmatched
    # edit anywhere in a long session stayed "unverified" forever, re-blocking every
    # subsequent turn even after later edits were genuinely verified, because `verified`
    # could only ever flip 0->1 and `edited_code` only ever grew. This is a scoping bug,
    # not an intentional ratchet: the gate's own docstring (line 5-6) already describes
    # its intent as "code files were edited but no verification command ran" — read
    # naturally that is about the CURRENT unverified edit(s), not a historical union.
    edited_code, edited_since_verify = [], []
    tpath = data.get("transcript_path", "")
    fh = _safe_open_transcript(tpath)
    if fh is None:
        sys.exit(0)   # F7: can't safely read the transcript -> no closeout assertion (fail-open)
    try:
        read_bytes = 0
        for line in fh:
            read_bytes += len(line)
            if read_bytes > 64 * 1024 * 1024:   # F7 cap: stop after 64 MB (giant-file guard)
                break
            try:
                entry = json.loads(line)
            except Exception:
                continue
            msg = entry.get("message") or {}
            for blk in msg.get("content") or []:
                if not isinstance(blk, dict) or blk.get("type") != "tool_use":
                    continue
                name, ti = blk.get("name", ""), blk.get("input", {}) or {}
                if name in ("Edit", "Write", "NotebookEdit"):
                    fp = str(ti.get("file_path", ""))
                    if fp.endswith(CODE_SUFFIXES) and not fp.startswith(NON_CLOSEOUT_PREFIXES):
                        edited_code.append(fp)
                        edited_since_verify.append(fp)
                elif name == "Bash":
                    cmd = str(ti.get("command", ""))
                    if any(m in cmd for m in VERIFY_MARKERS):
                        edited_since_verify = []   # a verification command clears the window
    except OSError:
        sys.exit(0)
    finally:
        fh.close()

    problems = []
    if edited_code:
        if _verify_armed():
            # VERIFY-armed: require an un-fabricable independent-verification receipt for the
            # WHOLE session change-set (the echo-test_ substring bypass does NOT satisfy this).
            # Deliberately still keyed on `edited_code` (full session), not the rolling window —
            # a receipt attests the entire change-set at once, so partial/rolling coverage isn't
            # the right model here. (Dormant today: no project is armed.)
            sample = ", ".join(sorted(set(edited_code))[:3])
            if not _has_valid_receipt(sorted(set(edited_code))):
                problems.append(
                    f"VERIFY-GATE (armed): code was edited ({sample}…) with no valid independent-"
                    f"verification receipt for this change-set — run the verify gate "
                    f"(_skills/verify_gate), or state why verification is impossible (Art. 1)")
        elif edited_since_verify:
            # Unarmed (advisory-first): per-edit check — only files edited SINCE the last
            # verification command are unverified. A verification command earlier in the
            # session that covered prior edits does not get re-litigated on every later turn.
            sample = ", ".join(sorted(set(edited_since_verify))[:3])
            problems.append(
                f"code was edited ({sample}…) since the last verification command — run "
                f"the relevant tests/checks, or state explicitly why verification is "
                f"impossible (Art. 1)")
    drift = _skill_version_drift()
    if drift:
        shown = "; ".join(drift[:3])
        more = f" (+{len(drift) - 3} more)" if len(drift) > 3 else ""
        problems.append(
            f"skill version drift — {shown}{more}. The SKILL.md frontmatter is the SSOT "
            f"(UNIVERSAL_SKILL_SPEC R-SHARED-4): align the other sources to it, then "
            f"`python3.12 _skills/build_index.py --strict` must exit 0")

    proj_root = _journal_project_root()
    if proj_root is not None:
        journal = proj_root / "NEOCORTEX" / "JOURNAL.md"
        try:
            if journal.is_file() and journal.stat().st_size > 64 * 1024:
                problems.append(
                    f"{journal} is {journal.stat().st_size // 1024} KB "
                    f"(bound: 64 KB) — rotate per NEOCORTEX_SPEC §2")
        except OSError:
            pass

        # NON-BLOCKING advisory — deliberately kept out of `problems` (founder ruling
        # 2026-07-27: advisory-first, not blocking). Durable in the telemetry log so it
        # survives the turn; echoed to stderr so it is not silently swallowed.
        advisory = _continuity_advisory(proj_root)
        if advisory:
            try:
                _safe_append(ROOT, ("governance", "pilot_telemetry.log"),
                             f"{datetime.datetime.now().isoformat(timespec='seconds')} "
                             f"CLOSEOUT-ADVISORY {proj_root.name}: {advisory[:80]}\n")
            except OSError:
                pass
            print(f"CLOSEOUT-ADVISORY (non-blocking) {proj_root.name}: {advisory} — "
                  f"run governance/scripts/continuity_sweep.py --project "
                  f"'{proj_root}' for detail", file=sys.stderr)

    if problems:
        try:
            _safe_append(ROOT, ("governance", "pilot_telemetry.log"),
                         f"{datetime.datetime.now().isoformat(timespec='seconds')} CLOSEOUT-BLOCK {problems[0][:60]}\n")
        except OSError:
            pass
        print(json.dumps({"decision": "block",
                          "reason": "CLOSEOUT-GATE: " + "; ".join(problems) + "."}))
    sys.exit(0)


if __name__ == "__main__":
    main()

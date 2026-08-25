#!/usr/bin/env python3
"""
verify_fires.py — Hook fire verifier (PAICodeConstitution-2026).

Reads INSTALLED.json (armed-projects manifest) and hook_heartbeat.log
(appended by session_anchor.sh on every real SessionStart), and reports for
each armed project whether a real SessionStart has PROVEN to have fired
from that directory, or whether it is PENDING (not yet observed).

PROVEN means: at least one heartbeat line whose PWD == the project path.
PENDING means: no matching line yet — correct and expected for freshly-armed
projects that have not yet had a real session opened in them.

Output format (one line per project):
  PROVEN   <project_path>  [<latest fire timestamp>]
  PENDING  <project_path>

Exit 0 always — PENDING is not a failure, it is the honest state.

Usage:
  python3 verify_fires.py
"""

import json
import pathlib
import sys

SCRIPT_DIR  = pathlib.Path(__file__).resolve().parent


def _find_root(start, explicit=None):
    """Locate the workspace root.

    Resolution order — explicit beats inferred, and the inferred answer is always ANNOUNCED:
      1. --root PATH on the command line
      2. $GOVERNANCE_WORKSPACE
      3. walk up from this script looking for .claude/settings.json + .claude/hooks/

    Why the announcement matters more than the search. The §5.5 publish-gate panel showed
    that bootstrap.sh takes a $WORKSPACE argument these tools never read: they infer a root
    by walking up from their own location, so when the kit is NOT inside the target workspace
    (the documented, ordinary case for an adopter) they operated on a completely different
    tree and said nothing. On the machine this kit is published from, the clone sits inside
    the private workspace, so the walk escaped the repository entirely and printed 23 real
    private project names. A silently-wrong root is the failure; a loudly-wrong one is a
    typo. Both markers are required — an ARMED project carries settings.json alone.
    """
    import os
    if explicit:
        cand = pathlib.Path(explicit).expanduser().resolve()
        if not (cand / ".claude" / "settings.json").is_file():
            sys.exit(f"--root {cand} has no .claude/settings.json — run bootstrap.sh there first")
        return cand
    env = os.environ.get("GOVERNANCE_WORKSPACE")
    if env:
        return pathlib.Path(env).expanduser().resolve()
    for candidate in (start, *start.parents):
        claude = candidate / ".claude"
        if (claude / "settings.json").is_file() and (claude / "hooks").is_dir():
            # ANNOUNCE it. An inferred root that is wrong and silent is the defect; wrong
            # and loud is a typo the operator sees immediately.
            print(f"workspace root (inferred): {candidate}", file=sys.stderr)
            print("  pass --root PATH or set $GOVERNANCE_WORKSPACE to be explicit",
                  file=sys.stderr)
            return candidate
    guess = (start / "../../..").resolve()
    print(f"workspace root (GUESSED, no marker found): {guess}", file=sys.stderr)
    print("  this is the \"..\"-hop counting that this function exists to replace — "
          "pass --root PATH", file=sys.stderr)
    return guess


def _root_from_argv():
    """Read --root from sys.argv without disturbing each tool's own arg parsing."""
    if "--root" in sys.argv:
        i = sys.argv.index("--root")
        if i + 1 < len(sys.argv):
            val = sys.argv[i + 1]
            del sys.argv[i:i + 2]
            return val
        sys.exit("--root requires a path")
    return None

ROOT = _find_root(SCRIPT_DIR, _root_from_argv())

INSTALLED_JSON  = ROOT / "governance" / "adapters" / "INSTALLED.json"
HEARTBEAT_LOG   = ROOT / "governance" / "hook_heartbeat.log"


def _load_heartbeats() -> dict[str, str]:
    """
    Parse the heartbeat log.  Each line is:
        <ISO-timestamp>\t<PWD>
    Returns a dict mapping PWD → latest timestamp seen for that PWD.
    """
    seen: dict[str, str] = {}
    if not HEARTBEAT_LOG.exists():
        return seen
    try:
        for raw in HEARTBEAT_LOG.read_text().splitlines():
            raw = raw.strip()
            if "\t" not in raw:
                continue
            ts, pwd = raw.split("\t", 1)
            # Keep the latest (last wins as we read top to bottom; lines are
            # appended newest-last so the final occurrence IS the latest).
            seen[pwd.strip()] = ts.strip()
    except OSError:
        pass
    return seen


def _load_installed() -> list[dict]:
    if not INSTALLED_JSON.exists():
        print(f"WARNING: INSTALLED.json not found at {INSTALLED_JSON}", file=sys.stderr)
        print("Run: python3 adapters/claude-code/install_adapters.py --apply",
              file=sys.stderr)
        return []
    with INSTALLED_JSON.open() as f:
        return json.load(f)


def main():
    records   = _load_installed()
    heartbeat = _load_heartbeats()

    armed    = [r for r in records if r.get("has_adapter")]
    unarmed  = [r for r in records if not r.get("has_adapter")]

    proven_count  = 0
    pending_count = 0

    print(f"Armed projects: {len(armed)}  |  Heartbeat entries: {len(heartbeat)}")
    print()

    for rec in sorted(armed, key=lambda r: r["project_path"]):
        path = rec["project_path"]
        ts   = heartbeat.get(path)
        if ts:
            proven_count += 1
            print(f"  PROVEN   {path}  [{ts}]")
        else:
            pending_count += 1
            print(f"  PENDING  {path}")

    if unarmed:
        print()
        print(f"Unarmed projects ({len(unarmed)}) — not yet installed:")
        for rec in sorted(unarmed, key=lambda r: r["project_path"]):
            print(f"  UNARMED  {rec['project_path']}")

    print()
    print(f"Summary: {proven_count} PROVEN, {pending_count} PENDING"
          + (f", {len(unarmed)} UNARMED" if unarmed else ""))
    if pending_count:
        print("PENDING is expected for freshly-armed projects awaiting their"
              " first real session — not a failure.")

    # Always exit 0 — PENDING is the honest pre-session state.
    sys.exit(0)


if __name__ == "__main__":
    main()

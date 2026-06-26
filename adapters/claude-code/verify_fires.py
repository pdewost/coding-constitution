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
ROOT        = (SCRIPT_DIR / "../../..").resolve()

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
        print("Run: python3 governance/adapters/claude-code/install_adapters.py --apply",
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

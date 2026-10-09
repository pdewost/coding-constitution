#!/usr/bin/env python3
"""
verify_fires.py — Hook fire verifier (PAICodeConstitution-2026).

Reads INSTALLED.json (armed-projects manifest) and hook_heartbeat.log
(appended by session_anchor.sh on every real SessionStart), and reports for
each armed project whether a real SessionStart has PROVEN to have fired
from that directory, or whether it is PENDING (not yet observed).

PROVEN means: a heartbeat line whose PWD == the project path, at most
FRESH_DAYS old (by its own UTC timestamp).
STALE means: the latest matching line is older than FRESH_DAYS — the hooks
fired once, which proves nothing about today (reviewer A S4-53, 2026-10-05).
PENDING means: no matching line yet — correct and expected for freshly-armed
projects that have not yet had a real session opened in them.

Output format (one line per project):
  PROVEN   <project_path>  [<latest fire timestamp>]
  STALE    <project_path>  [<latest fire timestamp>]
  PENDING  <project_path>

Exit 0 when the manifest was read and lists at least one project (PENDING and
STALE are reported states, not failures). Exit 2 when INSTALLED.json is
absent, empty, unparseable or unsafe (symlink, hardlink, FIFO), or the
heartbeat log is unsafe: then nothing was verified and the run must not look
like a success. (It used to exit 0 always, so a missing manifest printed
"Armed projects: 0" and passed.)

Usage:
  python3 verify_fires.py
"""

import json
import os
import pathlib
import stat
import sys
from datetime import datetime, timedelta, timezone

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

# A heartbeat older than this no longer PROVES the hooks fire (founder ruling 2026-10-05).
FRESH_DAYS = 14
# A heartbeat stamped in the FUTURE proves nothing either (clock error, a hand-edited or forged line): accept at most this much
# clock skew ahead of "now" (S4 F9, 2026-10-08).
SKEW = timedelta(minutes=5)


class UnsafeFile(Exception):
    pass


def _safe_read_text(path: pathlib.Path):
    """Read <path> without following a symlink in ANY component below ROOT and refusing a
    non-regular or multiply-linked final file (reviewer A S4-54: a FIFO wedged the verifier and a
    symlink chose its input). Returns None when the file is absent; raises UnsafeFile."""
    parts = path.relative_to(ROOT).parts
    dfd = os.open(str(ROOT), os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in parts[:-1]:
            try:
                nfd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=dfd)
            except FileNotFoundError:
                return None
            except OSError as e:
                raise UnsafeFile(f"{path}: unsafe path component {part!r} ({e})")
            os.close(dfd)
            dfd = nfd
        try:
            fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dfd)
        except FileNotFoundError:
            return None
        except OSError as e:
            raise UnsafeFile(f"{path}: refused ({e})")
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
                raise UnsafeFile(f"{path}: not a regular single-link file")
            chunks = []
            while True:
                buf = os.read(fd, 65536)
                if not buf:
                    break
                chunks.append(buf)
            return b"".join(chunks).decode("utf-8", errors="replace")
        finally:
            os.close(fd)
    finally:
        os.close(dfd)


def _is_fresh(ts: str, now: datetime) -> bool:
    try:
        when = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return False   # an unparseable stamp proves nothing
    age = now - when
    return -SKEW <= age <= timedelta(days=FRESH_DAYS)


def _stale_reason(ts: str, now: datetime) -> str:
    """Why a heartbeat is not fresh, for the STALE line (S4 re-check N7: a future-dated or unparseable stamp is not 'old')."""
    try:
        when = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return "unparseable timestamp"
    if now - when < -SKEW:
        return "dated in the future"
    return f"older than {FRESH_DAYS} days"


def _load_heartbeats() -> dict[str, str]:
    """
    Parse the heartbeat log.  Each line is:
        <ISO-timestamp>\t<PWD>
    Returns a dict mapping PWD → latest timestamp seen for that PWD.
    """
    seen: dict[str, str] = {}
    text = _safe_read_text(HEARTBEAT_LOG)
    if text is None:
        return seen
    try:
        for raw in text.splitlines():
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


def _load_installed():
    """The manifest's records, or None when it is absent (the caller exits 2)."""
    text = _safe_read_text(INSTALLED_JSON)
    if text is None:
        print(f"ERROR: INSTALLED.json not found at {INSTALLED_JSON}", file=sys.stderr)
        print("Run: python3 adapters/claude-code/install_adapters.py --apply",
              file=sys.stderr)
        return None
    return json.loads(text)


def main():
    try:
        records   = _load_installed()
        heartbeat = _load_heartbeats()
    except (UnsafeFile, ValueError) as e:
        print(f"ERROR: {e} — nothing verified.", file=sys.stderr)
        sys.exit(2)
    if not records:
        if records is not None:
            print(f"ERROR: INSTALLED.json at {INSTALLED_JSON} lists no project — nothing verified.",
                  file=sys.stderr)
        sys.exit(2)
    now = datetime.now(timezone.utc)

    armed    = [r for r in records if r.get("has_adapter")]
    unarmed  = [r for r in records if not r.get("has_adapter")]

    proven_count  = 0
    stale_count   = 0
    pending_count = 0

    print(f"Armed projects: {len(armed)}  |  Heartbeat entries: {len(heartbeat)}")
    print()

    for rec in sorted(armed, key=lambda r: r["project_path"]):
        path = rec["project_path"]
        ts   = heartbeat.get(path)
        if ts and _is_fresh(ts, now):
            proven_count += 1
            print(f"  PROVEN   {path}  [{ts}]")
        elif ts:
            stale_count += 1
            print(f"  STALE    {path}  [{ts}]  ({_stale_reason(ts, now)})")
        else:
            pending_count += 1
            print(f"  PENDING  {path}")

    if unarmed:
        print()
        print(f"Unarmed projects ({len(unarmed)}) — not yet installed:")
        for rec in sorted(unarmed, key=lambda r: r["project_path"]):
            print(f"  UNARMED  {rec['project_path']}")

    print()
    print(f"Summary: {proven_count} PROVEN, {stale_count} STALE, {pending_count} PENDING"
          + (f", {len(unarmed)} UNARMED" if unarmed else ""))
    if pending_count:
        print("PENDING is expected for freshly-armed projects awaiting their"
              " first real session — not a failure.")

    # PENDING and STALE are reported states, not failures; the failures exited 2 above.
    sys.exit(0)


if __name__ == "__main__":
    main()

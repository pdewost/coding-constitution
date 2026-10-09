#!/usr/bin/env python3
"""hook_support.py — two small services shared by the L1 hooks.

PLAN_hooks_outcome_tamper_loopbound_2026-10-05.md §3 H0 and H4c.

1. log_error(hook, exc): every hook swallows its own exceptions (S4-25: "every error path is
   fail-open ... the same property that makes lockout impossible makes death invisible"). From now
   on each swallowed exception leaves one JSON line in governance/hook_errors.jsonl (local, never
   committed; rotated at 1 MB). session_anchor.sh reports the last 24 h count.
2. integrity_report(): tamper evidence for the governor (H4c). The approved list
   governance/hooks_approved.sha256 (shasum format: "<sha256>  <path relative to ROOT>") is written
   at each founder-visible landing (`python3.12 .claude/hooks/hook_support.py --write-approved`; that
   write itself goes through the GOVERNOR ask, GOVERNOR_SCRIPTS in pretool_guard.py). Any governed
   file (see governed_files) that differs,
   is missing, or is not listed is reported. It detects; it cannot prevent (a determined shell can
   still write): the point is that a change cannot stay silent beyond one turn.

Stdlib only, Python 3.9-compatible. Every function is best-effort and never raises.
"""
import os as _os0, sys as _sys0
# B4-10 / B5-01 (reviewer A rounds 4-5): Python puts this script's directory first on sys.path, so a file in .claude/hooks/
# named like a stdlib module (json.py, __future__.py, ...) would run inside this hook before any check. Drop that
# entry FIRST: no import may precede this block (not even `from __future__`, which is itself an import).
if __name__ == "__main__" and _sys0.path and \
        _os0.path.realpath(_sys0.path[0] or ".") == _os0.path.dirname(_os0.path.realpath(__file__)):
    del _sys0.path[0]

import datetime
import hashlib
import json
import os
import re
import stat
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
ERRLOG = ("governance", "hook_errors.jsonl")
APPROVED = ("governance", "hooks_approved.sha256")
ERRLOG_MAX = 1024 * 1024


def _dir_fd(root: Path, dirs) -> int:
    dfd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    for d in dirs:
        nfd = os.open(d, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=dfd)
        os.close(dfd)
        dfd = nfd
    return dfd


def log_error(hook: str, exc: BaseException, root: Path = ROOT) -> None:
    """Append one line {ts, hook, type, msg} to governance/hook_errors.jsonl (O_NOFOLLOW walk,
    regular single-link file only, rotated to .1 past 1 MB). Never raises."""
    try:
        line = json.dumps({"ts": datetime.datetime.now().isoformat(timespec="seconds"),
                           "hook": hook, "type": type(exc).__name__,
                           "msg": str(exc)[:200]}) + "\n"
        *dirs, name = ERRLOG
        dfd = _dir_fd(root, dirs)
        try:
            try:
                st = os.stat(name, dir_fd=dfd, follow_symlinks=False)
                if stat.S_ISREG(st.st_mode) and st.st_size > ERRLOG_MAX:
                    os.rename(name, name + ".1", src_dir_fd=dfd, dst_dir_fd=dfd)
            except FileNotFoundError:
                pass
            fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW | os.O_NONBLOCK,
                         0o600, dir_fd=dfd)
            try:
                st = os.fstat(fd)
                if stat.S_ISREG(st.st_mode) and st.st_nlink == 1:
                    os.write(fd, line.encode())
            finally:
                os.close(fd)
        finally:
            os.close(dfd)
    except Exception:
        pass


def recent_error_count(hours: int = 24, root: Path = ROOT) -> int:
    try:
        p = root.joinpath(*ERRLOG)
        if p.is_symlink() or not p.is_file():
            return 0
        cutoff = (datetime.datetime.now() - datetime.timedelta(hours=hours)).isoformat(timespec="seconds")
        n = 0
        with open(p, encoding="utf-8", errors="replace") as fh:
            for ln in fh:
                try:
                    if json.loads(ln).get("ts", "") >= cutoff:
                        n += 1
                except Exception:
                    continue
        return n
    except Exception:
        return 0


# §3a (PLAN v0.7, founder ruling 2026-10-06, option A): H4c is THE guarantee, so the inventory is COMPLETE
# over the protected trees — every file of any kind and every directory MODE (B2-31, B2-33), symlinks by
# their target. The only exclusions, each justified because nothing in the governor executes or imports
# them (to make a hook run one, settings.json or a hook must change, and that change is reported):
#   tests (`test_*` files), caches (`__pycache__/`, `.pytest_cache/`), Finder metadata (`.DS_Store`) and
#   the hooks' own runtime logs (`*.log`, rewritten on every run).
# Scope, stated (reviewer A B-35): the WORKSPACE governor — root settings, `.claude/hooks/**`, the policy data,
# the canonical kit (`adapters/claude-code/**` in this repository, installed as `governance/adapters/claude-code` in a workspace). The fleet's generated per-project settings are
# audited by `install_adapters.py --check` (drift), not by this list.
_EXCLUDED_DIRS = {"__pycache__", ".pytest_cache"}
_TREES = (".claude/hooks", "governance/adapters/claude-code")
_SINGLE_FILES = (".claude/settings.json", ".claude/settings.local.json",
                 "governance/machine_config.yaml", "governance/routing_policy.yaml")
_PARENT_DIRS = (".claude", "governance", "governance/adapters")


def _excluded_file(name: str) -> bool:
    return name.startswith("test_") or name == ".DS_Store" or name.endswith(".log")


def governed_files(root: Path = ROOT) -> list:
    """ROOT-relative entries of the live governor (§3a): files as `path`, directories as `path/`."""
    out = []
    for rel in _SINGLE_FILES:
        if os.path.lexists(root / rel):
            out.append(rel)
    for rel in _PARENT_DIRS:
        if (root / rel).is_dir() and not (root / rel).is_symlink():
            out.append(rel + "/")
    for tree in _TREES:
        top = root / tree
        if top.is_symlink():
            out.append(tree)                         # the whole tree replaced by a link: hashed as a link
            continue
        if not top.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(top, followlinks=False):
            d = Path(dirpath)
            out.append(d.relative_to(root).as_posix() + "/")
            keep = []
            for dn in sorted(dirnames):
                if dn in _EXCLUDED_DIRS:
                    continue
                if (d / dn).is_symlink():
                    out.append((d / dn).relative_to(root).as_posix())
                    continue
                keep.append(dn)
            dirnames[:] = keep
            for fn in sorted(filenames):
                if not _excluded_file(fn):
                    out.append((d / fn).relative_to(root).as_posix())
    return out


def _sha256(p: Path) -> str:
    """Content hash + permission bits (Seat B B-26: `chmod -x` on a hook disables it with identical bytes);
    a directory is its mode (B2-33); a symlink is its target. Known limit, stated: a compiled `.pyc` in
    `__pycache__` is not hashed (Python regenerates it)."""
    st = os.lstat(p)
    if not (stat.S_ISREG(st.st_mode) or stat.S_ISDIR(st.st_mode) or stat.S_ISLNK(st.st_mode)):
        return "SPECIAL:" + _kind(st.st_mode)           # B4-09: a FIFO/device/socket is reported, never opened
    if stat.S_ISREG(st.st_mode) and st.st_size > _MAX_HASH_BYTES:
        return f"LARGE:{st.st_size}:{st.st_mtime_ns}"   # B4-09: never read past the hook's time budget
    if p.is_symlink():
        # B3-12 (reviewer A round 3): a link is compared by its text AND by the bytes/mode it resolves to (the code
        # actually imported); a link to a directory or to nothing is reported as such.
        link = hashlib.sha256(os.readlink(p).encode()).hexdigest()[:16]
        try:
            t = p.resolve(strict=True)
        except (OSError, RuntimeError):
            return f"SYMLINK:{link}:DANGLING"
        if t.is_dir():
            return f"SYMLINK:{link}:DIR"
        ts = os.stat(t)
        if not stat.S_ISREG(ts.st_mode) or ts.st_size > _MAX_HASH_BYTES:
            return f"SYMLINK:{link}:SPECIAL"            # B4-09: `-> /dev/zero` & co are never read
        h = hashlib.sha256(t.read_bytes()).hexdigest()
        return f"SYMLINK:{link}:{h}:{t.stat().st_mode & 0o7777:o}"
    if p.is_dir():
        return f"DIR:{p.stat().st_mode & 0o7777:o}"
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return f"{h.hexdigest()}:{p.stat().st_mode & 0o7777:o}"


_ENTRY_RE = re.compile(r"^(?:[0-9a-f]{64}(?::[0-7]{3,4})?|DIR:[0-7]{3,4}|SPECIAL:[a-z]+|LARGE:\d+:\d+"
                       r"|SYMLINK:[0-9a-f]{16}(?::(?:DANGLING|DIR|SPECIAL|[0-9a-f]{64}:[0-7]{3,4}))?)$")
_MAX_HASH_BYTES = 32 * 1024 * 1024


def _kind(mode: int) -> str:
    return ("fifo" if stat.S_ISFIFO(mode) else "socket" if stat.S_ISSOCK(mode) else "chardev" if stat.S_ISCHR(mode)
            else "blockdev" if stat.S_ISBLK(mode) else "other")


def integrity_report(root: Path = ROOT) -> list:
    """List of human-readable discrepancies; [] when the live governor matches the approved list."""
    try:
        ap = root.joinpath(*APPROVED)
        if ap.is_symlink() or not ap.is_file():
            return ["governance/hooks_approved.sha256 is missing: no approved hook set to compare against"]
        approved = {}
        for ln in ap.read_text(encoding="utf-8", errors="replace").splitlines():
            parts = ln.strip().split(None, 1)
            if len(parts) == 2 and _ENTRY_RE.match(parts[0]):
                approved[parts[1].strip()] = parts[0]
        issues = []
        live = governed_files(root)
        for rel in live:
            try:
                h = _sha256(root / rel)
            except OSError:
                h = "UNREADABLE"
            if h.startswith(("SPECIAL:", "LARGE:")) or h.endswith((":SPECIAL", ":DIR")):
                issues.append(f"{rel}: {h.split(':')[0] if not h.startswith('SYMLINK') else 'symlink to ' + h.split(':')[-1].lower()}"
                              f" entry, contents NOT compared (B4-09/B4-11)")
            if rel not in approved:
                issues.append(f"{rel}: NOT IN the approved list (new governor file)")
            elif approved[rel] != h:
                short = lambda x: x if not re.match(r"^[0-9a-f]{64}", x) else x[:12] + x[64:]   # keep the mode visible
                issues.append(f"{rel}: approved {short(approved[rel])}, live {short(h)}")
        for rel in approved:
            if rel not in live:
                issues.append(f"{rel}: approved but MISSING from the live hooks")
        return issues
    except Exception as exc:
        log_error("hook_support.integrity_report", exc, root)
        # B3-10 (reviewer A round 3): "could not compare" must never look like "matches"
        return [f"integrity check FAILED ({type(exc).__name__}): the governor could not be compared"]


def write_approved(root: Path = ROOT) -> str:
    lines = [f"{_sha256(root / rel)}  {rel}" for rel in governed_files(root)]
    p = root.joinpath(*APPROVED)
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return f"wrote {len(lines)} entries to {p}"


if __name__ == "__main__":
    if sys.argv[1:] == ["--write-approved"]:
        print(write_approved())
    elif sys.argv[1:] == ["--check"]:
        rep = integrity_report()
        print("\n".join(rep) if rep else "governor matches the approved list")
        sys.exit(1 if rep else 0)
    else:
        print("usage: hook_support.py --check | --write-approved")
        sys.exit(2)

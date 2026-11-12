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
import importlib.machinery
import importlib.util
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
_TREES = (".claude/hooks", "governance/adapters/claude-code", "_skills/verify_gate/scripts")
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


# --- GF-1 (PLAN_constitution_governed_fixes_s117 v0.8): the receipt checker under the governor, run from its approved bytes -------
# `_skills/verify_gate/scripts` is in _TREES, so a change to any of its files shows in integrity_report(). Both Stop hooks
# load `receipt.py` and every module it imports from that folder with vg_load(): each module's exact bytes (and mode)
# are checked against the approved list and EXECUTED FROM THOSE BYTES, never through the importer (a planted
# __pycache__/*.pyc, which the inventory excludes, therefore never runs). Every failure becomes one of the table rows of
# GF-1(d) (vg_resolve below); nothing raised by the loader or the loaded code escapes.
VG_REL = "_skills/verify_gate/scripts"
_VG_MAX_BYTES = 4_000_000


class VGRefused(Exception):
    """Row 4: a script is missing, a link, a FIFO, not regular, oversized, unreadable, has no approved entry or its
    sha256:mode differs. `args[0]` is the file name."""


class VGUnregistered(VGRefused):
    """Row 6: an import under the scripts folder that the AST scan did not pre-register."""


class VGProbeError(Exception):
    """Row 8: the marker probe could not tell armed from not armed."""


def _vg_approved(root):
    try:
        text = Path(root).joinpath(*APPROVED).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    out = {}
    for ln in text.splitlines():
        parts = ln.strip().split(None, 1)
        if len(parts) == 2 and _ENTRY_RE.match(parts[0]):
            out[parts[1].strip()] = parts[0]
    return out


def _vg_bytes(root, name, approved):
    """The exact bytes of `<scripts>/<name>.py`, checked against the approved `sha256:mode`; lstat, regular, bounded,
    no link followed, no blocking open (the `_load_sibling` checks of stop_closeout_gate.py)."""
    rel = "%s/%s.py" % (VG_REL, name)
    path = Path(root) / rel
    fname = name + ".py"
    try:
        st = os.lstat(path)
    except OSError:
        raise VGRefused(fname)
    if not stat.S_ISREG(st.st_mode) or st.st_size > _VG_MAX_BYTES:
        raise VGRefused(fname)
    want = approved.get(rel)
    if want is None:
        raise VGRefused(fname)
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    except OSError:
        raise VGRefused(fname)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise VGRefused(fname)
        chunks, total = [], 0
        while total <= _VG_MAX_BYTES:
            b = os.read(fd, 65536)
            if not b:
                break
            chunks.append(b)
            total += len(b)
    except OSError:
        raise VGRefused(fname)
    finally:
        os.close(fd)
    data = b"".join(chunks)
    if "%s:%o" % (hashlib.sha256(data).hexdigest(), st.st_mode & 0o7777) != want:
        raise VGRefused(fname)
    return data


def _vg_imports(data, root, name):
    """Names under the scripts folder that the approved bytes import (AST scan of every import statement, nested ones
    included). A relative import, or an import of a package folder under the scripts folder, cannot be pre-registered:
    VGUnregistered."""
    import ast
    scripts = Path(root) / VG_REL
    found = []
    for node in ast.walk(ast.parse(data, filename=name + ".py")):
        if isinstance(node, ast.Import):
            tops = [a.name.split(".")[0] for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                raise VGUnregistered(name + " (relative import)")
            tops = [(node.module or "").split(".")[0]]
        else:
            continue
        for top in tops:
            if not top or top == name:
                continue
            if os.path.lexists(scripts / (top + ".py")):
                found.append(top)
            elif os.path.lexists(scripts / top):
                raise VGUnregistered(top)
    return found


def _safe_text(exc, limit=200):
    """`str(exc)` that cannot raise (s232 F-1): an exception whose __str__ raises is named by its type."""
    try:
        return str(exc)[:limit]
    except BaseException:
        try:
            return "<%s>" % type(exc).__name__
        except BaseException:
            return "<exception>"


def _safe_name(exc):
    try:
        return type(exc).__name__
    except BaseException:
        return "Exception"


class _VGServed:
    """Loader that hands back an already-verified module object (a lazy import of a verified sibling)."""
    def __init__(self, mod):
        self.mod = mod

    def create_module(self, spec):
        return self.mod

    def exec_module(self, module):
        return None


class _VGGuard:
    """GF-1 (s232 F-4a): a `sys.meta_path` finder that makes the verified set the ONLY code of the scripts folder that can
    run. Consulted for any import not already in sys.modules, by the loaded scripts at load time AND later (a lazy import
    inside a function): a name that resolves to a file or package under a guarded folder is served from the verified
    module objects when it is one of them and refused with VGUnregistered otherwise, even when the folder is on sys.path
    or PYTHONPATH and the import would succeed. Imports that resolve elsewhere are untouched. Installed by vg_load and left
    in place for the life of the process (the hooks are one-shot processes); one entry per scripts folder."""
    folders = {}      # realpath of a scripts folder -> {name: module} of its verified set (filled as the load proceeds)

    @staticmethod
    def _inside(spec, folder):
        locs = list(spec.submodule_search_locations or [])
        if spec.origin and spec.origin not in ("built-in", "frozen"):
            locs.append(os.path.dirname(spec.origin))
        for loc in locs:
            try:
                real = os.path.realpath(loc)
            except OSError:
                continue
            if real == folder or real.startswith(folder + os.sep):
                return True
        return False

    @classmethod
    def find_spec(cls, fullname, path=None, target=None):
        if path is not None or not cls.folders:
            return None
        for folder, verified in list(cls.folders.items()):
            try:
                spec = importlib.machinery.PathFinder.find_spec(fullname, None)
            except Exception:
                spec = None
            if spec is None:
                if fullname in verified:
                    return importlib.util.spec_from_loader(fullname, _VGServed(verified[fullname]))
                continue
            if cls._inside(spec, folder):
                if fullname in verified:
                    return importlib.util.spec_from_loader(fullname, _VGServed(verified[fullname]))
                raise VGUnregistered(fullname)
        return None


def _vg_guard_install(root, verified):
    folder = os.path.realpath(str(Path(root) / VG_REL))
    _VGGuard.folders[folder] = verified
    if not any(f is _VGGuard for f in sys.meta_path):
        sys.meta_path.insert(0, _VGGuard)
    return folder


def _vg_refuse_preloaded(folder, verified):
    """A module of the scripts folder that is NOT in the verified set but is already in sys.modules (imported through the
    importer by something else) is code the approved list never covered: refuse (VGUnregistered)."""
    try:
        names = os.listdir(folder)
    except OSError:
        return
    for entry in names:
        stem = entry[:-3] if entry.endswith(".py") else entry
        if stem.startswith("__") or stem in verified or stem not in sys.modules:
            continue
        f = getattr(sys.modules.get(stem), "__file__", None)
        if not f:
            continue
        try:
            real = os.path.realpath(os.path.dirname(f))
        except OSError:
            continue
        if real == folder or real.startswith(folder + os.sep):
            raise VGUnregistered(stem)


def vg_load(root=None, entry="receipt"):
    """Load `entry` and, transitively, every module it imports from the scripts folder, each from its approved bytes,
    dependencies first, each put in sys.modules before its importer runs (the importer is never reached). Returns
    {name: module}. sys.modules is restored afterwards, so the hook leaves no trace in the process. Raises VGRefused,
    VGUnregistered, or whatever the loaded code raised."""
    import types
    root = Path(root) if root is not None else ROOT
    approved = _vg_approved(root)
    mods, order = {}, []
    saved = {}

    def visit(name, stack):
        if name in mods or name in stack:
            return
        data = _vg_bytes(root, name, approved)
        for dep in _vg_imports(data, root, name):
            visit(dep, stack + (name,))
        order.append((name, data))
        mods[name] = None

    try:
        visit(entry, ())
        verified = {}
        folder = _vg_guard_install(root, verified)
        _vg_refuse_preloaded(folder, set(mods))
        for name, data in order:
            saved.setdefault(name, sys.modules.get(name))
            mod = types.ModuleType(name)
            mod.__file__ = str(root / VG_REL / (name + ".py"))
            sys.modules[name] = mod
            try:
                exec(compile(data, mod.__file__, "exec", dont_inherit=True), mod.__dict__)
            except BaseException:
                sys.modules.pop(name, None)
                raise
            mods[name] = mod
            verified[name] = mod
    finally:
        for name, prev in saved.items():
            if prev is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = prev
    return mods


def _rr_walk(path):
    """Resolve an absolute, unnormalised `path` component by component with lstat/readlink only, as the kernel does (`..` applies
    after a link; at most 40 links). ("ok", resolved) | ("absent", lexical placement from the nearest existing ancestor: ENOENT
    or ENOTDIR on a component means the rest does not exist) | ("fail", None): ELOOP, EACCES, any other error, over 40 links."""
    cur, links = [], 0
    todo = [c for c in path.split("/") if c][::-1]
    while todo:
        c = todo.pop()
        if c == ".":
            continue
        if c == "..":
            if cur:
                cur.pop()
            continue
        p = "/" + "/".join(cur + [c])
        try:
            st = os.lstat(p)
        except (FileNotFoundError, NotADirectoryError):
            return "absent", os.path.normpath("/" + "/".join(cur + [c] + todo[::-1]))
        except OSError:
            return "fail", None
        if stat.S_ISLNK(st.st_mode):
            links += 1
            if links > 40:
                return "fail", None
            try:
                target = os.readlink(p)
            except OSError:
                return "fail", None
            if not target:
                return "fail", None
            if target.startswith("/"):
                cur = []
            todo.extend([x for x in target.split("/") if x][::-1])
        else:
            cur.append(c)
    return "ok", "/" + "/".join(cur)


def _rr_test_dir(d):
    """Test one ancestor `d`: lstat of BOTH `d/NEOCORTEX` and `d/.verify`, always both. "absent" when either is ENOENT/ENOTDIR (`d` is
    not a candidate; nothing is followed); "unreadable" when neither is proved absent and an error prevents deciding, or when a
    candidate is malformed (a wrong type of NEOCORTEX, .verify or armed, a dangling or looping link, any error but ENOENT);
    "unarmed" for a well-formed candidate with no `armed` (passed over); "armed" for NEOCORTEX folder + .verify folder + armed file."""
    gone = err = False
    for entry in ("NEOCORTEX", ".verify"):
        try:
            os.lstat(os.path.join(d, entry))
        except (FileNotFoundError, NotADirectoryError):
            gone = True
        except OSError:
            err = True
    if gone:
        return "absent"
    if err:
        return "unreadable"
    try:
        neo = os.stat(os.path.join(d, "NEOCORTEX")).st_mode
        ver = os.stat(os.path.join(d, ".verify")).st_mode
    except OSError:
        return "unreadable"
    if not (stat.S_ISDIR(neo) and stat.S_ISDIR(ver)):
        return "unreadable"
    marker = os.path.join(d, ".verify", "armed")
    try:
        os.lstat(marker)
    except FileNotFoundError:
        return "unarmed"
    except OSError:
        return "unreadable"
    try:
        return "armed" if stat.S_ISREG(os.stat(marker).st_mode) else "unreadable"
    except OSError:
        return "unreadable"


def _rr_ancestors(start):
    """From `start` up to `/`: ("unreadable", None) at the first unreadable candidate, ("armed", d) at the first armed root,
    (None, None) when neither is met."""
    d = start
    while True:
        state = _rr_test_dir(d)
        if state == "unreadable":
            return "unreadable", None
        if state == "armed":
            return "armed", d
        parent = os.path.dirname(d)
        if parent == d:
            return None, None
        d = parent


def _rr_place(path):
    """Place ONE path twice, lexically (normpath, `..` folded) and resolved (_rr_walk). Returns (kind, root, key) with kind in
    "unreadable" | "ambiguous" | "root" | "none". In this order: an unreadable candidate met on either placement is unreadable;
    an armed root from either placement is that root (one root spelled two ways, same device and inode, is one root; two
    different ones are ambiguous); a resolution failure and no armed root found lexically is unreadable; otherwise "none".
    A path that is not an absolute string is a resolution failure."""
    if not isinstance(path, str) or not path.startswith("/") or "\0" in path:
        return "unreadable", None, None
    lexical = _rr_ancestors(os.path.dirname(os.path.normpath(path)))
    how, placed = _rr_walk(path)
    resolved = _rr_ancestors(os.path.dirname(placed)) if how != "fail" else (None, None)
    if "unreadable" in (lexical[0], resolved[0]):
        return "unreadable", None, None
    roots = []
    for state, d in (resolved, lexical):
        if state == "armed":
            try:
                s = os.stat(d)
            except OSError:
                return "unreadable", None, None
            if (s.st_dev, s.st_ino) not in [r[0] for r in roots]:
                roots.append(((s.st_dev, s.st_ino), d))
    if len(roots) > 1:
        return "ambiguous", None, None
    if roots:
        return "root", roots[0][1], roots[0][0]
    if how == "fail":
        return "unreadable", None, None
    return "none", None, None


def _rr_scan(paths):
    """Across paths: unreadable > ambiguous > root > none; two different roots among paths are ambiguous. Returns (kind, root)."""
    try:
        items = list(paths or [])
    except Exception:
        return "unreadable", None
    roots, ambiguous = [], False
    for fp in items:
        try:
            kind, d, key = _rr_place(fp)
        except Exception:
            return "unreadable", None
        if kind == "unreadable":
            return "unreadable", None
        if kind == "ambiguous":
            ambiguous = True
        elif kind == "root" and key not in [r[0] for r in roots]:
            roots.append((key, d))
    if ambiguous or len(roots) > 1:
        return "ambiguous", None
    if roots:
        return "root", roots[0][1]
    return "none", None


def vg_probe(edited_paths):
    """Module-free armed-ness probe (GF-1(d) rule 2, decision D4), the R-3 v0.7 placement rule (`_rr_scan`, the same text as
    receipt.project_root). True iff the paths have an armed root (an ambiguous one counts: the gate treats it as armed); False
    when none does; raises VGProbeError where project_root says "armed state unreadable". Never names a root."""
    kind, _root = _rr_scan(edited_paths)
    if kind == "unreadable":
        raise VGProbeError("armed state unreadable")
    return kind in ("root", "ambiguous")


def vg_failure(exc, root=None):
    """Map an exception from the loader or the loaded code to (row, text) of GF-1(d). Never raises (s232 F-1): the
    exception's text is formatted by `_safe_text`, and any trouble inside the mapping itself is row 7."""
    try:
        if isinstance(exc, VGUnregistered):
            return 6, "verify_gate scripts not approved: %s (unregistered import)" % _safe_text(exc.args[0] if exc.args else "?")
        if isinstance(exc, VGRefused):
            return 4, "verify_gate scripts not approved: %s" % _safe_text(exc.args[0] if exc.args else "?")
        if isinstance(exc, ModuleNotFoundError) and getattr(exc, "name", None):
            top = exc.name.split(".")[0]
            base = Path(root) if root is not None else ROOT
            if os.path.lexists(base / VG_REL / (top + ".py")) or os.path.lexists(base / VG_REL / top):
                return 6, "verify_gate scripts not approved: %s (unregistered import)" % top
        return 7, "verify_gate script failed: %s: %s" % ("receipt", ("%s: %s" % (_safe_name(exc), _safe_text(exc)))[:200])
    except BaseException as inner:
        if isinstance(inner, KeyboardInterrupt):
            raise
        return 7, "verify_gate script failed: receipt: %s" % _safe_name(exc)


# One line of integrity_report() about ONE governed file: "<rel>: NOT IN the approved list ...", "<rel>: approved X, live Y",
# "<rel>: approved but MISSING ...", "<rel>: <kind> entry, contents NOT compared ...". Any other line is the report itself
# failing ("integrity check FAILED (...)", "governance/hooks_approved.sha256 is missing: ...") and is a failure (s232 F-4b).
_PER_FILE_LINE = re.compile(r"^.+?: (?:NOT IN the approved|approved |.+ entry, contents NOT compared)")


def vg_prepare(root):
    """One boundary (GF-1(d) rule 1): load the set from approved bytes, then check `integrity_report()` for the scripts
    folder. Never raises. {"ok": True, "mods": {...}} or {"ok": False, "row": 4|5|6|7, "text": ...} (row 5 reads as row 4)."""
    try:
        mods = vg_load(root)
    except BaseException as exc:
        if isinstance(exc, KeyboardInterrupt):
            raise
        row, text = vg_failure(exc, root)
        return {"ok": False, "row": row, "text": text}
    try:
        report = integrity_report(Path(root))
    except BaseException as exc:
        if isinstance(exc, KeyboardInterrupt):
            raise
        return {"ok": False, "row": 7, "text": "verify_gate script failed: integrity_report: %s: %s"
                % (_safe_name(exc), _safe_text(exc))}
    try:
        for line in report:
            if line.startswith(VG_REL + "/"):
                fname = line.split(":", 1)[0].rsplit("/", 1)[-1]
                return {"ok": False, "row": 5, "text": "verify_gate scripts not approved: %s" % fname}
        for line in report:
            if not _PER_FILE_LINE.match(line):
                return {"ok": False, "row": 7, "text": "verify_gate script failed: integrity_report: %s" % _safe_text(line)}
    except BaseException as exc:
        if isinstance(exc, KeyboardInterrupt):
            raise
        return {"ok": False, "row": 7, "text": "verify_gate script failed: integrity_report: %s" % _safe_name(exc)}
    return {"ok": True, "mods": mods}


def vg_resolve(root, edited_paths):
    """vg_prepare + `receipt.project_root(edited_paths)`, all behind the same boundary. Never raises. On success:
    {"ok": True, "root": Path or None, "reason": str, "mods": {...}}; else {"ok": False, "row", "text"}."""
    try:
        prep = vg_prepare(root)
        if not prep["ok"]:
            return prep
        r, reason = prep["mods"]["receipt"].project_root(edited_paths)
        return {"ok": True, "root": Path(r) if r is not None else None, "reason": reason, "mods": prep["mods"]}
    except BaseException as exc:
        if isinstance(exc, KeyboardInterrupt):
            raise
        row, text = vg_failure(exc, root)
        return {"ok": False, "row": row, "text": text}


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

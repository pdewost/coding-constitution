"""verify_gate.receipt — a VERIFY-RECEIPT bound to the CHANGE-SET, time-fresh, spent once by the CLI.

Why (3rd review, finding B5): a receipt that hashes only the verdict replays across every
NEOCORTEX loss mode — edit-after-receipt, session resume, a fresh session on the same work,
a crash/restart. So the receipt is bound to a hash of the actual change-set, is spent once
(`validate_and_consume`, which `verify_gate.py --receipt` calls) into a consumed-nonce ledger
under an exclusive lock, and is valid only within a freshness window AND newer than every
changed file's mtime. `validate_receipt` only checks (the hooks call it and spend nothing).
The ledger fails closed: absent = empty; present but unreadable or malformed = every receipt
invalid, and it is never rewritten. Honest limit: on a local box a determined agent can still forge a
receipt — this raises the bar from 'echo a magic string' to 'produce a real attested verdict +
a matching-changeset receipt', defence-in-depth, not a cryptographic guarantee.
"""
from __future__ import annotations

import contextlib
import dataclasses
import hashlib
import json
import math
import os
import re
import stat
import sys
import time
import uuid

import core  # sibling module: the hooks pre-register it from approved bytes (GF-1); the CLIs put this folder on sys.path

SCHEMA_V1 = "verify_gate.receipt.v1"
SCHEMA_V2 = "verify_gate.receipt.v2"
_MAX_READ = 64 * 1024 * 1024  # a verdict, attestation, ledger or changed file larger than this is refused
DEFAULT_WINDOW_S = 3600  # a receipt older than this is stale even if the change-set matches
FUTURE_SKEW_S = 300      # a receipt stamped further ahead than this is invalid (it would never go stale)
_NONCE = re.compile(r"[0-9a-f]{32}")  # the form of uuid4().hex, as write_receipt mints it
LOCK_TIMEOUT_S = 30      # the ledger lock is waited for at most this long (0.3.1); patchable for tests
_LOCK_RETRY_S = 0.05     # LOCK_EX|LOCK_NB is retried at this interval


class LedgerError(Exception):
    """The consumed-nonce ledger cannot be trusted or written; the caller fails closed."""


class UnsafeFile(Exception):
    """A file the validator must read is a link, a FIFO, hard-linked, too large or otherwise not a plain file."""


def _safe_read(path: str) -> tuple[bytes, float] | None:
    """Read `path` once: no symlink in ANY component (absolute walk from `/`, O_NOFOLLOW), a regular file with one
    link, opened non-blocking so a FIFO fails at once (pattern of verify_fires.py `_safe_read_text`, R-2).
    Returns (bytes, mtime) or None when a component or the file is absent; raises UnsafeFile otherwise."""
    full = path if os.path.isabs(path) else os.path.join(os.getcwd(), path)
    parts = [x for x in full.split(os.sep) if x and x != "."]   # ".." is left to the kernel, never folded lexically
    if not parts:
        raise UnsafeFile(f"{path}: not a file")
    dfd = os.open(os.sep, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in parts[:-1]:
            try:
                nfd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=dfd)
            except FileNotFoundError:
                return None
            except OSError as e:
                raise UnsafeFile(f"{path}: unsafe path component {part!r} ({e})") from e
            os.close(dfd)
            dfd = nfd
        try:
            fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dfd)
        except FileNotFoundError:
            return None
        except OSError as e:
            raise UnsafeFile(f"{path}: refused ({e})") from e
        try:
            try:
                st = os.fstat(fd)
                if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1 or st.st_size > _MAX_READ:
                    raise UnsafeFile(f"{path}: not a regular single-link file of bounded size")
                chunks, total = [], 0
                while True:
                    b = os.read(fd, 65536)
                    if not b:
                        break
                    total += len(b)
                    if total > _MAX_READ:     # a running cap: a file that grows under the read cannot run past the bound (s232 F-5)
                        raise UnsafeFile(f"{path}: grew past the {_MAX_READ}-byte bound while being read")
                    chunks.append(b)
                after = os.fstat(fd)
                if (after.st_size, after.st_mtime_ns, after.st_ino) != (st.st_size, st.st_mtime_ns, st.st_ino) \
                        or total != st.st_size:
                    raise UnsafeFile(f"{path}: modified while being read")
                return b"".join(chunks), st.st_mtime
            except OSError as e:     # a read or fstat failure (EIO, EINTR, ...) refuses like every other unsafe shape (s241 F-4)
                raise UnsafeFile(f"{path}: read failed ({e})") from e
        finally:
            os.close(fd)
    finally:
        os.close(dfd)


def _safe_read_bytes(path: str) -> bytes | None:
    r = _safe_read(path)
    return None if r is None else r[0]


def qualify_path(path: str) -> str:
    """R-2 path qualification (one rule): an absolute path is kept as spelled; a relative one is joined to the caller's cwd with a
    plain separator join: no `normpath`, no `realpath`, so `./` and `..` survive into the hash and into the resolved walk."""
    return path if os.path.isabs(path) else os.path.join(os.getcwd(), path)


def changeset_sha256(paths: list[str]) -> str:
    """Hash the content of the changed files, sorted by path. Any edit to any file flips it,
    so a receipt for change-set A cannot satisfy a gate over change-set B. Each file is read ONCE through `_safe_read` (the
    same read validation performs): a link in any component, a FIFO, a folder or a hard-linked file raises UnsafeFile
    (it hashed a FIFO as `<non-regular>` and a folder as `<missing>` before); an absent file is `<missing>`."""
    return _hash_changed(paths)[0]


@dataclasses.dataclass
class Receipt:
    changeset_sha256: str
    verdict_sha256: str
    gate_exit: int
    ts: float
    attestation_ref: str
    nonce: str
    verdict_ref: str = ""
    attestation_sha256: str = ""
    schema: str = SCHEMA_V2

    def as_dict(self) -> dict:
        return dataclasses.asdict(self)


def verdict_sha256(verdict_path: str) -> str:
    with open(verdict_path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def issuable(verdict: dict, attestation: dict) -> tuple[bool, str]:
    """R-1/R-2: the one predicate for issuing AND accepting a receipt. True only when core.evaluate returns PASS
    with no block finding (a refutation, even an un-anchored one, never earns a receipt) AND the attestation makes the
    verdict block-eligible (an un-anchored clean verdict earns none either)."""
    try:
        d = core.evaluate(verdict, attestation, require_blocking=False)
        if d.exit_code != core.ExitCode.PASS:
            return False, f"core.evaluate exit {d.exit_code} (not PASS)"
        if d.block_findings:
            return False, "the verdict holds a substantiated HIGH refutation"
        if not core.block_eligible(attestation, verdict)[0]:
            return False, "the verdict is not externally anchored (advisory only)"
    except Exception as e:  # a malformed shape must refuse, not raise (one try around evaluate AND block_eligible, s238 F-9)
        return False, f"verdict/attestation not evaluable: {type(e).__name__}"
    return True, "issuable"


def write_receipt(*, changed_paths: list[str], verdict_path: str, gate_exit: int,
                  attestation_ref: str) -> Receipt:
    """Issue a v2 receipt. Both artifact refs are stored absolute and link-free (os.path.realpath); the verdict and the
    attestation are each read once, evaluated with `issuable` and hashed from those same bytes. Raises UnsafeFile when an
    artifact cannot be read, is missing, or is not issuable."""
    v_ref, a_ref = os.path.realpath(verdict_path), os.path.realpath(attestation_ref)
    vb, ab = _safe_read_bytes(v_ref), _safe_read_bytes(a_ref)
    for ref, b in ((v_ref, vb), (a_ref, ab)):
        if b is None:
            raise UnsafeFile(f"{ref}: missing")      # s241 F-4: the one refusal contract (callers catch UnsafeFile)
    # s241 F-2: the decision is taken on the very bytes that are hashed below, not on an earlier read of the same path
    vobj, aobj = _json_obj(vb), _json_obj(ab)
    if vobj is None or aobj is None:
        raise UnsafeFile(f"{v_ref if vobj is None else a_ref}: not a JSON object")
    ok, why = issuable(vobj, aobj)
    if not ok:
        raise UnsafeFile(f"not issuable from the bytes read for hashing: {why}")
    return Receipt(
        changeset_sha256=changeset_sha256(changed_paths),
        verdict_sha256=hashlib.sha256(vb).hexdigest(),
        gate_exit=gate_exit,
        ts=time.time(),
        attestation_ref=a_ref,
        nonce=uuid.uuid4().hex,
        verdict_ref=v_ref,
        attestation_sha256=hashlib.sha256(ab).hexdigest(),
    )


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


def project_root(changed_paths):
    """R-3 (v0.7): the ONE project-root rule (the Stop gate and the CLI both call it; vg_probe and the gate's module-free probe
    hold the same placement text). Returns (root, reason); never raises. Paths are absolute (a relative one is a resolution
    failure). (root, "ok") when the paths that have an armed root all share one; (None, "no project root") when none has one;
    (None, "ambiguous project root") when two differ; (None, "armed state unreadable") when uncertainty meets an armed or
    candidate root, or a path cannot be resolved (uncertainty is never read as "not armed"). Precedence across paths:
    unreadable > ambiguous > root > none. Placement is `_rr_place`: lexical and resolved, both entries of every ancestor read."""
    import pathlib
    kind, root = _rr_scan(changed_paths)
    if kind == "root":
        return pathlib.Path(root), "ok"
    return None, {"unreadable": "armed state unreadable", "ambiguous": "ambiguous project root",
                  "none": "no project root"}[kind]


def _nonce_ok(nonce) -> bool:
    return isinstance(nonce, str) and _NONCE.fullmatch(nonce) is not None


def _load_ledger(ledger_path: str) -> set[str]:
    """Absent = empty. Present but unreadable, not a regular file, hard-linked (st_nlink != 1), reached through a
    link, not a JSON list, or holding anything but nonces of the uuid4().hex form => LedgerError (fail closed: it used to
    read as empty, which revived every spent nonce and, on the next consume, erased the history). One safe read (R-2)."""
    try:
        raw = _safe_read_bytes(ledger_path)
        if raw is None:
            return set()
        data = json.loads(raw.decode("utf-8"))
    except (OSError, UnsafeFile, ValueError, RecursionError, MemoryError) as e:
        raise LedgerError(f"nonce ledger unreadable: {ledger_path}") from e
    if not isinstance(data, list) or not all(_nonce_ok(n) for n in data):
        raise LedgerError(f"nonce ledger unreadable: {ledger_path}")
    return set(data)


def _folder(ledger_path: str) -> str:
    """The ledger's folder, as the path spells it (no abspath: the lock, the temp file and the folder fsync all
    resolve it the same way the kernel does for the ledger itself)."""
    return os.path.dirname(ledger_path) or "."


def _refuse_symlinked_folder(folder: str) -> None:
    try:
        is_link = stat.S_ISLNK(os.lstat(folder).st_mode)
    except OSError as e:
        raise LedgerError(f"nonce ledger folder unusable: {folder}: {e}") from e
    if is_link:
        raise LedgerError(f"nonce ledger folder is a symlink: {folder}")


@contextlib.contextmanager
def _locked(ledger_path: str):
    """An exclusive flock on the sidecar `<ledger>.lock`, which is never replaced (a lock on the ledger
    itself would be lost when os.replace swaps its inode). Yields the lock descriptor, so the write can check
    that the lock file is still the one at its path. The wait has a deadline (LOCK_TIMEOUT_S). Any OSError
    while preparing or taking the lock => LedgerError. Not POSIX (no fcntl) => LedgerError."""
    try:
        import fcntl
    except ImportError as e:
        raise LedgerError("nonce ledger lock unavailable (no fcntl: not POSIX); refusing") from e
    lock = ledger_path + ".lock"
    try:
        folder = _folder(ledger_path)
        os.makedirs(folder, exist_ok=True)
        _refuse_symlinked_folder(folder)
        fd = os.open(lock, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o666)
    except OSError as e:
        raise LedgerError(f"nonce ledger lock refused: {lock}: {e}") from e
    try:
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise LedgerError(f"nonce ledger lock is not a regular file: {lock}")
            deadline = time.monotonic() + LOCK_TIMEOUT_S
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except (BlockingIOError, InterruptedError):
                    if time.monotonic() >= deadline:
                        raise LedgerError("nonce ledger lock timeout") from None
                    time.sleep(_LOCK_RETRY_S)
        except OSError as e:
            raise LedgerError(f"nonce ledger lock refused: {lock}: {e}") from e
        yield fd
    finally:
        os.close(fd)


class _SpentNotSynced(LedgerError):
    """os.replace (the commit point) succeeded; the folder fsync after it failed. The nonce IS spent."""


def _sync(fd: int) -> None:
    """Flush to the drive: F_FULLFSYNC on Darwin (plain fsync does not flush the drive cache there), falling back
    to os.fsync on OSError; os.fsync elsewhere."""
    if sys.platform == "darwin":
        import fcntl
        full = getattr(fcntl, "F_FULLFSYNC", None)
        if full is not None:
            try:
                fcntl.fcntl(fd, full)
                return
            except OSError:
                pass
    os.fsync(fd)


def _write_ledger(ledger_path: str, used: set[str], lock_fd: int) -> None:
    """Write a temp file in the same folder, fsync, os.replace (the commit point), fsync the folder.
    Before the commit point a failure leaves the ledger unchanged and removes the temp; the final
    ledger path is never opened for writing. Just before the commit point the lock file at its path must still
    be the one `lock_fd` holds (same st_dev, st_ino), else LedgerError. Residual race: a swap of the lock file
    between that check and the rename is not detected (same-account write access is out of scope, see the
    header). The ledger folder must not be a symlink."""
    folder = _folder(ledger_path)
    try:
        _refuse_symlinked_folder(folder)
    except LedgerError as e:
        raise LedgerError(f"nonce ledger write failed, ledger unchanged: {e}") from e
    tmp = os.path.join(folder, f".{os.path.basename(ledger_path)}.{uuid.uuid4().hex}.tmp")
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_NONBLOCK, 0o666)
    except OSError as e:
        raise LedgerError(f"nonce ledger write failed, ledger unchanged: {e}") from e
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(sorted(used), fh)
            fh.flush()
            _sync(fh.fileno())
        held, at_path = os.fstat(lock_fd), os.stat(ledger_path + ".lock")
        if (held.st_dev, held.st_ino) != (at_path.st_dev, at_path.st_ino):
            raise LedgerError("nonce ledger lock file was replaced while held")
        os.replace(tmp, ledger_path)
    except BaseException as e:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        if isinstance(e, Exception):
            raise LedgerError(f"nonce ledger write failed, ledger unchanged: {e}") from e
        raise
    try:
        dfd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY)
        try:
            _sync(dfd)
        finally:
            os.close(dfd)
    except OSError as e:
        raise _SpentNotSynced(f"nonce spent, but the ledger folder was not synced: {e}") from e


def consume(nonce: str, ledger_path: str) -> None:
    """Spend `nonce` (kept as an API; 0.3.0: on the same locked atomic write as validate_and_consume).
    Raises LedgerError on a malformed nonce, a corrupt ledger, or a failed write."""
    if not _nonce_ok(nonce):
        raise LedgerError("nonce missing or malformed")
    with _locked(ledger_path) as lock_fd:
        used = _load_ledger(ledger_path)
        used.add(nonce)
        _write_ledger(ledger_path, used, lock_fd)


def _json_obj(raw: bytes):
    try:
        v = json.loads(raw.decode("utf-8"))
    except (ValueError, RecursionError, MemoryError):
        return None
    return v if isinstance(v, dict) else None


def _hash_changed(changed_paths: list[str]) -> tuple[str, float]:
    """The change-set hash, same encoding as changeset_sha256, but each file is read ONCE through _safe_read: a link,
    a FIFO or a multiply-linked file raises UnsafeFile (R-2: changed paths are not normalised, a link among them is
    refused). Absent => "<missing>", as at issuance. Returns (hash, newest mtime)."""
    h = hashlib.sha256()
    newest = 0.0
    for p in sorted(changed_paths):
        h.update(p.encode("utf-8"))
        h.update(b"\0")
        r = _safe_read(p)
        if r is None:
            h.update(b"<missing>")
        else:
            h.update(hashlib.sha256(r[0]).digest())
            newest = max(newest, r[1])
        h.update(b"\0")
    return h.hexdigest(), newest


def _check(receipt: dict, *, changed_paths: list[str], ledger_path: str,
           window_s: int, now: float | None) -> tuple[bool, str, set[str] | None]:
    """Order (R-2): schema and time window (no file read); the two artifacts; the change-set; the ledger."""
    now = time.time() if now is None else now
    if isinstance(receipt, dict) and receipt.get("schema") == SCHEMA_V1:
        return False, "re-issue under v2 (a v1 receipt is no longer accepted)", None
    if not isinstance(receipt, dict) or receipt.get("schema") != SCHEMA_V2:
        return False, "bad receipt schema", None
    if receipt.get("gate_exit") not in (0,):
        return False, f"receipt records a non-pass gate_exit={receipt.get('gate_exit')}", None
    ts = receipt.get("ts", 0)
    if isinstance(ts, bool) or not isinstance(ts, (int, float)):
        return False, "bad receipt ts", None
    try:
        finite = math.isfinite(ts)
    except OverflowError:   # an int too large for a float
        finite = False
    if not finite:
        return False, "bad receipt ts", None
    if now - ts > window_s:
        return False, f"receipt stale (> {window_s}s old)", None
    if ts - now > FUTURE_SKEW_S:
        return False, f"receipt stamped in the future (> {FUTURE_SKEW_S}s ahead)", None
    if not _nonce_ok(receipt.get("nonce")):
        return False, "receipt nonce missing or malformed", None
    refs = {k: receipt.get(k) for k in ("verdict_ref", "attestation_ref")}
    for k, v in refs.items():
        if not isinstance(v, str) or not os.path.isabs(v):
            return False, f"receipt {k} missing or not absolute", None
    shas = {k: receipt.get(k) for k in ("verdict_sha256", "attestation_sha256")}
    parsed = {}
    for ref_k, sha_k in (("verdict_ref", "verdict_sha256"), ("attestation_ref", "attestation_sha256")):
        try:
            raw = _safe_read_bytes(refs[ref_k])
        except UnsafeFile as e:
            return False, f"{ref_k} unsafe: {e}", None
        except OSError as e:
            return False, f"{ref_k} unreadable: {e}", None
        if raw is None:
            return False, f"{ref_k} missing", None
        if hashlib.sha256(raw).hexdigest() != shas[sha_k]:
            return False, f"{ref_k} changed since issuance ({sha_k} mismatch)", None
        obj = _json_obj(raw)
        if obj is None:
            return False, f"{ref_k} is not a JSON object", None
        parsed[ref_k] = obj
    ok, why = issuable(parsed["verdict_ref"], parsed["attestation_ref"])
    if not ok:
        return False, f"decision re-derived from the artifacts refuses: {why}", None
    try:
        cur, newest = _hash_changed(changed_paths)
    except UnsafeFile as e:
        return False, f"changed path unsafe: {e}", None
    except OSError as e:
        return False, f"changed path unreadable: {e}", None
    if receipt.get("changeset_sha256") != cur:
        return False, "change-set hash mismatch (work changed since the receipt)", None
    if ts < newest:
        return False, "receipt predates the newest changed file (edited after verification)", None
    try:
        used = _load_ledger(ledger_path)
    except LedgerError:
        return False, f"nonce ledger unreadable: {ledger_path}", None
    if receipt["nonce"] in used:
        return False, "receipt already consumed (replay)", None
    return True, "receipt valid for the current change-set", used


def validate_receipt(receipt: dict, *, changed_paths: list[str], ledger_path: str,
                     window_s: int = DEFAULT_WINDOW_S, now: float | None = None) -> tuple[bool, str]:
    """Returns (ok, reason). Read-only: no lock, no write (the hooks call it and spend nothing).
    A receipt is valid for the CURRENT change-set only if: its change-set hash matches now; it is
    within the freshness window, not stamped over FUTURE_SKEW_S ahead, and not older than the
    newest changed file; its nonce has the uuid4().hex form; and the ledger is readable and does
    not hold that nonce."""
    ok, why, _used = _check(receipt, changed_paths=changed_paths, ledger_path=ledger_path,
                            window_s=window_s, now=now)
    return ok, why


def validate_and_consume(receipt: dict, *, changed_paths: list[str], ledger_path: str,
                         window_s: int = DEFAULT_WINDOW_S, now: float | None = None) -> tuple[bool, str]:
    """Validate and spend in one step, under the ledger lock: of N concurrent calls on one receipt
    on one ledger, at most one returns True. Any failure returns (False, reason); after the commit point (os.replace)
    a failed folder fsync returns a distinct reason with the nonce spent."""
    try:
        with _locked(ledger_path) as lock_fd:
            ok, why, used = _check(receipt, changed_paths=changed_paths, ledger_path=ledger_path,
                                   window_s=window_s, now=now)
            if not ok:
                return False, why
            used.add(receipt["nonce"])
            _write_ledger(ledger_path, used, lock_fd)
            return True, why
    except LedgerError as e:
        return False, str(e)

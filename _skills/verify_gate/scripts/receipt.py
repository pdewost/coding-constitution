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

DEFAULT_WINDOW_S = 3600  # a receipt older than this is stale even if the change-set matches
FUTURE_SKEW_S = 300      # a receipt stamped further ahead than this is invalid (it would never go stale)
_NONCE = re.compile(r"[0-9a-f]{32}")  # the form of uuid4().hex, as write_receipt mints it
LOCK_TIMEOUT_S = 30      # the ledger lock is waited for at most this long (0.3.1); patchable for tests
_LOCK_RETRY_S = 0.05     # LOCK_EX|LOCK_NB is retried at this interval


class LedgerError(Exception):
    """The consumed-nonce ledger cannot be trusted or written; the caller fails closed."""


def changeset_sha256(paths: list[str]) -> str:
    """Hash the content of the changed files, sorted by path. Any edit to any file flips it,
    so a receipt for change-set A cannot satisfy a gate over change-set B."""
    h = hashlib.sha256()
    for p in sorted(paths):
        h.update(p.encode("utf-8"))
        h.update(b"\0")
        try:
            # O_NONBLOCK: opening a FIFO must not wait for a writer (0.3.1, VG-5). A non-regular file is
            # hashed as a marker and never read; a directory stays "<missing>", as when it was opened with open().
            fd = os.open(p, os.O_RDONLY | os.O_NONBLOCK)
        except OSError:
            h.update(b"<missing>")
        else:
            with os.fdopen(fd, "rb") as fh:
                mode = os.fstat(fh.fileno()).st_mode
                if stat.S_ISREG(mode):
                    h.update(hashlib.sha256(fh.read()).digest())
                elif stat.S_ISDIR(mode):
                    h.update(b"<missing>")
                else:
                    h.update(b"<non-regular>")
        h.update(b"\0")
    return h.hexdigest()


@dataclasses.dataclass
class Receipt:
    changeset_sha256: str
    verdict_sha256: str
    gate_exit: int
    ts: float
    attestation_ref: str
    nonce: str
    schema: str = "verify_gate.receipt.v1"

    def as_dict(self) -> dict:
        return dataclasses.asdict(self)


def verdict_sha256(verdict_path: str) -> str:
    with open(verdict_path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def write_receipt(*, changed_paths: list[str], verdict_path: str, gate_exit: int,
                  attestation_ref: str) -> Receipt:
    return Receipt(
        changeset_sha256=changeset_sha256(changed_paths),
        verdict_sha256=verdict_sha256(verdict_path),
        gate_exit=gate_exit,
        ts=time.time(),
        attestation_ref=attestation_ref,
        nonce=uuid.uuid4().hex,
    )


def _nonce_ok(nonce) -> bool:
    return isinstance(nonce, str) and _NONCE.fullmatch(nonce) is not None


def _load_ledger(ledger_path: str) -> set[str]:
    """Absent = empty. Present but unreadable, not a regular file, hard-linked (st_nlink != 1), not a JSON list, or holding anything
    but nonces of the uuid4().hex form => LedgerError (fail closed: it used to read as empty, which
    revived every spent nonce and, on the next consume, erased the history)."""
    try:
        fd = os.open(ledger_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return set()
    except OSError as e:
        raise LedgerError(f"nonce ledger unreadable: {ledger_path}") from e
    try:
        with os.fdopen(fd, "rb") as fh:
            st = os.fstat(fh.fileno())
            # st_nlink != 1: a second name for the ledger has its own lock file, so two locks guard one file
            if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
                raise LedgerError(f"nonce ledger unreadable: {ledger_path}")
            data = json.loads(fh.read().decode("utf-8"))
    except (OSError, ValueError, RecursionError, MemoryError) as e:
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


def _check(receipt: dict, *, changed_paths: list[str], ledger_path: str,
           window_s: int, now: float | None) -> tuple[bool, str, set[str] | None]:
    now = time.time() if now is None else now
    if not isinstance(receipt, dict) or receipt.get("schema") != "verify_gate.receipt.v1":
        return False, "bad receipt schema", None
    if receipt.get("gate_exit") not in (0,):
        return False, f"receipt records a non-pass gate_exit={receipt.get('gate_exit')}", None
    cur = changeset_sha256(changed_paths)
    if receipt.get("changeset_sha256") != cur:
        return False, "change-set hash mismatch (work changed since the receipt)", None
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
    newest = 0.0
    for p in changed_paths:
        try:
            newest = max(newest, os.path.getmtime(p))
        except OSError:
            pass
    if ts < newest:
        return False, "receipt predates the newest changed file (edited after verification)", None
    if not _nonce_ok(receipt.get("nonce")):
        return False, "receipt nonce missing or malformed", None
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

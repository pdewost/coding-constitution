"""verify_gate.receipt — a VERIFY-RECEIPT bound to the CHANGE-SET, single-use, time-fresh.

Why (3rd review, finding B5): a receipt that hashes only the verdict replays across every
NEOCORTEX loss mode — edit-after-receipt, session resume, a fresh session on the same work,
a crash/restart. So the receipt is bound to a hash of the actual change-set, is single-use via
a gate-owned consumed-nonce ledger, and is valid only within a freshness window AND newer than
every changed file's mtime. Honest limit: on a local box a determined agent can still forge a
receipt — this raises the bar from 'echo a magic string' to 'produce a real attested verdict +
a matching-changeset receipt', defence-in-depth, not a cryptographic guarantee.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import time
import uuid

DEFAULT_WINDOW_S = 3600  # a receipt older than this is stale even if the change-set matches


def changeset_sha256(paths: list[str]) -> str:
    """Hash the content of the changed files, sorted by path. Any edit to any file flips it,
    so a receipt for change-set A cannot satisfy a gate over change-set B."""
    h = hashlib.sha256()
    for p in sorted(paths):
        h.update(p.encode("utf-8"))
        h.update(b"\0")
        try:
            with open(p, "rb") as fh:
                h.update(hashlib.sha256(fh.read()).digest())
        except OSError:
            h.update(b"<missing>")
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


def _load_ledger(ledger_path: str) -> set[str]:
    try:
        with open(ledger_path, encoding="utf-8") as fh:
            return set(json.load(fh))
    except (OSError, ValueError):
        return set()


def consume(nonce: str, ledger_path: str) -> None:
    used = _load_ledger(ledger_path)
    used.add(nonce)
    os.makedirs(os.path.dirname(ledger_path) or ".", exist_ok=True)
    with open(ledger_path, "w", encoding="utf-8") as fh:
        json.dump(sorted(used), fh)


def validate_receipt(receipt: dict, *, changed_paths: list[str], ledger_path: str,
                     window_s: int = DEFAULT_WINDOW_S, now: float | None = None) -> tuple[bool, str]:
    """Returns (ok, reason). A receipt is valid for the CURRENT change-set only if: its
    change-set hash matches now; it is within the freshness window AND not older than the
    newest changed file; and its nonce has not already been consumed."""
    now = time.time() if now is None else now
    if receipt.get("schema") != "verify_gate.receipt.v1":
        return False, "bad receipt schema"
    if receipt.get("gate_exit") not in (0,):
        return False, f"receipt records a non-pass gate_exit={receipt.get('gate_exit')}"
    cur = changeset_sha256(changed_paths)
    if receipt.get("changeset_sha256") != cur:
        return False, "change-set hash mismatch (work changed since the receipt)"
    ts = receipt.get("ts", 0)
    if now - ts > window_s:
        return False, f"receipt stale (> {window_s}s old)"
    newest = 0.0
    for p in changed_paths:
        try:
            newest = max(newest, os.path.getmtime(p))
        except OSError:
            pass
    if ts < newest:
        return False, "receipt predates the newest changed file (edited after verification)"
    if receipt.get("nonce") in _load_ledger(ledger_path):
        return False, "receipt already consumed (replay)"
    return True, "receipt valid for the current change-set"

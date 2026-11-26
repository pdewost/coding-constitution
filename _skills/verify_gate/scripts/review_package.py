"""verify_gate.review_package — RV3-1: the review package, fingerprinted when built (receipt-v3).

`build(changed_paths, outdir)` reads each file ONCE with the receipt module's no-follow safe read (a link in any component,
a FIFO, a folder, a hard-linked file or an oversize file raises UnsafeFile) and writes:
  - `outdir/package/<i>`: a read-only (0444) copy of each file's bytes: the snapshot the judge reads (founder ruling D-2);
  - `outdir/package.json`: {"schema": "verify_gate.package.v1", "files": [{"path", "sha256", "size", "copy"}], "built_ts"},
    files sorted by their qualified path.
`package_sha256` is the sha256 of the canonical JSON (sorted keys, no spaces) of the `files` list. It is never stored in
package.json: every reader derives it from the bytes it read (`read`). Honest limit: a snapshot proves what the judge was
HANDED, not that it read every file (PLAN §6).
"""
from __future__ import annotations

import hashlib
import json
import os
import time

import receipt  # sibling module (the CLIs put this folder on sys.path; the hooks pre-register it from approved bytes)

SCHEMA = receipt.PACKAGE_SCHEMA


def fingerprint(files: list) -> str:
    return receipt.package_fingerprint(files)


def build(changed_paths: list[str], outdir: str) -> dict:
    """Build the package. Returns {"package_path", "package_sha256", "snapshot", "files"} (absolute, link-free paths).
    Raises receipt.UnsafeFile for a source the safe read refuses or that is absent, and FileExistsError when
    `outdir/package/` already exists (a package is built once, never refreshed in place)."""
    qualified = sorted({receipt.qualify_path(p) for p in changed_paths})
    blobs = []
    for p in qualified:
        b = receipt._safe_read_bytes(p)
        if b is None:
            raise receipt.UnsafeFile(f"{p}: missing (a package holds only files that exist)")
        blobs.append(b)
    os.makedirs(outdir, exist_ok=True)
    out = os.path.realpath(outdir)
    snap = os.path.join(out, "package")
    os.mkdir(snap)
    files = []
    for i, (p, b) in enumerate(zip(qualified, blobs)):
        copy = os.path.join(snap, str(i))
        fd = os.open(copy, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(b)
        os.chmod(copy, 0o444)
        files.append({"path": p, "sha256": hashlib.sha256(b).hexdigest(), "size": len(b), "copy": f"package/{i}"})
    pkg_path = os.path.join(out, "package.json")
    fd = os.open(pkg_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump({"schema": SCHEMA, "files": files, "built_ts": time.time()}, fh, indent=2)
    os.chmod(pkg_path, 0o444)
    return {"package_path": pkg_path, "package_sha256": fingerprint(files), "snapshot": snap, "files": files}


def read(package_path: str) -> tuple[list, str]:
    """Re-read package.json once (safe read) and derive its fingerprint from those bytes. Returns (files, package_sha256).
    Raises receipt.UnsafeFile when it is absent, unsafe, or not a well-formed v1 package (one reader: receipt.read_package)."""
    return receipt.read_package(package_path)

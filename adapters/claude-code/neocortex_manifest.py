#!/usr/bin/env python3
"""
neocortex_manifest.py — NEOCORTEX MANIFEST validator and regenerator.

Implements NEOCORTEX_SPEC v1.0 §2 validation and §4 reporting.

Usage:
    # Validate a project's NEOCORTEX:
    python3 neocortex_manifest.py --check /path/to/project

    # Rebuild files[] from directory (preserving known genre/hook entries):
    python3 neocortex_manifest.py --regenerate /path/to/project

Exit codes:
    0 — pass (or regeneration succeeded)
    1 — validation failure (violations found)
    2 — usage error

Stdlib only.
"""

import argparse
import json
import os
import stat
import sys
from datetime import date
from pathlib import Path

# ── Constants ──────────────────────────────────────────────────────────────────

SPEC_VERSION = "NEOCORTEX_SPEC v1.0"
TODAY = date.today().isoformat()

VALID_GENRES = {"PLAN", "AUDIT", "INCIDENT", "DECISION", "NOTE", "PROTOCOL"}

# .md files in NEOCORTEX/ root that are NOT subject to files[] listing
EXEMPT_FILES = {"STATUS.md", "JOURNAL.md"}

# Files allowed in NEOCORTEX/ root that are not .md
# (".DS_Store"/".localized" are macOS Finder metadata junk — tolerated, not sanctioned artifacts)
ALLOWED_NON_MD = {"MANIFEST.json", ".DS_Store", ".localized"}

STATUS_MAX_LINES = 150
DATA_MAX_BYTES = 64 * 1024  # 64 KB

REQUIRED_MANIFEST_FIELDS = {"spec", "project", "updated", "archive", "files"}
REQUIRED_ARCHIVE_FIELDS = {"exists", "span", "contents", "note"}
REQUIRED_FILE_FIELDS = {"file", "genre", "status"}  # hook is optional per spec sample


# ── Helpers ────────────────────────────────────────────────────────────────────

def _neocortex_dir(project_dir: Path) -> Path:
    return project_dir / "NEOCORTEX"


def _safe_confined_write(anchor: Path, target_dir: Path, filename: str,
                         content: bytes, exclusive: bool = False) -> None:
    """
    Write <target_dir>/<filename> WITHOUT following any symlink in the path from
    <anchor> (a trusted, already-resolved root) down to and including the final
    file. Walks the path component-by-component with openat()+O_NOFOLLOW (each
    intermediate dir AND the final file), creating missing dirs via
    os.mkdir(dir_fd=…) relative to the pinned parent. A symlink anywhere in the
    chain raises OSError — closing the parent-/intermediate-directory TOCTOU that
    a plain write_text() to an absolute path is exposed to (the OS resolves the
    intermediate components of an absolute path through any symlink).

    The final write avoids the O_TRUNC-at-open clobber (O_TRUNC truncates BEFORE
    any fstat, so a hardlink planted as the target — which O_NOFOLLOW does not
    catch and which IS a regular file — would have its external victim inode
    emptied):
      - exclusive=True  → O_CREAT|O_EXCL (atomic create-or-skip; FileExistsError
        on ANY existing node — regular/symlink/hardlink/FIFO).
      - exclusive=False → write a fresh O_EXCL temp, then renameat() over the
        target (rename swaps only the directory entry; never follows/truncates a
        planted node — no clobber, no race window).
    O_NONBLOCK + S_ISREG + st_nlink==1 refuse a FIFO/device/hardlink target.
    """
    rel_parts = target_dir.relative_to(anchor).parts
    if not rel_parts:
        raise OSError("refusing to write directly into the trusted anchor")

    dir_fd = os.open(str(anchor), os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in rel_parts:
            try:
                next_fd = os.open(
                    part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=dir_fd
                )
            except FileNotFoundError:
                os.mkdir(part, 0o755, dir_fd=dir_fd)
                next_fd = os.open(
                    part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=dir_fd
                )
            os.close(dir_fd)
            dir_fd = next_fd
        # dir_fd now pins the real target_dir, reached without traversing a symlink.
        if exclusive:
            # Atomic create-or-skip: O_EXCL refuses ANY existing node (regular file,
            # symlink, hardlink, FIFO, dir) → FileExistsError (caller: "already there,
            # skip"). A fresh O_EXCL inode is regular + single-link by construction.
            file_fd = os.open(
                filename,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_NONBLOCK,
                0o644, dir_fd=dir_fd,
            )
            try:
                st = os.fstat(file_fd)
                if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
                    raise OSError(f"{filename} is not a fresh regular file; refusing to write.")
                os.write(file_fd, content)
            finally:
                os.close(file_fd)
        else:
            # Refuse to overwrite anything that is not already a plain single-link
            # regular file: a symlink / hardlink / FIFO / device planted as <filename>
            # is skipped (OSError), not silently replaced. lstat() does not follow it.
            # Best-effort gate: a post-lstat race is still SAFE because the write goes
            # to a fresh temp + renameat (below), never opening/truncating the entry.
            try:
                est = os.lstat(filename, dir_fd=dir_fd)
            except FileNotFoundError:
                est = None
            if est is not None and (not stat.S_ISREG(est.st_mode) or est.st_nlink != 1):
                raise OSError(f"{filename} is not a plain regular file; refusing to overwrite.")
            # Atomic overwrite via fresh-temp + renameat: never opens/truncates the
            # existing entry, so even a node raced in after the lstat cannot be
            # followed, truncated (the O_TRUNC-at-open clobber), or written through.
            # renameat replaces only the directory entry (relative to the pinned dir-fd).
            tmp = f".{filename}.{os.urandom(8).hex()}.tmp"
            file_fd = os.open(
                tmp,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_NONBLOCK,
                0o644, dir_fd=dir_fd,
            )
            try:
                try:
                    st = os.fstat(file_fd)
                    if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
                        raise OSError("temp file is not a fresh regular file; refusing to write.")
                    os.write(file_fd, content)
                finally:
                    os.close(file_fd)
                os.rename(tmp, filename, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
            except BaseException:
                # any failure (fstat / write / rename) → remove the temp; never leak litter
                try:
                    os.unlink(tmp, dir_fd=dir_fd)
                except OSError:
                    pass
                raise
    finally:
        os.close(dir_fd)


def _safe_confined_read(anchor: Path, target_dir: Path, filename: str):
    """
    Read <target_dir>/<filename> WITHOUT following any symlink in the path from
    <anchor> (trusted, already-resolved) down to and including the final file.
    Mirror of _safe_confined_write: walks every component with openat()+O_NOFOLLOW.
    Returns the file's UTF-8 text, or None if the file (or an intermediate dir) is
    absent. Raises OSError on a symlink/special-file/unsafe component, so a read
    cannot be raced into following a symlink to an external file.
    """
    rel_parts = target_dir.relative_to(anchor).parts
    if not rel_parts:
        raise OSError("refusing to read directly from the trusted anchor")

    dir_fd = os.open(str(anchor), os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in rel_parts:
            try:
                next_fd = os.open(
                    part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=dir_fd
                )
            except FileNotFoundError:
                return None
            os.close(dir_fd)
            dir_fd = next_fd
        try:
            file_fd = os.open(
                filename, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dir_fd
            )
        except FileNotFoundError:
            return None
        try:
            st = os.fstat(file_fd)
            if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
                # st_nlink>1 ⇒ a hardlink (O_NOFOLLOW does not catch it, and it IS a
                # regular file): refuse, else its external bytes could be disclosed.
                raise OSError(f"{filename} is not a regular single-link file; refusing to read.")
            chunks = []
            while True:
                buf = os.read(file_fd, 65536)
                if not buf:
                    break
                chunks.append(buf)
            return b"".join(chunks).decode("utf-8")
        finally:
            os.close(file_fd)
    finally:
        os.close(dir_fd)


def _load_manifest(nc_dir: Path) -> tuple[dict | None, list[str]]:
    """Load and JSON-parse MANIFEST.json. Returns (data, errors)."""
    # Symlink-safe confined read (anchor = the project dir, i.e. NEOCORTEX/'s
    # parent): refuses to follow a symlinked NEOCORTEX/ or MANIFEST.json.
    try:
        text = _safe_confined_read(nc_dir.parent, nc_dir, "MANIFEST.json")
    except OSError as e:
        return None, [f"MANIFEST.json unreadable: {e}"]
    if text is None:
        return None, ["MANIFEST.json is missing"]
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        return None, [f"MANIFEST.json invalid JSON: {e}"]
    return data, []


def _md_files_in_root(nc_dir: Path) -> set[str]:
    """Return .md file names (not paths) in NEOCORTEX/ root (non-recursive)."""
    result = set()
    try:
        for entry in nc_dir.iterdir():
            if entry.is_file() and entry.suffix.lower() == ".md":
                result.add(entry.name)
    except OSError:
        pass
    return result


def _non_md_non_exempt_files(nc_dir: Path) -> list[str]:
    """Return names of files in NEOCORTEX/ root that are not .md and not in ALLOWED_NON_MD."""
    result = []
    try:
        for entry in nc_dir.iterdir():
            if entry.is_file() and entry.suffix.lower() != ".md":
                if entry.name not in ALLOWED_NON_MD:
                    result.append(entry.name)
    except OSError:
        pass
    return result


def _oversized_files(nc_dir: Path) -> list[str]:
    """Return names of files in NEOCORTEX/ root that exceed DATA_MAX_BYTES."""
    result = []
    try:
        for entry in nc_dir.iterdir():
            if entry.is_file():
                try:
                    size = entry.stat().st_size
                    if size > DATA_MAX_BYTES:
                        result.append(f"{entry.name} ({size // 1024} KB > 64 KB)")
                except OSError:
                    pass
    except OSError:
        pass
    return result


def _cold_start_tokens(nc_dir: Path, manifest_data: dict) -> int:
    """Estimate cold-start token cost (rough: bytes / 4)."""
    total_bytes = 0
    manifest_path = nc_dir / "MANIFEST.json"
    if manifest_path.exists():
        total_bytes += manifest_path.stat().st_size
    status_path = nc_dir / "STATUS.md"
    if status_path.exists():
        total_bytes += status_path.stat().st_size
    # Active plans
    for entry in manifest_data.get("files", []):
        if entry.get("status") == "ACTIVE" and entry.get("genre") == "PLAN":
            fpath = nc_dir / entry.get("file", "")
            if fpath.exists():
                total_bytes += fpath.stat().st_size
    return total_bytes // 4


# ── Check ──────────────────────────────────────────────────────────────────────

def check(project_dir: Path) -> int:
    """Validate a project's NEOCORTEX. Returns exit code (0=pass, 1=fail)."""
    violations: list[str] = []
    warnings: list[str] = []

    # 1. NEOCORTEX/ directory exists
    nc_dir = _neocortex_dir(project_dir)
    if not nc_dir.is_dir():
        print(f"FAIL: NEOCORTEX/ directory not found at {nc_dir}")
        return 1

    # 2. MANIFEST.json exists + valid JSON + required fields
    manifest_data, load_errors = _load_manifest(nc_dir)
    violations.extend(load_errors)

    if manifest_data is not None:
        # 2a. Top-level required fields
        missing_top = REQUIRED_MANIFEST_FIELDS - set(manifest_data.keys())
        for f in sorted(missing_top):
            violations.append(f"MANIFEST.json missing required top-level field: '{f}'")

        # 2b. archive sub-fields
        archive = manifest_data.get("archive")
        if archive is None:
            violations.append("MANIFEST.json: 'archive' field is null/missing")
        elif not isinstance(archive, dict):
            violations.append("MANIFEST.json: 'archive' must be an object")
        else:
            missing_arch = REQUIRED_ARCHIVE_FIELDS - set(archive.keys())
            for f in sorted(missing_arch):
                violations.append(f"MANIFEST.json archive missing required field: '{f}'")
            # archive.exists must be boolean
            if not isinstance(archive.get("exists"), bool):
                violations.append("MANIFEST.json archive.exists must be a boolean")

        # 2c. files[] validation
        files_list = manifest_data.get("files")
        if files_list is None:
            violations.append("MANIFEST.json: 'files' field is null/missing")
        elif not isinstance(files_list, list):
            violations.append("MANIFEST.json: 'files' must be an array")
        else:
            declared_files = set()
            for i, entry in enumerate(files_list):
                if not isinstance(entry, dict):
                    violations.append(f"MANIFEST.json files[{i}] is not an object")
                    continue
                missing_ef = REQUIRED_FILE_FIELDS - set(entry.keys())
                for f in sorted(missing_ef):
                    violations.append(f"MANIFEST.json files[{i}] missing required field: '{f}'")

                genre = entry.get("genre", "")
                if genre and genre not in VALID_GENRES:
                    violations.append(
                        f"MANIFEST.json files[{i}] ({entry.get('file', '?')}): "
                        f"invalid genre '{genre}', must be one of {sorted(VALID_GENRES)}"
                    )

                fname = entry.get("file", "")
                if fname:
                    declared_files.add(fname)

            # 3. Directory-is-truth checks
            actual_md = _md_files_in_root(nc_dir)
            # Files that must appear in files[]: all .md in root except EXEMPT
            required_in_manifest = actual_md - EXEMPT_FILES

            # Files declared but missing from disk
            for f in sorted(declared_files):
                if not (nc_dir / f).exists():
                    violations.append(
                        f"MANIFEST.json files[] lists '{f}' but file is absent from NEOCORTEX/"
                    )

            # .md files on disk but not declared in files[]
            for f in sorted(required_in_manifest):
                if f not in declared_files:
                    violations.append(
                        f"NEOCORTEX/{f} exists on disk but is not listed in MANIFEST.json files[]"
                    )

    # 4. Data-rule violations: non-.md files (except MANIFEST.json)
    bad_non_md = _non_md_non_exempt_files(nc_dir)
    for f in bad_non_md:
        violations.append(
            f"Data-rule violation: '{f}' in NEOCORTEX/ root is not a .md file "
            f"(only MANIFEST.json is the sanctioned non-.md exception)"
        )

    # 5. Data-rule violations: files >64 KB in NEOCORTEX/ root
    oversized = _oversized_files(nc_dir)
    for f in oversized:
        violations.append(f"Data-rule violation: {f} in NEOCORTEX/ root exceeds 64 KB limit")

    # 6. STATUS.md ≤150 lines (symlink-safe confined read; refuses a symlinked
    #    STATUS.md rather than following it off-project)
    status_text = None
    status_unreadable = False
    try:
        status_text = _safe_confined_read(nc_dir.parent, nc_dir, "STATUS.md")
    except OSError as e:
        status_unreadable = True
        violations.append(f"STATUS.md unreadable: {e}")
    if status_text is None and not status_unreadable:
        violations.append("STATUS.md is missing from NEOCORTEX/")
    elif status_text is not None:
        if len(status_text.splitlines()) > STATUS_MAX_LINES:
            violations.append(
                f"STATUS.md has {len(status_text.splitlines())} lines, "
                f"exceeds hard bound of {STATUS_MAX_LINES}"
            )

    # 7. JOURNAL.md presence
    if not (nc_dir / "JOURNAL.md").exists():
        violations.append("JOURNAL.md is missing from NEOCORTEX/")

    # 8. Cold-start cost report (informational)
    cs_tokens = 0
    if manifest_data:
        cs_tokens = _cold_start_tokens(nc_dir, manifest_data)

    # ── Output ─────────────────────────────────────────────────────────────────
    project_name = manifest_data.get("project", str(project_dir.name)) if manifest_data else str(project_dir.name)
    print(f"NEOCORTEX check: {project_name}")
    print(f"  Directory: {nc_dir}")
    if manifest_data:
        print(f"  Cold-start estimate: ~{cs_tokens:,} tokens")

    if not violations:
        print("  RESULT: PASS — no violations")
        return 0
    else:
        print(f"  RESULT: FAIL — {len(violations)} violation(s):")
        for v in violations:
            print(f"    [VIOLATION] {v}")
        return 1


# ── Regenerate ─────────────────────────────────────────────────────────────────

def regenerate(project_dir: Path) -> int:
    """Rebuild files[] from directory, preserving known genre/status/hook entries."""
    nc_dir = _neocortex_dir(project_dir)
    if not nc_dir.is_dir():
        print(f"ERROR: NEOCORTEX/ directory not found at {nc_dir}")
        return 2

    # Load existing manifest (if any) to preserve known entries
    existing_manifest, _ = _load_manifest(nc_dir)
    known_entries: dict[str, dict] = {}
    if existing_manifest and isinstance(existing_manifest.get("files"), list):
        for entry in existing_manifest["files"]:
            if isinstance(entry, dict) and "file" in entry:
                known_entries[entry["file"]] = entry

    # Discover .md files in NEOCORTEX/ root (excluding exempt)
    actual_md = _md_files_in_root(nc_dir)
    to_list = sorted(actual_md - EXEMPT_FILES)

    new_files = []
    for fname in to_list:
        if fname in known_entries:
            # Preserve existing entry, updating with any new info
            entry = dict(known_entries[fname])
            entry["file"] = fname  # ensure file key is correct
        else:
            # Default: NOTE + ACTIVE for human review
            entry = {
                "file": fname,
                "genre": "NOTE",
                "status": "ACTIVE",
                "hook": "[fill in — auto-generated, genre/status need human review]",
            }
        new_files.append(entry)

    # Build updated manifest
    project_name = (
        existing_manifest.get("project", project_dir.name)
        if existing_manifest
        else project_dir.name
    )
    archive_block = (
        existing_manifest.get("archive")
        if existing_manifest and "archive" in existing_manifest
        else {
            "exists": False,
            "span": "",
            "contents": "",
            "note": "no archive yet",
        }
    )
    spec_val = (
        existing_manifest.get("spec", SPEC_VERSION)
        if existing_manifest
        else SPEC_VERSION
    )

    new_manifest = {
        "spec": spec_val,
        "project": project_name,
        "updated": TODAY,
        "archive": archive_block,
        "files": new_files,
    }

    manifest_path = nc_dir / "MANIFEST.json"

    # Symlink guard: refuse to write if NEOCORTEX dir or target file is a symlink,
    # or if the resolved path escapes the project directory.
    # NOTE: broken symlinks have exists()==False but is_symlink()==True —
    # omit the exists() short-circuit so broken symlinks are caught too.
    if nc_dir.is_symlink() or manifest_path.is_symlink():
        print(f"ERROR: NEOCORTEX/ or MANIFEST.json is a symlink; refusing to write.")
        return 2
    try:
        manifest_path.resolve().relative_to(project_dir.resolve())
    except ValueError:
        print(f"ERROR: MANIFEST.json resolves outside project dir; refusing to write.")
        return 2

    # Symlink-safe confined write (walk from the resolved project dir; closes the
    # NEOCORTEX/-swap parent-dir TOCTOU the early guard above cannot race-proof).
    anchor = project_dir.resolve()
    try:
        _safe_confined_write(
            anchor, anchor / "NEOCORTEX", "MANIFEST.json",
            (json.dumps(new_manifest, indent=2, ensure_ascii=False) + "\n").encode("utf-8"),
        )
    except OSError as e:
        print(f"ERROR: refusing to write MANIFEST.json (symlink/unsafe path): {e}")
        return 2

    print(f"REGENERATE: wrote {manifest_path}")
    print(f"  {len(new_files)} file(s) listed:")
    for entry in new_files:
        marker = "[preserved]" if entry["file"] in known_entries else "[new/default]"
        print(f"    {marker} {entry['file']} genre={entry['genre']} status={entry['status']}")
    return 0


# ── Scaffold helper (used by project_scaffold) ─────────────────────────────────

def scaffold_neocortex(project_dir: Path, project_name: str, dry_run: bool = False) -> list[str]:
    """
    Create a spec-compliant NEOCORTEX/ skeleton at project birth.

    Creates:
      NEOCORTEX/MANIFEST.json  — archive.exists:false, empty files[]
      NEOCORTEX/STATUS.md      — 5 required §2 sections as headed placeholders
      NEOCORTEX/JOURNAL.md     — header + first entry

    Returns list of paths created (or that would be created in dry_run).
    """
    nc_dir = project_dir / "NEOCORTEX"
    created = []

    manifest_path = nc_dir / "MANIFEST.json"
    status_path = nc_dir / "STATUS.md"
    journal_path = nc_dir / "JOURNAL.md"

    manifest_content = json.dumps(
        {
            "spec": SPEC_VERSION,
            "project": project_name,
            "updated": TODAY,
            "archive": {
                "exists": False,
                "span": "",
                "contents": "",
                "note": "no archive yet — project just created",
            },
            "files": [],
        },
        indent=2,
        ensure_ascii=False,
    ) + "\n"

    status_content = f"""\
# {project_name} — Status
*Last updated: {TODAY}*

## What this project is
[≤5 lines: one-paragraph purpose statement]

## Current phase
[One line + date stamp, e.g.: "S0 — scaffolding — {TODAY}"]

## Invariants
1. [Add invariants as they are established — cite INCIDENT/DECISION doc that created each]

## Next actions
1. Fill in PROJECT_BRIEF.md (Goal + Tech Stack)
2. Define first implementation milestone
3. [Add more as needed — max 5, priority-ordered, each independently actionable cold]

## Pointers
- NEOCORTEX_SPEC: see your regime's NEOCORTEX_SPEC.md (e.g. spec/ or governance/)
- Constitution: `PAICodeConstitution-2026.md`
- Cold-start read order: MANIFEST.json → STATUS.md → active plans
- Test command: [add when tests exist]
"""

    journal_content = f"""\
# {project_name} — Journal
*Quarter: {TODAY[:7]} (rotation threshold: 64 KB or quarter boundary)*

---

### {TODAY} | Project created

- Scaffolded with `project_scaffold` skill (NEOCORTEX_SPEC v1.0).
- PROJECT_BRIEF.md generated; fill in Goal and Tech Stack.
- NEOCORTEX/ skeleton created: MANIFEST.json, STATUS.md, JOURNAL.md.

"""

    files_to_create = [
        (manifest_path, manifest_content),
        (status_path, status_content),
        (journal_path, journal_content),
    ]

    # Symlink guard: refuse to scaffold if NEOCORTEX dir already exists as a
    # symlink, or if any target file is a symlink, or if any resolved path
    # escapes the project directory.
    # NOTE: broken symlinks have exists()==False but is_symlink()==True —
    # omit the exists() short-circuit so broken symlinks are caught too.
    if nc_dir.is_symlink():
        print(f"ERROR: NEOCORTEX/ is a symlink; refusing to scaffold.")
        return []
    for fpath, _ in files_to_create:
        if fpath.is_symlink():
            print(f"ERROR: {fpath.name} is a symlink; refusing to scaffold.")
            return []
        try:
            fpath.resolve().relative_to(project_dir.resolve())
        except ValueError:
            print(f"ERROR: {fpath.name} resolves outside project dir; refusing to scaffold.")
            return []

    # Symlink-safe confined writes. NEOCORTEX/ is created by the walk on the first
    # file (no separate mkdir). exclusive=True → atomic create-or-skip (O_EXCL), so
    # an existing file is never clobbered and a raced symlink/FIFO is refused.
    anchor = project_dir.resolve()

    for fpath, content in files_to_create:
        if dry_run:
            if fpath.exists():
                print(f"  SKIP (exists): {fpath}")
            else:
                print(f"  WOULD CREATE: {fpath} ({len(content.splitlines())} lines)")
                created.append(str(fpath))
            continue
        try:
            _safe_confined_write(
                anchor, anchor / "NEOCORTEX", fpath.name,
                content.encode("utf-8"), exclusive=True,
            )
        except FileExistsError:
            print(f"  SKIP (exists): {fpath}")
            continue
        except OSError as e:
            print(f"  SKIP (unsafe path): {fpath} — {e}")
            continue
        print(f"  CREATED: {fpath}")
        created.append(str(fpath))

    return created


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="NEOCORTEX MANIFEST validator and regenerator (NEOCORTEX_SPEC v1.0 §2).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--check",
        metavar="PROJECT_DIR",
        help="Validate a project's NEOCORTEX (exit 0=pass, 1=fail)",
    )
    group.add_argument(
        "--regenerate",
        metavar="PROJECT_DIR",
        help="Rebuild MANIFEST.json files[] from directory contents",
    )

    args = parser.parse_args()

    if args.check:
        project_dir = Path(os.path.expanduser(args.check)).resolve()
        if not project_dir.is_dir():
            print(f"ERROR: project directory not found: {project_dir}", file=sys.stderr)
            return 2
        return check(project_dir)

    if args.regenerate:
        project_dir = Path(os.path.expanduser(args.regenerate)).resolve()
        if not project_dir.is_dir():
            print(f"ERROR: project directory not found: {project_dir}", file=sys.stderr)
            return 2
        return regenerate(project_dir)

    return 2


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""skill_index_regen.py — auto-regenerate the skills index after a SKILL.md/metadata edit.

PostToolUse hook (matcher: Edit|Write|NotebookEdit). WS-C / C1 of the 2026-07-05 skills
remediation plan. When the edited file is a `_skills/<skill>/SKILL.md` or
`_skills/<skill>/metadata.json`, this runs `python3.12 _skills/build_index.py` so the
generated `index.json` + `SKILLS_INDEX.md` reflect the edit in the SAME turn (§4: "registration
of a new skill = run build_index.py. Nothing else." — mechanized). It fires on the AGENT edit
regardless of git-tracking, which is the point: those files are `.gitignore`'d, so a
pre-commit / tracked-file trigger would never see them (plan v2 P1-F1).

WHY A SEPARATE HOOK (not folded into postedit_compile_gate.py): the compile-gate's explicit
contract is read-only / no-side-effects (it only py_compile / bash -n / osacompile -o
/dev/null — nothing that writes). A `build_index.py` regen WRITES two files; overloading the
compile-gate would violate that invariant. Keeping this in its own hook preserves the gate's
guarantee and keeps this side-effecting step independently reviewable / disable-able.

INVARIANTS this hook holds:
  * NEVER blocks. It is a convenience side-effect, not a policy gate — always exits 0, even on
    regen failure (a broken build_index.py must not wedge the agent's turn; the failure is
    logged, and the pre-existing index stays, marked STALE on its first line: N26, below).
  * LENIENT regen (no --strict): a live version split (the C3 guard's target) must still let
    the index be rewritten, not refuse. --strict is for the founder's manual/CI gate, not here.
  * No shell. `build_index.py`'s path is an argv element; the interpreter is resolved to the
    provisioned python3.12 (workspace rule: never bare python3 → an older system Python).
  * Idempotent + bounded. build_index.py is deterministic and greps a bounded tree; a short
    timeout caps the turn cost.

INPUT: the standard PostToolUse JSON on stdin — `{"tool_input": {"file_path": "..."}, ...}`.
"""
import os as _os0, sys as _sys0
# B4-10 / B5-01 (reviewer A rounds 4-5): Python puts this script's directory first on sys.path, so a file in .claude/hooks/
# named like a stdlib module (json.py, __future__.py, ...) would run inside this hook before any check. Drop that
# entry FIRST: no import may precede this block (not even `from __future__`, which is itself an import).
if __name__ == "__main__" and _sys0.path and \
        _os0.path.realpath(_sys0.path[0] or ".") == _os0.path.dirname(_os0.path.realpath(__file__)):
    del _sys0.path[0]
import datetime
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

# .claude/hooks/skill_index_regen.py → workspace root is three parents up.
ROOT = Path(__file__).resolve().parent.parent.parent
SKILLS_DIR = ROOT / "_skills"
BUILD_INDEX = SKILLS_DIR / "build_index.py"
INDEX_MD = SKILLS_DIR / "SKILLS_INDEX.md"
_LOG = (".claude", "hooks", ".skill_index_regen.log")
_MARK = b"> **STALE INDEX**"


def _log(msg: str) -> None:
    """Best-effort fixed-path log; never raises into the hook."""
    try:
        (ROOT / _LOG[0] / _LOG[1] / _LOG[2]).open("a", encoding="utf-8").write(
            f"{datetime.datetime.now().isoformat(timespec='seconds')} {msg}\n")
    except OSError:
        pass


def _is_skill_manifest(fp: str) -> bool:
    """True iff `fp` is a `_skills/<skill>/SKILL.md` or `_skills/<skill>/metadata.json`.
    Matched structurally against the resolved _skills/ root (not a brittle substring), so a
    path like `.../notes/_skills_ideas/SKILL.md` outside the real tree does NOT trigger."""
    if not fp:
        return False
    name = os.path.basename(fp)
    if name not in ("SKILL.md", "metadata.json"):
        return False
    try:
        p = Path(fp).resolve()
        skills = SKILLS_DIR.resolve()
    except OSError:
        return False
    # Expect exactly _skills/<skill>/<name>: the file's parent's parent is the _skills dir.
    return p.name == name and p.parent.parent == skills and p.parent != skills


def _mark_stale(fp: str, why: str) -> None:
    """N26 (2026-10-27, B2 F15): a failed regen used to leave the previous SKILLS_INDEX.md with nothing to say it was
    stale, and the session anchor injects its first 40 lines as current. Put ONE marker line on top (an older marker is
    replaced, never stacked); the next successful regen rewrites the file and the marker goes with it. The new text is
    written to a temp file and renamed over the index, so nothing is written through a planted link; an index that is
    absent, not a regular file or hardlinked is left as it is (the anchor does not show such an index)."""
    tmp = None
    try:
        with os.fdopen(os.open(INDEX_MD, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as fh:
            st = os.fstat(fh.fileno())
            if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1 or st.st_size > 1_000_000:
                _log("MARK skipped: the index is not a single-link regular file")
                return
            body = fh.read()
        if body.startswith(_MARK):
            body = body.split(b"\n", 1)[1] if b"\n" in body else b""
        what = " ".join(f"{os.path.basename(os.path.dirname(fp))}/{os.path.basename(fp)}".split())[:80]
        line = (f"{_MARK.decode()} regen failed {datetime.datetime.now().isoformat(timespec='seconds')} after an edit of "
                f"{what} ({why}): the entries below may be out of date. Fix it, then run "
                f"`python3.12 _skills/build_index.py`.\n").encode("utf-8")
        tmp = SKILLS_DIR / f".SKILLS_INDEX.md.n26.{os.getpid()}"
        with os.fdopen(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, stat.S_IMODE(st.st_mode)),
                       "wb") as out:
            os.fchmod(out.fileno(), stat.S_IMODE(st.st_mode))      # the umask must not narrow the index's mode
            out.write(line + body)
        os.replace(tmp, INDEX_MD)
        tmp = None
        _log(f"MARK stale index ({why})")
    except Exception as e:   # marking is best-effort too: it must not wedge the turn
        _log(f"MARK failed {e!r}")
    finally:
        if tmp is not None:
            try:
                os.unlink(tmp)
            except OSError:
                pass


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)   # malformed payload → nothing to do
    fp = (data.get("tool_input", {}) or {}).get("file_path", "")
    if not _is_skill_manifest(fp):
        sys.exit(0)   # not a skill manifest edit → no regen
    if not BUILD_INDEX.is_file():
        _log(f"SKIP build_index.py missing (edited {fp})")
        _mark_stale(fp, "build_index.py missing")
        sys.exit(0)

    py = shutil.which("python3.12") or shutil.which("python3") or ""
    if not py:
        _log("SKIP no python interpreter found")
        _mark_stale(fp, "no python interpreter")
        sys.exit(0)
    try:
        # Lenient (no --strict); argv list (no shell); build_index.py writes index.json +
        # SKILLS_INDEX.md and is otherwise read-only over the tree.
        r = subprocess.run([py, str(BUILD_INDEX)],
                           capture_output=True, text=True, timeout=120, cwd=str(ROOT))
        if r.returncode == 0:
            _log(f"REGEN ok after edit of {os.path.basename(os.path.dirname(fp))}/"
                 f"{os.path.basename(fp)}")
        else:
            _log(f"REGEN nonzero rc={r.returncode}: {(r.stderr or r.stdout or '')[-300:]}")
            _mark_stale(fp, f"rc={r.returncode}")
    except subprocess.TimeoutExpired:
        _log("REGEN timeout")
        _mark_stale(fp, "timeout")
    except Exception as e:   # never let a regen failure wedge the turn
        _log(f"REGEN error {e!r}")
        _mark_stale(fp, f"error {type(e).__name__}")
    sys.exit(0)   # convenience side-effect: ALWAYS succeed, never block


if __name__ == "__main__":
    main()

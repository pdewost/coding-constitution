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
    logged, and the pre-existing index simply stays as-is).
  * LENIENT regen (no --strict): a live version split (the C3 guard's target) must still let
    the index be rewritten, not refuse. --strict is for the founder's manual/CI gate, not here.
  * No shell. `build_index.py`'s path is an argv element; the interpreter is resolved to the
    provisioned python3.12 (workspace rule: never bare python3 → Xcode 3.9.6).
  * Idempotent + bounded. build_index.py is deterministic and greps a bounded tree; a short
    timeout caps the turn cost.

INPUT: the standard PostToolUse JSON on stdin — `{"tool_input": {"file_path": "..."}, ...}`.
"""
import datetime
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

# .claude/hooks/skill_index_regen.py → workspace root is three parents up.
ROOT = Path(__file__).resolve().parent.parent.parent
SKILLS_DIR = ROOT / "_skills"
BUILD_INDEX = SKILLS_DIR / "build_index.py"
_LOG = (".claude", "hooks", ".skill_index_regen.log")


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
        sys.exit(0)

    py = shutil.which("python3.12") or shutil.which("python3") or ""
    if not py:
        _log("SKIP no python interpreter found")
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
    except subprocess.TimeoutExpired:
        _log("REGEN timeout")
    except Exception as e:   # never let a regen failure wedge the turn
        _log(f"REGEN error {e!r}")
    sys.exit(0)   # convenience side-effect: ALWAYS succeed, never block


if __name__ == "__main__":
    main()

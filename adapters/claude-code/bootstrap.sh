#!/usr/bin/env bash
# bootstrap.sh — make this adapter kit runnable in YOUR workspace.
#
# WHY THIS EXISTS. install_adapters.py reads the canonical hook set from
# <workspace-root>/.claude/, not from this repo — that indirection is deliberate (one
# canonical copy, every armed project points at it by absolute path, so nothing can
# drift). But it means a fresh clone of this repo is NOT self-contained: the installer's
# first act is to look for a file the clone does not have, and exit. Found and fixed
# 2026-08-25 by running the published kit as an adopter would.
#
# Usage:  ./adapters/claude-code/bootstrap.sh /path/to/your/workspace
set -euo pipefail

KIT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE="${1:-}"

if [[ -z "$WORKSPACE" ]]; then
  echo "usage: $(basename "$0") /path/to/your/workspace" >&2
  echo "  (the directory holding the projects you want governed)" >&2
  exit 2
fi
if [[ -e "$WORKSPACE" && ! -d "$WORKSPACE" ]]; then
  echo "exists but is not a directory: $WORKSPACE" >&2; exit 2
fi

# ONE interpreter for everything this script runs or prints: $PYTHON, else python3.12, else python3. The kit's installers
# (install_adapters.py, neocortex_manifest.py) need Python >= 3.10 (they use `X | None` annotations and fail at import
# below that; the stock macOS /usr/bin/python3 is 3.9). Refuse HERE, before creating or copying anything, rather than
# install a half-working kit and print commands that fail (S4 F5, 2026-10-08).
PY="${PYTHON:-}"
if [[ -z "$PY" ]]; then
  PY="$(command -v python3.12 || command -v python3 || true)"
fi
if [[ -z "$PY" ]] || ! command -v "$PY" >/dev/null 2>&1; then
  echo "REFUSING: no Python interpreter found (looked for \$PYTHON, python3.12, python3). This kit needs Python 3.10 or newer;" >&2
  echo "  install one and re-run, or set PYTHON=/path/to/python3.12" >&2
  exit 2
fi
if ! "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
  PYV="$("$PY" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || echo unknown)"
  echo "REFUSING: $PY is Python $PYV; this kit needs Python 3.10 or newer (the installers fail at import below that)." >&2
  echo "  Nothing was installed. Install Python 3.12 and re-run, or set PYTHON=/path/to/python3.12" >&2
  exit 2
fi
# Create it if absent. A bootstrap that refuses to bootstrap into a fresh path is not a
# bootstrap: the README's own quickstart named a throwaway directory and died on "not a
# directory" before doing anything (§5.5 follow-up, 2026-08-25).
if [[ ! -d "$WORKSPACE" ]]; then
  mkdir -p "$WORKSPACE"
  echo "created workspace directory: $WORKSPACE"
fi
WORKSPACE="$(cd "$WORKSPACE" && pwd)"   # absolutise, so the --root lines below are copy-pasteable

DEST="$WORKSPACE/.claude"
mkdir -p "$DEST/hooks"

if [[ -e "$DEST/settings.json" ]]; then
  echo "REFUSING: $DEST/settings.json already exists."
  echo "  This script never overwrites an existing canonical settings file — yours may"
  echo "  carry permissions or hooks this kit knows nothing about. Merge by hand:"
  echo "  the block you need is the \"hooks\" key of $KIT/settings.canonical.json"
  exit 1
fi

cp "$KIT/hooks/"*.py "$KIT/hooks/"*.sh "$DEST/hooks/"
cp "$KIT/settings.canonical.json" "$DEST/settings.json"
chmod +x "$DEST/hooks/"*.sh 2>/dev/null || true
# Every hook is run by its path, so each non-test hook must be executable (S0, 2026-10-07: three registered hooks were
# published as 0644 and never ran for an adopter); the siblings get it too, harmlessly.
for f in "$DEST/hooks/"*.py; do
  case "$(basename "$f")" in test_*) ;; *) chmod +x "$f" ;; esac
done

# Instruments that must live AT THE WORKSPACE ROOT, not in the kit: the C3 skill-version-drift
# gate reads _skills/build_index.py, the closeout advisory runs governance/scripts/
# continuity_sweep.py, and the closeout staleness advisory (M-9) runs governance/scripts/
# closeout_lint.py. Publishing those scripts was not enough -- a reviewer showed the first two
# gates STILL could not find them, because nothing in the documented adopt sequence ever copied
# them out of the kit. A gate that cannot reach its instrument is not revived, it is relocated
# (§5.5 re-review, 2026-08-25). closeout_lint.py joined this list the same way (2026-09-04):
# without it, the CLOSEOUT-GATE's own staleness advisory fails open with "not found" for every
# adopter, silently, forever -- the identical shape of gap, caught before it shipped that way.
REPO="$(cd "$KIT/../.." && pwd)"
if [[ -f "$REPO/_skills/build_index.py" ]]; then
  mkdir -p "$WORKSPACE/_skills"
  [[ -e "$WORKSPACE/_skills/build_index.py" ]] || cp "$REPO/_skills/build_index.py" "$WORKSPACE/_skills/"
  echo "  placed _skills/build_index.py        (C3 skill-version-drift gate)"
fi
if [[ -f "$REPO/governance/scripts/continuity_sweep.py" ]]; then
  mkdir -p "$WORKSPACE/governance/scripts"
  [[ -e "$WORKSPACE/governance/scripts/continuity_sweep.py" ]] || \
    cp "$REPO/governance/scripts/continuity_sweep.py" "$WORKSPACE/governance/scripts/"
  echo "  placed governance/scripts/continuity_sweep.py  (closeout continuity advisory)"
fi
if [[ -f "$REPO/governance/scripts/closeout_lint.py" ]]; then
  mkdir -p "$WORKSPACE/governance/scripts"
  [[ -e "$WORKSPACE/governance/scripts/closeout_lint.py" ]] || \
    cp "$REPO/governance/scripts/closeout_lint.py" "$WORKSPACE/governance/scripts/"
  echo "  placed governance/scripts/closeout_lint.py     (closeout staleness advisory, M-9)"
fi

# The APPROVED LIST (hooks batch 2, v0.10+): a hook sibling (verifier_recognizer, hook_support, loop_bound) runs only as
# bytes listed in governance/hooks_approved.sha256 -- with no list the stop gate never runs its recognizer, so no test
# run can clear a turn, and the session anchor reports the governor as unchecked. Write it now, from what was just
# installed; re-write it after EVERY deliberate change to .claude/hooks/, .claude/settings*.json or governance/*.yaml
# (that re-approval is the moment a human vouches for the new bytes; any other change is then reported as tampering).
mkdir -p "$WORKSPACE/governance"
"$PY" "$DEST/hooks/hook_support.py" --write-approved
echo "  wrote governance/hooks_approved.sha256 (the approved list of the hooks just installed)"

echo "Installed the canonical hook set into $DEST"
echo
# Print every command with the interpreter chosen above and every path argument shell-quoted (%q): a workspace or kit path
# may contain a space, and an unquoted line would split when pasted.
q() { printf '%q' "$1"; }
echo "Next, in order — note --root: these tools do NOT infer the workspace you just named,"
echo "and without it they search upward from their own location and may act on another tree:"
echo "  1. bash $(q "$KIT/test_hooks.sh") --root $(q "$WORKSPACE")"
echo "  2. $(q "$PY") $(q "$KIT/install_adapters.py") --root $(q "$WORKSPACE")            # DRY RUN"
echo "  3. $(q "$PY") $(q "$KIT/install_adapters.py") --root $(q "$WORKSPACE") --apply    # arm the projects"
echo "  4. $(q "$PY") $(q "$KIT/verify_fires.py") --root $(q "$WORKSPACE")                # PROVEN vs PENDING"
echo "  5. after copying governance/*.yaml or changing any hook or settings file, re-approve:"
echo "     $(q "$PY") $(q "$DEST/hooks/hook_support.py") --write-approved"
echo
echo "Or export it once:  export GOVERNANCE_WORKSPACE=$(q "$WORKSPACE")"
echo
echo "Step 4 matters more than it looks: 'installed' is not 'firing'. A project can sit"
echo "armed for months without a hook ever running. Until verify_fires.py prints PROVEN"
echo "for a project, its enforcement is a plan, not a control."

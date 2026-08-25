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

# Two hooks consult instruments that must live AT THE WORKSPACE ROOT, not in the kit:
# the C3 skill-version-drift gate reads _skills/build_index.py, and the closeout advisory
# runs governance/scripts/continuity_sweep.py. Publishing those scripts was not enough --
# a reviewer showed both gates STILL could not find them, because nothing in the documented
# adopt sequence ever copied them out of the kit. A gate that cannot reach its instrument is
# not revived, it is relocated (§5.5 re-review, 2026-08-25).
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

echo "Installed the canonical hook set into $DEST"
echo
echo "Next, in order — note --root: these tools do NOT infer the workspace you just named,"
echo "and without it they search upward from their own location and may act on another tree:"
echo "  1. bash $KIT/test_hooks.sh --root $WORKSPACE"
echo "  2. python3.12 $KIT/install_adapters.py --root $WORKSPACE            # DRY RUN"
echo "  3. python3.12 $KIT/install_adapters.py --root $WORKSPACE --apply    # arm the projects"
echo "  4. python3.12 $KIT/verify_fires.py --root $WORKSPACE                # PROVEN vs PENDING"
echo
echo "Or export it once:  export GOVERNANCE_WORKSPACE=$WORKSPACE"
echo
echo "Step 4 matters more than it looks: 'installed' is not 'firing'. A project can sit"
echo "armed for months without a hook ever running. Until verify_fires.py prints PROVEN"
echo "for a project, its enforcement is a plan, not a control."

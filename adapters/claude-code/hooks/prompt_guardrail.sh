#!/bin/bash
# prompt_guardrail.sh — GUARDRAIL policy (POLICY_CORE.md), Claude Code adapter.
# UserPromptSubmit hook. Stdout becomes added context (≤4 lines). Task-aware:
# planning and destructive prompts get targeted checklists; everything else
# gets ONE rotated reminder (rotation by wall-clock minute defeats the
# habituation that eroded the 2025 manual guardrail suffix).
p=$(python3 -c 'import json,sys
try: print(json.load(sys.stdin).get("prompt",""))
except Exception: pass' 2>/dev/null)
lower=$(printf '%s' "$p" | tr '[:upper:]' '[:lower:]')
out=""

case "$lower" in *plan\ *|*plan.*|plan*|*design*|*architect*|*sprint*|*roadmap*|*phase\ *|*"start "*)
  out="[Guardrail/plan] Budgets = task classes (routing_policy.yaml); ~4+ files or structural shift => PLAN before code (Art. 13). When the PLAN is drafted: PROPOSE an adversarial review BEFORE validation, with reasons specific to this plan (omissions vs predecessor artifacts, contradictions, unenforceable mandates, unnamed deliverables) — NEOCORTEX_SPEC §3 gate. If the running model@effort differs from the sprint's declared budget: emit the one-line ROUTING ADVISORY (escalation => await go; downshift or user-ruling => surface + proceed) BEFORE the first tool action — routing_policy deviation_advisory." ;;
esac
case "$lower" in *delete*|*remove*|*erase*|*drop\ *|*migrate*|*"rm "*|*apply*|*destructive*)
  out="${out:+$out
}[Guardrail/destructive] Dry-run default + explicit apply gate (Art. 4); never delegate destructive ops (Art. 8); AMENDMENT I: no stop/rm/replace of a serving artifact without consent naming it — one consent = ONE operation, verify-then-destroy (replacement proven BEFORE rm), failed prod action = INCIDENT, never retry; check project invariants (NEOCORTEX/STATUS.md) and machine_config.yaml forbidden windows first." ;;
esac

if [ -z "$out" ]; then
  case $(( $(date +%M) % 3 )) in
    0) out="[Guardrail] Evidence before done: reformulate as a testable goal first; verification log is part of the deliverable; 3 failed fix-verify cycles => STOP and report (Art. 1, 14)." ;;
    1) out="[Guardrail] Surgical: minimum diff, no phantom dependencies, no speculative abstraction; out-of-scope findings => NOTICED BUT NOT TOUCHING (Art. 2)." ;;
    2) out="[Guardrail] Continuity: before ending — STATUS overwritten within bound, JOURNAL entry, MANIFEST current; dual-audience writing, no implicit references (Art. 6)." ;;
  esac
fi
printf '%s\n' "$out"
exit 0

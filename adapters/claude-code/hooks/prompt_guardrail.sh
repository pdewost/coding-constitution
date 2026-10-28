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
case "$lower" in *delete*|*remove*|*erase*|*drop\ *|*migrate*|*"rm "*|*apply*|*destructive*|*wipe*|*destroy*|*purge*|*truncate*)   # N29: last four, A2 4b-4
  out="${out:+$out
}[Guardrail/destructive] Dry-run default + explicit apply gate (Art. 4); never delegate destructive ops (Art. 8); AMENDMENT I: no stop/rm/replace of a serving artifact without consent naming it — one consent = ONE operation, verify-then-destroy (replacement proven BEFORE rm), failed prod action = INCIDENT, never retry; check project invariants (NEOCORTEX/STATUS.md) and machine_config.yaml forbidden windows first." ;;
esac

if [ -z "$out" ]; then
  # 10# forces base 10. WITHOUT IT THIS HOOK IS DEAD FOR 2 MINUTES OF EVERY HOUR: `date +%M`
  # returns a zero-padded 08 or 09, bash reads a leading zero as OCTAL, and the arithmetic
  # aborts with "value too great for base" — the hook exits non-zero and injects NOTHING.
  # Pre-existing since this file was written; found 2026-08-27 while adding Art. 3, by running
  # the hook rather than reading it. The idiom was already in test_hooks.sh:73 and simply had
  # not been applied here. 3.3% of all prompts got no guardrail, silently.
  case $(( 10#$(date +%M) % 4 )) in
    0) out="[Guardrail] Evidence before done: reformulate as a testable goal first; verification log is part of the deliverable; 3 failed fix-verify cycles => STOP and report (Art. 1, 14)." ;;
    1) out="[Guardrail] Surgical: minimum diff, no phantom dependencies, no speculative abstraction; out-of-scope findings => NOTICED BUT NOT TOUCHING (Art. 2)." ;;
    2) out="[Guardrail] Continuity: before ending — STATUS overwritten within bound, JOURNAL entry, MANIFEST current; dual-audience writing, no implicit references (Art. 6)." ;;
    3) out="[Guardrail] Ambiguity (Art. 3): incompatible readings => ASK, offering 2-3 concrete options; low ambiguity => proceed and state the assumption VISIBLY. Never guess silently. A contradiction between existing artifacts => STOP and report \`CONTRADICTION:\` citing BOTH sides. Measured 2026-08-27: that prefix has NEVER been emitted in 193 transcripts, while five contradictions were found by hand in a single day." ;;
  esac
fi
printf '%s\n' "$out"
# >>> cold-start handoff (PLAN_cold_start_handoff_harmonization_2026-10-26.md §3, §10). On the prompt `cold start` (also
# `cold-start`, `coldstart`, or one of them + ONE role), handoff_resolve.py prints the open handoff of this folder's role(s),
# a choice when several roles are open, or why there is none. Every other prompt pays one `case` test; nothing above
# this marker is changed (D4: the hook minus this block is byte-identical to the live base). The resolver's own prompt
# test is authoritative; this shell filter only avoids starting Python for prompts that cannot be a trigger.
case "$lower" in *cold*start*)
  _cs_n=$(printf '%s' "$lower" | tr -s '[:space:]' ' ' | sed -e 's/^ //' -e 's/ $//' -e 's/[.!]$//')
  case "$_cs_n" in "cold start"|"cold-start"|"coldstart"|"cold start "*|"cold-start "*|"coldstart "*)
    _cs_root="$(cd "$(dirname "$0")/../.." && pwd)"
    # Interpreter chain of session_anchor.sh: first reachable python3.12 wins, bare python3 is the last resort.
    _cs_py=""
    for _c in python3.12 /usr/local/bin/python3.12 /opt/homebrew/bin/python3.12 python3; do
      if command -v "$_c" >/dev/null 2>&1; then _cs_py="$_c"; break; fi
    done
    [ -n "$GUARDRAIL_PY_BIN" ] && _cs_py="$GUARDRAIL_PY_BIN"   # test-harness escape hatch (as ANCHOR_PY_BIN)
    _cs_res="${_cs_root}/governance/adapters/claude-code/handoff_resolve.py"
    _cs_out=""; _cs_rc=127
    if [ -f "$_cs_res" ] && [ ! -L "$_cs_res" ]; then
      _cs_out="$(_CS_PROMPT="$p" "$_cs_py" -I "$_cs_res" hook --root "$_cs_root" --cwd "$PWD")"; _cs_rc=$?
    fi
    # Printed only on rc 0 under the "[Cold start]" header (python exits 2 when it cannot open a script: header check, as
    # the anchor does). rc 0 with no output = not a trigger. Anything else is loud on a bare trigger, silent otherwise.
    case "${_cs_rc}:${_cs_out}" in
      "0:[Cold start]"*) printf '%s\n' "$_cs_out" ;;
      "0:") ;;
      *) case "$_cs_n" in "cold start"|"cold-start"|"coldstart")
           echo "[Cold start] RESOLVER FAILED (rc=${_cs_rc}): no handoff was injected. Run: python3.12 ${_cs_root}/governance/adapters/claude-code/handoff_resolve.py resolve --cwd \"\$PWD\" and tell the founder the cold-start hook failed." ;;
         esac ;;
    esac ;;
  esac ;;
esac
# <<< cold-start handoff
exit 0

#!/bin/bash
# test_hooks.sh — fire/no-fire matrix for every Claude Code L1 hook
# (POLICY_CORE.md self-test duty, Constitution Art. 12). Run standalone or
# from the monthly audit. Exit 0 = all green; non-zero = failures listed.
# Workspace root = nearest ancestor holding BOTH .claude/settings.json and .claude/hooks/.
# Was a fixed "../../..", correct only for the private tree's layout; in the published kit
# (one level shallower) $H pointed at a directory that did not exist and 13 of 23 cases
# "failed" for want of a hook to run. Both markers are required because an ARMED project
# carries settings.json alone and would otherwise be mistaken for the root.
# --root PATH / $GOVERNANCE_WORKSPACE beat the upward search, which is announced when used.
# bootstrap.sh installs into a workspace this script cannot infer when the kit lives outside
# it -- the ordinary adopter layout -- and the old behaviour was to search upward and run
# against whatever unrelated tree it happened to find, silently (§5.5 panel, three reviewers).
ROOT=""
if [ "${1:-}" = "--root" ] && [ -n "${2:-}" ]; then ROOT="$2"; shift 2
elif [ -n "${GOVERNANCE_WORKSPACE:-}" ]; then ROOT="$GOVERNANCE_WORKSPACE"; fi
if [ -n "$ROOT" ]; then
  if [ ! -f "$ROOT/.claude/settings.json" ]; then
    echo "root $ROOT has no .claude/settings.json - run bootstrap.sh there first" >&2; exit 2
  fi
else
ROOT="$(cd "$(dirname "$0")" && pwd)"
while [ "$ROOT" != "/" ]; do
  if [ -f "$ROOT/.claude/settings.json" ] && [ -d "$ROOT/.claude/hooks" ]; then break; fi
  ROOT="$(dirname "$ROOT")"
done
if [ "$ROOT" = "/" ]; then
  echo "could not locate a workspace root (.claude/settings.json + .claude/hooks/) above $(dirname "$0")" >&2
  echo "  run: bootstrap.sh /path/to/workspace   then: $0 --root /path/to/workspace" >&2
  exit 2
fi
  echo "workspace root (inferred): $ROOT" >&2
  echo "  pass --root PATH or set \$GOVERNANCE_WORKSPACE to be explicit" >&2
fi
H="$ROOT/.claude/hooks"
pass=0; fail=0

check() { # check <desc> <expect:fire|silent> <actual_output>
  local desc="$1" expect="$2" out="$3" got="silent"
  [ -n "$out" ] && got="fire"
  if [ "$expect" = "$got" ]; then pass=$((pass+1)); printf 'PASS  %-58s %s\n' "$desc" "$got";
  else fail=$((fail+1)); printf 'FAIL  %-58s expected %s, got %s\n' "$desc" "$expect" "$got"; fi
}

j() { printf '{"tool_input":{"command":"%s"}}' "$1"; }

echo "── post_push_audit (PUSH-AUDIT) ──"
check "git push fires"            fire   "$(j 'git push origin main' | "$H/post_push_audit.sh")"
check "git -C path push fires"    fire   "$(j 'git -C /tmp/x push' | "$H/post_push_audit.sh")"
check "find is silent"            silent "$(j 'find . -name *.md' | "$H/post_push_audit.sh")"
check "git log is silent"         silent "$(j 'git log --oneline' | "$H/post_push_audit.sh")"

echo "── pretool_guard (DENY-*) ──"
check "delete person denied"      fire   "$(j 'osascript -e tell app Contacts to delete person 1' | "$H/pretool_guard.py")"
check "rm _archive/ denied"       fire   "$(j 'rm -rf NEOCORTEX/_archive/old' | "$H/pretool_guard.py")"
check "mv frozen archive denied"  fire   "$(j 'mv ANTIGRAVITY-2025.md /tmp/' | "$H/pretool_guard.py")"
check "mv OUT of _archive denied" fire   "$(j 'mv NEOCORTEX/_archive/x.md /tmp/' | "$H/pretool_guard.py")"
check "mv INTO _archive allowed"  silent "$(j 'mv NEOCORTEX/old.md NEOCORTEX/_archive/' | "$H/pretool_guard.py")"
check "normal rm is silent"       silent "$(j 'rm /tmp/scratch.txt' | "$H/pretool_guard.py")"
check "normal command silent"     silent "$(j 'ls -la' | "$H/pretool_guard.py")"
# DENY-WINDOW is time-dependent: assert correct behavior for the current clock.
hm=$((10#$(date +%H%M)))
if [ $hm -ge 2230 ] || [ $hm -lt 240 ]; then expect_w=fire; else expect_w=silent; fi
check "extraction vs window (now)" $expect_w "$(j 'python3 unified_extractor.py --full' | "$H/pretool_guard.py")"

echo "── postedit_compile_gate (COMPILE-GATE) ──"
good="$(mktemp /tmp/hookt_XXXXXX).py"; echo 'x = 1' > "$good"
bad="$(mktemp /tmp/hookt_XXXXXX).py";  echo 'def broken(:' > "$bad"
badsh="$(mktemp /tmp/hookt_XXXXXX).sh"; echo 'if [ x; then' > "$badsh"
fj() { printf '{"tool_input":{"file_path":"%s"}}' "$1"; }
check "valid .py is silent"       silent "$(fj "$good" | "$H/postedit_compile_gate.py")"
check "broken .py blocks"         fire   "$(fj "$bad" | "$H/postedit_compile_gate.py")"
check "broken .sh blocks"         fire   "$(fj "$badsh" | "$H/postedit_compile_gate.py")"
rm -f "$good" "$bad" "$badsh" /tmp/hookt_*.pyc 2>/dev/null

echo "── stop_closeout_gate (CLOSEOUT-GATE) ──"
t1=$(mktemp); cat > "$t1" <<'EOF'
{"message":{"content":[{"type":"tool_use","name":"Edit","input":{"file_path":"/workspace/proj/a.py"}}]}}
EOF
t2=$(mktemp); cat > "$t2" <<'EOF'
{"message":{"content":[{"type":"tool_use","name":"Edit","input":{"file_path":"/workspace/proj/a.py"}}]}}
{"message":{"content":[{"type":"tool_use","name":"Bash","input":{"command":"python3.12 -m pytest tests/ -q"}}]}}
EOF
sj() { printf '{"transcript_path":"%s","stop_hook_active":%s}' "$1" "$2"; }
check "edit w/o verification blocks" fire   "$(sj "$t1" false | "$H/stop_closeout_gate.py")"
check "edit + pytest is silent"      silent "$(sj "$t2" false | "$H/stop_closeout_gate.py")"
check "re-entry (active) is silent"  silent "$(sj "$t1" true  | "$H/stop_closeout_gate.py")"
rm -f "$t1" "$t2"

echo "── context injectors (smoke: non-empty, bounded) ──"
a=$("$H/session_anchor.sh"); check "session_anchor emits"  fire "$a"
[ "$(printf '%s' "$a" | wc -l)" -lt 60 ] && { pass=$((pass+1)); echo "PASS  anchor bounded (<60 lines)"; } || { fail=$((fail+1)); echo "FAIL  anchor exceeds 60 lines"; }
g1=$(printf '{"prompt":"draft a plan for the migration"}' | "$H/prompt_guardrail.sh")
check "guardrail: plan prompt -> adversarial-review line" fire "$(printf '%s' "$g1" | grep -i 'adversarial review')"
g2=$(printf '{"prompt":"delete the stale rows and apply"}' | "$H/prompt_guardrail.sh")
check "guardrail: destructive prompt -> dry-run line"     fire "$(printf '%s' "$g2" | grep -i 'dry-run')"
g3=$(printf '{"prompt":"hello"}' | "$H/prompt_guardrail.sh")
check "guardrail: generic prompt -> rotated reminder"     fire "$g3"

echo "──────────────────────────────"
echo "RESULT: $pass passed, $fail failed"
exit $fail

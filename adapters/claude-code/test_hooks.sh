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
# M-14 (2026-09-04): bound the upward search at this kit's OWN git checkout, so a clone
# nested inside an already-governed tree (e.g. cloned for review INSIDE an adopter's real
# workspace) cannot silently escape past itself and test an unrelated, unconnected root
# purely because that ancestor also happens to carry both markers. Found exactly this way:
# a fresh clone placed inside a governed workspace walked past its own directory entirely
# and ran every case against that OUTER workspace's real, live hooks instead of the kit's
# own copies -- passing most cases for the wrong reason and failing one on the outer
# workspace's own unrelated state. git, not just directory nesting, because a plain tarball
# download (no .git) has no such boundary to detect and keeps the prior best-effort search.
FLOOR="$(git -C "$ROOT" rev-parse --show-toplevel 2>/dev/null || true)"
while [ "$ROOT" != "/" ]; do
  if [ -f "$ROOT/.claude/settings.json" ] && [ -d "$ROOT/.claude/hooks" ]; then break; fi
  if [ -n "$FLOOR" ] && [ "$ROOT" = "$FLOOR" ]; then ROOT="/"; break; fi
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
# P0.0a L0 write-guard. $L0 keeps the literal away from a `>` in this file's own text --
# the guard matches command text, so spelling it inline denies the write that creates this
# file. That is not a quirk to work around; it is the guard proving it fires.
L0="PAICodeConstitution-2026.md"
check "L0 redirect-overwrite denied" fire   "$(j "cat draft.md > $L0" | "$H/pretool_guard.py")"
check "L0 sed -i denied"             fire   "$(j "sed -i '' s/a/b/ $L0" | "$H/pretool_guard.py")"
check "L0 cp-onto denied"            fire   "$(j "cp draft.md $L0" | "$H/pretool_guard.py")"
check "L0 rm denied"                 fire   "$(j "rm $L0" | "$H/pretool_guard.py")"
check "L0 clone-path denied"         fire   "$(j "cat d.md > 'GitHub pdewost repositories/antigravity/$L0'" | "$H/pretool_guard.py")"
check "L0 read allowed"              silent "$(j "cat $L0" | "$H/pretool_guard.py")"
check "L0 read-into-copy allowed"    silent "$(j "cat $L0 > /tmp/copy.md" | "$H/pretool_guard.py")"
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
# M-14 (2026-09-04): the two Python-produced-section checks below assert on
# _skills/SKILLS_INDEX.md and governance/OPEN_WORK.md content that a genuinely fresh
# bootstrap.sh destination does not have -- found because they FAILED the first time this
# script ran against an isolated fixture instead of a workspace with real content lying
# around already. Seed the minimal rows each check needs, ONLY IF ABSENT (never clobber a
# real adopter file this happens to run against), and remove only what was seeded.
_ANCHOR_IDX_SEEDED=0; _ANCHOR_OW_SEEDED=0
if [ ! -e "$ROOT/_skills/SKILLS_INDEX.md" ]; then
  mkdir -p "$ROOT/_skills"
  printf '| `example_skill` | 1.0.0 | library | active | smoke-test fixture row |\n' > "$ROOT/_skills/SKILLS_INDEX.md"
  _ANCHOR_IDX_SEEDED=1
fi
if [ ! -e "$ROOT/governance/OPEN_WORK.md" ]; then
  mkdir -p "$ROOT/governance"
  printf '| Item | Where | Status |\n|---|---|---|\n| smoke-test fixture item | here | READY |\n' > "$ROOT/governance/OPEN_WORK.md"
  _ANCHOR_OW_SEEDED=1
fi
a=$("$H/session_anchor.sh"); check "session_anchor emits"  fire "$a"
[ "$(printf '%s' "$a" | wc -l)" -lt 60 ] && { pass=$((pass+1)); echo "PASS  anchor bounded (<60 lines)"; } || { fail=$((fail+1)); echo "FAIL  anchor exceeds 60 lines"; }
# --- interpreter resolution (2026-08-27). `session_anchor.sh` used bare `python3` at four sites,
# the PATH-dependent hazard machine_config.yaml names. It is now resolved once via a fallback chain.
# ASSERTING "the anchor emits" DOES NOT DISCRIMINATE: with every Python block dead the script still
# emits its bash-printed headers (measured: 599 bytes vs 6973). So the assertion is on a section
# that ONLY Python can produce, and the negative case proves that section is the discriminator.
check "anchor: python-produced section present"  fire   "$(printf '%s' "$a" | grep -F 'Open fleet work')"
check "anchor: skill rows present"               fire   "$(printf '%s' "$a" | grep -F '| library |')"
bad=$(ANCHOR_PY_BIN=/nonexistent/python "$H/session_anchor.sh" 2>/dev/null)
check "anchor: broken interpreter LOSES that section" silent "$(printf '%s' "$bad" | grep -F 'Open fleet work')"
[ "$_ANCHOR_IDX_SEEDED" = 1 ] && rm -f "$ROOT/_skills/SKILLS_INDEX.md"
[ "$_ANCHOR_OW_SEEDED" = 1 ] && rm -f "$ROOT/governance/OPEN_WORK.md"
check "anchor: broken interpreter still emits headers" fire "$bad"
g1=$(printf '{"prompt":"draft a plan for the migration"}' | "$H/prompt_guardrail.sh")
check "guardrail: plan prompt -> adversarial-review line" fire "$(printf '%s' "$g1" | grep -i 'adversarial review')"
g2=$(printf '{"prompt":"delete the stale rows and apply"}' | "$H/prompt_guardrail.sh")
check "guardrail: destructive prompt -> dry-run line"     fire "$(printf '%s' "$g2" | grep -i 'dry-run')"
g3=$(printf '{"prompt":"hello"}' | "$H/prompt_guardrail.sh")
check "guardrail: generic prompt -> rotated reminder"     fire "$g3"
# THE OCTAL TRAP, pinned 2026-08-27. `date +%M` returns a ZERO-PADDED minute; bash reads a leading
# zero as OCTAL, so `$(( 08 % 4 ))` aborts with "value too great for base" and the hook injects
# NOTHING. Pre-existing since the file was written — 2 minutes of every hour, silently unguarded.
# ASSERT ON THE FILE, NOT ON BASH. A first version of this test ran `bash -c "$(( 10#08 % 4 ))"`,
# which passes whatever the hook contains: it tested the idiom, not its adoption. Caught by
# neuter-verification. `grep -c` is also avoided here — it prints "0", a NON-EMPTY string, so
# `check ... fire "$(grep -c ...)"` fires unconditionally. Both mistakes were mine, same commit.
check "guardrail: minute forced to base 10 (octal trap)" fire "$(grep -F '10#$(date +%M)' "$H/prompt_guardrail.sh")"
check "guardrail: Art. 3 is in the rotation"             fire "$(grep -F 'Art. 3' "$H/prompt_guardrail.sh")"
check "guardrail: names the CONTRADICTION token"         fire "$(grep -F 'CONTRADICTION:' "$H/prompt_guardrail.sh")"

echo "──────────────────────────────"
echo "RESULT: $pass passed, $fail failed"
exit $fail

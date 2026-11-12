#!/bin/bash
# test_hooks.sh — fire/no-fire matrix for every Claude Code L1 hook
# (POLICY_CORE.md self-test duty, Constitution Art. 12). Run standalone or
# from a scheduled job of your own. Exit 0 = all green; non-zero = failures listed.
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
check "L0 clone-path denied"         fire   "$(j "cat d.md > 'GitHub example repositories/antigravity/$L0'" | "$H/pretool_guard.py")"
check "L0 read allowed"              silent "$(j "cat $L0" | "$H/pretool_guard.py")"
check "L0 read-into-copy allowed"    silent "$(j "cat $L0 > /tmp/copy.md" | "$H/pretool_guard.py")"
check "normal rm is silent"       silent "$(j 'rm /tmp/scratch.txt' | "$H/pretool_guard.py")"
check "normal command silent"     silent "$(j 'ls -la' | "$H/pretool_guard.py")"
# DENY-WINDOW is time-dependent: assert correct behavior for the current clock. The guard reads every
# `window: "HH:MM-HH:MM"` line of <root>/governance/machine_config.yaml and ASKS when that file cannot be read; the
# expectation follows the same rules (2026-10-28: it used to hard-code one machine's window, which failed every night
# for an adopter whose config has another window or a placeholder).
MC="$ROOT/governance/machine_config.yaml"
expect_w=silent
if [ ! -r "$MC" ]; then
  expect_w=fire
else
  hm=$((10#$(date +%H%M)))
  while IFS= read -r w; do
    ws=$((10#${w:0:2}${w:3:2})); we=$((10#${w:6:2}${w:9:2}))
    if [ $ws -gt $we ]; then
      if [ $hm -ge $ws ] || [ $hm -lt $we ]; then expect_w=fire; fi
    elif [ $hm -ge $ws ] && [ $hm -lt $we ]; then expect_w=fire; fi
  done < <(grep -oE 'window:[[:space:]]*"[0-9]{2}:[0-9]{2}-[0-9]{2}:[0-9]{2}"' "$MC" | grep -oE '[0-9]{2}:[0-9]{2}-[0-9]{2}:[0-9]{2}')
fi
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
# Hermetic (2026-11-02, founder "Fix both"): run from an empty temp dir. Inside a real project the gate may add
# NON-BLOCKING advisories about that project's records (continuity_sweep, closeout_lint), which a "silent" check
# counts as a fire: the result then depended on the cwd's live state and timing, not on the gate's logic.
qcwd="$(mktemp -d)"
check "edit + pytest is silent"      silent "$(cd "$qcwd" && sj "$t2" false | "$H/stop_closeout_gate.py")"
check "re-entry (active) is silent"  silent "$(cd "$qcwd" && sj "$t1" true  | "$H/stop_closeout_gate.py")"
rmdir "$qcwd" 2>/dev/null
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
# Run from $ROOT (PLAN_cold_start v0.3 S2, 2026-10-26): the OPEN_WORK digest is now printed only at the workspace
# root and in governance/, so the assertion below on 'Open fleet work' held only when this script happened to be
# launched from one of those two places. The cwd is now pinned instead of inherited.
a=$(cd "$ROOT" && "$H/session_anchor.sh"); check "session_anchor emits"  fire "$a"
[ "$(printf '%s' "$a" | wc -l)" -lt 60 ] && { pass=$((pass+1)); echo "PASS  anchor bounded (<60 lines)"; } || { fail=$((fail+1)); echo "FAIL  anchor exceeds 60 lines"; }
# --- interpreter resolution (2026-08-27). `session_anchor.sh` used bare `python3` at four sites,
# the PATH-dependent hazard machine_config.yaml names. It is now resolved once via a fallback chain.
# ASSERTING "the anchor emits" DOES NOT DISCRIMINATE: with every Python block dead the script still
# emits its bash-printed headers (measured: 599 bytes vs 6973). So the assertion is on a section
# that ONLY Python can produce, and the negative case proves that section is the discriminator.
check "anchor: python-produced section present"  fire   "$(printf '%s' "$a" | grep -F 'Open fleet work')"
check "anchor: skill rows present"               fire   "$(printf '%s' "$a" | grep -F '| library |')"
bad=$(cd "$ROOT" && ANCHOR_PY_BIN=/nonexistent/python "$H/session_anchor.sh" 2>/dev/null)
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

# The four hooks below had no case until 2026-11-09 (the public README said so). Three have no stdout to read, or would
# act on the live tree (regen rewrites the index, the cold-start resolver reads real handoffs), so each runs as a COPY of
# the real hook inside a throwaway root; those hooks locate their root from their own file. Stubs replace only what lies
# BEYOND the hook (build_index.py, handoff_resolve.py, receipt.py), and each has its own tests.
FX="$(mktemp -d)"
tj() { printf '{"tool_name":"%s","tool_input":{"%s":"%s"}}' "$1" "$2" "$3"; }
fx_hook() { mkdir -p "$1/.claude/hooks" && cp "$H/$2" "$1/.claude/hooks/$2"; }

echo "── session_record_gate (session-record tripwire) ──"
check "record: Read of session_record asks"       fire   "$(tj Read file_path governance/session_record/a.jsonl | "$H/session_record_gate.py" | grep -F '"ask"')"
check "record: Bash cat of session_record asks"   fire   "$(tj Bash command 'cat governance/session_record/a.jsonl' | "$H/session_record_gate.py" | grep -F '"ask"')"
check "record: Read of another file is silent"    silent "$(tj Read file_path governance/notes.md | "$H/session_record_gate.py")"
check "record: Bash with no reader is silent"     silent "$(tj Bash command 'ls governance/session_record' | "$H/session_record_gate.py")"

echo "── skill_index_regen (SKILL-INDEX-REGEN) ──"
R="$FX/regen"; fx_hook "$R" skill_index_regen.py; mkdir -p "$R/_skills/x" "$R/notes/_skills_ideas"
printf 'open("regen_ran","a").write("x")\n' > "$R/_skills/build_index.py"
ran() { [ -f "$R/regen_ran" ] && echo ran; }
tj Edit file_path "$R/_skills/x/notes.md" | "$R/.claude/hooks/skill_index_regen.py"
check "regen: other file in a skill is silent"    silent "$(ran)"
tj Edit file_path "$R/notes/_skills_ideas/SKILL.md" | "$R/.claude/hooks/skill_index_regen.py"
check "regen: SKILL.md outside _skills is silent" silent "$(ran)"
tj Edit file_path "$R/_skills/x/SKILL.md" | "$R/.claude/hooks/skill_index_regen.py"
check "regen: edit of a SKILL.md rebuilds"        fire   "$(ran)"

echo "── verify_advisory (VERIFY-GATE, advisory path) ──"
V="$FX/verify"; fx_hook "$V" verify_advisory.py; mkdir -p "$V/governance"
printf 'x = 1\n' > "$FX/code.py"; printf 'text\n' > "$FX/doc.md"
tr_of() { printf '{"message":{"content":[{"type":"tool_use","name":"Write","input":{"file_path":"%s"}}]}}\n' "$1" > "$FX/t.jsonl"; }
stop() { (cd "$V" && printf '{"transcript_path":"%s","stop_hook_active":%s}' "$FX/t.jsonl" "$1" | "$V/.claude/hooks/verify_advisory.py"); }
vlog() { grep -F "$1" "$V/governance/hook_telemetry.log" 2>/dev/null; }
tr_of "$FX/doc.md"; stop false
check "verify: edit of a non-code file is silent" silent "$(vlog VERIFY-)"
tr_of "$FX/code.py"; stop true
check "verify: stop_hook_active is silent"        silent "$(vlog VERIFY-)"
stop false
check "verify: code edit, no receipt: PENDING"    fire   "$(vlog VERIFY-PENDING)"
check "verify: code edit, no receipt: not PROVEN" silent "$(vlog VERIFY-PROVEN)"
mkdir -p "$V/_skills/verify_gate/scripts" "$V/.verify"; printf '{}\n' > "$V/.verify/receipt.json"
printf 'import pathlib\ndef project_root(changed_paths):\n    return pathlib.Path(%s), "ok"\ndef validate_receipt(rec, changed_paths, ledger_path):\n    return True, ""\n' "'$V'" > "$V/_skills/verify_gate/scripts/receipt.py"   # receipt-v2 R-3: the advisory asks project_root first
# receipt-v2 GF-1: the advisory runs the scripts only from approved bytes, through the fixture's own hook_support.py and
# approved list, so the fixture approves its stub (inside the fixture only; the live list is never written).
fx_hook "$V" hook_support.py; python3.12 "$V/.claude/hooks/hook_support.py" --write-approved >/dev/null
stop false
check "verify: valid receipt: PROVEN"             fire   "$(vlog VERIFY-PROVEN)"

echo "── prompt_guardrail (HANDOFF cold start path) ──"
C="$FX/cold"; fx_hook "$C" prompt_guardrail.sh; mkdir -p "$C/governance/adapters/claude-code"
CS="$C/governance/adapters/claude-code/handoff_resolve.py"
printf 'print("[Cold start] stub")\n' > "$CS"
say() { (cd "$C" && printf '{"prompt":"%s"}' "$1" | "$C/.claude/hooks/prompt_guardrail.sh" | grep -F "$2"); }
check "cold start: the bare prompt injects"       fire   "$(say 'cold start' '[Cold start] stub')"
check "cold start: prompt + role injects"         fire   "$(say 'cold start governance' '[Cold start] stub')"
check "cold start: inside a sentence is silent"   silent "$(say 'how does cold start work' '[Cold start]')"
check "cold start: another prompt is silent"      silent "$(say 'hello there' '[Cold start]')"
rm -f "$CS"
check "cold start: no resolver says so, loudly"   fire   "$(say 'cold start' 'RESOLVER FAILED')"
rm -rf "$FX"

echo "──────────────────────────────"
echo "RESULT: $pass passed, $fail failed"
exit $fail

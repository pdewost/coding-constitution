#!/bin/bash
# post_push_audit.sh — PostToolUse(Bash) hook filter for the github_commit_audit gate.
#
# Fires the post-push audit instruction ONLY when the executed Bash command actually
# contains a `git push` invocation. Replaces the agent-type hook (retired 2026-06-10)
# whose `if:` condition was evaluated AFTER spawning an audit agent, causing the agent
# to fire on every Bash call (two documented false fires on `find`/`grep` commands
# during the constitution-upgrade session of 2026-06-10).
#
# Contract (Claude Code PostToolUse command hook):
#   stdin  — JSON payload; the executed command is at .tool_input.command
#   exit 0 with no stdout      → silent no-op (non-push commands)
#   stdout {"decision":"block"} → "reason" is fed back to the main agent as an instruction
#
# Known limitation: matches `git push` and `git -C <path> push` (with intervening
# option flags); a quoted literal like echo "git push" also matches — acceptable,
# rare, and fails toward auditing rather than away from it.

cmd=$(python3 -c '
import json, sys
try:
    data = json.load(sys.stdin)
    print(data.get("tool_input", {}).get("command", ""))
except Exception:
    pass
')

if printf '%s' "$cmd" | grep -qE '(^|[;&|[:space:]])git([[:space:]]+-C[[:space:]]+[^[:space:]]+)?([[:space:]]+-[^[:space:]]+)*[[:space:]]+push([[:space:]]|$)'; then
  cat <<'JSON'
{"decision": "block", "reason": "A git push just completed (post-push audit gate). Run the github_commit_audit post-push audit for the pushed repository now: (1) remote sync - git fetch origin && git diff HEAD origin/<branch> must be empty; (2) README vs reality - verify feature claims, version strings, and file paths against actual repo contents, flag phantom references with line numbers; (3) version-tag consistency - cross-check *.md version strings vs source code; (4) doc-code coherence - CHANGELOG / PROJECT_BRIEF / other docs match current state; (5) structural completeness - README, LICENSE, .gitignore present; TODO/FIXME in code (excluding string literals); (6) external-auditor test - could a cold-start LLM understand what this project does, what changed, and whether the change is complete from ONLY the README + last commit + file tree? Output the audit as a Check / Status / Evidence table; list remediation steps for any FAIL."}
JSON
fi
exit 0

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

cmd=$(python3.12 -c '
import json, sys
try:
    data = json.load(sys.stdin)
    print(data.get("tool_input", {}).get("command", ""))
except Exception:
    pass
')

if printf '%s' "$cmd" | grep -qE '(^|[;&|[:space:]])git([[:space:]]+-C[[:space:]]+[^[:space:]]+)?([[:space:]]+-[^[:space:]]+)*[[:space:]]+push([[:space:]]|$)'; then
  cat <<'JSON'
{"decision": "block", "reason": "A git push just completed (post-push audit gate — github_commit_audit). Run the TESTED audit SCRIPT (not a hand-derived checklist): `python3.12 _skills/github_commit_audit/scripts/post_audit.py --repo-path \"$(pwd)\" --repo-name <the pushed repo name>` (add `--deep` for a secrets scan). This executes the 42-test-covered audit logic — remote-sync, README-vs-reality, version-tag consistency, doc-code coherence, structural completeness, external-auditor test — instead of re-deriving those six checks by prose. Report the script's Check/Status/Evidence table and remediate any FAIL before proceeding. FALLBACK: if the script errors or is unavailable, run the six checks manually and report the error (never skip the audit). BYPASS a false-positive for one push by noting the reason and proceeding (founder-owned; set GHCA_AUDIT_BYPASS=1 to document it)."}
JSON
fi
exit 0

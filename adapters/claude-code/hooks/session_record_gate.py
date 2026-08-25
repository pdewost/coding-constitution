#!/usr/bin/env python3
"""session_record_gate.py — ASK-SESSION-RECORD policy, Claude Code adapter.

PreToolUse hook (matchers: Read, Grep, Glob, Bash). Returns permissionDecision "ask" for
any access to `governance/session_record/`, so an agent must obtain explicit founder
authorization before reading raw dialog. Founder ruling 2026-07-28:

    "we will just gate the session's outputs and have any LLM request explicit
     authorization to access them. After all this is raw, uncompressed content
     absolutely not suited for regular ingestion."

WHY A HOOK AND NOT ONLY A PERMISSION RULE. Both are installed, deliberately:
  - The `permissions.ask` rule is the declarative layer. It failed silently on first
    attempt (2026-07-28) because the pattern was CWD-relative while the tool call used an
    absolute path; the `/` project-root anchor fixes that. A rule that depends on path
    spelling is a rule that can miss.
  - A PreToolUse hook is evaluated BEFORE the allow/ask/deny rules and cannot be overridden
    by a broad `allow` in a higher-precedence scope (`settings.local.json` here carries 404
    allow rules). It also catches `Bash(cat/head/grep …)` paths at the command-text level.

NEITHER covers a subprocess that opens the file itself (a python script doing open()).
That is a documented, accepted limit — stated here rather than left for someone to
discover, per the workspace documented-invariant discipline.

Fail-open on ANY error: a gate that crashes must not block unrelated work. The cost of a
missed prompt is a read of local data the founder owns; the cost of a wedged session is
every other task. Exit 0 silently when the path is unrelated.
"""
import json
import re
import sys

# Deliberately the bare directory name, not a rooted path. The corpus moved out of
# governance/ on 2026-07-28 (Art. 5: personal data is not L4 reference material) and will
# move again into the future project's data/. A path-anchored marker would have silently
# stopped matching at each move — the gate would still "exist" and guard nothing, which is
# the failure class this workspace spent 2026-07-27 removing. Matching the name covers
# governance/session_record, _session_record, and <project>/data/session_record alike.
MARKER = "session_record"

# Bash readers that would surface file contents without going through the Read tool.
BASH_READERS = re.compile(
    r"\b(cat|bat|head|tail|less|more|sed|awk|grep|rg|strings|open|cp|xxd|od)\b")

REASON = (
    "ASK-SESSION-RECORD (founder ruling 2026-07-28): governance/session_record/ holds raw, "
    "uncompressed verbatim dialog — not suited to routine ingestion, and never publishable. "
    "Reading it requires explicit founder authorization for THIS access. Approve only if the "
    "task genuinely needs the transcript rather than the NEOCORTEX summary."
)


def _payload_mentions_record(data: dict) -> bool:
    ti = data.get("tool_input") or {}
    name = data.get("tool_name") or ""
    if name == "Bash":
        cmd = str(ti.get("command", ""))
        return MARKER in cmd and bool(BASH_READERS.search(cmd))
    # Read / Grep / Glob and anything else carrying a path-ish field
    for key in ("file_path", "path", "pattern", "notebook_path"):
        if MARKER in str(ti.get(key, "")):
            return True
    return False


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)          # unreadable payload -> fail open
    try:
        if not _payload_mentions_record(data):
            sys.exit(0)
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "ask",
                "permissionDecisionReason": REASON,
            }
        }))
    except Exception:
        pass                 # never block on our own bug
    sys.exit(0)


if __name__ == "__main__":
    main()

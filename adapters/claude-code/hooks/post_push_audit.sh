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
# Detection (S4-45): `git [global options with values] push` as the command of a segment; an
# `echo 'git push'` literal no longer matches. A parse failure falls back to whitespace splitting.

PY=$(command -v python3.12 || command -v python3 || echo /usr/bin/python3)   # S4: bare python3.12 failed open when PATH lacked it
# S4-45 (final, 2026-10-05): tokenized detection. `git` must be the COMMAND of a segment (not an
# argument of echo/grep), and its global options are consumed WITH their values (`-C "a b"`, `-c k=v`,
# `--git-dir=…`) before the subcommand is read. The regex this replaces missed quoted-space paths and
# `-c` values, and fired on `echo 'git push'`.
is_push=$("$PY" -c '
import json, re, shlex, sys
try:
    cmd = json.load(sys.stdin).get("tool_input", {}).get("command", "")
except Exception:
    sys.exit(0)
VAL = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path", "--config-env"}
WRAP = {"sudo", "env", "command", "nohup", "time", "nice", "exec", "builtin"}
for line in cmd.replace("\r", "\n").split("\n"):
    try:
        lx = shlex.shlex(line, posix=True, punctuation_chars=True)
        lx.whitespace_split = True
        toks = list(lx)
    except ValueError:
        toks = line.split()
    seg = []
    for t in toks + [";"]:
        if t in (";", "&&", "||", "|", "&", "(", ")"):
            w = seg
            seg = []
            while w and (w[0] in WRAP or re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", w[0]) or (w[0].startswith("-") and len(w) > 1 and w[1] != "git")):
                w = w[1:]
            if w and w[0].split("/")[-1] == "git":
                i = 1
                while i < len(w) and w[i].startswith("-"):
                    i += 2 if w[i] in VAL else 1
                if i < len(w) and w[i] == "push":
                    print("1")
                    sys.exit(0)
        else:
            seg.append(t)
' <<< "$(cat)")

if [ "$is_push" = "1" ]; then
  cat <<'JSON'
{"decision": "block", "reason": "A git push just completed (post-push audit gate — github_commit_audit). Run the TESTED audit SCRIPT (not a hand-derived checklist): `python3.12 _skills/github_commit_audit/scripts/post_audit.py --repo-path \"$(pwd)\" --repo-name <the pushed repo name>` (add `--deep` for a secrets scan). This executes the 42-test-covered audit logic — remote-sync, README-vs-reality, version-tag consistency, doc-code coherence, structural completeness, external-auditor test — instead of re-deriving those six checks by prose. Report the script's Check/Status/Evidence table and remediate any FAIL before proceeding. FALLBACK: if the script errors or is unavailable, run the six checks manually and report the error (never skip the audit). BYPASS a false-positive for one push by noting the reason and proceeding (founder-owned; set GHCA_AUDIT_BYPASS=1 to document it)."}
JSON
fi
exit 0

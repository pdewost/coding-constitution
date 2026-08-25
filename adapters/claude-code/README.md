# Claude Code adapter — L1 policy → harness mechanism map
Status: ACTIVE · v1.0 · 2026-06-10

Implements `spec/POLICY_CORE.md` on Claude Code. Scripts live in
`.claude/hooks/` (tracked by the governance repo); wiring in
`.claude/settings.json`. Antigravity and Codex adapters (plan Phase 8) port
the same policies to their native mechanisms using this table as the template.

| Policy ID | Mechanism | Script |
|---|---|---|
| ANCHOR | SessionStart hook (`startup\|resume\|compact`) | `session_anchor.sh` |
| GUARDRAIL | UserPromptSubmit hook | `prompt_guardrail.sh` |
| COMPILE-GATE | PostToolUse hook (`Edit\|Write\|NotebookEdit`) | `postedit_compile_gate.py` |
| CLOSEOUT-GATE | Stop hook (loop-safe via `stop_hook_active`) | `stop_closeout_gate.py` |
| DENY-ARCHIVE / DENY-CONTACT-DELETE / DENY-WINDOW | PreToolUse hook (`Bash\|mcp__Control_your_Mac__osascript`) | `pretool_guard.py` |
| PUSH-AUDIT | PostToolUse hook (`Bash`, filtered) | `post_push_audit.sh` |
| (static) frozen-file immutability | `permissions.deny` Edit/Write rules | `settings.json` |

**Installer:** `python3 adapters/claude-code/install_adapters.py` — derives the hooks block from the workspace-root `.claude/settings.json` (single source of truth) and writes `<project>/.claude/settings.json` for every **migrated** (NEOCORTEX-bearing) project — enforcement follows migration (NEOCORTEX_SPEC §5). `--dry-run` (default) shows diffs; `--apply` writes; `--check` drift-checks (exit nonzero on drift). Armed-projects manifest: `governance/adapters/INSTALLED.json`. Fire-test: `python3 adapters/claude-code/verify_fires.py` — reads `governance/hook_heartbeat.log` (appended by `session_anchor.sh` on every real SessionStart) and reports PROVEN or PENDING per project.

**Self-test:** `bash adapters/claude-code/test_hooks.sh` — 23-case
fire/no-fire matrix; re-run by the monthly audit (Art. 12). Last green:
2026-06-10, 23/23.

**Known limits (honest per Art. 10):** hooks load at session start — changes
require a fresh session; the closeout gate sees only transcript-visible
actions; DENY-WINDOW matches a command pattern list, not semantics. The
judgment halves of enforcement live in `_skills/sprint_closeout`.

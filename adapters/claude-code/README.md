# Claude Code adapter — L1 policy → harness mechanism map
Status: ACTIVE · v1.0 · 2026-06-10

Implements the PAICodeConstitution-2026 on Claude Code. Scripts live in
`$WORKSPACE/.claude/hooks/` (tracked by the governance repo); wiring in
`$WORKSPACE/.claude/settings.json`. Adapters for other harnesses (Antigravity,
Codex) port the same policies to their native mechanisms using this table as
the template.

| Policy ID | Mechanism | Script |
|---|---|---|
| ANCHOR | SessionStart hook (`startup\|resume\|compact`) | `session_anchor.sh` |
| GUARDRAIL | UserPromptSubmit hook | `prompt_guardrail.sh` |
| COMPILE-GATE | PostToolUse hook (`Edit\|Write\|NotebookEdit`) | `postedit_compile_gate.py` |
| CLOSEOUT-GATE | Stop hook (loop-safe via `stop_hook_active`) | `stop_closeout_gate.py` |
| DENY-ARCHIVE / DENY-CONTACT-DELETE / DENY-WINDOW | PreToolUse hook (`Bash\|mcp__...`) | `pretool_guard.py` |
| PUSH-AUDIT | PostToolUse hook (`Bash`, filtered) | `post_push_audit.sh` |
| (static) frozen-file immutability | `permissions.deny` Edit/Write rules | `settings.json` |

**Installer:** `python3 adapters/claude-code/install_adapters.py` — derives the hooks block from the workspace-root `.claude/settings.json` (single source of truth) and writes `<project>/.claude/settings.json` for every **migrated** (NEOCORTEX-bearing) project — enforcement follows migration (NEOCORTEX_SPEC §5). `--dry-run` (default) shows diffs; `--apply` writes; `--check` drift-checks (exit nonzero on drift). Armed-projects manifest: `governance/adapters/INSTALLED.json`. Fire-test: `python3 adapters/claude-code/verify_fires.py` — reads `governance/hook_heartbeat.log` (appended by `session_anchor.sh` on every real SessionStart) and reports PROVEN or PENDING per project.

**Self-test:** `bash adapters/claude-code/test_hooks.sh` — fire/no-fire matrix covering every hook; re-run by the monthly audit (Art. 12). Customize DENY-WINDOW bounds in the script to match your `machine_config.example.yaml` forbidden_windows.

**Known limits (honest per Art. 10):** hooks load at session start — changes
require a fresh session; the closeout gate sees only transcript-visible
actions; DENY-WINDOW matches a command pattern list, not semantics. The
judgment halves of enforcement live in the `sprint_closeout` skill.

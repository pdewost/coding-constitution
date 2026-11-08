# Claude Code adapter — L1 policy → harness mechanism map
Status: ACTIVE · v1.0 (2026-06-10), table updated 2026-10-28

Implements `spec/POLICY_CORE.md` on Claude Code. Scripts live in
`.claude/hooks/` (tracked by the governance repo); wiring in
`.claude/settings.json`. Antigravity and Codex adapters (plan Phase 8) port
the same policies to their native mechanisms using this table as the template.

| Policy ID | Mechanism | Script |
|---|---|---|
| ANCHOR | SessionStart hook (`startup\|resume\|compact`) | `session_anchor.sh` |
| GUARDRAIL | UserPromptSubmit hook | `prompt_guardrail.sh` |
| HANDOFF | UserPromptSubmit hook: the marked `cold-start handoff` block at the end of `prompt_guardrail.sh`, entered only on a `cold start` prompt | `prompt_guardrail.sh` → `adapters/claude-code/handoff_resolve.py` |
| COMPILE-GATE | PostToolUse hook (`Edit\|Write\|NotebookEdit`) | `postedit_compile_gate.py` |
| CLOSEOUT-GATE | Stop hook (loop-safe via `stop_hook_active`) | `stop_closeout_gate.py` |
| DENY-ARCHIVE / DENY-CONTACT-DELETE / DENY-WINDOW | PreToolUse hook (`Bash\|mcp__Control_your_Mac__osascript`) | `pretool_guard.py` |
| PUSH-AUDIT | PostToolUse hook (`Bash`, filtered) | `post_push_audit.sh` |
| SKILL-INDEX-REGEN | PostToolUse hook (edits under `_skills/`) | `skill_index_regen.py` |
| VERIFY-GATE | Stop hook advisory; blocking only where a project holds `.verify/armed` | `verify_advisory.py`, `stop_closeout_gate.py` + `_skills/verify_gate/` |
| ASK-SESSION-RECORD | RETIRED 2026-10-26 (the gated stores no longer exist); the hook still asks on that path | `session_record_gate.py` |
| LOOP-BOUND (adapter only) | PreToolUse on test/verification runs (Art. 14) | `loop_bound.py` |
| APPROVED LIST (adapter only) | SessionStart and Stop: governed files must match `governance/hooks_approved.sha256` | `hook_support.py` |
| (static) frozen-file immutability | `permissions.deny` Edit/Write rules | `settings.json` |

**Installer:** `python3.12 adapters/claude-code/install_adapters.py --root <workspace>` — derives the hooks block from the workspace-root `.claude/settings.json` (single source of truth) and writes `<project>/.claude/settings.json` for every **migrated** (NEOCORTEX-bearing) project — enforcement follows migration (NEOCORTEX_SPEC §5). `--dry-run` (default) shows diffs; `--apply` writes; `--check` drift-checks (exit nonzero on drift). Armed-projects manifest: `governance/adapters/INSTALLED.json`. Fire-test: `python3 adapters/claude-code/verify_fires.py` — reads `governance/hook_heartbeat.log` (appended by `session_anchor.sh` on every real SessionStart) and reports, per project, for SessionStart only (the one event with a live trace: `Live proof: SessionStart only (session_anchor.sh heartbeat).` It then lists the other wired events, from the workspace `.claude/settings.json`, as no live proof here, covered by the synthetic `test_hooks.sh`; `wired events: unknown` when that file cannot be read): PROVEN (a heartbeat from that directory at most 14 days old), STALE (the latest heartbeat is older than 14 days, or stamped in the future beyond a 5-minute clock skew: it no longer proves the hooks fire) or PENDING (no heartbeat yet). PENDING and STALE are reported states, not failures (exit 0); it exits 2 when `INSTALLED.json` is absent, empty, unparseable or unsafe (symlink, hardlink, FIFO), or the heartbeat log is unsafe, because then nothing was verified.

**Self-test:** `bash adapters/claude-code/test_hooks.sh --root <workspace>` — 37-case
fire/no-fire matrix (Art. 12); its window case reads the workspace's own `machine_config.yaml`.

**Known limits (honest per Art. 10):** hooks load at session start — changes
require a fresh session; the closeout gate sees only transcript-visible
actions; DENY-WINDOW matches a command pattern list, not semantics. The
judgment halves of enforcement live in the author's `sprint_closeout` skill (not shipped in the public kit).

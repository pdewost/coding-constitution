# L1 Policy Core — harness-neutral enforcement definitions
Status: ACTIVE · v1.1 · 2026-06-20 (added VERIFY-GATE, advisory-first)

Each policy below is implemented by a per-harness adapter
(`governance/adapters/<harness>/`). The policy is normative; adapters are
dumb translators into the harness's native mechanism. A new harness means a
new adapter, never a policy rewrite. Data values (windows, ports) come from
`governance/machine_config.yaml` — policies reference, never embed, them.

| ID | Policy | Trigger | Action |
|---|---|---|---|
| ANCHOR | Inject cold-start context: Constitution pointer, project NEOCORTEX MANIFEST + STATUS (or legacy-BRAIN notice), skill index (≤40 lines) | Session start, resume, and post-compaction | Add to context (replaces 2025 §9bis "re-read Tier 0" ceremony) |
| GUARDRAIL | Inject a task-aware checklist: planning prompts → task-class budgets + PLAN-before-code threshold + **propose adversarial review of drafted PLANs before validation, with reasons** (NEOCORTEX_SPEC §3 gate); destructive prompts → dry-run/apply gates + project invariants; otherwise → one rotated Art. 1/2/6 reminder (rotation defeats habituation) | Every user prompt | Add ≤4 lines to context (mechanizes the user's 2025-era manual guardrail suffix) |
| COMPILE-GATE | Syntax-check every edited file: `.py` → py_compile; `.sh` → `bash -n`; `.applescript` → reject `¬` then `osacompile -o /dev/null` | After each file edit/write | Block with the error fed back (2025 §6.1). Import smoke tests are NOT in the gate — importing executes top-level code; they belong in the closeout procedure |
| CLOSEOUT-GATE | Before a turn ends after code edits: verification commands ran this session? `JOURNAL.md` over its 64 KB bound? | Agent attempts to stop | Block once with the missing item named (never loops: respects the harness's re-entry flag) |
| DENY-ARCHIVE | `rm`/`mv`/`rmdir` touching `_archive/` or `ANTIGRAVITY-2025.md` | Before tool execution | Deny (Constitution Art. 6: archives are never deleted) |
| DENY-CONTACT-DELETE | AppleScript `delete person`/`delete people` in any command or script | Before tool execution | Deny (irreversible contact-data loss; Art. 4) |
| VERIFY-GATE | Independent verification of code-affecting work: a verdict whose independence is **externally anchored** (harness-spawned subagent / stateless API / CI / human with external identity) — never the drafting agent self-grading. Advisory where un-anchored; bound to a change-set, single-use receipt | Before a code-edited turn completes (couples to CLOSEOUT-GATE) | Block once on an anchored, substantiated-HIGH refutation; **advisory (warn) where un-anchored — never bricks.** Mechanizes "Assume Independent Audit" / Art. 1 / Art. 12 via `_skills/verify_gate` |
| DENY-WINDOW | Extraction/vectorization commands during `machine_config.yaml` forbidden windows | Before tool execution | Deny with the window cited (2025 §14) |
| PUSH-AUDIT | Real `git push` executed | After tool execution | Feed the 6-check post-push audit instruction back (Phase 0, the adapter template) |

**L1 distribution (Claude Code):** `governance/adapters/claude-code/install_adapters.py` generates project adapters from the workspace-root `.claude/settings.json` as the single policy source; `governance/adapters/INSTALLED.json` tracks the armed-projects manifest. Hook fires are self-proving via `governance/hook_heartbeat.log` (written by `session_anchor.sh`); `verify_fires.py` reports PROVEN/PENDING per project.

**VERIFY-GATE — trust anchor + rollout (2026-06-20, advisory-first; 3 review rounds).** A single in-context agent cannot mechanically verify itself, so the trust anchor sits OUTSIDE the producing agent: the attestation is written by the orchestrator, never the judge, and a verdict can BLOCK only when its `anchor_kind` is external AND `attestor != producer` (a faked self-spawn collapses to advisory). Where no external anchor exists, the gate is advisory. Implemented by `_skills/verify_gate/` (Python+shell, harness-neutral). **Rolled out advisory-first:** pushed to armed projects in warn-mode; a project flips to blocking only with a wired reviewer backend + a proven change-set receipt. **Honest scope:** un-bypassable only on the remote+CI path; locally = in-session adapter + a bypassable `pre-commit`. Judge-down = fail-closed-with-`--override`, never silent fail-open.

**Self-test duty (Art. 12):** every policy ships with a fire/no-fire test
matrix (`governance/adapters/<harness>/test_hooks.sh`); the monthly audit
re-runs it. A hook that cannot prove it fires is presumed dead.

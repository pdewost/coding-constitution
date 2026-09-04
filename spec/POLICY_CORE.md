# L1 Policy Core — harness-neutral enforcement definitions
Status: ACTIVE · v1.3 · 2026-08-27 (CONTEXT-GUARD: vendor-neutral SESSION TOKEN clause)

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
| DENY-ARCHIVE | (a) `rm`/`mv`/`rmdir` touching `_archive/`, `ANTIGRAVITY-2025.md` or `PAICodeConstitution-2026.md`; (b) **in-place overwrite of `PAICodeConstitution-2026.md`** by any shell write form (`>`/`>>`, `sed -i`, `tee`, `truncate`, `dd of=`, or `cp`/`mv`/`rsync` with it as DEST). Scope = **both live copies** — the workspace-root authoritative L0 and the published clone under `GitHub pdewost repositories/antigravity/`; matched by basename, so both are covered by one rule. The `.publish_staging/` copy is regenerated from root by `PROTOCOL_publish_regime.md` and is deliberately **unguarded** (a stated decision, not an omission) | Before tool execution | Deny (Art. 6: archives are never deleted; Constitution invariant 1: L0 is never edited mid-session without founder consent — **the founder owns every lift**). **Reads stay allowed** — the cold start reads L0 every session, so the guard matches write TARGETS only, never a file named as a reader's argument |
| DENY-CONTACT-DELETE | AppleScript `delete person`/`delete people` in any command or script | Before tool execution | Deny (the 2026-03 contact-deletion incident; Art. 4) |
| ASK-SESSION-RECORD | Reads/greps/globs of session-record stores (`_session_record/`, `governance/session_record/`) | Before tool execution | Ask (never silent-allow). Session content containing personal data is Art.-5 data wherever it travels; the archive is QUERIED, never ingested |
| SKILL-INDEX-REGEN | Edits/writes under `_skills/` | After tool execution | Regenerate the skill index so Art. 7's "discoverable through the generated index" cannot go stale between sessions |
| DENY-WINDOW | Extraction/vectorization commands during `machine_config.yaml` forbidden windows | Before tool execution | Deny with the window cited (2025 §14) |
| PUSH-AUDIT | Real `git push` executed | After tool execution | Feed the 6-check post-push audit instruction back (Phase 0, the adapter template) |
| VERIFY-GATE | Independent verification of code-affecting work: a verdict whose independence is **externally anchored** (harness-spawned subagent / stateless API / CI / human with external identity) — never the drafting agent self-grading. Advisory where un-anchored; bound to a change-set, single-use receipt | Before a code-edited turn completes (couples to CLOSEOUT-GATE) | Block once on an anchored, substantiated-HIGH refutation; **advisory (warn) where un-anchored — never bricks.** Mechanizes "Assume Independent Audit" / Art. 1 / Art. 12 via `_skills/verify_gate` |
| CONTEXT-GUARD | Context-window fill is a **budget with a cliff**: work started near the ceiling is finished by a compaction that nobody reviewed. Two thresholds over an observed fill fraction — **70% advisory** ("close out soon; do not open new scope") and **80% deny on new code edits** ("context ≥80%: close out, no new scope"). Reading a fill signal is **not optional but its availability is**: no harness hook payload carries context usage (P0.1, CONFIRMED ABSENT), so the signal comes from a harness-specific bridge writing an observed value to `governance/.session_state/` | 70%: on user-prompt submission. 80%: before `Edit`/`Write` tool execution. Compaction: flush JOURNAL only, never a full closeout | 70% → add ≤2 advisory lines. 80% → deny the edit, naming the threshold and the remedy (close out). **Degradation is the rule, not the exception** — see the stanza below |

**L1 distribution (Claude Code):** `adapters/claude-code/install_adapters.py` generates project adapters from the workspace-root `.claude/settings.json` as the single policy source; `governance/adapters/INSTALLED.json` tracks the armed-projects manifest. Hook fires are self-proving via `governance/hook_heartbeat.log` (written by `session_anchor.sh`); `verify_fires.py` reports PROVEN/PENDING per project.

**VERIFY-GATE — trust anchor + rollout (2026-06-20, advisory-first; 3 §3-review rounds).**
A single in-context agent cannot mechanically verify itself, so the trust anchor sits OUTSIDE
the producing agent: the attestation is written by the orchestrator, never the judge, and a
verdict can BLOCK only when its `anchor_kind` is external AND `attestor != producer` (a faked
self-spawn collapses to advisory). Where no external anchor exists, the gate is advisory.
Implemented by `_skills/verify_gate/` (Python+shell, harness-neutral). **Rolled out advisory-
first:** pushed to armed projects in warn-mode; a project flips to blocking only with a wired
reviewer backend + a proven change-set receipt (machine-checked, not a heartbeat). **Honest
scope:** un-bypassable only on the remote+CI path; locally = in-session adapter + a bypassable
`pre-commit`. The judge-down fail-mode is fail-closed-with-`--override`, never silent fail-open.

**CONTEXT-GUARD — thresholds, degradation, and the fail-mode bound (2026-08-26, P0.0b).**
Delivers `PLAN_lifecycle_routing_evolution_2026-08-24.md` P0.0b; its Claude adapter is P0.2,
**held until that plan clears its own §3 gate** (founder ruling 2026-08-26). Policy precedes
adapter by design invariant 2 — this stanza is normative now and binds whatever adapter lands.

**Why a bridge at all.** P0.1 verified against the installed binary that **no hook payload carries
context usage** — the field exists only in `StatusLineCommandInput` (`context_window.used_percentage`).
So the guard cannot read its own trigger; something outside it must observe and record. That is a
constraint discovered, not a design preference, and it is why every clause below is written as a
degradation.

**The thresholds.** 70% ⇒ advisory on prompt submission. 80% ⇒ deny new `Edit`/`Write`. Both read a
fill fraction OBSERVED THIS SESSION. Compaction flushes `JOURNAL.md` only — a compaction is not a
sprint boundary and must never be allowed to masquerade as a closeout.

**Fail-mode (Constitution-plan invariant 4) — deny-gates never fail open silently.** Any gate reading
`governance/.session_state/*` treats a value that is **missing, null, unparseable, or stale** as
**advisory-only**, logged **once per session**, never a silent pass and never a session-wide lock.
A value that is **defaulted or last-known rather than OBSERVED this session is treated exactly as
stale** — deny-eligibility requires a fresh observation, not a plausible one.

**The staleness bound: N = 1 turn**, and it is derived, not chosen. P0.1 measured the statusline's
write cadence: event-driven on `tokenUsage` change with a 300 ms debounce, so an interactive session
gets **at least one write per assistant message**. One turn of lag is therefore normal jitter between
the writer's render and the reader's execution; **two or more turns without a write means the writer
is not running** — a non-interactive/`-p` run, `disableAllHooks`, un-accepted workspace trust, or a
crash. In every one of those cases the correct posture is advisory, which is what N = 1 yields.

**Where turn distance cannot be established, the value is STALE by definition** — advisory, not deny.
This is deliberate and it is a burden placed on the adapter: if P0.2 wants a deny that actually
fires, P0.2 must make turn distance observable (a counter or message id stamped alongside the
fraction). A gate that cannot prove its input is fresh does not get to block. No wall-clock constant
is invented here, because none was measured.

**SESSION TOKEN — vendor neutrality (AMENDMENT 2026-08-27, founder-ruled).** CONTEXT-GUARD needs
one thing from the harness beyond the fill fraction: a way for the PRODUCER of context state and its
CONSUMER to agree they are talking about the same session. **That requirement is stated abstractly
and MUST NOT be satisfied by naming a vendor field.**

> A conforming adapter obtains a **SESSION TOKEN**: an opaque, per-session identifier readable by
> both the producer and the consumer of context state. An adapter **MAY** use a harness-provided
> identifier where one exists. Where none exists it **MUST MINT** one at session start — a value
> written once to a per-session path both sides can locate. **No mechanism above L1 may depend on a
> vendor-specific field name**, and an adapter that can neither obtain nor mint a token declares
> CONTEXT-GUARD **unavailable** rather than degrading to a global, session-blind file.

**Why this is a policy clause and not an implementation note.** The Claude Code adapter spent three
days blocked on whether its status line and its hooks shared a session key — a question that only
exists because the design reached for the vendor's field instead of the regime's requirement. The
answer turned out to be yes, which is luck, not architecture: Codex has no status line and Gemini
CLI has no such payload, so a mechanism built on that field ports to neither. **Minting needs only a
session-start hook and a writable per-session path, which every harness worth adapting has.** The
harness-provided id is therefore an OPTIMISATION, never a dependency. This is the Declaration's
independence principle acting as a design constraint rather than a preamble: had the clause existed
first, the three days would not have been spent.

**If the bridge is ABSENT entirely**, the documented fallback is a Stop-hook transcript-byte proxy,
**advisory-only and recorded as a proxy** — never promoted to a deny, because a byte count is not a
token count and the guard must not assert precision it does not have.

**Self-test duty (Art. 12):** every policy ships with a fire/no-fire test
matrix (`governance/adapters/<harness>/test_hooks.sh`); the monthly audit
re-runs it. A hook that cannot prove it fires is presumed dead.

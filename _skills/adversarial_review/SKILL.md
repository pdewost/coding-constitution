---
name: adversarial_review
version: 0.9.0
kind: procedure
status: incubating
description: One red-team harness + swappable lens-packs (plan/code/project/visual/ux) — independent refutation reviews returning evidence-backed findings and a blocking-ready verdict
interface:
  - "agent procedure: execute the numbered steps in §Procedure below"
  - "cli: python3.12 _skills/adversarial_review/scripts/assemble_review.py --pack <p> --artifact <path> --tier <skeptic|panel|workflow> --drafter <model|unknown> --reviewer <model>"
  - "cli: python3.12 _skills/adversarial_review/scripts/merge_findings.py <findings.json ...> [--emit-audit-md <path>]"
  - "cli: python3.12 _skills/adversarial_review/scripts/validate_pack.py --all | --pack <p>"
env: []
requires: []
---

# SKILL: adversarial_review (v0.9.0)

**Purpose** — The single adversarial-review mechanism for every gate that the
PAICodeConstitution-2026 regime declares: NEOCORTEX_SPEC §3 PLAN validation,
`sprint_closeout` step 3, and ad-hoc founder audits. The regime supplies WHEN
and WHY; this skill supplies HOW. It **returns findings + a verdict; it never
blocks anything itself — the CALLER blocks.**

## Invocation surface — which pack for which audit

| Audit needed | Pack | Artifact passed | Composes / delegates to |
|---|---|---|---|
| PLAN strengthening (pre-validation gate) | `plan` | the PLAN .md + its predecessor artifacts | nothing external — the NEOCORTEX §3 gate made executable (4 loss modes, omissions, unenforceable mandates, unnamed deliverables) |
| Code review (sprint closeout, diffs) | `code` | branch / diff / changed files | `/code-review` harness command **where available** (effort: skeptic→medium, panel→high); built-in refutation fallback otherwise — never silently zero findings |
| Project-wide review (migration, "complete" claims) | `project` | project root (+ NEOCORTEX/) | `_skills/github_commit_audit` (doc-code coherence, stale claims, phantom paths) + Art. 6 cold-start probes + stale-token greps |
| Visual design review | `visual` | running URL or screenshot set | `_skills/visual_audit` capture (FULL_PAGE / SELECTOR / SKETCH_TO_PROOF) or harness preview_* tools — **refuses to run text-only** |
| UX flow review | `ux` | running app + flow description | preview_* tools / visual_audit; dual-walkthrough (desktop→mobile) → walk-map → gallery → crumb-hunt; persona lenses (non-geek / copywriter / skeptic-lawyer); frontend checklist — **refuses to run text-only** |
| Typeface design review | `typeface` | proof renders of produced glyphs (proof.html / WOSTE_PROOF.html) | Persona A — Principal Typographer (`Design Projects/02_Typography/AUDIT_PERSONAS.md` §2): overshoot/optical balance, spacing rhythm, weight/contrast, metric integrity, revival fidelity, multi-script, accessibility — **refuses to run text-only** (requires_rendering) |
| Font-tooling / engineering review | `font_tooling` | source repo (.glyphs/.ufo/.designspace) + build scripts + compiled font | Persona B — Font-Tooling/Rendering engineer (`AUDIT_PERSONAS.md` §3): build pipeline, contour/winding↔fill-rule fidelity, interpolation/designspace, metrics-as-proxy, color fonts, QA-gate scoping — runs on source + binaries |
| Composed (PAIA, then each fleet project) | several packs | per-pack artifacts | run packs independently → `merge_findings.py` dedups across packs → ONE merged verdict. Tier `workflow` requires explicit user opt-in before fan-out |

## Effort tiers (Art. 4 — stakes, not difficulty)

- `skeptic` — 1 independent refuter. Default for reversible artifacts and routine
  sprint-completion claims.
- `panel` — 3 refuters with distinct lens emphases. Vote semantics: any refuter's
  evidence-backed HIGH surfaces (union); majority governs per-axis HOLDS/REFUTED;
  ties → REFUTED (default suspicious). For destructive applies, publication,
  schema migrations, constitution/governance text.
- `workflow` — Workflow-tool fan-out (multi-lens, dedup barrier, adversarial
  re-verify). **User opt-in required** — surface a one-line fan-out request and
  wait. For fleet-wide or multi-pack composition.

## Procedure

1. **Frame** — identify: artifact(s), pack(s) (table above), drafter provenance
   (from the sprint telemetry line; `unknown` if genuinely absent), stakes → tier.
   Pick a reviewer model ≠ drafter (different family where available —
   routing_policy `verifier-not-drafter`); reviewers clamp ≤ the session ceiling.
2. **Assemble** — `assemble_review.py` (interface above). It REFUSES when:
   reviewer == drafter (exit 2); pack `requires_rendering` and no
   `--render-evidence` paths given (exit 3); the pack has ≥3 consecutive
   zero-finding HOLDS and neither a changed reviewer nor a `--lens-emphasis`
   override is supplied (exit 4; a `--rotated` note alone does not count).
   Same-family reviewer → warning, recorded in provenance. Output: reviewer
   prompt(s) (1 for skeptic, 3 for panel) embedding the pack's refutation
   framing, evidence requirements, and the findings JSON schema.
3. **Spawn** — run each prompt on an INDEPENDENT subagent of the chosen reviewer
   model (Agent tool; or Workflow after opt-in). The reviewer never sees any
   answer key or the drafter's self-assessment. Collect each reviewer's findings
   JSON verbatim.
4. **Merge** — `merge_findings.py`: validates findings against the schema,
   demotes evidence-less findings to `UNSUBSTANTIATED` (excluded from verdict),
   dedups across reviewers/packs, applies panel vote semantics, updates the
   zero-finding streak state, emits merged findings + verdict.
5. **Record** — write `AUDIT_adversarial_<slug>_<YYYY-MM-DD>.md` into the
   **caller project's NEOCORTEX** (markdown; machine block = fenced JSON —
   honours the §3 no-data rule). Reference it in the caller's telemetry line:
   `... | reviewed-by <model> via adversarial_review/<pack>@<tier> | <n> findings → <verdict>`.
6. **Return** — hand findings + verdict to the caller. The caller blocks
   (plan validation, sprint ✅) on unresolved HIGH/MED findings — not this skill.
7. **Absorb loop** — the DRAFTER absorbs (fix → disposition table), never grades.
   Re-verify: the SAME independent reviewer re-runs on the fixed artifact; the
   verdict is upgraded only by that re-run, never by the drafter's claim of fix.

## Leverage map (compose, don't reinvent — Art. 7)

| Existing capability | Used by | How |
|---|---|---|
| `/code-review` (harness command) | `code` pack | sole delegate when present; pack carries fallback prompt when absent |
| `_skills/github_commit_audit` | `project` pack | executed as a procedure for repo/doc-coherence evidence |
| `_skills/visual_audit` | `visual`, `ux` packs | `scripts/capture.py` screenshots = `render_capture` evidence |
| preview_* tools (harness) | `ux`, `visual` packs | live walkthrough evidence when the harness offers a browser |
| Agent tool / Workflow tool | harness steps 3 | independent reviewer spawning; workflow tier (opt-in) |
| `_skills/sprint_closeout` | CALLER | step 3 invokes this skill; its telemetry line feeds `--drafter` |

## Common Gotchas

- **Drafter unknown ≠ drafter omitted**: `--drafter` is always required;
  pass the literal `unknown` (downgrades refusal → family-warning, recorded).
- `ux`/`visual` need a RUNNING rendering source; assemble refuses text-only runs
  by design — do not work around it with prose descriptions of the UI.
- The reviewer's output is a **claim** (Art. 8): the invoking agent re-verifies
  the most build-changing HIGHs before absorbing (read the cited file:line, run
  the cited command).
- Zero findings across 3 runs is a signal about the REVIEWER, not the artifacts:
  the streak refusal (exit 4) is deliberate — rotate, don't override.
- State in `state/zero_streaks.json` is local and untracked; never commit it.

## Common Rationalizations

| Rationalization | Why it's wrong |
|---|---|
| "The artifact is small, a self-check is enough" | The drafter is structurally blind to its own omissions — proven on this skill's own PLAN (F-02: a false mechanism claim the drafter could not see; an independent reviewer caught it by reading the code). |
| "The reviewer found nothing, ship it" | A zero-finding verdict carries weight only with the probes it ran shown (HOLDS requires ≥1 evidence-backed probe per axis). |
| "I fixed all findings, so the verdict is now HOLDS" | Only the re-verify run (step 7, same reviewer) upgrades the verdict. |
| "No /code-review here, skip the code pack" | The pack carries a fallback refutation prompt precisely for that case. |

## CHANGELOG

- **0.9.0** (2026-06-10) — Initial build per `PLAN_adversarial_review_skill_2026-06-10.md`
  (S0–S5). Status `incubating` until the S6 calibration gate (C0–C4) passes and the
  first production consumer invokes it; then 1.0.0 / `active`.

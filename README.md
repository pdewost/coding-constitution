# Coding Constitution — an Enforcement Regime for AI Coding Agents

> Governance for agentic coding that you **run**, not just write down.
> A ratified constitution, a harness-neutral enforcement layer, and the
> adversarial-review harness used to harden them — extracted clean-room from a
> real multi-project workspace.

![License](https://img.shields.io/badge/license-MIT%20%2B%20CC--BY--4.0-blue)
![Constitution](https://img.shields.io/badge/constitution-v1.0%20ratified-success)
![Harness](https://img.shields.io/badge/adapter-Claude%20Code-informational)
![Status](https://img.shields.io/badge/enforcement-self--tested-success)

---

## The problem

AI coding agents are capable and confident — and that's the hazard. A `CLAUDE.md`
full of good intentions changes nothing the moment the model is mid-task and
under pressure. Rules that live only in prose get skimmed, then ignored.

**The thesis: a rule you can't enforce is a rule you don't have.**
Every governing decision is pushed down to the cheapest medium that can *make it
true* — a hook, a permission, a test, a skill — and only what genuinely can't be
mechanized is left as prose.

> **The Sorting Rule.** A rule earns a place in the L0 Constitution *only* if it
> cannot be expressed as a hook, a permission, a test, or a skill. Prose is the
> medium of last resort.

---

## What it actually enforces

These nine policies are defined once in `spec/POLICY_CORE.md` (harness-neutral)
and translated by a thin per-harness adapter. The Claude Code adapter ships
complete and self-tested:

| Policy | Fires on | Effect |
|---|---|---|
| **ANCHOR** | session start / resume / compaction | injects cold-start context: Constitution pointer, the project's state manifest + status, a bounded skill index |
| **GUARDRAIL** | every user prompt | a ≤4-line, task-aware reminder: planning → budgets + "PLAN before code" + *propose an adversarial review*; destructive → dry-run/apply gates; else → one rotated principle (rotation defeats habituation) |
| **COMPILE-GATE** | after each file edit | syntax-checks the edited file (`py_compile` / `bash -n` / `osacompile`) and **blocks** with the error fed straight back |
| **CLOSEOUT-GATE** | agent tries to end the turn | **blocks once** if code was edited but no verification ran, or the journal blew its size bound |
| **VERIFY-GATE** | a code-edited turn completes | checks for an **independently-anchored** verdict — a reviewer that could not see the author's intent (subagent / stateless API / CI / human); **advisory** where un-anchored, blocking once a project is armed; a change-set-bound, single-use receipt (`_skills/verify_gate`) |
| **DENY-ARCHIVE** | before tool run | denies `rm`/`mv`/`find -delete`/`xargs` etc. that would delete or move an archive *out* |
| **DENY-CONTACT-DELETE** | before tool run | denies irreversible `delete person` AppleScript (born from a real data-loss incident) |
| **DENY-WINDOW** | before tool run | denies heavy extraction/vectorization during machine-specific forbidden windows |
| **PUSH-AUDIT** | after `git push` | feeds back a 6-check repo-hygiene audit |

A denial isn't advice — it's a decision the harness obeys. For example, when the
agent tries `osascript -e 'delete person 1'`, the hook returns:

```json
{ "hookSpecificOutput": { "hookEventName": "PreToolUse",
  "permissionDecision": "deny",
  "permissionDecisionReason": "DENY-CONTACT-DELETE (Art. 4 …): forbidden …" } }
```

> **Honest scope.** The deny-hooks are **defence-in-depth, not a sandbox.** They
> match command text and stop the common and accidental cases; a determined shell
> (variables, `eval`, `mv -t`, base64) can still evade them. The real protection
> for irreversible actions is that *the human performs them manually.* This is
> stated in the code, not hidden.

---

## The five layers

| Layer | Holds | Medium |
|---|---|---|
| **L0 — Constitution** | `PAICodeConstitution-2026.md`: principles only | prose, ≤190 lines |
| **L1 — Enforcement** | hooks, permissions, tests, CI | machine-executed; per-harness adapters in `adapters/` |
| **L2 — Procedures** | the `_skills/` registry | skills, loaded on demand |
| **L3 — Project state** | a `NEOCORTEX/` per project | per `spec/NEOCORTEX_SPEC.md` |
| **L4 — Reference data** | routing policy, machine facts, specs | data files + specs |

L3 (**NEOCORTEX**) keeps every project *cold-startable*: a bounded `MANIFEST.json`
+ `STATUS.md` + `JOURNAL.md` that a fresh agent session reads to know where it is,
with size bounds the validator enforces — so context never silently rots.

---

## See it work (60 seconds)

```bash
git clone https://github.com/pdewost/coding-constitution
cd coding-constitution

# Run the enforcement self-test — 23 fire/no-fire cases over the real hooks:
bash adapters/claude-code/test_hooks.sh

# Read the centerpiece (14 articles + 1 amendment, ≤190 lines):
less PAICodeConstitution-2026.md

# ...and the Declaration of AIndependence — the why: vendor / model / coder independence:
less DECLARATION.md
```

A hook that can't prove it fires is presumed dead (Art. 12) — so every policy
ships with that matrix, and `verify_fires.py` reports which projects are actually
armed.

---

## Adopt it

```bash
# 1. Install the enforcement hooks into your workspace
mkdir -p .claude/hooks && cp adapters/claude-code/hooks/* .claude/hooks/

# 2. Fill in the two config templates (no machine-specific values ship in this repo)
cp routing_policy.example.yaml   governance/routing_policy.yaml
cp machine_config.example.yaml   governance/machine_config.yaml   # set your DENY-WINDOW bounds etc.

# 3. Arm every migrated project (dry-run first; enforcement follows migration)
python3 adapters/claude-code/install_adapters.py            # preview
python3 adapters/claude-code/install_adapters.py --apply

# 4. Give a project its L3 state, then validate it
python3 adapters/claude-code/neocortex_manifest.py --regenerate /path/to/project
python3 adapters/claude-code/neocortex_manifest.py --check       /path/to/project
```

Full procedure: `adapters/claude-code/README.md` (policy → mechanism map) and
`spec/NEOCORTEX_SPEC.md §5` (migration).

---

## Adversarial review is built in — and was used on this repo

The regime doesn't trust its own authors. `_skills/adversarial_review/` is a
generic red-team harness with swappable lens-packs (plan / code / project /
visual / ux): a **drafter** model proposes, a **different reviewer** model tries
to refute, findings are merged and the caller blocks on the verdict.

```bash
python3.12 _skills/adversarial_review/scripts/assemble_review.py \
  --pack plan --artifact NEOCORTEX/PLAN_feature_2026-07-01.md \
  --tier skeptic --drafter <model-a> --reviewer <model-b>
```

This was not theoretical for this release:

- The three core documents were adversarially reviewed before ratification —
  the round caught **10+ HIGH findings**, several invisible to the drafter.
- The L1 installer and hooks were red-teamed and **structurally hardened against
  the symlink / intermediate-component / TOCTOU / FIFO-hang / hardlink-clobber
  classes** before this repo was prepared. The file I/O walks each path
  component with `O_NOFOLLOW` from a trusted anchor and refuses non-regular /
  hardlinked targets — verified with reproducing exploit harnesses.

---

## Harness support

The policy is harness-neutral; an adapter is a *dumb translator* into a harness's
native mechanism. Adding a harness means writing an adapter, never rewriting a policy.

- **Claude Code** — ships complete: hooks, installer, validator, fire-verifier, self-test.
- **Google Antigravity** — `adapters/antigravity/AGENTS.md.template` entry-point template.
- **Anything else** — implement `spec/POLICY_CORE.md`'s nine policies in your harness; PRs welcome.

---

## Repository layout

```
PAICodeConstitution-2026.md     # L0 — the centerpiece; read this first
ANTIGRAVITY.md                  # 2025 predecessor — frozen archive, kept for lineage
spec/
  POLICY_CORE.md                # L1 — the eight enforcement policies (harness-neutral)
  NEOCORTEX_SPEC.md             # L3 — the cold-startable project-state model
adapters/
  claude-code/                  # L1 adapter: hooks/ + installer + validator + self-test
  antigravity/                  # AGENTS.md template for the Google Antigravity harness
routing_policy.example.yaml     # L4 — task-class structure + hard rules (bind your models)
machine_config.example.yaml     # L4 — machine-specific facts template (fill in your values)
_skills/
  UNIVERSAL_SKILL_SPEC.md       # L2 — skill lifecycle + cross-project contracts
  adversarial_review/           # L2 — the red-team harness (lenses + scripts)
LICENSE                         # MIT (code) + CC BY 4.0 (docs)
```

---

## The Constitution at a glance

14 articles + one amendment, ≤190 lines of prose:

| | | | |
|---|---|---|---|
| 1 Evidence Before Done | 2 Surgical Integrity | 3 Honest Disambiguation | 4 Escalation by Irreversibility |
| 5 Code / Data / State Separation | 6 Continuity Duty | 7 Skill Mandate | 8 Delegation |
| 9 Routing (task classes, not models) | 10 Honest Reporting | 11 Amendment Doctrine | 12 Audit Reality |
| 13 Prepare Before Acting | 14 Diagnostic Discipline | **Amendment I — Inviolability of Serving Artifacts** | |

---

## Status & maturity

- **Constitution / NEOCORTEX_SPEC / UNIVERSAL_SKILL_SPEC** — v1.0, ratified 2026-06-10 after line-by-line + adversarial review.
- **Claude Code adapter** — in daily use; self-tested; hooks adversarially hardened.
- **Config files are *examples*** — they ship with placeholders and zero machine-specific values; you supply yours.
- This is a working extraction of a personal regime, shared as a reference design. Expect to adapt it, not drop it in untouched.

---

## Lineage

`PAICodeConstitution-2026.md` supersedes the 2025 `ANTIGRAVITY.md`, which is kept
frozen here to show what the operational lessons were distilled *from*.

---

## Contributing

Issues and PRs welcome — especially **new harness adapters** and **adversarial
review lens-packs**. Changes to L0 prose follow the Constitution's own Amendment
Doctrine (Art. 11): a dedicated review, and the 190-line budget must hold.

## License

- **Code & adapters** (`adapters/`, `_skills/adversarial_review/scripts/`): **MIT**.
- **Documentation** (Constitution, `ANTIGRAVITY.md`, `spec/`, skill docs & lenses): **CC BY 4.0**.

See `LICENSE`.

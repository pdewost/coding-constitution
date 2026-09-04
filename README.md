# Coding Constitution — an Enforcement Regime for AI Coding Agents

> Governance for agentic coding that you **run**, not just write down.
> A ratified constitution, a harness-neutral enforcement layer, and the
> adversarial-review harness used to harden them — extracted clean-room from a
> real multi-project workspace.

![License](https://img.shields.io/badge/license-MIT%20%2B%20CC--BY--4.0-blue)
![Constitution](https://img.shields.io/badge/constitution-v1.0%20ratified-success)
![Harness](https://img.shields.io/badge/adapter-Claude%20Code-informational)
![Status](https://img.shields.io/badge/enforcement-self--tested-success)
![Reviewed](https://img.shields.io/badge/reviewed-2026--08--25%20%C2%B7%200%20amendments-success)

---

## Reviews

The constitution carries its own amendment doctrine (Art. 11): a **frontier-model generation
change** convenes a review, which asks of every article only *"does this still bind correctly
under current conditions?"*

| Date | Scope | Outcome |
|---|---|---|
| [2026-08-25](reviews/REVIEW_2026-08-25.md) | All 14 articles + Preamble + Amendment I | **BIND-AS-IS. Zero amendments.** 188/190 lines, unchanged |

The 2026-08-25 record is worth reading for the failure modes more than the verdict: an article
that went missing from a ratification, a rule that bound a budget written to ignore it, tests that
passed whether or not the fix was present, and a status indicator that showed green *because*
things were bad. Every gap the review found was one layer below the constitution — which is the
Sorting Rule working, not a gap in it.

**Extraction integrity.** This repository is a clean-room extraction, not a mirror.
[`redaction_map.yaml`](redaction_map.yaml) declares every intentional difference from the private
source, and [`publish_scan.py`](publish_scan.py) enforces the pre-publish secrets gate as code —
after a measurement found 6 of 8 published files had silently drifted from their originals.

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
| **COMPILE-GATE** | after each file edit | syntax-checks the edited file (`py_compile` / `bash -n` / `osacompile`) and **blocks** with the error fed straight back — Python / Bash / AppleScript only, no JS/TS syntax check today (see *TypeScript & JavaScript*, below) |
| **CLOSEOUT-GATE** | agent tries to end the turn | **blocks once** if code was edited but no verification ran, or the journal blew its size bound — sees an edit made through the harness's own file-editing tools OR a Bash command that writes/overwrites a tracked file (heredoc, `sed -i`, `tee`, `cp`/`mv`/`rsync`, `dd of=`); a write form neither of those catches is invisible to it |
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

## TypeScript & JavaScript

The Claude Code adapter's language coverage is **uneven by design, not by omission** — read
this before assuming either "fully supported" or "Python-only":

| Policy | `.ts` / `.js` coverage |
|---|---|
| **CLOSEOUT-GATE** | Full. `.ts`/`.js` are recognized code files: an edit to one — through the harness's own editing tools, or a Bash write (heredoc/`sed -i`/`tee`/`cp`/`mv`/`rsync`/`dd of=`) — starts the same "was this verified?" window a `.py` edit does, and `npm test` is a recognized verification command that clears it, same as `pytest`. |
| **COMPILE-GATE** | None. It syntax-checks `.py` / `.sh` / `.applescript` only; a broken `.ts`/`.js` file is not caught at edit time. Per-file TS/JS syntax checking (`tsc --noEmit`, or your own linter) is not wired in — add it yourself if you want an equivalent to COMPILE-GATE's immediate feedback. |
| **DENY-\*** | Language-agnostic — these match Bash command text, not file type, so they apply the same regardless of what you're editing. |

None of this is a stack requirement: the kit was built and hardened against a Python-and-Bash
project, and a TypeScript monorepo gets CLOSEOUT-GATE's full duty-tracking with zero
configuration, but not COMPILE-GATE's per-edit syntax gate. If that gap matters to you, it's a
small, self-contained addition to `postedit_compile_gate.py` — the same `elif fp.endswith(...)`
pattern the existing three branches already use.

---

## Governing more than one project (a "fleet")

The adopt sequence above scales from **one project to many without changing shape** — there
is no separate "fleet mode" to opt into, and no requirement that your workspace root itself be
a project:

- **A single project.** Run `bootstrap.sh` pointed at that project's own root. Its hooks are
  live directly there; you're done. `install_adapters.py`'s discovery step deliberately
  excludes the root it's run against (it looks for *children* to arm), so for a lone project
  it has nothing to do — skip straight to `verify_fires.py` to confirm the hooks actually fire.
- **Several projects sharing one policy.** Run `bootstrap.sh` once at a root that *contains*
  your project directories as children (the root need not be a project itself — this repo's
  own origin workspace isn't one). `install_adapters.py --root <that-root> --apply` then finds
  every child directory carrying its own state dir (`NEOCORTEX/`, or the pre-`NEOCORTEX` `BRAIN/`
  layout) and arms each with a generated `settings.json` pointing at the **one** canonical hook
  copy `bootstrap.sh` installed — so fixing or extending a hook once (this repo's own CLOSEOUT-
  GATE widening above was tested exactly this way) updates every armed project the next time
  each reads its settings, with nothing to keep in sync by hand.
- **Checking the whole set.** `install_adapters.py --root <root> --check` reports drift (an
  armed project whose generated settings no longer match the canonical source) and `--apply` is
  idempotent, so re-running it after a hook change is the normal way to propagate it. `verify_fires.py
  --root <root>` then reports PROVEN/PENDING per project — this is where the origin workspace's own
  76-day gap (above) was ultimately visible, once the missing project was actually in scope.

The dividing line is simply: does your root need a project-discovery step, or is the root
itself the only project? Nothing else about the hooks, permissions, or policies changes.

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

# The hooks run from a workspace, so seed a throwaway one first (this is the step whose
# absence made this quickstart fail on a fresh clone until 2026-08-25):
./adapters/claude-code/bootstrap.sh /tmp/cc-demo

# Run the enforcement self-test — 23 fire/no-fire cases over the real hooks:
bash adapters/claude-code/test_hooks.sh --root /tmp/cc-demo

# Read the centerpiece (14 articles + 1 amendment, ≤190 lines):
less PAICodeConstitution-2026.md

# ...and the Declaration of AIndependence — the why: vendor / model / coder independence:
less DECLARATION.md
```

A hook that can't prove it fires is presumed dead (Art. 12) — so every policy
ships with that matrix, and `adapters/claude-code/verify_fires.py` reports which projects are actually
armed.

---

## Adopt it

```bash
# 1. Install the canonical hook set + settings into YOUR workspace root. NOTE: the tools do
#    NOT remember this path — pass --root (or export GOVERNANCE_WORKSPACE) to every command
#    below. Without it they search upward from their own location and may act on another tree.
#    (The kit deliberately keeps ONE canonical copy; every armed project points at it
#     by absolute path, so no project's hooks can drift from the source.)
./adapters/claude-code/bootstrap.sh /path/to/your/workspace

# 2. Prove the hooks fire AND stay silent — 23 fire/no-fire cases
bash adapters/claude-code/test_hooks.sh

# 3. Fill in the two config templates, in YOUR workspace (no machine-specific values ship here).
#    `governance/` is a directory in your workspace, not in this repo — create it:
mkdir -p /path/to/your/workspace/governance
cp routing_policy.example.yaml /path/to/your/workspace/governance/routing_policy.yaml
cp machine_config.example.yaml /path/to/your/workspace/governance/machine_config.yaml

# 4. Arm every migrated project — dry-run first; enforcement follows migration
python3.12 adapters/claude-code/install_adapters.py --root /path/to/your/workspace          # preview
python3.12 adapters/claude-code/install_adapters.py --root /path/to/your/workspace --apply
python3.12 adapters/claude-code/install_adapters.py --root /path/to/your/workspace --check  # drift check

# 5. Give a project its L3 state, then validate it.
#    --init originates a NEOCORTEX/ for a project that has none (the usual case);
#    --regenerate only rebuilds files[] inside one that already exists.
python3.12 adapters/claude-code/neocortex_manifest.py --init       /path/to/project
python3.12 adapters/claude-code/neocortex_manifest.py --check      /path/to/project

# 6. THE STEP PEOPLE SKIP: has a hook actually fired here, or is it merely installed?
python3.12 adapters/claude-code/verify_fires.py --root /path/to/your/workspace
```

**Step 6 is not a formality.** A project can sit armed for months without a hook ever
running, and "installed" reads exactly like "working" until you look. In the workspace this
was extracted from, the *governance* directory itself was excluded from adapter discovery by
a one-line filter — so the enforcement layer had never once run on the project that defines
it, for 76 days. The gap was visible the moment someone ran step 4's `--check` and read the
project list: `verify_fires.py` couldn't have shown it (it only reports on projects already
present in the discovery list `--apply` wrote — a project the list never named is invisible to
it, not merely PENDING). What `verify_fires.py` gave, once the missing entry was found and
fixed, was the follow-up number worth knowing: **PROVEN** (a hook demonstrably fired from this
path) vs. **PENDING** (armed, never fired) — in this case, 0 of 146 Stop-hook firings, because
it had never been armed at all. Until a project says PROVEN, its enforcement is a plan, not a
control — and until it's in step 4's own discovery list, `verify_fires.py` can't even tell you
that much.

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
  POLICY_CORE.md                # L1 — the nine enforcement policies (harness-neutral)
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

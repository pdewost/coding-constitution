# Coding Constitution — an Enforcement Regime for AI Coding Agents

> Governance for agentic coding that you **run**, not just write down.
> A ratified constitution, a harness-neutral enforcement layer, and the
> adversarial-review harness used to harden them — extracted clean-room from a
> real multi-project workspace.

![License](https://img.shields.io/badge/license-MIT%20%2B%20CC--BY--4.0-blue)
![Constitution](https://img.shields.io/badge/constitution-v1.0%20ratified-success)
![Harness](https://img.shields.io/badge/adapter-Claude%20Code-informational)
![Status](https://img.shields.io/badge/enforcement-self--tested-success)
![Reviewed](https://img.shields.io/badge/reviewed%20%26%20amended-2026--10--27-success)

---

## Reviews

The constitution carries its own amendment doctrine (Art. 11): a **frontier-model generation
change** convenes a review, which asks of every article only *"does this still bind correctly
under current conditions?"*

| Date | Scope | Outcome |
|---|---|---|
| [2026-08-25](reviews/REVIEW_2026-08-25.md) | All 14 articles + Preamble + Amendment I | **BIND-AS-IS. Zero amendments.** 188/190 lines, unchanged |
| [2026-10-27](reviews/REVIEW_2026-10-27_constitution_amendment.md) | All 18 clauses, one ruling each | **Four changes ratified** (Art. 6 consented deletion, Sorting Rule wording, dated asides removed, vendor-neutral "instruction file"); two external reviews by a model of another lineage before the author applied them by hand. 190/190 lines |

The 2026-08-25 record is worth reading for the failure modes more than the verdict: an article
that went missing from a ratification, a rule that bound a budget written to ignore it, tests that
passed whether or not the fix was present, and a status indicator that showed green *because*
things were bad. Every gap the review found was one layer below the constitution — which is the
Sorting Rule working, not a gap in it.

**Enforcement-layer reviews.** The hooks that enforce the constitution are reviewed on their own, by
models from other labs than the author's, with every finding reproduced as a failing test before its fix.

| Date | Scope | Outcome |
|---|---|---|
| [2026-10-07](reviews/REVIEW_2026-10-07_hooks_batch2.md) | Claude Code adapter, hooks batch 2 (v0.7 to v0.12) | 7 external rounds; every finding reproduced as a failing test before its fix |

**Extraction integrity.** This repository is a clean-room extraction, not a mirror.
[`redaction_map.yaml`](redaction_map.yaml) declares every intentional difference from the private
source, and [`publish_scan.py`](publish_scan.py) enforces the pre-publish secrets gate as code —
after a measurement found 6 of 8 published files had silently drifted from their originals. The gate reads your
private terms from an untracked `.publish_scan_terms`; without that file it refuses to pass, unless
`PUBLISH_SCAN_NO_PRIVATE_TERMS=1` says you have none.

---

## The problem

AI coding agents are capable and confident — and that's the hazard. A `CLAUDE.md`
full of good intentions changes nothing the moment the model is mid-task and
under pressure. Rules that live only in prose get skimmed, then ignored.

**The thesis: a rule you can't enforce is a rule you don't have.**
Every governing decision is pushed down to the cheapest medium that can *make it
true* — a hook, a permission, a test, a skill — and only what genuinely can't be
mechanized is left as prose.

> **The Sorting Rule** (as amended 2026-10-27). L0 holds durable obligations, with
> the bounds they state, and authority boundaries; their procedures, checks and
> tunable criteria belong in lower layers. Prose is the medium of last resort.

---

## What it actually enforces

The policies are defined once in `spec/POLICY_CORE.md` (harness-neutral) and translated by a thin
per-harness adapter. That file is the count, not this README: at v0.13 it has 13 rows, of which one
is retired (ASK-SESSION-RECORD, whose hook still ships and still asks before any read of a
`governance/session_record/` folder the kit no longer creates) and one has no hook in this kit
(CONTEXT-GUARD, below). The Claude Code adapter ships the rest, self-tested:

| Policy | Fires on | Effect |
|---|---|---|
| **ANCHOR** | session start / resume / compaction | injects cold-start context: Constitution pointer, the project's state manifest + status, a bounded skill index |
| **GUARDRAIL** | every user prompt | a ≤4-line, task-aware reminder: planning → budgets + "PLAN before code" + *propose an adversarial review*; destructive → dry-run/apply gates; else → one rotated principle (rotation defeats habituation) |
| **HANDOFF** | a prompt of exactly `cold start` | resumes a line of work from its predecessor's handoff file (`NEOCORTEX/HANDOFF_<role>_<date>_<time>Z.md`) with nothing pasted: one open role → its cold-start section; several → the list to choose from; none → why; never a guess (`handoff_resolve.py`) |
| **COMPILE-GATE** | after each file edit | syntax-checks the edited file (`py_compile` / `bash -n` / `zsh -n` / `node --check` for `.js`, `.mjs`, `.cjs` when Node is installed / `osacompile`) and **blocks** with the error fed straight back; no TypeScript check (see *TypeScript & JavaScript*, below) |
| **CLOSEOUT-GATE** | agent tries to end the turn | **blocks once** if code was edited but no verification ran, or the journal blew its size bound — sees an edit made through the harness's own file-editing tools OR a Bash command that writes/overwrites a tracked file (heredoc, `sed -i`, `tee`, `cp`/`mv`/`rsync`, `dd of=`); a write form neither of those catches is invisible to it; a verification counts only if it is a recognised test run (`verifier_recognizer.py`: piped, backgrounded, echoed or `|| true` runs do not count) |
| **VERIFY-GATE** | a code-edited turn completes | checks for an **independently-anchored** verdict — a reviewer that could not see the author's intent (subagent / stateless API / CI / human); **advisory** by default, blocking once a project opts in with a `.verify/armed` file; a change-set-bound, single-use receipt (`_skills/verify_gate`, which `bootstrap.sh` places in your workspace) |
| **DENY-ARCHIVE** | before tool run | denies `rm`/`mv`/`find -delete`/`xargs` etc. that would delete or move an archive *out*; paths resolved physically (symlinks, `..`, `cd`, `git -C`) |
| **DENY-CONTACT-DELETE** | before tool run | denies irreversible `delete person` AppleScript (born from a real data-loss incident) |
| **DENY-WINDOW** | before tool run | denies heavy extraction/vectorization during machine-specific forbidden windows |
| **PUSH-AUDIT** | after `git push` | prints a 6-check repo-hygiene checklist to run (the scripted audit it also names is not shipped) |
| **SKILL-INDEX-REGEN** | after an edit under `_skills/` | regenerates the skill index; if the regeneration fails, marks the index **STALE** at its top instead of leaving it silently out of date |

**CONTEXT-GUARD** (advisory at 70% of the context window, deny new edits at 80%) is defined in the spec
but has **no hook in this kit**: no hook payload carries context usage, so it needs a harness-specific
bridge that writes the observed fill to disk. The author's workspace has one for a single desktop
harness; it is not shipped.

Two more ship in the Claude Code adapter only (added 2026-10; not yet in `spec/POLICY_CORE.md`):

| Policy | Fires on | Effect |
|---|---|---|
| **LOOP-BOUND** | before a test or verification run | after 3 fix-and-retry cycles on the same failure, or 6 failing runs in a turn with no pass, **asks** before the next run (Art. 14 made mechanical); a question, not a refusal |
| **APPROVED LIST** | session start, and every stop | helper hooks run only as the bytes listed in `governance/hooks_approved.sha256`; any other change to the hooks, the settings, `governance/routing_policy.yaml` or `governance/machine_config.yaml` is reported as tampering until re-approved |

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
>
> **Two known limits in this release** (the kit is wired exactly as its author's own workspace): the DENY guard is
> registered for `Bash` and the osascript tool only, so the file-editing tools (Edit, Write, MultiEdit, NotebookEdit) never
> reach it; the constitution and `ANTIGRAVITY-2025.md` are protected from them by the settings' static `deny` rules instead.
> And a guard that cannot even start (no usable Python) is, like any failing hook, non-blocking. A fail-closed launcher and
> the editing-tool routing exist in the author's tree and are being hardened; they are not part of this release.
>
> **No git hook, no CI.** Where the specs mention a `pre-commit` gate, that is the author's own git hook; this kit ships
> none. A check becomes un-bypassable only where you run it yourself at a shared remote (a server-side hook or CI).

---

## Upgrading an earlier install

`bootstrap.sh` refuses to run over an existing `.claude/settings.json` (it never overwrites your
settings), so an existing install is upgraded by hand, from a fresh clone of this repository:

    W=/path/to/your/workspace
    cp adapters/claude-code/hooks/*.py adapters/claude-code/hooks/*.sh "$W/.claude/hooks/"
    chmod +x "$W"/.claude/hooks/*.sh; for f in "$W"/.claude/hooks/*.py; do case "${f##*/}" in test_*) ;; *) chmod +x "$f";; esac; done
    mkdir -p "$W/governance/adapters/claude-code" "$W/governance/scripts" "$W/_skills"
    cp adapters/claude-code/handoff_resolve.py adapters/claude-code/anchor_render.py "$W/governance/adapters/claude-code/"
    cp governance/scripts/lint_dispatch_class.py governance/scripts/continuity_sweep.py governance/scripts/closeout_lint.py \
       "$W/governance/scripts/"
    cp _skills/build_index.py "$W/_skills/"
    cp -R _skills/verify_gate "$W/_skills"
    cp PAICodeConstitution-2026.md "$W/"     # the constitution the session anchor points to
    # now merge any new hook entry of adapters/claude-code/settings.canonical.json into "$W/.claude/settings.json" by hand
    python3.12 adapters/claude-code/install_adapters.py --root "$W" --apply     # re-arm child projects
    (cd "$W" && python3.12 .claude/hooks/hook_support.py --write-approved)     # LAST: approves what you just installed

Why each step matters:

1. **Three hooks never ran** before 2026-10-09: the kit shipped `verify_advisory.py`, `session_record_gate.py`
   and `skill_index_regen.py` without the executable bit. Every non-test hook must be executable.
2. **v0.13: what the hooks load from your workspace.** `prompt_guardrail.sh` runs `handoff_resolve.py` on a
   `cold start` prompt and `session_anchor.sh` runs `anchor_render.py`, both from `governance/adapters/claude-code/`;
   VERIFY-GATE imports `_skills/verify_gate/`; COMPILE-GATE runs `governance/scripts/lint_dispatch_class.py`.
   Without them a cold start prints "RESOLVER FAILED", the anchor falls back to its unrendered form, no
   verification receipt can validate, and the task-class lint stays disabled. The closeout gate and
   `skill_index_regen.py` run `_skills/build_index.py`, `governance/scripts/continuity_sweep.py` and
   `closeout_lint.py`; `bootstrap.sh` places these on a fresh install but never replaces an existing copy, so an
   upgrade copies them here, or the hooks keep running the old ones.
3. **The approved list, last.** After ANY deliberate change to `.claude/hooks/`, `.claude/settings*.json`,
   `governance/routing_policy.yaml` or `governance/machine_config.yaml`, re-approve. Approve after the merge, not
   before: a change made after the approval is reported as tampering, and the stop gate then cannot recognise your
   test runs (a turn that edited code is blocked once at its end, "no verification ran", until you re-approve).

---

## TypeScript & JavaScript

The Claude Code adapter's language coverage is **uneven by design, not by omission** — read
this before assuming either "fully supported" or "Python-only":

| Policy | `.ts` / `.js` coverage |
|---|---|
| **CLOSEOUT-GATE** | Full. `.ts`/`.js` are recognized code files: an edit to one — through the harness's own editing tools, or a Bash write (heredoc/`sed -i`/`tee`/`cp`/`mv`/`rsync`/`dd of=`) — starts the same "was this verified?" window a `.py` edit does, and `npm test` is a recognized verification command that clears it, same as `pytest`. |
| **COMPILE-GATE** | Partial. `.js` / `.mjs` / `.cjs`: `node --check` at edit time when Node is on the PATH (otherwise the check is skipped and the hook says so). `.ts`: none; a broken `.ts` file is not caught at edit time. TypeScript checking (`tsc --noEmit`, or your own linter) is not wired in — add it yourself if you want an equivalent to COMPILE-GATE's immediate feedback. |
| **DENY-\*** | Language-agnostic — these match Bash command text, not file type, so they apply the same regardless of what you're editing. |

None of this is a stack requirement: the kit was built and hardened against a Python-and-Bash
project, and a TypeScript monorepo gets CLOSEOUT-GATE's full duty-tracking with zero
configuration, plus COMPILE-GATE's `node --check` on plain JavaScript, but no per-edit TypeScript
check. If that gap matters to you, it's a small, self-contained addition to
`postedit_compile_gate.py` — the same `elif ...endswith(...)` pattern its existing branches use.

---

## Governing more than one project (a "fleet")

The adopt sequence below scales from **one project to many without changing shape** — there
is no separate "fleet mode" to opt into, and no requirement that your workspace root itself be
a project:

- **A single project.** Run `bootstrap.sh` pointed at that project's own root; its hooks are
  live directly there. Then still run `install_adapters.py --root <project> --apply`: discovery
  includes the root itself when it holds `NEOCORTEX/`, and `verify_fires.py` needs the
  `INSTALLED.json` that `--apply` writes. One known limit: `cold start` at a lone project's root
  does not yet find that project's handoff (the resolver looks for a `governance/` project there).
- **Several projects sharing one policy.** Run `bootstrap.sh` once at a root that *contains*
  your project directories as children (the root need not be a project itself — this repo's
  own origin workspace isn't one). `install_adapters.py --root <that-root> --apply` then finds
  every child directory carrying its own state dir (`NEOCORTEX/`; the older `BRAIN/` layout is
  not discovered) and arms each with a generated `settings.json` pointing at the **one** canonical hook
  copy `bootstrap.sh` installed (the root's own settings, from `settings.canonical.json`, reach it
  through `$CLAUDE_PROJECT_DIR`; each armed child's generated settings name it by absolute path)
  — so fixing or extending a hook once (this repo's own CLOSEOUT-
  GATE widening was tested exactly this way) updates every armed project the next time
  each reads its settings, with nothing to keep in sync by hand.
- **Checking the whole set.** `install_adapters.py --root <root> --check` reports drift (an
  armed project whose generated settings no longer match the canonical source) and `--apply` is
  idempotent, so re-running it after a hook change is the normal way to propagate it. `verify_fires.py
  --root <root>` then reports PROVEN/PENDING per project — this is where the origin workspace's own
  76-day gap (below) was ultimately visible, once the missing project was actually in scope.

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

**Prerequisite: Python 3.12** on the PATH as `python3.12`: the commands below name it, and COMPILE-GATE looks for
`python3.12` by name (without it the `.py` syntax check is disabled and one self-test case fails). The install tools
alone run on 3.10 or later. The adversarial-review scripts also need PyYAML (`pip install pyyaml`).

```bash
git clone https://github.com/pdewost/coding-constitution
cd coding-constitution

# The hooks run from a workspace, so seed a throwaway one first (this is the step whose
# absence made this quickstart fail on a fresh clone until 2026-08-25):
./adapters/claude-code/bootstrap.sh /tmp/cc-demo

# The guard reads its forbidden time windows from governance/machine_config.yaml and ASKS when it
# cannot read them (safe by default), so give the demo the example config (its placeholder window
# means "no window"; the self-test reads the same file). Then re-approve: a new machine_config.yaml
# is reported as tampering until it is approved.
cp machine_config.example.yaml /tmp/cc-demo/governance/machine_config.yaml
(cd /tmp/cc-demo && python3.12 .claude/hooks/hook_support.py --write-approved)

# Run the enforcement self-test — 37 fire/no-fire cases over the real hooks:
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

**Prerequisite: Python 3.12** (see *See it work*). `bootstrap.sh` itself accepts 3.10 or later for the install tools, refuses anything older before it installs anything, honours `PYTHON=/path/to/python3`, and prints every next command with the interpreter it checked; the hooks also run under macOS's bundled 3.9, but COMPILE-GATE's `.py` check needs `python3.12` by name.

```bash
# 1. Install the canonical hook set + settings into YOUR workspace root. NOTE: the tools do
#    NOT remember this path — pass --root (or export GOVERNANCE_WORKSPACE) to every command
#    below. Without it they search upward from their own location and may act on another tree.
#    (The kit deliberately keeps ONE canonical copy; every armed project points at it
#     by absolute path, so no project's hooks can drift from the source.)
./adapters/claude-code/bootstrap.sh /path/to/your/workspace

# 2. Fill in the two config templates, in YOUR workspace (no machine-specific values ship here),
#    then re-approve: a new routing_policy.yaml or machine_config.yaml is reported as tampering until approved.
mkdir -p /path/to/your/workspace/governance
cp routing_policy.example.yaml /path/to/your/workspace/governance/routing_policy.yaml
cp machine_config.example.yaml /path/to/your/workspace/governance/machine_config.yaml
(cd /path/to/your/workspace && python3.12 .claude/hooks/hook_support.py --write-approved)

# 3. Prove the hooks fire AND stay silent — 37 fire/no-fire cases (run after step 2: without
#    machine_config.yaml the guard asks on every heavy job)
bash adapters/claude-code/test_hooks.sh --root /path/to/your/workspace

# 4. Give each project its L3 state FIRST, then validate it: step 5 discovers projects by their
#    NEOCORTEX/, so a project without one is not armed.
#    --init originates a NEOCORTEX/ for a project that has none (the usual case);
#    --regenerate only rebuilds files[] inside one that already exists.
python3.12 adapters/claude-code/neocortex_manifest.py --init       /path/to/project
python3.12 adapters/claude-code/neocortex_manifest.py --check      /path/to/project

# 5. Arm every migrated project — dry-run first; enforcement follows migration
#    (re-run these three after you add a project in step 4)
python3.12 adapters/claude-code/install_adapters.py --root /path/to/your/workspace          # preview
python3.12 adapters/claude-code/install_adapters.py --root /path/to/your/workspace --apply
python3.12 adapters/claude-code/install_adapters.py --root /path/to/your/workspace --check  # drift check

# 6. THE STEP PEOPLE SKIP: has a hook actually fired here, or is it merely installed?
python3.12 adapters/claude-code/verify_fires.py --root /path/to/your/workspace
```

**Step 6 is not a formality.** A project can sit armed for months without a hook ever
running, and "installed" reads exactly like "working" until you look. In the workspace this
was extracted from, the *governance* directory itself was excluded from adapter discovery by
a one-line filter — so the enforcement layer had never once run on the project that defines
it, for 76 days. The gap was visible the moment someone ran step 5's `--check` and read the
project list: `verify_fires.py` couldn't have shown it (it only reports on projects already
present in the discovery list `--apply` wrote — a project the list never named is invisible to
it, not merely PENDING). What `verify_fires.py` gave, once the missing entry was found and
fixed, was the follow-up number worth knowing: **PROVEN** (a hook demonstrably fired from this
path) vs. **PENDING** (armed, never fired) — in this case, 0 of 146 Stop-hook firings, because
it had never been armed at all. Until a project says PROVEN, its enforcement is a plan, not a
control — and until it's in step 5's own discovery list, `verify_fires.py` can't even tell you
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
# needs PyYAML: pip install pyyaml
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
- **Anything else** — implement the policies in `spec/POLICY_CORE.md` in your harness; PRs welcome.

---

## Repository layout

```
PAICodeConstitution-2026.md     # L0 — the centerpiece; read this first
DECLARATION.md                  # the why: vendor / model / coder independence
ANTIGRAVITY.md                  # superseded stub; the 2025 text (ANTIGRAVITY-2025.md, named by L0) is not shipped
spec/
  POLICY_CORE.md                # L1 — the enforcement policies (harness-neutral)
  NEOCORTEX_SPEC.md             # L3 — the cold-startable project-state model
adapters/
  claude-code/                  # L1 adapter: hooks/ + installer + validator + self-test
                                #   + handoff_resolve.py (cold start) + anchor_render.py (session anchor)
  antigravity/                  # AGENTS.md template for the Google Antigravity harness
governance/scripts/             # continuity sweep, closeout lint, task-class lint
routing_policy.example.yaml     # L4 — task-class structure + hard rules (bind your models)
machine_config.example.yaml     # L4 — machine-specific facts template (fill in your values)
_skills/
  UNIVERSAL_SKILL_SPEC.md       # L2 — skill lifecycle + cross-project contracts
  adversarial_review/           # L2 — the red-team harness (lenses + scripts)
  verify_gate/                  # L2 — independent-verification receipts (VERIFY-GATE)
reviews/                        # scrubbed review records
redaction_map.yaml              # every intentional difference from the private source
publish_scan.py                 # the pre-publish secrets gate, as code
LICENSE                         # MIT (code) + CC BY 4.0 (docs)
```

---

## The Constitution at a glance

14 articles + one amendment, ≤190 lines of prose (exactly 190 since the 2026-10-27 amendment, so the
next change that adds a line must remove one or amend the budget, per Art. 11):

| | | | |
|---|---|---|---|
| 1 Evidence Before Done | 2 Surgical Integrity | 3 Honest Disambiguation | 4 Escalation by Irreversibility |
| 5 Code / Data / State Separation | 6 Continuity Duty | 7 Skill Mandate | 8 Delegation |
| 9 Routing (task classes, not models) | 10 Honest Reporting | 11 Amendment Doctrine | 12 Audit Reality |
| 13 Prepare Before Acting | 14 Diagnostic Discipline | **Amendment I — Inviolability of Serving Artifacts** | |

---

## Status & maturity

- **Release v0.13** (2026-10-27): the amended constitution, the cold-start handoff, and the documentation fixes listed in
  the 2026-10-27 review record.
- **Constitution** — v1.0, ratified 2026-06-10 after line-by-line + adversarial review; the Independence Preamble was added
  2026-06-20 and four changes were ratified 2026-10-27 (see Reviews). **NEOCORTEX_SPEC** and **UNIVERSAL_SKILL_SPEC** state
  their own versions and ratification dates in their headers.
- **Claude Code adapter** — in daily use; self-tested; hooks adversarially hardened.
- **Config files are *examples*** — they ship with placeholders and zero machine-specific values; you supply yours.
- This is a working extraction of a personal regime, shared as a reference design. Expect to adapt it, not drop it in untouched.
- Some specs, comments and examples cite documents, scripts and skills of the author's private workspace (for example
  `PROTOCOL_publish_regime.md`, `query_interaction_log.py`, `cold_start_ledger.py`, `monthly_audit.py`, and the skills
  `local_llm`, `sprint_closeout`, `github_commit_audit`, `visual_audit`, `calendar_bridge`, `applescript_bridge`). They are
  named for provenance; they are not shipped. The one import of such a skill, the default model call in
  `_skills/verify_gate/scripts/judges.py`, raises an error that says to pass your own `call_fn` instead.

---

## Lineage

`PAICodeConstitution-2026.md` supersedes the 2025 `ANTIGRAVITY.md`. Only a superseded stub is kept here; the 2025
prose itself (`ANTIGRAVITY-2025.md`, which L0's header names) stays in the author's private archive.

---

## Contributing

Issues and PRs welcome — especially **new harness adapters** and **adversarial
review lens-packs**. Changes to L0 prose follow the Constitution's own Amendment
Doctrine (Art. 11): a dedicated review, and the 190-line budget must hold.

## License

Code under **MIT**, documentation under **CC BY 4.0**. `LICENSE` is the authority on which file falls under which;
this README does not restate the list.

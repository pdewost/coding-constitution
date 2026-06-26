# UNIVERSAL SKILL SPECIFICATION (v2.0)

**Status: v2.0 — RATIFIED 2026-06-10** with PAICodeConstitution-2026 (Art. 7).
Supersedes v1.1 (archived: `governance/_archive/UNIVERSAL_SKILL_SPEC_v1.1.md`).
This file — `_skills/UNIVERSAL_SKILL_SPEC.md` in the adopting workspace — is the canonical location all
consumers reference; it is **L4 reference data**: amended freely via git.

**What changed from v1.1 and why** (evidence: 2026-06-10 skills audit):
- **Machine-readable frontmatter + generated index** — v1.1 discovery ran on
  hand-maintained lists; the constitutional registry listed 1 of 22 skills, and
  six near-duplicate notification senders were built before `notify` existed.
- **Interface contract + deprecation policy** — the `llm_client.py` (631 LOC)
  migration stalled indefinitely on a signature mismatch; APIs that cannot bend
  strand their consumers.
- **Promotion/demotion lifecycle** — ~13 of 22 skills had zero external
  consumers and were indistinguishable from abandoned experiments.
- **Local Shadow rule removed** — zero uses in 12 months; replaced by version
  pinning.
- **§7 R-SHARED contracts carried over verbatim in substance** — battle-tested,
  unchanged.

---

## 1. Definition and kinds

A **Skill** is a shared capability with a declared interface. Three kinds, with
different lifecycles:

| Kind | What it is | Consumed via | Example |
|---|---|---|---|
| `library` | Importable code | Declared Python/CLI API | `local_llm`, `notify` |
| `procedure` | A process an agent executes | Harness skill invocation (per-harness adapters) | `github_commit_audit`, `strategic_rebase` |
| `snippets` | Copy-paste-ready patterns | Reading SKILL.md | `applescript_operations` |

## 2. Folder structure

```
_skills/
  index.json             # GENERATED — never hand-edited (see §4)
  SKILLS_INDEX.md        # GENERATED — human view of index.json
  <skill_name>/
    SKILL.md             # mandatory; begins with YAML frontmatter (§3)
    scripts/             # mandatory for library/procedure
    tests/               # mandatory for library; optional otherwise
    examples/            # optional
    CHANGELOG section    # inside SKILL.md, newest first
```

## 3. SKILL.md frontmatter (mandatory, machine-readable)

```yaml
---
name: notify
version: 1.1.0            # semver — Major: breaking API; Minor: capability; Patch: fix
kind: library             # library | procedure | snippets
status: active            # active | incubating | archived
description: One shared iMessage/SMS sender for the whole fleet (one line, discovery-time summary)
interface:                # the ONLY entry points consumers may use
  - "python: from _skills.notify.scripts.notify import send"
env: [NOTIFY_RECIPIENT]   # env vars honoured
requires: []              # other skills this one imports
---
```

Body keeps the v1.1 contract: Purpose, Inputs, Output, CLI/API interface,
Common Gotchas, Common Rationalizations table (recommended). Progressive
loading rule unchanged: agents read frontmatter `description` at discovery
time; full SKILL.md only when the skill is selected.

## 4. The generated index — discovery that cannot drift

- `_skills/build_index.py` (L1 deliverable, rollout plan Phase 4) walks
  `_skills/`, parses frontmatter, **collects consumer evidence per kind**, and
  emits `index.json` + `SKILLS_INDEX.md` (name · version · kind · status ·
  description · consumer count). Consumer evidence by kind — `library`: real
  imports grepped across the workspace; `procedure`: invocation records and
  CLAUDE.md usage notes; `snippets`: provenance markers (§5.5) grepped in
  project code. Without per-kind evidence, two of three kinds would count
  zero consumers forever and §6 would auto-archive them wrongly.
- Hand-maintained skill registries are **forbidden** — in constitutions, in
  CLAUDE.md files, anywhere. CLAUDE.md files may say "see the skill index" and
  list only project-specific usage notes.
- A session-start hook (per-harness adapter) injects the one-screen index
  (**≤40 lines**; `archived` skills excluded from the default view).
- Registration of a new skill = run `build_index.py`. Nothing else.
- The index generator FAILS loudly on: missing frontmatter, missing SKILL.md,
  version in frontmatter ≠ version in title line. An `active` skill with
  failing tests is **demoted to a flagged state in the index** (visible to
  every session) — it does not fail the build; one broken skill must never
  block fleet-wide registration.

## 5. Interface contract & deprecation (the Bezos rule)

1. Consumers import **only** the entry points declared in `interface:`.
   Reaching into a skill's internal modules is a defect of the consumer.
2. **Breaking changes ship a shim**: the old signature keeps working for at
   least one minor version, emitting a `DeprecationWarning` naming the
   replacement. Consumers migrate during the shim window; the shim's removal
   is the major bump.
3. The skill side owns API stability; the consumer side owns migration. A
   stalled migration (cf. `llm_client.py`) is a *skill* defect if no shim
   exists, a *consumer* defect if one does.
4. Consumers may pin: `requires: [local_llm>=3.12,<4]` in their own docs.
   This replaces v1.1 §4.2 "Local Shadow" (removed: zero uses in 12 months).
5. **Snippets provenance**: code copied from a `snippets`-kind skill carries a
   marker — `# from _skills/<name> v<x.y>` — at the copy site. This is the
   sanctioned use of that kind (Constitution Art. 7), not R-SHARED-2
   duplication; the marker doubles as the kind's consumer evidence (§4) and
   as the upgrade trail when the source skill evolves.

## 6. Lifecycle: promotion and demotion

- **Promote at the second consumer.** Capability used by one project = project
  code. The moment a second project needs it → promote to `_skills/` with
  frontmatter, tests, and an index rebuild. Never promote speculatively —
  that is how a zero-consumer tail of 13 skills grew in 2025.
- **Incubating**: new skills enter as `status: incubating` until they have a
  green test suite and one consumer in production.
- **Archive, never delete**: `status: archived` after ~6 months sustained at
  zero consumers (per-kind evidence, §4) — this grace period is the binding
  reading of Constitution Art. 7's "sustained period." A skill that drops to
  **one** consumer stays `active`: the two-consumer bar gates promotion, not
  survival. Archived skills stay in place, are excluded from the default
  index view, and may be revived by any future consumer. (NEOCORTEX
  principle: archives exist and are declared.)

## 7. Cross-Project Runtime Contracts (R-SHARED-1..6)

Carried over from v1.1 in substance, unchanged — these rules are battle-tested
(formalized after the `local_llm` OOM-hazard audit):

- **R-SHARED-1** — No project-specific imports inside skill code. Config via
  constructor args or namespaced env vars.
- **R-SHARED-2** — Skills are the single source of truth for cross-cutting
  concerns; project code re-implementing skill behaviour is a defect: track
  it, CI-guard it, migrate it.
- **R-SHARED-3** — Cross-project resources (GPU, ports, RAM, model caches,
  AppleScript bridges) coordinate at the skill layer via system-shared lock
  paths, with stale-holder recovery, a non-blocking "is held?" probe, and a
  backend-priority env override.
- **R-SHARED-4** — Lockstep versioning: breaking bumps coordinate with every
  consumer; the bumping party opens migration items in each consumer's
  NEOCORTEX (or legacy `BRAIN/` until that project migrates). (v2 softens
  the blast radius via §5 shims.)
- **R-SHARED-5** — Memory-pressure default is fall-through, not block; opt-in
  waiting for consumers that need a specific backend. Tests must cover both.
- **R-SHARED-6** — Skill state: **skills hold no `BRAIN/` or `NEOCORTEX/`** (NEOCORTEX is L3
  project state; a skill is an L2 library).
  Durable operational rules live under a dedicated `SKILL.md` `## Operational Rules` heading (kept
  separate from the interface contract and the Changelog); version history is the mandated `SKILL.md`
  Changelog (R-SHARED-4); cross-consumer change coordination is delegated to consumers' NEOCORTEX.
  Transient build-history may sit in a skill-local `_archive/` and is **not** governed state. A skill's
  manifest is its `SKILL.md` frontmatter.

Compliance checklist (v1.1) still applies, plus: frontmatter present and
parseable; skill appears in a freshly generated index; shim policy stated for
any breaking change.

## 8. Testing

v1.1 rules unchanged: `python3.12 -m pytest tests/ -v` (use `python3.12`
explicitly — bare `python3` may resolve to a system Python with an old version);
all green before `status: active`.

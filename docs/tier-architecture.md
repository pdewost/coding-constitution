# Tier Architecture

Antigravity organizes project knowledge into a strict hierarchy. Each tier has a clear scope, a clear owner, and a clear rule about when it can be modified.

```
Tier 0 — ANTIGRAVITY.md          Behavioral (model-agnostic, project-agnostic)
Tier 1 — *_SPEC.md               Platform (domain-specific hard truths)
Tier 2 — PROJECT_BRIEF.md        Tactical (project goals, tech stack, constraints)
Tier 2.5 — BRAIN/                Live State (cross-session intelligence)
```

## Tier 0: Behavioral Master (`ANTIGRAVITY.md`)

**Scope**: How the agent should behave — planning, verification, continuity, surgical precision.

**Location**: Workspace root. One copy. All projects inherit it.

**Immutability**: Cannot be edited during project sessions. Changes require a dedicated session with explicit user approval. This prevents mid-task drift where the agent "improves" its own rules to justify shortcuts.

**Why it exists**: Without a behavioral anchor, agents gradually relax their standards over long sessions. Tier 0 is the constant.

## Tier 1: Platform Specs (`*_SPEC.md`)

**Scope**: Technical truths specific to a technology domain — AppleScript encoding quirks, CSS framework conventions, database access patterns.

**Location**: Domain root directory (e.g., `macOS Contacts Management/`).

**Examples**:
- `MACOS_AUTOMATION_SPEC.md` — ASOC bridge coercion rules, compilation requirements, UX dialog caps
- `WEB_FRONTEND_SPEC.md` — design tokens, accessibility standards, bundler config
- `DATA_PIPELINE_SPEC.md` — schema versioning, migration safety, idempotency requirements

**Why it's separate from Tier 0**: Platform truths change when the platform changes (new macOS version, new framework major version). Behavioral truths don't.

## Tier 2: Project Brief (`PROJECT_BRIEF.md`)

**Scope**: What this specific project does, what tech it uses, what skills it needs, what safety constraints apply.

**Location**: Project root directory.

**Contents**: Goal, tech stack, safety rules, skills used, verification protocol. Static — describes the project as designed, not its current execution state.

**Why it's separate from Tier 2.5**: A brief doesn't change session to session. It's the "constitution" of the project. The BRAIN is the "daily news."

## Tier 2.5: BRAIN/ (Live State)

**Scope**: Cross-session tactical intelligence — what's running, what happened, what to do next.

**Location**: `BRAIN/` subdirectory in the project root.

```
BRAIN/
├── JOURNAL.md       — Chronological decisions, incidents, observations (newest-first)
├── STATUS.md        — Snapshot: running, pending, blocked
├── TASK.md          — Active task list (Antigravity §10 progress tracking)
└── WALKTHROUGH.md   — Step-by-step operational guide (updated each session)
```

**When to deploy BRAIN/**: When the project spans multiple sessions AND losing current context would require significant reconstruction.

**Why not just use git history?**: Git tracks *what changed*. BRAIN tracks *why*, *what's next*, and *what's currently running*. An agent doing a cold start needs the latter.

## Domain Entry Points

Each domain has a lightweight entry-point file (e.g., `CLAUDE.md` for Claude Code, `.cursorrules` for Cursor) that composites the tiers:

```
Load Order:
1. Read ANTIGRAVITY.md (Tier 0)
2. Read *_SPEC.md (Tier 1)
3. Read PROJECT_BRIEF.md (Tier 2)
4. Read BRAIN/ if present (Tier 2.5)
```

This file is ~30-40 lines and includes: load order, available skills, model routing heuristic, and domain safety rules. It's the only thing you need to paste to cold-start an agent in a new session.

## Conflict Resolution

When tiers conflict (they shouldn't, but):
- Tier 0 wins over Tier 1 (behavior over platform)
- Tier 1 wins over Tier 2 (platform truth over project preference)
- BRAIN/ is authoritative for *current state* but not for *policy*

If a conflict is discovered, the agent must invoke the §7.1 Mid-Execution Contradiction Protocol: stop, report with `CONTRADICTION:`, wait for resolution.

# Antigravity

Behavioral guidelines and skill framework for AI coding agents working across multiple models and sessions.

## What this is

Antigravity is a system of Markdown documents that make AI coding agents (Claude Code, Cursor, Gemini, Copilot, etc.) behave like disciplined engineers rather than eager interns. It addresses problems that emerge when agents work on real projects over time:

- **Context drift**: The agent forgets what it was doing after 20 tool calls
- **Silent assumptions**: The agent picks an interpretation and never tells you
- **Sycophantic compliance**: The agent does what you ask even when it will break things
- **Session amnesia**: A new session starts from scratch, re-discovering everything
- **Skill duplication**: The agent writes a utility that already exists in the project

### The tier system

Antigravity organizes project knowledge into a strict hierarchy. Each tier has a different scope, owner, and change frequency:

| Tier | File | Scope | Changes when... |
|------|------|-------|-----------------|
| **Tier 0** | `ANTIGRAVITY.md` | How the agent should *behave* — planning, verification, continuity | Rarely. Behavioral rules are stable. |
| **Tier 1** | `*_SPEC.md` | Platform truths — encoding quirks, framework conventions, API gotchas | The platform changes (new OS, new framework major version). |
| **Tier 2** | `PROJECT_BRIEF.md` | What *this project* does, its tech stack, safety constraints | The project scope changes. |
| **Tier 2.5** | `BRAIN/` directory | Live state — what's running, what happened, what to do next | Every session. |

The agent loads them in order: Tier 0 first, then 1, then 2, then 2.5. Higher tiers override lower tiers only for current-state facts, never for behavioral rules. See [`docs/tier-architecture.md`](docs/tier-architecture.md) for the full explanation.

> **Note:** `PROJECT_BRIEF.md`, `BRAIN/`, `CLAUDE.md`, and `AGENTS.md` are user-created files that live in your own projects. They are not part of this repository — Antigravity only provides the behavioral master (`ANTIGRAVITY.md`), templates, and documentation.

## How it compares to [agent-skills](https://github.com/addyosmani/agent-skills)

Addy Osmani's `agent-skills` is excellent — we adopted several of its ideas (anti-rationalization tables, confusion management, scope discipline). The two systems complement each other:

| | agent-skills | Antigravity |
|---|---|---|
| **Target** | Single agent, single session, IDE | Multiple models, multiple sessions, any runtime |
| **Skills** | Process docs (Markdown only) | Process docs + executable scripts + tests |
| **Session continuity** | Not addressed | §10 LLM Continuity Protocol — cold-start handover is a hard requirement |
| **Multi-model** | Not addressed | §8 Model routing protocol, §10 Dual-audience intelligibility |
| **Environment awareness** | Not addressed | §2 Environment Detection — probes runtime before acting |
| **Observability** | Verification checklists | Grep-able markers (`CONTRADICTION:`, `RISK:`, `NOTICED BUT NOT TOUCHING:`) |
| **Skill isolation** | Global only | Global + Local Shadow override per project |
| **Anti-rationalization** | In every skill (signature feature) | Adopted — recommended in SKILL.md template (§3.1) |

**Use agent-skills** if you work primarily in one IDE with one model on greenfield projects.
**Use Antigravity** if you orchestrate multiple models, maintain long-lived projects, or need verifiable behavioral compliance.
**Use both**: Osmani's skills as process checklists, Antigravity as the behavioral + continuity layer.

## Quick start

### 1. Copy the behavioral master
Place [`ANTIGRAVITY.md`](ANTIGRAVITY.md) at your workspace root. This is Tier 0 — it governs how every agent behaves regardless of project.

### 2. Add to your agent's rules
**Claude Code**: Reference it in your `CLAUDE.md`:
```markdown
## Load Order
1. Read `ANTIGRAVITY.md` (Tier 0 — behavioral master)
```

**Cursor**: Add to `.cursorrules`.
**Gemini**: Include in your system prompt or `AGENTS.md`.
**Any agent**: Paste it as the first message of a new session.

### 3. Set up project tiers
```
your-workspace/
├── ANTIGRAVITY.md              # Tier 0: Behavioral (you just added this)
├── COLD_START.md               # Strategic Rebase Protocol
├── _skills/                    # Global skill registry
│   ├── your-skill/
│   │   ├── SKILL.md
│   │   └── scripts/
│   └── ...
├── your-domain/
│   ├── CLAUDE.md               # Domain entry point (load order + safety rules)
│   ├── DOMAIN_SPEC.md          # Tier 1: Platform truths
│   └── your-project/
│       ├── PROJECT_BRIEF.md    # Tier 2: Tactical goals
│       └── BRAIN/              # Tier 2.5: Live cross-session state
│           ├── JOURNAL.md
│           ├── STATUS.md
│           └── TASK.md
```

### 4. Create skills
Use the [`templates/SKILL.md.template`](templates/SKILL.md.template) to create skills with:
- Purpose (doubles as discovery-time summary)
- Interface (CLI + programmatic)
- Common Gotchas
- Anti-Rationalization table
- Verification Protocol
- Phase tag (ORIENT / PLAN / BUILD / VERIFY / SHIP)

See [`docs/skill-anatomy.md`](docs/skill-anatomy.md) for the full specification.

## Key concepts

### Observability markers
Behavioral rules can't be unit-tested. Antigravity solves this by requiring agents to emit specific string prefixes when certain situations arise:

| Marker | When it fires |
|--------|--------------|
| `CONTRADICTION:` | Agent discovers conflicting docs/code mid-execution |
| `RISK:` | User's request would harm the codebase |
| `NOTICED BUT NOT TOUCHING:` | Agent spots a defect outside current scope |
| `Source:` | Agent introduces an API/command not previously used |

Grep your session transcripts for these markers to audit compliance. See [`docs/observability-markers.md`](docs/observability-markers.md).

### Governance checkpoints (§9bis)
Rules loaded at session start decay as context fills up. §9bis defines mandatory self-reinforcement triggers: task-boundary re-reads, post-compaction re-anchoring, autonomy-proportional rigor, and drift detection (if the user has to remind you of a rule, something already went wrong). See the `Governance Checkpoints` section in [`ANTIGRAVITY.md`](ANTIGRAVITY.md).

### Model routing advisory (§8)
The model routing table is now an active protocol, not a passive reference. Before starting any execution phase, the agent must identify the prescribed model and warn the user if they're on a higher-cost model than the task requires.

### Multi-model continuity
When Model A runs out of context, Model B picks up cold using:
- `COLD_START.md` — the rebase protocol
- `BRAIN/` — live project state
- In-code `# v4.7 FIX:` tags — grep-able change history

See [`docs/multi-model-orchestration.md`](docs/multi-model-orchestration.md).

### Operational safety
When agents run alongside cron jobs, scheduled syncs, or other agents, temporal collisions corrupt data silently. Antigravity defines patterns for this: forbidden time windows, sentinel files, pre-flight process checks, and destructive mode gates. See [`docs/operational-safety.md`](docs/operational-safety.md).

### Incident-driven safety rules
The best safety rules trace back to real incidents: "NEVER use `delete person` — it destroyed data that iCloud propagated to every device within seconds." Each rule has a prohibition, a consequence, an alternative, and an incident reference. See [`docs/safety-rules.md`](docs/safety-rules.md).

### Anti-rationalization
LLMs generate plausible excuses for skipping steps ("this is too simple to need tests"). Each skill includes a table of common rationalizations paired with rebuttals. Adopted from [agent-skills](https://github.com/addyosmani/agent-skills) — credit where due.

### Assume Independent Audit
All agent outputs — code, documentation, logs, plans — are subject to evaluation by a third-party LLM with equivalent or superior capabilities. This principle pervades every section of `ANTIGRAVITY.md`: decisions must be traceable, artifacts self-consistent, and completion claims backed by verifiable evidence. The audit succeeds when there are no contradictions between what the code does, what the docs claim, and what the logs record.

## Repository structure

```
antigravity/
├── ANTIGRAVITY.md                  # Tier 0 behavioral master (copy to your workspace root)
├── COLD_START.md                   # Strategic Rebase Protocol
├── docs/
│   ├── tier-architecture.md        # Tier 0/1/2/2.5 explained
│   ├── observability-markers.md    # How to audit agent compliance
│   ├── multi-model-orchestration.md # Cross-model continuity
│   ├── skill-anatomy.md            # SKILL.md specification
│   ├── example-tier1-spec.md       # Real Tier 1 spec (macOS Automation)
│   ├── operational-safety.md       # Temporal constraints, collision windows, sentinel files
│   └── safety-rules.md             # Incident-driven safety rule pattern
├── skills/
│   ├── strategic-rebase/SKILL.md   # Example: project onboarding skill
│   └── example-applescript-bridge/SKILL.md  # Example: platform integration skill
└── templates/
    ├── SKILL.md.template           # Blank skill template
    ├── CLAUDE.md.template          # Domain entry point template
    └── PROJECT_BRIEF.md.template   # Project brief template
```

## Origin

Antigravity was developed for a multi-project macOS automation workspace orchestrating Claude (Code + API) and Gemini across long-lived projects. The behavioral rules, skill framework, and continuity protocol evolved from real incidents — contact data destruction, session amnesia bugs, and the subtle failures that come from agents being too agreeable.

The name comes from the workspace where it was born. The metaphor: these guidelines push against the natural gravity of LLM behavior — the pull toward shortcuts, sycophancy, and forgetting.

## License

Apache 2.0 — see [LICENSE](LICENSE) and [NOTICE](NOTICE).

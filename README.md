# Antigravity

Behavioral guidelines and skill framework for AI coding agents working across multiple models and sessions.

## What this is

Antigravity is a system of Markdown documents that make AI coding agents (Claude Code, Cursor, Gemini, Copilot, etc.) behave like disciplined engineers rather than eager interns. It addresses problems that emerge when agents work on real projects over time:

- **Context drift**: The agent forgets what it was doing after 20 tool calls
- **Silent assumptions**: The agent picks an interpretation and never tells you
- **Sycophantic compliance**: The agent does what you ask even when it will break things
- **Session amnesia**: A new session starts from scratch, re-discovering everything
- **Skill duplication**: The agent writes a utility that already exists in the project

## How it compares to [agent-skills](https://github.com/addyosmani/agent-skills)

Addy Osmani's `agent-skills` is excellent — we adopted several of its ideas (anti-rationalization tables, confusion management, scope discipline). The two systems complement each other:

| | agent-skills | Antigravity |
|---|---|---|
| **Target** | Single agent, single session, IDE | Multiple models, multiple sessions, any runtime |
| **Skills** | Process docs (Markdown only) | Process docs + executable scripts + tests |
| **Session continuity** | Not addressed | §10 LLM Continuity Protocol — cold-start handover is a hard requirement |
| **Multi-model** | Not addressed | §8 Model routing heuristic, §10 Dual-audience intelligibility |
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

See [`docs/tier-architecture.md`](docs/tier-architecture.md) for the full explanation.

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

### Multi-model continuity
When Model A runs out of context, Model B picks up cold using:
- `COLD_START.md` — the rebase protocol
- `BRAIN/` — live project state
- In-code `# v4.7 FIX:` tags — grep-able change history

See [`docs/multi-model-orchestration.md`](docs/multi-model-orchestration.md).

### Anti-rationalization
LLMs generate plausible excuses for skipping steps ("this is too simple to need tests"). Each skill includes a table of common rationalizations paired with rebuttals. Adopted from [agent-skills](https://github.com/addyosmani/agent-skills) — credit where due.

## Repository structure

```
antigravity/
├── ANTIGRAVITY.md                  # Tier 0 behavioral master (copy to your workspace root)
├── COLD_START.md                   # Strategic Rebase Protocol
├── docs/
│   ├── tier-architecture.md        # Tier 0/1/2/2.5 explained
│   ├── observability-markers.md    # How to audit agent compliance
│   ├── multi-model-orchestration.md # Cross-model continuity
│   └── skill-anatomy.md            # SKILL.md specification
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

# Skill Anatomy (Universal Skill Specification)

A **Skill** is the atomic unit of reusable agentic capability. It encapsulates both reasoning (what to do and why) and execution (code that does it).

## What Makes This Different from Osmani's Agent Skills

[Osmani's agent-skills](https://github.com/addyosmani/agent-skills) defines skills as **process documents** — step-by-step workflows with no executable code. This is effective for guiding single-agent IDE workflows.

Antigravity skills are **process + code + tests**:

| | Osmani | Antigravity |
|---|---|---|
| SKILL.md (process doc) | Yes | Yes |
| Executable scripts | No | Yes (`scripts/`) |
| Tests/examples | No | Yes (`tests/`, `examples/`) |
| Anti-rationalization tables | Yes (in every skill) | Yes (recommended, §3.1) |
| Lifecycle phase tags | Yes (DEFINE/PLAN/BUILD/VERIFY/REVIEW/SHIP) | Yes (ORIENT/PLAN/BUILD/VERIFY/SHIP) |
| Local shadow overrides | No | Yes (`_local_skills/`) |
| Cross-skill dependency trace | No | Yes (Global Impact Audit) |

## Folder Structure

```
_skills/
  <skill_name>/
    SKILL.md           # Mandatory: Purpose, Contract, and Usage
    scripts/           # Mandatory: The executable code
    examples/          # Optional: Past successes and known issues
    tests/             # Optional: Verification and robustness tests
    metadata.json      # Optional: Version, Author, Dependency Trace, Phase
```

## The SKILL.md Contract

Every `SKILL.md` MUST contain:

1. **Purpose**: High-level goal. This serves as the discovery-time summary — agents load only this field during skill discovery and load the full SKILL.md only when the skill is selected for use.
2. **Inputs**: Expected data format (JSON, environment variables, file paths).
3. **Output**: Expected result and error states.
4. **CLI / API Interface**: How to run the scripts from the terminal.
5. **Common Gotchas**: Known failure modes and recovery loops.

### Common Rationalizations (Recommended)

Each `SKILL.md` SHOULD include a table countering excuses an LLM might use to skip steps:

| Rationalization | Why it's wrong |
|---|---|
| *"This is too simple to need [step X]"* | *Simple changes in complex systems cause complex bugs.* |
| *"I'll do [step X] after the main change"* | *"After" never comes. The step exists because skipping it has caused failures before.* |
| *"The tests pass, so verification is redundant"* | *Tests cover known cases. Verification catches unknown interactions.* |

This is inspired by Osmani's anti-rationalization pattern — the most distinctive and effective feature of his system.

## The Local Shadow Rule

If a project needs a skill variant that differs from the global version:

1. Create `_local_skills/<skill_name>/` in the project root
2. Place the modified `SKILL.md` and scripts there
3. The agent uses the local version for that project
4. Do NOT edit the global skill until the local variant is proven stable

This prevents "breaking the fleet" — a change that helps one project but breaks three others.

## Lifecycle Phase Tags

Each skill MAY include a `phase:` field to indicate which development stage it primarily serves:

| Phase | Purpose | Example skills |
|---|---|---|
| `ORIENT` | Project discovery, onboarding | strategic-rebase, project-scaffold |
| `PLAN` | Architecture, design decisions | (project-specific) |
| `BUILD` | Implementation, integration | applescript-bridge, api-client |
| `VERIFY` | Testing, validation, auditing | visual-audit, identity-resolution |
| `SHIP` | Deployment, release, documentation | (project-specific) |

This enables agents to filter the skill registry by current phase.

## Versioning

Skills use Capability-Based Versioning:
- **Major**: Breaking change in output format or engine
- **Minor**: New capability added
- **Patch**: Bug fix

## Deployment Workflow

1. **Draft**: Create `_skills/new_skill/SKILL.md`
2. **Logic**: Implement `scripts/`
3. **Verify**: Run the self-audit test
4. **Register**: Update project docs to reference the new skill

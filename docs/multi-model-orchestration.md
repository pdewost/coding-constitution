# Multi-Model Orchestration

Most agent guideline systems assume a single model running in a single session. Antigravity is designed for a different reality: **multiple models working on the same codebase across multiple sessions**, where any model might pick up where another left off.

## The Continuity Problem

When Model A exhausts its context or credits mid-task, Model B must:
1. Understand what the project does
2. See what was changed and why
3. Know exactly what to do next

Without a continuity protocol, Model B wastes 30-50% of its context re-discovering what Model A already knew. Worse, it may make different assumptions and introduce contradictions.

## Antigravity's Solution: §10 LLM Continuity Protocol

### Atomic Progress Tracking
After each meaningful change, the agent updates the task file with:
- What was done (file, line range, purpose)
- What remains (next items, in priority order)
- Current state (running processes, test results, blockers)

This isn't optional documentation — it's the handover mechanism.

### Code Tagging
Every code change includes an in-code version tag:
```python
# v4.7 B1-FIX: prevent UnboundLocalError when API returns empty response
if not response:
    return []
```

This allows `grep`-based discovery: a new model can find all changes made by previous models without reading external docs.

### The Strategic Rebase
When a model enters a project (new session or picking up from another model), it executes the `COLD_START.md` protocol:
1. Read Tier 0 (behavioral rules)
2. Read Tier 1 (platform rules)
3. Read project state (BRAIN/ or PROJECT_BRIEF.md)
4. Locate active task file
5. Confirm alignment with user

This takes 30-60 seconds of context but prevents the much larger cost of re-discovery and assumption drift.

## Model Routing

Different models have different strengths. Antigravity §8 provides a routing heuristic:

| Task type | Preferred model tier | Rationale |
|-----------|---------------------|-----------|
| Bulk exploration, search, scaffolding | Speed (Flash/Haiku) | Volume at low cost |
| Planning, risk analysis, architecture | Precision (Sonnet/Opus) | Reasoning depth |
| File editing, execution, debugging | Precision (Sonnet/Opus) | Tool-use reliability |
| Codebase-wide reasoning, visual tasks | Context (Gemini Pro) | Large window + multimodal |
| Interactive iteration | Interactive (Claude Code) | Human-in-the-loop feedback |

This is advisory, not prescriptive. The executing agent doesn't choose its own model — the orchestrator or user does.

## Dual-Audience Intelligibility

Every output must be readable by both:
- **A human developer** reviewing the work
- **A GenAI developer** (the next model) continuing the work

This means: no idioms, no implicit references to conversation history, no "as discussed above." Every statement must stand on its own with explicit file paths, line numbers, and measurements.

## Practical Example

**Session 1** (Claude Opus): Implements feature, hits context limit after 3 of 5 subtasks.
- Task file shows: subtasks 1-3 done with file paths, subtask 4-5 pending with specs
- Code has `# v2.1 FEAT:` tags on all changes
- BRAIN/STATUS.md shows: "3/5 subtasks complete, no blockers"

**Session 2** (Gemini Pro): Picks up cold.
- Reads COLD_START.md → loads tiers → reads BRAIN/STATUS.md
- Greps for `v2.1` tags to see what changed
- Reads task file for subtask 4-5 specs
- Continues without re-asking the user what to do

The handover is invisible to the user. That's the goal.

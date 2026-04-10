# SKILL: Strategic Rebase (v1.0)

Automates the "cold start" onboarding protocol when an agent enters a new project or resumes after a session break.

---

## Purpose
To serve as the "Onboarding Specialist" for any project directory. It ensures the agent is aligned with global guidelines, project-specific truths, and current task state, eliminating "Context Drift."

---

## Interface

### 1. **Onboard (`scripts/rebase.py`)**
Automates the discovery and summary process of a project directory.
- **Workflow**:
  1. **Tier 0 Rebase**: Locates and reads the global `ANTIGRAVITY.md`.
  2. **Skill Discovery**: Indexes the `_skills/` registry and maps relevant capabilities.
  3. **Ground Truth Discovery**: Identifies `PROJECT_BRIEF.md`, `README.md`, or `ABOUT.md`.
  4. **State Discovery**: Identifies `task.md`, `TODO.md`, and recent history entries.
  5. **BRAIN/ Support**: If a `BRAIN/` directory exists, reads `BRAIN/JOURNAL.md` and `BRAIN/STATUS.md` first.
  6. **Snapshot**: Runs a shallow `ls -R` to map entry points, logic modules, and data stores.
  7. **Hygiene Audit**: Scans for debris (`.DS_Store`, `*.bak`, etc.) and presents cleaning suggestions.
- **Output**: A dual-audience (human + AI) report summarizing the "Current Ground Truth" and "Next Tactical Step."

---

## Usage Patterns

### CLI
```bash
python3 _skills/strategic-rebase/scripts/rebase.py /path/to/project
```

---

## Common Gotchas & Fixes

1. **Duplicate Documents**: If multiple `PROJECT_BRIEF.md` files are found (e.g., parent and child directory), the rebase script prioritizes the one closest to the current working directory.
2. **Legacy State**: If a project is missing its ground-truth documents, the rebase script flags this as "Strategic Debt" and suggests an Autonomous Onboarding task.
3. **Process Detection**: The script attempts to find running PIDs and tail their logs to provide immediate context.

---

## Common Rationalizations

| Rationalization | Why it's wrong |
|---|---|
| *"I already know this project from earlier in the conversation"* | *Context drifts. The rebase takes 30 seconds and catches contradictions that memory misses.* |
| *"The user just told me what to do, I don't need to read the docs"* | *The user assumes you know the project's safety constraints. The rebase loads them.* |
| *"I'll read the docs if I get stuck"* | *By then you've already written code based on wrong assumptions. Rebase first, code second.* |

---

## Verification Protocol
A rebase is considered complete if:
1. Tier 0 and Tier 1 documents have been located and read.
2. The active task file has been identified (or its absence flagged).
3. A summary has been presented to the user for alignment confirmation.

## Metadata
- **Phase**: ORIENT
- **Last verified**: 2026-04-09
- **Status**: Active

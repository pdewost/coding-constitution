# STRATEGIC REBASE PROTOCOL (v3.0)

This document contains procedural instructions for the AI Agent. It is 100% generic and self-discovering.

---

## Phase 0: Upward Guideline Rebase
**Agent Instruction**: Traverse up the directory tree to locate and read the following foundational guidelines.
- **Tier 0**: `ANTIGRAVITY.md` (Behavioral Master -- DO NOT EDIT during sessions)
- **Tier 1**: `*_SPEC.md` (Domain-specific hard truths)
*Action: If multiple Tier 1 files are found, identify the most relevant one for the current directory.*

**Project Context Layer**: If a `PROJECT_COLD_START.md` or domain-level `CLAUDE.md` / `.cursorrules` / `AGENTS.md` exists in the current directory or immediate parent, read it immediately after Tier 0/1. It contains project-specific identity, architecture heuristics, and resumption state.

---

## Phase 1: Project State Discovery
**Agent Instruction**: Identify the project's current ground truth.

1. **BRAIN first**: If a `BRAIN/` directory exists, read `BRAIN/JOURNAL.md` and `BRAIN/STATUS.md` before any other doc. These are the most authoritative current-state signals.
2. **Locate Strategy**: If no BRAIN/, look for `PROJECT_BRIEF.md`, `README.md`, or `ABOUT.md`.
3. **Locate Active Tasks**: Find `BRAIN/TASK.md`, `task.md`, or `TODO.md`.
4. **Consistency Audit**: Verify the active task file aligns with the project strategy. Report any "Context Drift" to the user.

---

## Phase 6: Tactical Resume
1. **Target**: Define the immediate next step based on the active task file.
2. **Rebase Confirmation**: Summarize the current "Ground Truth" and project state to the user to confirm full alignment.

---

## Extended Protocol (invoke only when needed)

### Phase 0.5: Global Skill Discovery
*Invoke when: the task involves logic that might already exist as a global skill.*

1. **Mount Registry**: Traverse to workspace root, locate `_skills/`.
2. **List Capabilities**: `ls -F _skills/` to see available modules.
3. **Map Relevance**: If a skill matches the task, use it. Check `SKILL.md` for interface.

### Phase 2: Autonomous Onboarding
*Invoke when: Tier 2 documents (Brief, BRAIN, Task) are missing or clearly legacy.*

- Analyze entry points (`ls -R`) and draft a new `PROJECT_BRIEF.md` and `BRAIN/TASK.md`.
- Infer project trajectory from file modification dates and log summaries.

### Phase 3: Technical Snapshot
*Invoke when: you need to verify execution state before acting.*

- Run `ls -F` and `ls -R -maxdepth 2`.
- Check `logs/`, status files, `.env` for current status.
- Check for running processes related to the project.

### Phase 4: Structural Comprehension
*Invoke when: the project entry point or data flow is unclear.*

- **Entry Point**: Identify main launcher.
- **Core Logic**: Locate primary business logic modules.
- **Data Flow**: Identify where state is stored (JSON, DB, local folders).

### Phase 5: Operational Hygiene
*Invoke when: onboarding a legacy project or doing a periodic cleanup pass.*

- Find orphaned test scripts, temp files, or obsolete snapshots.
- Report candidates for `archive/` -- ask before moving anything.

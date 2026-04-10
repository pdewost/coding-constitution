# Observability Markers

Behavioral guidelines for LLMs can't be unit-tested. But they CAN be designed so that **compliance produces visible artifacts** and **violation leaves a detectable absence**.

This document catalogs the observable markers that Antigravity rules produce when followed. Use them to audit agent sessions and measure guideline effectiveness.

## The Measurement Problem

Traditional code quality has linters, tests, and CI gates. Agent behavioral quality has none of these. A guideline that says "be careful" is unfalsifiable — you can never prove it wasn't followed.

Antigravity solves this by requiring specific **string prefixes** in agent output when certain situations arise. If the situation arose and the prefix is absent, the guideline was violated.

## Marker Catalog

### `CONTRADICTION:`
**Source**: §7.1 Mid-Execution Contradiction Protocol

**When it fires**: The agent discovers that two existing artifacts (docs, code, config) prescribe incompatible behaviors.

**What follows**: File paths and line numbers for both sides of the contradiction.

**Audit**: If a session produced a bug caused by conflicting specs, grep the transcript for `CONTRADICTION:`. Absence = the agent didn't catch it (or didn't report it).

---

### `RISK:`
**Source**: §7 Anti-Sycophancy Clause

**When it fires**: The user requests something that will cause measurable harm to the codebase (data loss, regression, architectural debt).

**What follows**: Description of the risk and a proposed alternative.

**Audit**: Post-mortem on a bad change — "did the agent flag this?" Harder to measure absence (the agent might genuinely have seen no risk), but a pattern of zero `RISK:` across many sessions suggests over-compliance.

---

### `NOTICED BUT NOT TOUCHING:`
**Source**: §3 Scope Scouting

**When it fires**: The agent spots a defect or risk outside the current task scope.

**What follows**: `<file:line> — <description>`

**Audit**: Each occurrence = a potential future task surfaced without scope creep. Over time, these can be collected into a tech debt backlog. Zero occurrences across many sessions in a messy codebase = the agent isn't looking.

---

### `Source:`
**Source**: Skill-level source verification rules (e.g., AppleScript Bridge §4)

**When it fires**: The agent introduces a command or API call not previously used in the project.

**What follows**: Documentation URL, man page reference, or explicit "verified against [platform version]."

**Audit**: If a command turns out to be deprecated or behave differently than expected, check whether `Source:` was cited. Absence = the agent relied on training data without verification.

---

### Version tags (`# v4.7 B1-FIX:`)
**Source**: §10 LLM Continuity Protocol

**When it fires**: Every code change.

**What follows**: In-code comment explaining the fix rationale.

**Audit**: `grep -r "v[0-9]\+\.[0-9]\+" src/` should return tagged changes. A commit without version tags = continuity protocol was skipped.

---

## How to Audit

### After a session
```bash
# Check for marker presence
grep -n "CONTRADICTION:\|RISK:\|NOTICED BUT NOT TOUCHING:\|Source:" session_transcript.md

# Count occurrences over time
grep -c "NOTICED BUT NOT TOUCHING:" logs/sessions/*/transcript.md | sort -t: -k2 -n
```

### After a bug
1. Identify the root cause (conflicting spec? deprecated API? scope creep?)
2. Map it to the marker that should have fired
3. Grep the session transcript for that marker
4. If absent: the guideline was violated — consider strengthening it or adding a rationalization entry to the relevant SKILL.md

### Across sessions (trend)
Track marker frequency over time. Healthy trends:
- `CONTRADICTION:` decreasing (docs are getting more consistent)
- `NOTICED BUT NOT TOUCHING:` steady (agent is still observing)
- `RISK:` low but non-zero (agent pushes back when warranted)
- `Source:` present whenever new APIs are introduced

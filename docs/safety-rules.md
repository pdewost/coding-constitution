# Safety Rules: The Incident-Driven Pattern

The best safety rules aren't written by committee — they're extracted from incidents. Every rule in a well-maintained system traces back to something that actually went wrong.

This document describes the pattern for writing safety rules that agents will actually follow, illustrated with real (anonymized) examples.

## Why Agents Ignore Abstract Safety Rules

An agent told "be careful with data" will nod and proceed to delete your contacts. An agent told "NEVER use `delete person` in AppleScript — this permanently destroys contact data with no undo, and iCloud propagates the deletion to every device within seconds" will actually stop.

The difference:
- **Abstract**: "Be careful with destructive operations"
- **Concrete**: "NEVER use `delete person`. Use group removal or note edits only. Reference: INCIDENT_CONTACT_DELETION_20260309"

Agents respond to **specificity and consequences**, not to vague caution.

## The Anatomy of an Incident-Driven Safety Rule

Every effective safety rule has four components:

```
1. PROHIBITION  — what is forbidden (specific command, pattern, or action)
2. CONSEQUENCE  — what happens if violated (data loss, propagation, corruption)
3. ALTERNATIVE  — what to do instead
4. REFERENCE    — the incident that motivated the rule
```

### Example 1: Data Destruction Prevention

```markdown
**NEVER use `delete person` in AppleScript.** This permanently destroys
contact data with no undo. iCloud propagates the deletion to every synced
device within seconds. Use group removal or note edits only.
(INCIDENT_CONTACT_DELETION_20260309)
```

- **Prohibition**: `delete person` command
- **Consequence**: Permanent data loss, propagated across all devices
- **Alternative**: Group removal, note edits
- **Reference**: Named incident with date

### Example 2: Destructive Mode Gate

```markdown
**Supervisor requires `--live` flag for FULL mode.** Default is SIMULATION.
Never start a live sync without explicit user confirmation.
```

- **Prohibition**: Running in live mode without the flag
- **Consequence**: Unintended data modifications
- **Alternative**: Default simulation mode (safe by default)
- **Reference**: Implicit — the flag exists because someone once ran it live by accident

### Example 3: Backup-Before-Write

```markdown
**Any script that writes to a contact note or field MUST create a timestamped
backup first.** Use the `backup_contact` pattern from `applescript_operations`.
```

- **Prohibition**: Writing without backup
- **Consequence**: Irreversible field modification
- **Alternative**: Timestamped backup before write
- **Reference**: General principle, reinforced by multiple incidents

### Example 4: Compilation After Edit

```markdown
**Recompile `.scpt` after editing `.applescript` sources.** The runtime loads
compiled `.scpt` binaries, NOT `.applescript` text sources. After any edit,
run `osacompile -o X.scpt X.applescript`. For multi-module projects,
recompile ALL modules.
(INCIDENT_B4_20260402, repeated ZAJDENWEBER_20260402)
```

- **Prohibition**: Editing source without recompiling
- **Consequence**: Runtime uses stale compiled version — edits appear to have no effect
- **Alternative**: Always recompile after edit
- **Reference**: Two named incidents (it happened twice before the rule stuck)

## Where Safety Rules Live

Safety rules belong in the **domain entry point** (e.g., `CLAUDE.md`), not buried in project docs. They must be loaded at session start — before the agent has a chance to violate them.

```markdown
## Domain Safety Rules
1. **NEVER use `delete person` in AppleScript.** [...]
2. **Supervisor requires `--live` flag for FULL mode.** [...]
3. **Backup before modification.** [...]
4. **Recompile `.scpt` after editing `.applescript` sources.** [...]
```

Number them. Keep them short. Every rule fits in 2-3 lines.

## The Incident Reference Pattern

Each rule references an incident by codename and date: `INCIDENT_CONTACT_DELETION_20260309`. This serves three purposes:

1. **Credibility**: The agent (and future maintainers) know this rule exists because something real happened, not because someone was being cautious
2. **Traceability**: If the rule seems outdated, you can find the incident report and evaluate whether the risk still applies
3. **Deterrence**: Named incidents carry more weight than anonymous warnings. "the 2026-03 contact-deletion incident" becomes part of the project vocabulary

You don't need a formal incident management system. A markdown file in the project's `BRAIN/` directory with a date, description, root cause, and resulting rule change is sufficient.

## Writing Rules That Survive Model Switches

When a new model starts a session, it reads the domain entry point and loads the safety rules. For rules to survive this context transition:

- **Be specific**: Command names, file patterns, exact flags
- **State consequences**: Not "this is dangerous" but "this will delete all contacts on all synced devices"
- **Provide alternatives**: The agent needs to know what to do *instead*
- **Keep them in one place**: A single numbered list in the domain entry point, not scattered across multiple files

## The Rule of Two Incidents

One incident is an accident. Two incidents with the same root cause is a pattern that demands a rule. If you find yourself explaining the same failure twice, stop and write the safety rule.

In the examples above, the compilation rule (Example 4) references two incidents. The second incident is what prompted codifying it — the first time it happened, it was fixed locally. The second time, it became a permanent rule.

---

## Template

```markdown
## Domain Safety Rules

1. **NEVER [specific action].** [Consequence in one sentence.] [Alternative.]
   ([INCIDENT_NAME_DATE])

2. **[Tool/process] requires [flag/confirmation] for [destructive mode].**
   Default is [safe mode]. Never [run destructive mode] without [explicit trigger].

3. **[Prerequisite action] before [risky action].** [Consequence of skipping.]
   Use [specific tool/pattern].
   ([INCIDENT_NAME_DATE])
```

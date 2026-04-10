# Operational Safety

When AI agents run alongside scheduled processes, cron jobs, or other agents, temporal collisions become a real failure mode. This document describes patterns for preventing them.

## The Collision Problem

A workspace with both **interactive agents** (responding to your prompts) and **scheduled agents** (running on cron/launchd) faces a category of bug that pure code review will never catch: two processes writing to the same resource at the same time.

Examples:
- An agent runs a database migration while a nightly ETL job is writing to the same tables
- An agent starts a long-running sync while a scheduled backup locks the data directory
- Two agents (one interactive, one scheduled) both modify the same config file

These don't crash cleanly. They produce silent data corruption, partial writes, and states that are extremely hard to diagnose after the fact.

## Pattern: The Forbidden Window

If your workspace has scheduled processes, define explicit time windows where certain operations are off-limits.

### Real example: The 02:40 AM Window

```markdown
## Operational Sync Safety

- **Collision Awareness**: The system has a critical daily task scheduled
  at 02:40 AM via LaunchAgent (background extraction + vectorization).
- **Forbidden Window**: Do NOT initiate large manual extraction or
  database-heavy tasks between 22:30 PM and 02:40 AM.
- **Reason**: Database (SQLite) and Vector Store (ChromaDB) contention
  will cause both processes to stall, requiring force-termination and
  manual recovery.
- **Pre-Task Check**: Always check for running background processes
  (`ps aux | grep <process_name>`) before starting a new one.
```

### Generalizing the pattern

Any Forbidden Window rule needs four components:

1. **What runs and when** — the scheduled process, its schedule, and what resources it touches
2. **The exclusion zone** — the time window where conflicts are possible (typically wider than the scheduled process's actual runtime to account for overruns)
3. **Why it matters** — the specific failure mode (data corruption, lock contention, race condition)
4. **The pre-flight check** — a concrete command the agent runs before starting work that would conflict

### Where to put it

Forbidden Window rules belong in the **domain entry point** (e.g., `CLAUDE.md`) or the **Tier 1 spec**, not in individual project docs. The agent needs to know about them *before* entering any project in that domain.

## Pattern: The Sentinel File

For critical processes that must not run concurrently, use a sentinel file that both the interactive agent and the scheduled process check:

```
logs/PROCESS_LOCK          — if present, a critical process is running
logs/CAPTCHA_KILL          — if present, automation has been suspended
data/MIGRATION_IN_PROGRESS — if present, schema is being modified
```

Rules:
- Before starting a conflicting operation, check for the sentinel
- If present, report to the user and wait — do not delete it
- The process that creates the sentinel is responsible for removing it on completion
- If a sentinel is stale (process that created it is no longer running), the agent may flag it but should NOT remove it without user confirmation

### Real example: CAPTCHA detection

An automation agent that scrapes external services can be interrupted by CAPTCHAs. When detected:

1. The agent writes a `CAPTCHA_KILL` sentinel file
2. The agent terminates with a specific exit code (e.g., 99)
3. The supervisor process recognizes exit code 99 as "CAPTCHA, stop everything"
4. No further automation runs until the user manually resolves the CAPTCHA and removes the sentinel

This prevents the agent from burning through rate limits or getting the account flagged by retrying against a CAPTCHA wall.

## Pattern: The Pre-Flight Process Check

Before any operation that touches shared resources, the agent should check what's already running:

```bash
# Check for running processes by name
ps aux | grep <process_name> | grep -v grep

# Check for lock files
ls -la logs/*.lock data/*.lock 2>/dev/null

# Check for PID files and validate them
if [ -f process.pid ]; then
    kill -0 $(cat process.pid) 2>/dev/null && echo "RUNNING" || echo "STALE PID"
fi
```

This belongs in ANTIGRAVITY.md §2 (Environment Detection) as part of the agent's session startup. Knowing what's running is as important as knowing what OS you're on.

## Pattern: Destructive Mode Gates

Operations that can cause irreversible damage should require an explicit flag to enable destructive mode:

```bash
# Default: simulation mode (read-only, reports what would happen)
python3 supervisor.py

# Explicit: live mode (actually modifies data)
python3 supervisor.py --live
```

The agent should NEVER add the destructive flag without explicit user confirmation. This is analogous to `--force` in git — the default must be safe, and the dangerous path must require conscious opt-in.

---

## Putting It Together

A well-defended workspace combines all four patterns:

1. **Forbidden Windows** prevent temporal collisions with scheduled processes
2. **Sentinel Files** prevent concurrent execution of conflicting operations
3. **Pre-Flight Checks** ensure the agent knows what's already running
4. **Destructive Mode Gates** ensure irreversible operations require explicit opt-in

These rules go in the domain entry point or Tier 1 spec — they must be loaded *before* the agent starts working, not discovered after a collision has already happened.

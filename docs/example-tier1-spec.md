# Example Tier 1 Spec: macOS Automation

This is a real Tier 1 specification used in production. It demonstrates what belongs in a platform-specific spec — the "hard truths" that agents must know before writing code for a particular technology domain.

Tier 1 specs are not style guides. They're **landmine maps** — the places where the platform will silently fail, corrupt data, or behave differently than the agent's training data suggests.

---

## 1. Versioning & Continuity
- **Atomic Increments**: Every functional update must increment the version by **0.0.1** (e.g., `0.3.1` -> `0.3.2`).
- **Institutional Memory**: Every script MUST contain a "Fortress Header" detailing Version, Purpose, Architecture, and Next Steps. This header is the first thing a new model reads during a cold-start rebase.

## 2. The ASOC Bridge Shield & Integrity

AppleScriptObjC (ASOC) bridges AppleScript to Cocoa frameworks. The bridge is powerful but unforgiving — type mismatches crash silently or produce garbage data.

- **Explicit Coercion**: ALWAYS use `as NSString`, `as NSArray`, or `as NSDictionary` when passing data into Cocoa handlers. AppleScript's implicit coercion is unreliable across macOS versions.
- **Defensive String Handling**: Never assume `split(",")` on an empty string returns `[]`. It returns `['']`. ALWAYS check `if not raw: return []`.
- **Compilation Check**: Every generated script must be verified via `osacompile -o /dev/null <script>` before delivery.

## 3. Storage & Backup Standards
- **File Extensions**:
  - Compiled: `.scpt` or `.app`.
  - Text Source (Source of Truth): `.applescript` (Never `.txt`).
- **Surgical Snapshot**: For database-altering scripts (Contacts, Mail, Calendar), implement an atomic backup *before* the first write. This is non-negotiable — one bad write to the Contacts database can destroy data that iCloud propagates to every device within seconds.

## 4. Universal Handler Protocol

When managing a fleet of AppleScript files, shared logic must be versioned and propagated carefully.

- **Signature Tagging**: Universal handlers must be tagged with a signature and version:
  - `-- SIGNATURE: [Handler_Name] | VERSION: [X.Y.Z]`
- **Managed-Push ONLY**: Propagation of updated handlers must NEVER be automated. It is a maintenance task requiring:
  - Scan for matching signatures across all scripts
  - Identification of outdated versions
  - One-at-a-time deployment with mandatory compilation and verification for each script
- **Micro-Service Handlers**: Shared handlers must be atomic and dependency-free. They carry their own internal coercions and error handling to prevent side-effects in consuming scripts.

## 5. Encoding Safety

- **Avoid Line Continuations**: Use explicit string concatenation (`set msg to msg & ...`) instead of the `¬` character for multi-line strings. The `¬` character frequently causes encoding errors during automated compilation — it looks correct to the eye but fails silently in many toolchains.
- **Verification**: Always inspect generated script text for `¬` before running `osacompile`.
- **Property List Awareness**: When reading/writing plist values, always coerce types explicitly. AppleScript's implicit coercion between `text`, `string`, and `Unicode text` is unreliable across macOS versions.

## 6. UX Standards
- **The Rule of Three**: Dialogs are capped at **3 buttons**. Complex options use the Emoji-Prefix Menu pattern (`choose from list` with descriptive emoji prefixes). Every dialog title MUST include `[ScriptName] ([Version])`.
- **Stealth Execution**: Shell/Python tasks invoked from AppleScript must run in the background (routed to logs), never popping up a terminal unless explicitly requested for interactive debugging.
- **Silence is Failure**: Every major loop start or logic branch must emit a log heartbeat. A script that produces no output has crashed.

---

## Why This Matters as an Example

Every technology domain has equivalents of these rules:

| macOS Automation | Web Frontend | Data Pipeline |
|---|---|---|
| `¬` encoding artifact | Template literal whitespace in URLs | Schema migration ordering |
| ASOC bridge coercion | CSS specificity in design systems | ORM type mapping |
| `osacompile` syntax gate | ESLint + TypeScript strict mode | `dbt test` before `dbt run` |
| iCloud propagation of bad writes | CDN cache invalidation | Replication lag after writes |
| Handler signature versioning | Component version in design system | API version negotiation |

A Tier 1 spec captures whatever the platform-specific equivalent of "the `¬` character looks fine but will crash your build" is. If you're writing a Tier 1 spec for your domain, ask: **"What has silently broken in this stack that we never want to debug again?"** — then write those answers down.

*Scope: macOS Automation (AppleScript, ASOC, Cocoa) | Tier: 1*

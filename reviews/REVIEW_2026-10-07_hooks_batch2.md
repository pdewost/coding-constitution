# Enforcement-Layer Review: Claude Code hooks, batch 2 (2026-10-07)

**Outcome:** the hooks that enforce the constitution in Claude Code went from v0.7 to v0.12 through **seven
external review rounds**. Each round was run by a model from a different lab than the author's. Every
finding was reproduced as a failing test before it was fixed. The later versions were also replayed against the
full command history of a real workspace before they went live (v0.12: 253,414 commands, with the guard's and the stop
gate's decisions identical to the previous live version). The constitution itself did not change: every gap
found was in enforcement, one layer down, which is where the Sorting Rule says gaps belong.

## Why

The constitution says a rule you can't enforce is a rule you don't have. This batch added three
enforcements that had been prose:
- Art. 14's loop bound (stop after three fix-and-verify cycles);
- a stop gate that counts only real verification;
- a tamper report on the hooks themselves.

New enforcement code is new attack surface, so it went through review before and after landing.

## How it was run

| Round | Reviewer | Result | Led to |
|---|---|---|---|
| 1, 2b, 3 | external reviewer A | findings absorbed during the build | v0.7 to v0.8 |
| 4 | external reviewer A | NEEDS CORRECTIONS: 31 findings | v0.9 |
| 5 | external reviewer A, on the *landed* hooks | NEEDS CORRECTIONS: 2 regressions introduced by the v0.9 fixes | v0.10 |
| 6 | Google Gemini | findings accepted; the run itself was voided by rule (it wrote outside its seat) | v0.11 |
| 7 | OpenAI Codex (confined: no tools, everything inlined) | 3 findings that also held on the live hooks, one a regression of round 6's fix | v0.12 |

Each reviewer worked from a frozen "seat" file. Its hash was taken before the run and checked after it, so a
reviewer that touched anything but its seat had its run voided, as round 6's was. Its findings still stood,
because they could be checked independently.

## What the review found worth keeping

- **Fixes create the next bugs.** Round 5 found two regressions caused by round 4's fixes. Round 7 found one
  caused by round 6's. Reviewing only the original code would have missed all three. A re-check of the fix
  diff is not optional.
- **The cheapest finding was in packaging, not logic.** Three registered hooks had shipped without the
  executable bit, so for adopters they never ran. A test suite run from the author's own tree could not see
  that. Running the install exactly as an adopter would (`bootstrap.sh --verify-only`) did.
- **Severity drops fast once the method holds.** On a related build (a root-owned variant of these hooks, not
  yet published), three successive Codex rounds found 19, then 9, then 6 issues. None survived triage as HIGH
  after the first round, and the last round's findings were all in ask-only checks, documentation, a test, or a
  path Claude Code does not reach (a relative working directory).
  That is the stopping rule: more rounds stop paying, and live use becomes the better reviewer.

## What did not change

- **The constitution:** 14 articles, unchanged.
- **The honest scope:** the hooks are defence in depth, not a sandbox. They run as the same user as the agent.

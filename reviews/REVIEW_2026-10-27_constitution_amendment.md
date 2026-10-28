# Constitutional Review — 2026-10-27

**Outcome: four changes ratified and applied the same day. The constitution now stands at 190 of its
190-line budget.** Release v0.13 of this repository carries the amended text and the documentation fixes
listed at the end.

## Why the review happened

Art. 11 names two standing triggers. One fired: a **logged `CONTRADICTION:` against L0 itself**. A
lower-layer rule allowed erasing one named person's records from a conversation archive, because third
parties never consented to a never-delete rule by being written about. Art. 6 said archives "are never
deleted", and L0 forbids lower layers to contradict it. A critical review round (a blind audit, then
adversarial audits) had also left a docket of questions only a Constitutional Review may settle.

## How it was run

Every clause got its own ruling: the Preamble, the Purpose, the Layers table with the Sorting Rule,
Articles 1 to 14 and Amendment I, 18 ids in all. A coverage script had to exit 0 before the sitting closed:
ruled ∪ deferred must equal all 18 ids. The 2026-08-25 review showed why: a batch ratification nearly lost
the article governing amendments itself.

- **Text changed:** 6 of the 18 clauses, by the four changes below (the vendor-name change alone touches three).
- **Kept as written:** the other 12. Several were kept *with a reading*: an interpretation recorded below L0,
  where Art. 11 lets it live without spending a line of prose.

## The four changes

1. **Art. 6, consented deletion.**
   - Before: "Archives are never deleted, and their existence is always declared …".
   - After: "Archives are never deleted without the user's explicit consent, recorded in the project's
     NEOCORTEX, naming the archive and the operation, and their existence is always declared …".
   - "Never" stays the default; a recorded, scoped consent is the only door.
2. **The Sorting Rule.**
   - Before: "A rule earns L0 prose only if it cannot be expressed as a hook, a permission, a test, or a skill."
   - After: "L0 holds durable obligations, with the bounds they state, and authority boundaries; their
     procedures, checks and tunable criteria belong in lower layers."
   - Read literally, the old rule would have expelled parts of L0 that are already mechanized, such as
     Art. 14's three-cycle bound (it has a hook) and the line budget itself (a script counts it).
3. **Dated asides removed.** The line-budget history moved out of the text: a perishable fact, which the
   Sorting Rule bans.
4. **"Instruction file" instead of a vendor's file name**, in three places. The Preamble binds "mechanisms
   that depend on no single vendor", and these were the only vendor name left in L0.

Two readings were recorded with the changes, below L0:
- **The line budget is a cap:** an amendment that keeps L0 within 190 lines needs no offset.
- **A project with several rules files:** its instruction file is the one the others point to; if none
  points to another, a required record goes in each.

## External review before applying

The draft went to a reviewer from a **different model lineage** than the drafter, twice, before the author
applied it.

| Round | Reviewed | Verdict | Findings | Outcome |
|---|---|---|---|---|
| 1 | the draft patch | APPLY WITH FIXES; all four rulings carried | 6 (0 high, 4 medium, 2 low) | all 6 accepted; text revised |
| 2 | the revised text | APPLY WITH FIXES | 5 closed, 1 partly closed; 2 new (1 medium, 1 low) | both accepted: one wording fix, one reading |

The findings are worth more than the verdicts. **Each was found by the external reviewer, and none by the drafter:**
- "consent naming the subject" could name a **person or an archive**, and the two readings give the consent a
  different reach;
- the new Sorting Rule's own words could have expelled the thresholds the sitting meant to keep;
- the budget clause read two ways;
- a phrase placed after "the operation" could leave the consent itself unrecorded on one reading.

## How it was applied

The agent that drafted the patch cannot write L0: the file carries an operating-system immutable flag, and
a guard refuses edits to it. The author applied it by hand from a terminal:
1. check the file's hash against the pre-image the patch was built on;
2. clear the flag;
3. apply the patch;
4. check the post-image hash;
5. set the flag again;
6. commit.

The agent then verified the committed text: hash, line count, and no vendor name left. (The copy published here
differs by one declared redaction, R5: Amendment I's enforcement pointer names the shipped guardrail hook, whose path
contains the adapter's name.)

## Also in v0.13 (documentation and kit fixes)

- **The kit is no longer half-wired.** Shipped hooks load four things from the adopter's workspace: the cold-start
  handoff resolver, the session-anchor renderer, the verification-receipt checker (`_skills/verify_gate/`) and the
  task-class lint. They ship, and `bootstrap.sh` now places all four where the hooks look (r2; r1 placed only the
  first two). The monthly audit the spec names is not shipped: it depends on the author's private layout.
- **The README's policy count** comes from `spec/POLICY_CORE.md` (13 rows, one retired; CONTEXT-GUARD has no
  kit hook). The "nine policies" text had regressed as the file grew.
- **COMPILE-GATE's JavaScript coverage** (`node --check`) is documented; the README had said there was none.
- **The quickstart** now seeds the example machine config and re-approves before the self-test. Without it,
  one of the 37 cases failed on a fresh clone; the README had also said 23 cases.
- **`routing_policy.example.yaml`** parses: the v0.12 copy did not. It now follows the
  author's v1.5 shape, adding review-independence levels and what a valid consent to a destructive operation is.
- **The Declaration** no longer says the kit ships a git hook and CI: it does not. The law becomes
  un-bypassable only where an adopter runs it at a shared remote.
- **Amendment I's enforcement pointer** resolves: it now names the shipped guardrail hook instead of a
  spec section that does not exist.

## The bundle's own external review (2026-10-28)

Before the first push of v0.13, the bundle itself went to an external reviewer of another model lineage. Verdict: do not
push yet, with 7 high, 15 medium and 7 low findings; none was rejected. The second candidate (r2) fixed:
- **The adopt sequence:** the steps are reordered so that projects get their state before they are armed.
- **What the hooks load:** the install places everything the hooks load from the workspace.
- **The self-test:** it reads the forbidden window from the adopter's own config instead of the author's.
- **Python:** the prerequisite is stated as 3.12, the version the syntax check needs.
- **The secrets gate:** it now blocks on commit identities and commit messages, and no longer carries the author's
  personal strings.
- **History:** a third party's surname that had survived in this repository's history was removed by a history rewrite,
  and every commit was re-authored with a no-reply address.

A second external review (a delta review of the third candidate, same day) found one more problem: an unpublished
intermediate commit still held a script with facts of the author's machine (a home path, a job's run time, an
interpreter remark), deleted only at the tip. The fourth candidate (r4) folds the unpublished v0.13 commits into one, so
that state is never published, and it fixes five smaller findings: machine facts in five comments (already public in
earlier versions), a gate that passed without its private term list, identity and message scans that read refs a push
does not publish, and stale labels.

A third external review (a delta review of r4) found no high finding and four small ones, fixed in the fifth candidate
(r5): the upgrade steps did not copy three scripts the hooks run; a stock interpreter version was left in three comments;
this record miscounted the comments above; and names from the author's workspace that the code and docs still use (all
public in earlier versions) were not declared. They are now declared, not renamed: see the README's status section and
`redaction_map.yaml`.

A fourth external review (a delta review of r5) and an internal read of the same change found no high finding and six
small ones, fixed in the sixth candidate (r6): the policy file named the self-test at a path this layout does not have;
the list of kept names was incomplete, and so was the command that lists them (r6 extended both; r7 replaced the list
with a declared class); a stale date; a private record cited without its mark in the policy table; and `bootstrap.sh`
said "placed" when it kept an existing copy of a script.

A fifth external review (a delta review of r6) found five more, none a new exposure, fixed in the seventh candidate
(r7): three rule descriptions in `redaction_map.yaml` quoted the author's window times their rules
remove (public in earlier versions); the kept-names entry claimed a completeness no list can show, and now declares a
class with examples; two record citations in the review skill lacked their mark; and the README's clone URL carries the
account name, now declared.

A sixth external review (a delta review of r7) found no high finding and, once checked, three small ones, fixed in the
candidate published here, the eighth (r8): the quickstart said the self-test's window case fails without the machine
config, which has not been true since r2 (all 37 cases pass); the exposure section of `redaction_map.yaml` did not say
it also declares two names the current tree keeps on purpose; and this record credited r6 with completing the
kept-names list, which r7 replaced with a class.


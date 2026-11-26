---
name: verify_gate
version: 0.5.0
kind: library
status: active
description: Harness- & coder-neutral VERIFY-GATE — validates an independently-produced, externally-attested verdict (LLM, subagent, or human) and blocks only when independence is anchored OUTSIDE the producing agent; v3 receipts bound to a fingerprinted review package (a read-only snapshot the reviewer is handed, whose fingerprint the verdict repeats), re-derived from the verdict and the attestation at every validation, one receipt per verdict, spent once by the CLI under a locked, fail-closed, per-project ledger.
---

DISPATCH-CLASS: adversarial@high
<!-- Art. 9 (declare task classes, never model names), enforced by
     governance/scripts/lint_dispatch_class.py. WHY: the primary anchor is a harness-spawned fresh-context reviewer subagent whose verdict can BLOCK. Independence is the mechanism, so the class is `adversarial`, never `mechanical`. -->

# verify_gate

The mechanical form of "Assume Independent Audit" (ANTIGRAVITY §15) — pure Python + shell,
no harness plugin. Built advisory-first after 3 §3 adversarial-review rounds
(`governance/PLAN_verify_gate_and_ux_verify_2026-06-20.md` (private, not shipped) REV 4 CONVERGED).

## The one idea

**A single in-context agent cannot mechanically verify itself.** Every self-written control
recurses to a field the policed party writes. So the trust anchor must sit **outside the
producing agent's address space**:

| Anchor | Blocking? |
|---|---|
| `harness_subagent` (a fresh subagent spawned outside the producing agent's context), `stateless_api`, `ci` (a CI run is anchored by its own id, never by an environment variable), `human_external` | **mechanical — can block** |
| `self` / `same_context` / `human_self` (inside the agent's reach) | **advisory only** |

The load-bearing rule: a verdict can BLOCK only when its **attestation** (written out-of-band
by the orchestrator, never by the judge) has an external `anchor_kind` **and `attestor !=
producer`.** A faked self-spawn sets `attestor == producer` ⇒ collapses to advisory. The lie
doesn't help.

## Public interface (`scripts/`)

- `core.evaluate(verdict, attestation, *, require_blocking=False, judge_unreachable=False) -> Decision`
  — the pure gate. Exit contract: `0` pass/advisory · `1` block · `2` identity (producer==drafter)
  · `3` unanchored (blocking required but advisory-only) · `4` judge-unreachable · `5` tamper
  (self-asserted attestation inside the verdict) · `6` receipt invalid.
- `verify_gate.py` — the CLI every adapter shells:
  `verify_gate.py --verdict V --attestation A [--require-blocking] [--changed f… --write-receipt R --package D/package.json] [--override "<reason>"] [--judge-unreachable]` (`--write-receipt` without `--package`, or without a `--changed` file, writes nothing, exit 6; on a PASS that is not issuable, such as an unanchored or v1 review, no receipt is written and the exit is the gate's decision), or `verify_gate.py --changed f… --receipt R` (validate and spend a receipt). The receipt mode serves **armed roots** only (a project holding `NEOCORTEX/` and `.verify/armed`): the ledger is always `<project>/.verify/_consumed_nonces.json`, derived from the changed files by `receipt.project_root`, and the CLI takes no ledger option. Exit 6 with a `decision` of "no project root", "ambiguous project root" or "armed state unreadable" when no single armed root can be named. A relative `--changed` path is joined to the cwd with a plain separator join (no folding of `./` or `..`); issuance refuses a path validation would refuse (a link in any component, a FIFO, a folder, a hard-linked file, a file over 64 MiB or one that changes while it is read): exit 6, no receipt.
- `review_runner.run_review(...)` — the orchestrator: seals the bundle, spawns the judge,
  **writes the attestation out-of-band**. `harness_subagent | stateless_llm | human` backends;
  `judge_fn` injected (the real model call / harness spawn / form).
- `review_package.build(changed_paths, outdir)` — the **review package** (receipt-v3): read-only (0444) copies of the change-set
  in `outdir/package/<i>` and `outdir/package.json` (`verify_gate.package.v1`). Its fingerprint `package_sha256` is the sha256 of
  the canonical JSON of the `files` list; it is never stored, every reader derives it (`receipt.read_package`).
- `receipt.py` — `write_receipt` / `validate_receipt` / `validate_and_consume` / `consume` / `project_root`: a v3 VERIFY-RECEIPT
  bound to a **review package** and **time-fresh** (a `ts` over 300 s ahead is invalid); it records `verdict_ref`,
  `verdict_sha256`, `attestation_ref`, `attestation_sha256`, `package_sha256` and `package_ref`, and validation re-derives the
  decision from those two files (hashes equal, `core.evaluate` PASS, no block finding, block-eligible). Three package checks run
  at issuance and at every validation: the package's fingerprint equals the verdict's, the attestation's and the receipt's (and
  `package_ref` resolves to the attestation's); the package's paths include every changed path (a superset passes, a subset
  fails); every package file still hashes to its entry. Issuance needs a `verify_gate.verdict.v2` and a
  `verify_gate.attestation.v2`; a verdict whose `package_sha256` differs from the attestation's is tamper (exit 5). **One verdict
  earns one receipt:** the ledger records spent verdicts, and a spent verdict is refused at issuance and at validation. **Single-use: the CLI
  spends it through `verify_gate.py --receipt`; the hooks check without spending, so inside its window a receipt
  satisfies every Stop over the same unchanged change-set until it is spent once, by the CLI included: the CLI spends
  into `<project>/.verify/_consumed_nonces.json`, the ledger the Stop gate reads, and the next Stop refuses it.** `validate_and_consume` validates and spends under an exclusive `fcntl.flock` on a sidecar
  `<ledger>.lock` and writes the ledger atomically (temp file, `fsync`, `os.replace`, folder `fsync`), so of
  concurrent runs on one receipt at most one passes. The ledger is `{"nonces": [...], "verdicts": [...]}` (a 0.4.0 list of nonces
  is read as nonces and rewritten in the object shape at its next consume). It fails closed: absent = empty; present but
  unreadable or malformed = every receipt invalid, never rewritten (move it aside to restore service, which
  also reopens every nonce it held). A receipt whose nonce is missing or not of the `uuid4().hex` form is
  invalid. Platform: `flock` is advisory and unreliable on some network filesystems; without `fcntl` (not
  POSIX) `validate_and_consume` refuses. Edit-after and crash re-block; resume and fresh re-block once the
  receipt is spent or stale.
- `evidence_bundle.seal(*, artifact, evidence, package)` — strips intent/answer-key from what a judge may see; `package` (the
  review package's `package_sha256` and `snapshot`) is required, and the bundle tells the judge to read only the snapshot and
  repeat the fingerprint.
- `verdict_form.build_verdict(...)` / `--out` CLI — the **human** path; a human verdict blocks
  only via a substantiated finding (`evidence.ref` file:line **+** `evidence.literal` quote).
- `produce_review.produce(...)` / CLI — **the PRODUCER** (runs a review end to end: package → seal → judge →
  out-of-band attest → gate → receipt). `produce_review.py prepare` / `finish` — the two-step hand-over for an agent-spawned
  reviewer (see "Running a review"). `judges.stateless_llm_judge(...)` — the stateless
  fresh-context judge (pluggable `call_fn`; the default needs a local_llm-style client — not self-contained, inject your own; strips any
  self-attestation a model emits).

## Running a review

**Primary — harness-spawned subagent (mechanical, agent-driven), in two steps.**

```bash
produce_review.py prepare --changed f1 f2 --outdir DIR     # builds DIR/package/ + DIR/package.json; prints the reviewer
                                                           # brief and a JOURNAL line listing the package's paths
# spawn a fresh-context reviewer subagent with the brief: it reads only DIR/package/ and returns a
# verify_gate.verdict.v2 that repeats the package_sha256; save it as V.json
produce_review.py finish --verdict-json V.json --outdir DIR --orchestrator-id <the spawning agent> --anchor-id <spawn id>
```
`finish` keeps the verdict's bytes as returned, attests it (stamping the package), evaluates it and, on an issuable PASS, writes
`DIR/receipt.json` over the package's paths. Because the orchestrator attests and `attestor != producer`, the verdict can BLOCK.
`produce_review.produce(..., agent_verdict=...)` still runs end to end, but its package is built inside the call, so a verdict
prepared beforehand cannot repeat its fingerprint and earns no receipt; use `prepare` / `finish`.

**Rule for agents — a receipt describes the whole change-set (RV3-10).** Issue the receipt over every file you changed under the
armed root this session, whether or not the Stop gate lists it. A receipt over your full change-set satisfies a gate that lists
fewer files. Never narrow a receipt to match a smaller list. If the gate blocks on a path your receipt lacks, review that file and
re-issue over the wider set, or stop and report BLOCKED with both lists. (Example: under 0.4.0 the typeface project's 4-file
receipt failed against the gate's 1-file list, which pushed the session to narrow it.)

**Fallback — stateless model (CI / hooks; pass your own `call_fn`):**
```bash
produce_review.py --artifact X --evidence-json E.json --backend stateless_llm \
  --producer-id reviewer --drafter-id author --orchestrator-id ci --anchor-id "$RUN_ID" \
  --changed f1 f2 --outdir DIR
```

## Honest scope (stated, not overclaimed)

- Mechanical only with an external anchor; **advisory** everywhere else.
- The enforcement floor is un-bypassable only **where a shared remote + CI exists**; locally it
  is the in-session adapter + a `--no-verify`-bypassable `pre-commit` (defence-in-depth).
- Human independence is **by identity** (an external producer id), not context-denial.
- Receipt forgery is possible on a local box — it raises the bar from "echo a magic string"
  to "produce a real attested verdict + matching-change-set receipt," not a crypto guarantee. Honest limits of a receipt:
  on a local box a determined agent can still forge the whole evidence set (attestation, verdict, receipt), and single use is per project root: moving the ledger aside reopens every spent nonce. R-1 to R-4 close the accidental paths named above; they do not raise the cryptographic floor.
  A receipt proves the reviewer was handed a snapshot whose files cover the change-set with matching bytes, and one verdict earns one receipt per project ledger once the CLI spends it; it does not prove the reviewer read every file of the snapshot. Validation reads the changed files one after another, so an edit made during validation can pass. The Stop hook sees edits as the transcript spells them: a write under a scratch or temp exemption (an alias or a `..` escape into the project included), a `cd` inside a command other than a plain `&&` chain, an unexpanded variable, a file written by an interpreter (inline code or a script), or a link changed before the turn ends can escape the armed check. The hooks' import guard does not authenticate modules already in sys.modules; it refuses only an unverified module cached under a scripts-file name whose file lies in the scripts folder. The CLIs have no import guard.

## Status / consumers

`active` · advisory-mode only (promoted from incubating 2026-07-05 — its own `--lint` counts 6 real consumers). **Gate + producer both built** (the gate validates; the producer
runs reviews — harness-subagent primary + stateless-llm fallback; the fallback's default model client is a
`local_llm`-style client that is not shipped: without your own `call_fn` the producer, and its CLI's default
`--backend stateless_llm`, end at exit 4 `judge_unreachable` with a reason naming `call_fn`). `POLICY_CORE += VERIFY-GATE` is
landed (live, v1.1). **Wired as the `verify_advisory` Claude Code adapter hook (advisory-mode), deployable via `install_adapters`; blocking is per-project opt-in (`.verify/armed` + a wired reviewer + a proven change-set receipt).**
Next (founder-gated): per-harness adapters + the advisory fleet push; the change-set receipt in
`CLOSEOUT-GATE`. Tests: `python3.12 -m pytest _skills/verify_gate/tests/ -q` (**235 cases** at 0.5.0).

## Changelog

- **0.5.0** (2026-11-14; `governance/NEOCORTEX/PLAN_verify_gate_receipt_v3_2026-11-13.md` (private, not shipped) v0.4, RV3-1 to RV3-10; consumers:
  `governance/NEOCORTEX/NOTE_verify_gate_0.5.0_consumers_2026-11-14.md` (private, not shipped)). Closes three gaps: a receipt bound to any later
  change-set (G3-1), one verdict earning any number of receipts (G3-2), an edit made during the review going undetected (G3-3).
  New: `review_package.py` (`build`, `read`, `fingerprint`); `receipt.read_package`, `receipt.package_fingerprint`; schemas
  `verify_gate.package.v1`, `verify_gate.verdict.v2` and `verify_gate.attestation.v2` (each adds `package_sha256`; the
  attestation adds `package_ref`), `verify_gate.receipt.v3` (adds `package_sha256`, `package_ref`, `ledger`).
  Signatures that change: `evidence_bundle.seal(package=)` is required; `verify_gate.py --write-receipt` needs `--package`;
  `produce_review.py` gains `prepare` / `finish`; `produce_review.produce(agent_verdict=)` no longer issues without a package
  whose fingerprint the verdict repeats; `receipt.write_receipt` gains `package_path` (required) and `ledger_path`;
  `review_runner.attest` is split out of `run_review`; `judges` asks for a v2 verdict and marks a reply v2 only when it repeats
  a `package_sha256` (a non-empty string) or declares v2 (a declared v2 is never relabelled v1: core then refuses its missing or
  malformed sha). `produce_review.produce` reports an unsafe changed path, a reused `outdir` or any other `outdir` error as a
  refusal (exit 6) before any judge runs, and, on a PASS, a receipt that `write_receipt` refuses or that cannot be written as
  exit 6; a PASS that cannot earn a receipt (unanchored, a v1 verdict) keeps exit 0, as `verify_gate.py --write-receipt` does,
  with "no receipt written: <reason>" in `reasons` (founder card 2026-11-26, "Keep exit 0, fix wording"); with no changed path
  it is a gate-only run. `finish` exists only to issue a receipt, so there every PASS without one is exit 6; it reports an
  unsafe or unreadable package, an unsafe verdict file and a failed receipt write as exit 6 (a verdict that is absent or not a
  JSON object stays exit 5, tamper, as in `verify_gate.py`), and reads a verdict through symlinked parent folders, resolved as
  the kernel does (`link/..` is the link target's parent), while a verdict file that is itself a link stays refused. `finish`
  writes its `verdict.json` copy, and both write `receipt.json`, without following a link (s520 M1-M3, s519 F1-F3);
  `produce`'s `verdict.json` is written by `review_runner.run_review` into the `outdir` it has just created.
  `write_receipt` refuses an empty change-set ("receipt over no changed path") and resolves its default ledger over the
  changed paths and the package's paths together; the package checks also verify each snapshot copy (`package/<i>`: present,
  a regular single-link file, sha256 and size equal to its entry) and each live file's size (s329 fixes F-1 to F-4, F-6).
  Every shape that turns invalid: a **v2 receipt** (and a v1) — "re-issue under v3"; for issuance, a **v1 verdict** or a **v1
  attestation**; a v2 verdict whose `package_sha256` differs from the attestation's (tamper, exit 5); a package whose
  fingerprint, `package_ref`, paths (a subset of the change-set) or file bytes no longer match; a verdict already spent
  ("verdict already spent: one verdict earns one receipt"); the **list-shaped ledger** after its first v3 consume (it is
  rewritten as `{"nonces", "verdicts"}`); an object ledger with any other key or a malformed entry. v2's change-set equality is
  removed: a receipt whose package is wider than the gate's change-set now passes (RV3-10).
- **0.4.0** (2026-11-10; `governance/NEOCORTEX/PLAN_verify_gate_receipt_v2_2026-11-02.md` (private, not shipped) v0.7, R-1 to R-5; consumers:
  `governance/NEOCORTEX/NOTE_verify_gate_0.4.0_consumers_<date>.md` (private, not shipped)). Schema `verify_gate.receipt.v2` adds `verdict_ref` and
  `attestation_sha256` (refs absolute, `realpath`-normalised at issuance); API: `receipt.project_root`, `receipt.qualify_path`,
  `receipt.UnsafeFile` added; `changeset_sha256` raises `UnsafeFile`. CLI: `--ledger` removed; the receipt mode serves armed
  roots only; `produce_review.produce` returns `receipt_refused` (a reason) when it withholds a receipt. Every shape that turns
  invalid (all return `(False, reason)` without consuming; the CLI exits 6): a **v1 receipt** ("re-issue under v2"); a verdict or
  attestation whose bytes changed since issuance (`verdict_sha256` / `attestation_sha256` mismatch, a whitespace re-serialisation
  included); a decision that no longer re-derives (a HIGH refutation, a producer mismatch, no external anchor); a `verdict_ref`
  or `attestation_ref` that is missing, a symlink (or behind a symlinked component), a FIFO, hard-linked, over 64 MiB, or changed
  while being read; a changed path that is a symlink, behind a symlinked component (`link/../x.py` included), a FIFO, a folder, a
  hard link, over 64 MiB or changed while read; a ledger that is a symlink or FIFO or reached through one; a receipt copied or
  spent under another ledger (replay; the ledger is the project's); changed paths with **no project root**, an **ambiguous
  project root** (two armed roots) or an **armed state unreadable** (a loop, a permission error, a link chain over 40, a
  wrong-type `NEOCORTEX`, `.verify` or `armed`). At issuance the same shapes raise `UnsafeFile`: no receipt, exit 6. A receipt is
  no longer issued for a self-attested HIGH refutation or an un-anchored clean verdict, and `CI=1` (or `GITHUB_ACTIONS`,
  `GITLAB_CI`, `BUILDKITE`) no longer anchors a review as `ci`. The CLI's `receipt_valid` line says the receipt is spent.
- **0.3.1** (2026-11-07; `governance/NEOCORTEX/_prep/fs1b_plan_v01_approved_text_2026-11-07/PLAN_verify_gate_031_s218_fixes_v0.1_approved_text.md` (private, not shipped),
  the nine LOW findings of seat s218, VG-1 to VG-9). Receipt format, `validate_receipt` signature and read-only
  contract, and the CLI exit codes are unchanged. Outcome changes, all toward invalid or toward not hanging: a
  non-finite `ts` (NaN, +inf, -inf) is "bad receipt ts"; a ledger with `st_nlink != 1` (a hard-linked name) is
  unreadable; a ledger folder that is a symlink is refused on the write path; a lock file replaced while held
  refuses the write before the commit point; the lock wait ends after `LOCK_TIMEOUT_S` = 30 s with
  "nonce ledger lock timeout" (exit 6); `RecursionError`, `MemoryError` and any `OSError` while preparing the lock
  are `LedgerError`, so exit 6 with no traceback; a FIFO or other non-regular file in the change-set hashes as
  `<non-regular>` instead of hanging; on Darwin the temp file and the folder are synced with `F_FULLFSYNC`
  (falling back to `os.fsync`). Single use holds per ledger: of N concurrent runs on one receipt on one ledger,
  at most one passes.
- **0.3.0** (2026-11-02; `governance/NEOCORTEX/PLAN_fix_external_reader_claims_verify_gate_2026-10-30.md` (private, not shipped) v0.6,
  FS-1 and FS-3). API: `receipt.validate_and_consume` added (the CLI's one locked validate-and-spend call);
  `receipt.LedgerError` added; `receipt.consume` kept, now on the same locked atomic write, and it raises
  `LedgerError` on a malformed nonce, a corrupt ledger or a failed write; `judges.stateless_llm_judge` raises
  where it returned `None` when `call_fn` raises; `review_runner.run_review` and `produce_review.produce` return
  a `reason` with `judge_unreachable`. Outcome changes: a corrupt ledger (unreadable, not a JSON list, or holding
  anything but `uuid4().hex` nonces) invalidates every receipt, where it read as empty; a receipt with a missing
  or malformed nonce, a boolean `ts`, or a `ts` over 300 s ahead, goes from valid to invalid; a symlinked or
  non-regular ledger (a directory, a FIFO) invalidates every receipt, where a symlink was followed and a directory
  read as empty; `verify_gate.py --receipt` exits 6
  on any ledger failure. Consumers: `governance/NEOCORTEX/NOTE_verify_gate_0.3.0_consumers_<date>.md` (private, not shipped).
- **0.2.0** and earlier: no changelog was kept (`_skills/UNIVERSAL_SKILL_SPEC.md` R-SHARED-6 mandates one).

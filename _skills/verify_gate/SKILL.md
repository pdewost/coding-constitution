---
name: verify_gate
version: 0.3.1
kind: library
status: active
description: Harness- & coder-neutral VERIFY-GATE — validates an independently-produced, externally-attested verdict (LLM, subagent, or human) and blocks only when independence is anchored OUTSIDE the producing agent; change-set-bound receipts, spent once by the CLI under a locked, fail-closed ledger.
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
| `harness_subagent` (a fresh subagent the agent can't forge), `stateless_api`, `ci`, `human_external` | **mechanical — can block** |
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
  `verify_gate.py --verdict V --attestation A [--require-blocking] [--changed f… --receipt R --ledger L] [--write-receipt R] [--override "<reason>"] [--judge-unreachable]`
- `review_runner.run_review(...)` — the orchestrator: seals the bundle, spawns the judge,
  **writes the attestation out-of-band**. `harness_subagent | stateless_llm | human` backends;
  `judge_fn` injected (the real model call / harness spawn / form).
- `receipt.py` — `write_receipt` / `validate_receipt` / `validate_and_consume` / `consume`: a VERIFY-RECEIPT
  bound to a **change-set hash** and **time-fresh** (a `ts` over 300 s ahead is invalid). **Single-use: the CLI
  spends it through `verify_gate.py --receipt`; the hooks check without spending, so inside its window a receipt
  satisfies every Stop over the same unchanged change-set until it is spent once, by the CLI included: for a
  receipt stored in `<project>/.verify/` the CLI spends into the ledger the Stop gate reads, and the next Stop
  refuses it.** `validate_and_consume` validates and spends under an exclusive `fcntl.flock` on a sidecar
  `<ledger>.lock` and writes the ledger atomically (temp file, `fsync`, `os.replace`, folder `fsync`), so of
  concurrent runs on one receipt at most one passes. The ledger fails closed: absent = empty; present but
  unreadable or malformed = every receipt invalid, never rewritten (move it aside to restore service, which
  also reopens every nonce it held). A receipt whose nonce is missing or not of the `uuid4().hex` form is
  invalid. Platform: `flock` is advisory and unreliable on some network filesystems; without `fcntl` (not
  POSIX) `validate_and_consume` refuses. Edit-after and crash re-block; resume and fresh re-block once the
  receipt is spent or stale.
- `evidence_bundle.seal(...)` — strips intent/answer-key from what a judge may see.
- `verdict_form.build_verdict(...)` / `--out` CLI — the **human** path; a human verdict blocks
  only via a substantiated finding (`evidence.ref` file:line **+** `evidence.literal` quote).
- `produce_review.produce(...)` / CLI — **the PRODUCER** (runs a review end to end: seal → judge →
  out-of-band attest → gate → receipt). `judges.stateless_llm_judge(...)` — the stateless
  fresh-context judge (pluggable `call_fn`; the default needs a local_llm-style client — not self-contained, inject your own; strips any
  self-attestation a model emits).

## Running a review

**Primary — harness-spawned subagent (mechanical, agent-driven).** The agent spawns a fresh-context
reviewer subagent (Agent/Task tool or a workflow) over the sealed evidence, then finalizes:

```python
out = produce_review.produce(
    artifact=..., evidence=[...], drafter_id="<author>", producer_id="<subagent-id>",
    backend="harness_subagent", orchestrator_id="<the spawning agent>", anchor_id="<spawn id>",
    agent_verdict=<the subagent's verdict dict>, changed_paths=[...], outdir=...)
```
Because the orchestrator (the spawning agent) attests and `attestor != producer`, the verdict can BLOCK.

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
  to "produce a real attested verdict + matching-change-set receipt," not a crypto guarantee.

## Status / consumers

`active` · advisory-mode only (promoted from incubating 2026-07-05 — its own `--lint` counts 6 real consumers). **Gate + producer both built** (the gate validates; the producer
runs reviews — harness-subagent primary + stateless-llm fallback; the fallback's default model client is a
`local_llm`-style client that is not shipped: without your own `call_fn` the producer, and its CLI's default
`--backend stateless_llm`, end at exit 4 `judge_unreachable` with a reason naming `call_fn`). `POLICY_CORE += VERIFY-GATE` is
landed (live, v1.1). **Wired as the `verify_advisory` Claude Code adapter hook (advisory-mode), deployable via `install_adapters`; blocking is per-project opt-in (`.verify/armed` + a wired reviewer + a proven change-set receipt).**
Next (founder-gated): per-harness adapters + the advisory fleet push; the change-set receipt in
`CLOSEOUT-GATE`. Tests: `python3.12 -m pytest _skills/verify_gate/tests/ -q` (**69 cases**).

## Changelog

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

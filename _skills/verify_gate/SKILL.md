---
name: verify_gate
version: 0.2.0
kind: library
status: incubating
description: Harness- & coder-neutral VERIFY-GATE — validates an independently-produced, externally-attested verdict (LLM, subagent, or human) and blocks only when independence is anchored OUTSIDE the producing agent; change-set-bound single-use receipts.
---

# verify_gate

The mechanical form of "Assume Independent Audit" (ANTIGRAVITY §15) — pure Python + shell,
no harness plugin. Built advisory-first after 3 §3 adversarial-review rounds
(`governance/PLAN_verify_gate_and_ux_verify_2026-06-20.md` REV 4 CONVERGED).

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
- `receipt.py` — `write_receipt` / `validate_receipt` / `consume`: a VERIFY-RECEIPT bound to a
  **change-set hash**, **single-use** (consumed-nonce ledger), **time-fresh** — closes the four
  NEOCORTEX loss modes (edit-after / resume / fresh / crash all re-block).
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

`incubating` · advisory-mode only. **Gate + producer both built** (the gate validates; the producer
runs reviews — harness-subagent primary + stateless-llm fallback). `POLICY_CORE += VERIFY-GATE` is
landed (live, v1.1). **Wired as the `verify_advisory` Claude Code adapter hook (advisory-mode), deployable via `install_adapters`; blocking is per-project opt-in (`.verify/armed` + a wired reviewer + a proven change-set receipt).**
Next (founder-gated): per-harness adapters + the advisory fleet push; the change-set receipt in
`CLOSEOUT-GATE`. Tests: `python3 -m pytest _skills/verify_gate/tests/ -q` (**32 cases**).

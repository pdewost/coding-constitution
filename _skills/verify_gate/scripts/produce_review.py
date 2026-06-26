#!/usr/bin/env python3
"""verify_gate.produce_review — run an independent review end to end and gate it.

seal evidence -> spawn/run the judge -> orchestrator attests out-of-band -> verify_gate
evaluates -> on PASS, emit a change-set-bound receipt.

Two ways in:
  - harness_subagent (PRIMARY, agent-driven): the agent spawns a fresh reviewer subagent and
    passes its verdict via `judge_fn` (or `agent_verdict=`). The orchestrator (the agent/harness)
    is the attestor; the subagent is the producer => attestor != producer => can block.
  - stateless_llm (FALLBACK, deployable): omit judge_fn; this calls local_llm in a fresh context.

CLI (stateless path, for CI / hooks):
  produce_review.py --artifact X --evidence-json E.json --backend stateless_llm
    --producer-id reviewer --drafter-id author --orchestrator-id ci --anchor-id <run-id>
    --changed f1 f2 --outdir DIR [--require-blocking]
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core            # noqa: E402
import evidence_bundle # noqa: E402
import review_runner   # noqa: E402
import receipt as receipt_mod  # noqa: E402
import judges          # noqa: E402


def produce(*, artifact: str, evidence: list[dict], drafter_id: str, producer_id: str,
            backend: str, orchestrator_id: str, anchor_id: str, outdir: str,
            changed_paths=None, judge_fn=None, call_fn=None, agent_verdict: dict | None = None,
            require_blocking: bool = False) -> dict:
    bundle = evidence_bundle.seal(artifact=artifact, evidence=evidence)
    evidence_bundle.assert_no_intent(bundle)

    if agent_verdict is not None:
        judge_fn = lambda _b: agent_verdict  # noqa: E731 (the agent already ran the subagent)
    if judge_fn is None:
        if backend == "stateless_llm":
            _call = call_fn or judges.default_call_fn
            judge_fn = lambda b: judges.stateless_llm_judge(  # noqa: E731
                b, producer_id=producer_id, drafter_id=drafter_id, call_fn=_call)
        else:
            raise ValueError(
                f"backend {backend!r} is agent-driven: pass the spawned subagent's verdict via "
                "judge_fn= or agent_verdict=")

    res = review_runner.run_review(
        bundle=bundle, backend=backend, judge_fn=judge_fn,
        orchestrator_id=orchestrator_id, anchor_id=anchor_id, context_isolated=True, outdir=outdir)

    if res["judge_unreachable"]:
        return {"exit": core.ExitCode.JUDGE_UNREACHABLE, "decision": "judge_unreachable",
                "verdict_path": None, "attestation_path": None, "receipt_path": None,
                "blocking": False, "advisory": False, "block_findings": []}

    verdict = core.load_json(res["verdict_path"])
    attestation = core.load_json(res["attestation_path"])
    d = core.evaluate(verdict, attestation, require_blocking=require_blocking)

    receipt_path = None
    if d.exit_code == core.ExitCode.PASS and changed_paths:
        rec = receipt_mod.write_receipt(changed_paths=changed_paths, verdict_path=res["verdict_path"],
                                        gate_exit=0, attestation_ref=res["attestation_path"])
        receipt_path = os.path.join(outdir, "receipt.json")
        with open(receipt_path, "w", encoding="utf-8") as fh:
            json.dump(rec.as_dict(), fh, indent=2)

    return {"exit": d.exit_code, "decision": _name(d.exit_code),
            "verdict_path": res["verdict_path"], "attestation_path": res["attestation_path"],
            "receipt_path": receipt_path, "blocking": d.blocking, "advisory": d.advisory,
            "block_findings": d.block_findings, "anchor_kind": d.anchor_kind, "reasons": d.reasons}


def _name(code: int) -> str:
    return {0: "pass_or_advisory", 1: "block", 2: "identity", 3: "unanchored",
            4: "judge_unreachable", 5: "tamper", 6: "receipt_invalid"}.get(code, str(code))


def main(argv=None) -> int:  # pragma: no cover (CLI; the stateless local_llm path needs a model)
    import argparse
    ap = argparse.ArgumentParser(description="produce + gate an independent review")
    ap.add_argument("--artifact", required=True)
    ap.add_argument("--evidence-json", required=True, help="path to a JSON list of evidence items")
    ap.add_argument("--backend", default="stateless_llm")
    ap.add_argument("--producer-id", required=True)
    ap.add_argument("--drafter-id", required=True)
    ap.add_argument("--orchestrator-id", required=True)
    ap.add_argument("--anchor-id", required=True)
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--changed", nargs="*", default=[])
    ap.add_argument("--require-blocking", action="store_true")
    a = ap.parse_args(argv)
    evidence = core.load_json(a.evidence_json)
    out = produce(artifact=a.artifact, evidence=evidence, drafter_id=a.drafter_id,
                  producer_id=a.producer_id, backend=a.backend, orchestrator_id=a.orchestrator_id,
                  anchor_id=a.anchor_id, outdir=a.outdir, changed_paths=a.changed,
                  require_blocking=a.require_blocking)
    print(json.dumps(out, indent=2))
    return out["exit"]


if __name__ == "__main__":
    sys.exit(main())

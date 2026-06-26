"""verify_gate.review_runner — the ORCHESTRATOR. Spawns a judge and writes the attestation
OUT-OF-BAND (the judge never attests itself).

The trust model in one line: the runner records `attestor = orchestrator_id` and
`producer_id = <the judge>`. The gate (core) only lets a verdict BLOCK when `attestor !=
producer`. So:
  - A real harness spawn: parent (orchestrator) attests a fresh subagent (the judge) =>
    attestor != producer => can block.
  - A bare local agent calling its own runner: orchestrator_id == the judge's id (one agent)
    => attestor == producer => the gate downgrades to ADVISORY. The lie doesn't help.

`judge_fn` is injected — the actual model call / harness subagent spawn / human form. This
module owns the attestation, not the judging.
"""
from __future__ import annotations

import json
import os
from typing import Callable, Optional


def run_review(
    *,
    bundle: dict,
    backend: str,                 # harness_subagent | stateless_llm | human
    judge_fn: Callable[[dict], Optional[dict]],
    orchestrator_id: str,         # who is running THIS runner (the parent/harness/CI/runner-process)
    anchor_id: str,               # the spawn's external id (subagent id / api call id / CI run id)
    context_isolated: bool,
    outdir: str,
) -> dict:
    """Returns {'verdict_path','attestation_path','producer_id','judge_unreachable'}.
    On judge failure, returns judge_unreachable=True with no verdict (gate => exit 4)."""
    os.makedirs(outdir, exist_ok=True)
    anchor_kind = _anchor_kind_for(backend, orchestrator_id, anchor_id)

    try:
        verdict = judge_fn(bundle)
    except Exception:
        verdict = None
    if not verdict:
        return {"verdict_path": None, "attestation_path": None,
                "producer_id": None, "judge_unreachable": True}

    producer_id = verdict.get("producer_id")
    verdict_path = os.path.join(outdir, "verdict.json")
    with open(verdict_path, "w", encoding="utf-8") as fh:
        json.dump(verdict, fh, indent=2)

    # The orchestrator stamps the attestation — never the judge.
    attestation = {
        "schema": "verify_gate.attestation.v1",
        "anchor_kind": anchor_kind,
        "anchor_id": anchor_id,
        "attestor": orchestrator_id,
        "producer_id": producer_id,
        "context_isolated": bool(context_isolated),
    }
    attestation_path = os.path.join(outdir, "attestation.json")
    with open(attestation_path, "w", encoding="utf-8") as fh:
        json.dump(attestation, fh, indent=2)

    return {"verdict_path": verdict_path, "attestation_path": attestation_path,
            "producer_id": producer_id, "judge_unreachable": False}


def _anchor_kind_for(backend: str, orchestrator_id: str, anchor_id: str) -> str:
    """Map the backend to an anchor kind HONESTLY. A backend that claims external anchoring
    but whose orchestrator is empty/unspecified is reported as 'self' (the gate then makes it
    advisory). CI is detected from the environment, which an agent cannot mint."""
    if _in_ci():
        return "ci"
    if backend == "stateless_llm" and anchor_id:
        return "stateless_api"
    if backend == "harness_subagent" and anchor_id and orchestrator_id:
        return "harness_subagent"
    if backend == "human":
        # human_external requires an externally-supplied identity (e.g. a signed author);
        # without one the orchestrator can only assert 'human_self' => advisory.
        return "human_external" if anchor_id else "human_self"
    return "self"


def _in_ci() -> bool:
    return any(os.environ.get(v) for v in ("CI", "GITHUB_ACTIONS", "GITLAB_CI", "BUILDKITE"))

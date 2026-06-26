"""verify_gate.judges — the review PRODUCERS (what actually runs an independent review).

Two backends, per the converged architecture:
  - harness_subagent (PRIMARY): the AGENT spawns a fresh-context reviewer subagent and passes
    its verdict in. There is no function here for it — it is agent-driven (the harness's spawn
    primitive is the external anchor; an agent cannot forge a subagent's identity). The agent
    hands the subagent's verdict to produce_review.py, which attests anchor_kind=harness_subagent.
  - stateless_llm (FALLBACK): a fresh stateless model call (clean context window) — the anchor is
    the call-id the agent cannot mint. Implemented here; **requires a `call_fn`** — pass your own, or
    the default expects a local_llm-style client to be installed (NOT self-contained on its own).

The judge NEVER writes its own attestation. parse_verdict strips any inline attestation a model
might emit (the gate would reject it as tamper anyway).
"""
from __future__ import annotations

import json
import re
from typing import Callable, Optional

_SCHEMA_HINT = """Return ONLY a JSON object, no prose, matching:
{"schema":"verify_gate.verdict.v1","artifact":"<id>","drafter_id":"<id>","producer_id":"<id>",
 "lenses":[{"lens":"<name>","verdict":"refuted|holds|partial","probe":"<what you checked — required if holds>",
   "findings":[{"severity":"low|medium|high","claim":"<what's wrong>","cheapest_fix":"<fix>",
     "evidence":{"type":"file|render|source","ref":"<file:line / locator>","literal":"<verbatim quote from the artifact/evidence>"}}]}]}
RULES: a refuting finding WITHOUT evidence.ref AND evidence.literal will be DISCARDED — do not invent;
quote real text from the evidence. A 'holds' lens MUST carry a non-empty probe. Do NOT add any
attestation/provenance/anchor field — you are the judge, not the attestor."""


def build_review_prompt(bundle: dict, *, lenses: Optional[list[str]] = None) -> str:
    lens_line = ("Review under these lenses: " + ", ".join(lenses) + ".\n") if lenses else ""
    return (
        "You are an INDEPENDENT reviewer. You see ONLY the sealed evidence below — not the "
        "author's intent or answer key. Refute where you can; a review that finds nothing is "
        "suspect, so show your probe.\n"
        f"{lens_line}\n"
        "SEALED EVIDENCE BUNDLE:\n"
        + json.dumps(bundle, indent=2)
        + "\n\n" + _SCHEMA_HINT
    )


def parse_verdict(raw: str, *, producer_id: str, drafter_id: str, artifact: str) -> Optional[dict]:
    """Extract the verdict JSON from a model's reply; stamp the ids; strip any self-attestation."""
    obj = _extract_json(raw)
    if not isinstance(obj, dict):
        return None
    obj["schema"] = "verify_gate.verdict.v1"
    obj["producer_id"] = producer_id
    obj["drafter_id"] = drafter_id
    obj.setdefault("artifact", artifact)
    # The judge must not attest itself — drop any such keys (defence; the gate also rejects them).
    for k in ("attestation", "provenance", "anchor_kind", "context_isolated"):
        obj.pop(k, None)
    for ln in obj.get("lenses") or []:
        if isinstance(ln, dict):
            for k in ("attestation", "provenance", "anchor_kind"):
                ln.pop(k, None)
    return obj


def _extract_json(raw: str):
    raw = raw.strip()
    # strip ```json fences if present
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", raw, re.DOTALL)
    if m:
        raw = m.group(1)
    try:
        return json.loads(raw)
    except ValueError:
        pass
    # fall back: first balanced {...}
    start = raw.find("{")
    if start < 0:
        return None
    depth = 0
    for i in range(start, len(raw)):
        depth += 1 if raw[i] == "{" else (-1 if raw[i] == "}" else 0)
        if depth == 0:
            try:
                return json.loads(raw[start:i + 1])
            except ValueError:
                return None
    return None


def stateless_llm_judge(bundle: dict, *, producer_id: str, drafter_id: str,
                        call_fn: Callable[[str], str], lenses: Optional[list[str]] = None) -> Optional[dict]:
    """Run a fresh stateless review. `call_fn(prompt) -> model_text`. Returns a verdict or None."""
    prompt = build_review_prompt(bundle, lenses=lenses)
    try:
        raw = call_fn(prompt)
    except Exception:
        return None
    if not raw:
        return None
    return parse_verdict(raw, producer_id=producer_id, drafter_id=drafter_id,
                         artifact=bundle.get("artifact", "artifact"))


def default_call_fn(prompt: str) -> str:
    """The default stateless judge call. NOT self-contained: it expects a local_llm-style client to
    be installed. If absent, it raises a clear, actionable error (=> the runner reports
    judge_unreachable). For a portable deployment, pass your own `call_fn=` instead."""
    import sys
    import os
    ws = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
    if ws not in sys.path:
        sys.path.insert(0, ws)
    try:
        from _skills.local_llm.scripts.cascade import CascadeRouter  # type: ignore
    except ImportError as e:
        raise RuntimeError(
            "stateless_llm backend has no model client: install a local_llm-style cascade, OR pass "
            "call_fn=<your model call> to stateless_llm_judge()/produce_review() — the default is "
            "not self-contained."
        ) from e
    # A new router per call => a clean context window (stateless). task=reasoning for judgment.
    return CascadeRouter().call(prompt, task="reasoning")

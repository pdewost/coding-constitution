"""verify_gate.evidence_bundle — seal the evidence a judge may see, stripping intent.

Honest scope: for a fresh stateless/subagent judge this denial is REAL (the judge's context
only ever contains the bundle). For a same-context re-prompt or a human (eyes/memory), the
'denial' is a no-op — the core gate handles that by treating those as advisory, not by
pretending the seal worked. This module only guarantees that what the runner PASSES is
intent-free; it cannot scrub a context the judge already holds.
"""
from __future__ import annotations

# Keys that reveal the drafter's intent / answer-key — never put in a judge's bundle.
_INTENT_KEYS = ("intent", "answer_key", "self_assessment", "spec", "transcript", "rationale",
                "expected", "drafter_notes")


def seal(*, artifact: str, evidence: list[dict]) -> dict:
    """Build the bundle a judge is allowed to see: the artifact reference + evidence items,
    with any intent-bearing key removed. Raises if an evidence item smuggles intent."""
    clean: list[dict] = []
    for item in evidence:
        if not isinstance(item, dict):
            raise ValueError("evidence items must be objects")
        bad = [k for k in item if k in _INTENT_KEYS]
        if bad:
            raise ValueError(f"evidence item carries intent key(s) {bad} — refusing to seal")
        clean.append(item)
    return {"schema": "verify_gate.bundle.v1", "artifact": artifact, "evidence": clean}


def assert_no_intent(bundle: dict) -> None:
    """Belt-and-suspenders: confirm a sealed bundle contains no intent keys anywhere."""
    def _scan(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k in _INTENT_KEYS:
                    raise AssertionError(f"sealed bundle leaked intent key {k!r}")
                _scan(v)
        elif isinstance(o, list):
            for x in o:
                _scan(x)
    _scan(bundle)

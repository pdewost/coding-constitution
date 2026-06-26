"""verify_gate.core — the heart of the VERIFY-GATE (harness- & coder-neutral, stdlib only).

CONVERGED DESIGN (after 3 §3 review rounds; see governance/PLAN_verify_gate_and_ux_verify_2026-06-20.md):

  A single in-context agent cannot mechanically verify itself. Every self-written control
  recurses to a field the policed party writes. So the trust anchor MUST be EXTERNAL to the
  producing agent's address space. Where one exists (harness-spawned subagent / stateless API
  call-id / CI / a human with an external identity) the verdict can BLOCK. Where none exists
  (a bare local agent), the verdict is ADVISORY only — never silently treated as enforcement.

  This module is honest about that line: it computes `block_eligible` from the *attestation*
  (written out-of-band by the orchestrator, NEVER by the judge), and it refuses (exit 5) any
  verdict that tries to carry its own attestation/provenance inline.

Exit-code contract (consumed by verify_gate.py CLI + adapters):
  0  PASS or ADVISORY (no enforceable block this run)
  1  BLOCK            (a substantiated HIGH refutation AND externally anchored)
  2  IDENTITY         (the verdict's producer == the drafter — not independent)
  3  UNANCHORED       (caller required a blocking verdict but this one is advisory-only)
  4  JUDGE_UNREACHABLE (the reviewer could not be produced — runner signals this)
  5  TAMPER           (the verdict carries a self-asserted attestation/provenance block)
  6  RECEIPT_INVALID  (stale / replayed / change-set mismatch — handled in receipt.py)
"""
from __future__ import annotations

import dataclasses
import json
from typing import Any, Optional

# Anchors that sit OUTSIDE the producing agent and can make a verdict blocking.
EXTERNAL_ANCHORS = frozenset({"harness_subagent", "stateless_api", "ci", "human_external"})
# Anchors that are inside the agent's reach => advisory only, never blocking.
SELF_ANCHORS = frozenset({"self", "same_context", "human_self"})

# Keys a judge must NOT place inside its own verdict.json (would be self-attestation).
_FORBIDDEN_INLINE = ("attestation", "provenance", "anchor_kind", "context_isolated")

SEV_RANK = {"low": 1, "medium": 2, "high": 3}


class ExitCode:
    PASS = 0
    BLOCK = 1
    IDENTITY = 2
    UNANCHORED = 3
    JUDGE_UNREACHABLE = 4
    TAMPER = 5
    RECEIPT_INVALID = 6


@dataclasses.dataclass
class Decision:
    exit_code: int
    blocking: bool          # would this stop the turn?
    advisory: bool          # findings exist but cannot block (un-anchored)
    reasons: list[str]
    block_findings: list[dict]      # the substantiated HIGH refutations that block
    demoted: list[dict]             # findings dropped for lack of evidence
    anchor_kind: Optional[str]

    def as_dict(self) -> dict:
        return dataclasses.asdict(self)


def _is_substantiated(finding: dict) -> bool:
    """A finding counts toward a verdict ONLY if it carries a concrete evidence pointer:
    both a ref (file:line / locator) AND a verbatim literal. This is the demotion rule the
    reviews demanded — a prose assertion a liar can type is not evidence."""
    ev = finding.get("evidence") or {}
    ref = (ev.get("ref") or "").strip()
    lit = (ev.get("literal") or "").strip()
    return bool(ref) and bool(lit)


def validate_verdict_schema(v: dict) -> list[str]:
    """Structural validation. Returns a list of problems (empty == valid)."""
    problems: list[str] = []
    if v.get("schema") != "verify_gate.verdict.v1":
        problems.append("verdict.schema must be 'verify_gate.verdict.v1'")
    if not v.get("producer_id"):
        problems.append("verdict.producer_id is required (who produced THIS verdict)")
    if not v.get("drafter_id"):
        problems.append("verdict.drafter_id is required (who produced the artifact)")
    # TAMPER guard handled separately (exit 5); here only structure.
    lenses = v.get("lenses")
    if not isinstance(lenses, list) or not lenses:
        problems.append("verdict.lenses must be a non-empty list")
        return problems
    for i, ln in enumerate(lenses):
        if not isinstance(ln, dict):
            problems.append(f"lens[{i}] must be an object"); continue
        if not ln.get("lens"):
            problems.append(f"lens[{i}].lens (name) is required")
        if ln.get("verdict") not in ("refuted", "holds", "partial"):
            problems.append(f"lens[{i}].verdict must be refuted|holds|partial")
        # A HOLDS lens must show its probe (a zero-finding all-clear is suspect).
        if ln.get("verdict") == "holds" and not (ln.get("probe") or "").strip():
            problems.append(f"lens[{i}] (holds) must carry a non-empty probe")
        for j, f in enumerate(ln.get("findings") or []):
            if not isinstance(f, dict):
                problems.append(f"lens[{i}].findings[{j}] must be an object"); continue
            if f.get("severity") not in SEV_RANK:
                problems.append(f"lens[{i}].findings[{j}].severity must be low|medium|high")
    return problems


def has_inline_attestation(v: dict) -> bool:
    """TAMPER: a judge must never write its own attestation/provenance. If the verdict
    carries any of these keys (top-level or inside a lens/finding), reject (exit 5)."""
    def _scan(obj: Any) -> bool:
        if isinstance(obj, dict):
            if any(k in obj for k in _FORBIDDEN_INLINE):
                return True
            return any(_scan(x) for x in obj.values())
        if isinstance(obj, list):
            return any(_scan(x) for x in obj)
        return False
    return _scan(v)


def validate_attestation_schema(a: dict) -> list[str]:
    problems: list[str] = []
    if a.get("schema") != "verify_gate.attestation.v1":
        problems.append("attestation.schema must be 'verify_gate.attestation.v1'")
    ak = a.get("anchor_kind")
    if ak not in (EXTERNAL_ANCHORS | SELF_ANCHORS):
        problems.append(f"attestation.anchor_kind invalid: {ak!r}")
    if not a.get("attestor"):
        problems.append("attestation.attestor is required (who WROTE this attestation)")
    if not a.get("producer_id"):
        problems.append("attestation.producer_id is required (the judge this attests)")
    return problems


def block_eligible(attestation: dict, verdict: dict) -> tuple[bool, list[str]]:
    """Can a refutation in this verdict actually BLOCK? Only if independence is externally
    anchored: an external anchor kind AND the attestor is not the producer (the judge cannot
    attest itself) AND the producer is not the drafter (handled as IDENTITY upstream)."""
    reasons: list[str] = []
    ak = attestation.get("anchor_kind")
    attestor = attestation.get("attestor")
    producer = verdict.get("producer_id")
    if ak in SELF_ANCHORS:
        reasons.append(f"anchor_kind={ak!r} is inside the agent's reach => advisory only")
        return False, reasons
    if ak not in EXTERNAL_ANCHORS:
        reasons.append(f"anchor_kind={ak!r} not a recognized external anchor => advisory")
        return False, reasons
    if attestor == producer:
        # The judge wrote its own attestation out-of-band == self-attestation, one frame up.
        reasons.append("attestor == producer (judge self-attested) => advisory only")
        return False, reasons
    if not attestation.get("context_isolated", False) and ak != "human_external":
        reasons.append("context_isolated is false => advisory only")
        return False, reasons
    reasons.append(f"externally anchored ({ak}, attestor={attestor}) => blocking-eligible")
    return True, reasons


def evaluate(verdict: dict, attestation: dict, *, require_blocking: bool = False,
             judge_unreachable: bool = False) -> Decision:
    """The core gate decision. Pure; no I/O. (Receipt freshness is checked separately.)"""
    reasons: list[str] = []

    if judge_unreachable:
        return Decision(ExitCode.JUDGE_UNREACHABLE, False, False,
                        ["the reviewer could not be produced (judge unreachable)"],
                        [], [], attestation.get("anchor_kind"))

    # 5 — TAMPER: judge must not self-attest inside the verdict.
    if has_inline_attestation(verdict):
        return Decision(ExitCode.TAMPER, False, False,
                        ["verdict carries an inline attestation/provenance block (self-attestation)"],
                        [], [], None)

    vp = validate_verdict_schema(verdict)
    ap = validate_attestation_schema(attestation)
    if vp or ap:
        return Decision(ExitCode.TAMPER, False, False,
                        ["malformed verdict/attestation: " + "; ".join(vp + ap)],
                        [], [], attestation.get("anchor_kind"))

    # 2 — IDENTITY: the reviewer is the drafter.
    if verdict.get("producer_id") == verdict.get("drafter_id"):
        return Decision(ExitCode.IDENTITY, False, False,
                        ["producer_id == drafter_id (reviewer is the drafter)"],
                        [], [], attestation.get("anchor_kind"))
    if attestation.get("producer_id") != verdict.get("producer_id"):
        return Decision(ExitCode.TAMPER, False, False,
                        ["attestation.producer_id does not match verdict.producer_id"],
                        [], [], attestation.get("anchor_kind"))

    # Demote evidence-less findings; collect the substantiated HIGH refutations.
    demoted: list[dict] = []
    block_findings: list[dict] = []
    for ln in verdict["lenses"]:
        if ln.get("verdict") != "refuted":
            continue
        for f in ln.get("findings") or []:
            if not _is_substantiated(f):
                demoted.append({"lens": ln["lens"], **f})
                continue
            if SEV_RANK.get(f.get("severity")) == SEV_RANK["high"]:
                block_findings.append({"lens": ln["lens"], **f})

    eligible, anchor_reasons = block_eligible(attestation, verdict)
    reasons.extend(anchor_reasons)
    ak = attestation.get("anchor_kind")

    if block_findings and eligible:
        reasons.append(f"{len(block_findings)} substantiated HIGH refutation(s), externally anchored => BLOCK")
        return Decision(ExitCode.BLOCK, True, False, reasons, block_findings, demoted, ak)

    if block_findings and not eligible:
        # Real findings, but un-anchored: advisory. If the caller REQUIRED a blocking verdict
        # (e.g. a closeout gate), it cannot be satisfied here => exit 3.
        reasons.append(f"{len(block_findings)} HIGH refutation(s) but advisory-only (un-anchored)")
        if require_blocking:
            return Decision(ExitCode.UNANCHORED, False, True, reasons, block_findings, demoted, ak)
        return Decision(ExitCode.PASS, False, True, reasons, block_findings, demoted, ak)

    # No substantiated HIGH refutation. If the caller required a blocking-capable verdict and
    # this one is un-anchored, still flag UNANCHORED (a pass from an un-attestable judge is not
    # an enforceable pass).
    if require_blocking and not eligible:
        reasons.append("clean verdict but un-anchored => cannot satisfy a blocking gate")
        return Decision(ExitCode.UNANCHORED, False, True, reasons, [], demoted, ak)

    reasons.append("no substantiated HIGH refutation => PASS")
    return Decision(ExitCode.PASS, False, False, reasons, [], demoted, ak)


def load_json(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)

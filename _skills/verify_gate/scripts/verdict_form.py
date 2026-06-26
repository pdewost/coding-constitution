"""verify_gate.verdict_form — the HUMAN reviewer path (coder-neutrality made real).

A human and an LLM hit the identical gate. For a human to BLOCK, the form must elicit
substantiated findings — every finding needs evidence.ref (file:line) AND evidence.literal
(a verbatim quote), or core demotes it. This builder produces a schema-valid verdict; the CLI
wraps it interactively. Human independence is by IDENTITY (an external producer id), never by
context-denial — the orchestrator attests human_external only when given an external identity.
"""
from __future__ import annotations

import json
import sys


def build_verdict(*, producer_id: str, drafter_id: str, artifact: str, lenses: list[dict]) -> dict:
    """lenses: [{lens, verdict, probe?, findings:[{severity, claim, cheapest_fix,
    evidence:{type,ref,literal}}]}]. Returns a schema-valid verdict (no inline attestation)."""
    out_lenses = []
    for ln in lenses:
        out_lenses.append({
            "lens": ln["lens"],
            "verdict": ln["verdict"],
            "probe": ln.get("probe", ""),
            "findings": [
                {
                    "id": f.get("id", f"{ln['lens']}-{i}"),
                    "severity": f["severity"],
                    "claim": f.get("claim", ""),
                    "cheapest_fix": f.get("cheapest_fix", ""),
                    "evidence": {
                        "type": (f.get("evidence") or {}).get("type", "file"),
                        "ref": (f.get("evidence") or {}).get("ref", ""),
                        "literal": (f.get("evidence") or {}).get("literal", ""),
                    },
                }
                for i, f in enumerate(ln.get("findings") or [])
            ],
        })
    return {
        "schema": "verify_gate.verdict.v1",
        "artifact": artifact,
        "drafter_id": drafter_id,
        "producer_id": producer_id,
        "lenses": out_lenses,
    }


def _prompt(msg: str) -> str:
    sys.stderr.write(msg)
    sys.stderr.flush()
    return sys.stdin.readline().rstrip("\n")


def interactive(producer_id: str, drafter_id: str, artifact: str) -> dict:  # pragma: no cover
    """Minimal lens-by-lens CLI. A real deployment would expand this; the contract is fixed:
    every finding requires ref + literal."""
    lenses = []
    sys.stderr.write("verify_gate human review — enter lenses (blank lens name to finish)\n")
    while True:
        name = _prompt("lens name: ").strip()
        if not name:
            break
        verdict = _prompt("  verdict [refuted/holds/partial]: ").strip() or "holds"
        probe = _prompt("  probe (what you checked): ").strip()
        findings = []
        while verdict == "refuted":
            claim = _prompt("  finding claim (blank to stop): ").strip()
            if not claim:
                break
            sev = _prompt("    severity [low/medium/high]: ").strip() or "medium"
            ref = _prompt("    evidence ref (file:line): ").strip()
            lit = _prompt("    evidence literal (verbatim quote): ").strip()
            fix = _prompt("    cheapest fix: ").strip()
            findings.append({"severity": sev, "claim": claim, "cheapest_fix": fix,
                             "evidence": {"type": "file", "ref": ref, "literal": lit}})
        lenses.append({"lens": name, "verdict": verdict, "probe": probe, "findings": findings})
    return build_verdict(producer_id=producer_id, drafter_id=drafter_id, artifact=artifact, lenses=lenses)


if __name__ == "__main__":  # pragma: no cover
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--producer-id", required=True)
    ap.add_argument("--drafter-id", required=True)
    ap.add_argument("--artifact", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    v = interactive(a.producer_id, a.drafter_id, a.artifact)
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(v, fh, indent=2)
    sys.stderr.write(f"wrote {a.out}\n")

"""
schema.py — Finding / ReviewerReport dataclasses + JSON (de)serialization + validation.

Importable as a module (tests do: import schema) and runnable as a CLI to validate
a reviewer-report JSON file:
    python3.12 scripts/schema.py <report.json>
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field, asdict
from typing import Optional


# ---------------------------------------------------------------------------
# Error
# ---------------------------------------------------------------------------

class SchemaError(ValueError):
    """Raised with a precise human-readable message on any schema violation."""


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

VALID_SEVERITY = {"HIGH", "MED", "LOW", "UNSUBSTANTIATED"}
VALID_EVIDENCE_TYPES = {"file_line", "command_output", "render_capture"}
VALID_TIERS = {"skeptic", "panel", "workflow"}
VALID_VERDICTS = {"HOLDS", "REFUTED"}


@dataclass
class Evidence:
    type: str          # file_line | command_output | render_capture
    ref: str
    literal: str

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Evidence":
        return cls(
            type=d.get("type", ""),
            ref=d.get("ref", ""),
            literal=d.get("literal", ""),
        )


@dataclass
class Finding:
    id: str
    lens: str
    severity: str      # HIGH | MED | LOW | UNSUBSTANTIATED
    claim: str
    evidence: Evidence
    cheapest_fix: str
    pack: str = ""     # may be set by the merge step

    def to_dict(self) -> dict:
        d = asdict(self)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Finding":
        return cls(
            id=d.get("id", ""),
            lens=d.get("lens", ""),
            severity=d.get("severity", ""),
            claim=d.get("claim", ""),
            evidence=Evidence.from_dict(d.get("evidence", {})),
            cheapest_fix=d.get("cheapest_fix", ""),
            pack=d.get("pack", ""),
        )


@dataclass
class ReviewerReport:
    reviewer: str
    pack: str
    pack_version: str
    tier: str
    drafter: str
    findings: list[Finding]
    verdicts: dict          # {lens_id: {"verdict": "HOLDS|REFUTED", "probe": str}}
    overall: str

    def to_dict(self) -> dict:
        return {
            "reviewer": self.reviewer,
            "pack": self.pack,
            "pack_version": self.pack_version,
            "tier": self.tier,
            "drafter": self.drafter,
            "findings": [f.to_dict() for f in self.findings],
            "verdicts": self.verdicts,
            "overall": self.overall,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ReviewerReport":
        return cls(
            reviewer=d.get("reviewer", ""),
            pack=d.get("pack", ""),
            pack_version=d.get("pack_version", ""),
            tier=d.get("tier", ""),
            drafter=d.get("drafter", ""),
            findings=[Finding.from_dict(f) for f in d.get("findings", [])],
            verdicts=d.get("verdicts", {}),
            overall=d.get("overall", ""),
        )


@dataclass
class MergedResult:
    findings: list[Finding]
    verdicts: dict
    overall: str
    counts: dict
    provenance: dict

    def to_dict(self) -> dict:
        return {
            "findings": [f.to_dict() for f in self.findings],
            "verdicts": self.verdicts,
            "overall": self.overall,
            "counts": self.counts,
            "provenance": self.provenance,
        }


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_report(d: dict) -> ReviewerReport:
    """
    Validate a reviewer-report dict and return a ReviewerReport.
    Raises SchemaError with a precise message on any violation.
    """
    required_top = ["reviewer", "pack", "pack_version", "tier", "drafter",
                    "findings", "verdicts", "overall"]
    for k in required_top:
        if k not in d:
            raise SchemaError(f"Missing required key: '{k}'")

    tier = d["tier"]
    if tier not in VALID_TIERS:
        raise SchemaError(
            f"Invalid tier '{tier}': must be one of {sorted(VALID_TIERS)}"
        )

    if not isinstance(d["findings"], list):
        raise SchemaError("'findings' must be a list")

    findings: list[Finding] = []
    for i, fd in enumerate(d["findings"]):
        if not isinstance(fd, dict):
            raise SchemaError(f"findings[{i}] is not a dict")
        for fk in ["id", "lens", "severity", "claim", "evidence", "cheapest_fix"]:
            if fk not in fd:
                raise SchemaError(f"findings[{i}] missing key '{fk}'")

        sev = fd["severity"]
        if sev not in VALID_SEVERITY:
            raise SchemaError(
                f"findings[{i}] invalid severity '{sev}': "
                f"must be one of {sorted(VALID_SEVERITY)}"
            )

        ev = fd.get("evidence", {})
        if not isinstance(ev, dict):
            raise SchemaError(f"findings[{i}].evidence is not a dict")
        for ek in ["type", "ref", "literal"]:
            if ek not in ev:
                raise SchemaError(f"findings[{i}].evidence missing key '{ek}'")
        ev_type = ev["type"]
        if ev_type not in VALID_EVIDENCE_TYPES:
            raise SchemaError(
                f"findings[{i}].evidence.type '{ev_type}' is not one of "
                f"{sorted(VALID_EVIDENCE_TYPES)}"
            )

        findings.append(Finding.from_dict(fd))

    if not isinstance(d["verdicts"], dict):
        raise SchemaError("'verdicts' must be a dict")

    for lens_id, vobj in d["verdicts"].items():
        if not isinstance(vobj, dict):
            raise SchemaError(
                f"verdicts['{lens_id}'] is not a dict"
            )
        if "verdict" not in vobj:
            raise SchemaError(
                f"verdicts['{lens_id}'] missing key 'verdict'"
            )
        v = vobj["verdict"]
        if v not in VALID_VERDICTS:
            raise SchemaError(
                f"verdicts['{lens_id}'].verdict '{v}' must be HOLDS or REFUTED"
            )
        if v == "HOLDS":
            probe = vobj.get("probe", "")
            if not (isinstance(probe, str) and probe.strip()):
                raise SchemaError(
                    f"verdicts['{lens_id}'] verdict is HOLDS but probe is "
                    f"empty or missing (probe is required when verdict=HOLDS)"
                )

    return ReviewerReport(
        reviewer=d["reviewer"],
        pack=d["pack"],
        pack_version=d["pack_version"],
        tier=d["tier"],
        drafter=d["drafter"],
        findings=findings,
        verdicts=d["verdicts"],
        overall=d["overall"],
    )


# ---------------------------------------------------------------------------
# Demotion helper
# ---------------------------------------------------------------------------

def demote_unsubstantiated(finding: Finding) -> Finding:
    """
    If evidence.ref or evidence.literal is empty/whitespace, severity becomes
    UNSUBSTANTIATED. Returns a (possibly new) Finding.
    """
    ref = finding.evidence.ref or ""
    literal = finding.evidence.literal or ""
    if not ref.strip() or not literal.strip():
        if finding.severity != "UNSUBSTANTIATED":
            return Finding(
                id=finding.id,
                lens=finding.lens,
                severity="UNSUBSTANTIATED",
                claim=finding.claim,
                evidence=finding.evidence,
                cheapest_fix=finding.cheapest_fix,
                pack=finding.pack,
            )
    return finding


# ---------------------------------------------------------------------------
# JSON round-trip helpers
# ---------------------------------------------------------------------------

def to_dict(obj) -> dict:
    """Generic to_dict dispatcher."""
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    raise TypeError(f"Cannot convert {type(obj)} to dict")


def from_dict(d: dict, cls):
    """Generic from_dict dispatcher."""
    if hasattr(cls, "from_dict"):
        return cls.from_dict(d)
    raise TypeError(f"Cannot construct {cls} from dict")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main():
    import argparse
    parser = argparse.ArgumentParser(
        description="Validate a reviewer-report JSON file against the schema."
    )
    parser.add_argument("report", help="Path to reviewer-report JSON file")
    args = parser.parse_args()

    with open(args.report) as fh:
        data = json.load(fh)

    try:
        report = validate_report(data)
    except SchemaError as exc:
        print(f"INVALID: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"OK: reviewer={report.reviewer} pack={report.pack} "
          f"findings={len(report.findings)}")


if __name__ == "__main__":
    _main()

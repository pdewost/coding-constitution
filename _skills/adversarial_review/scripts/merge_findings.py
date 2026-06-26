"""
merge_findings.py — Merge, dedup, vote, and emit a combined verdict.

CLI:
    python3.12 scripts/merge_findings.py <report.json> [<report.json> ...] \\
        [--emit-audit-md <path>] \\
        [--slug <s>] \\
        [--state-dir <dir>]

Reads one or more reviewer-report JSON files, validates each, demotes
evidence-less findings, deduplicates, applies panel vote semantics, updates
zero-finding streak state, and emits the merged result to stdout.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

_SKILL_DIR = Path(__file__).parent.parent
_DEFAULT_STATE_DIR = _SKILL_DIR / "state"

# Insert scripts dir into sys.path so we can import schema
import os as _os
_scripts_dir = str(Path(__file__).parent)
if _scripts_dir not in sys.path:
    sys.path.insert(0, _scripts_dir)

from schema import (
    Finding,
    ReviewerReport,
    SchemaError,
    demote_unsubstantiated,
    validate_report,
)


# ---------------------------------------------------------------------------
# Severity ordering (higher index = higher severity)
# ---------------------------------------------------------------------------

_SEV_ORDER = {"UNSUBSTANTIATED": 0, "LOW": 1, "MED": 2, "HIGH": 3}


def _higher_severity(a: str, b: str) -> str:
    """Return the severity with the higher rank."""
    if _SEV_ORDER.get(a, -1) >= _SEV_ORDER.get(b, -1):
        return a
    return b


# ---------------------------------------------------------------------------
# Dedup logic
# ---------------------------------------------------------------------------

def _dedup_key(finding: Finding) -> tuple:
    """Dedup key = (pack, lens, evidence.ref, claim).
    claim is normalised (strip+lower) so only true duplicates collapse;
    two different claims at the same file:line are kept separately.
    """
    return (finding.pack, finding.lens, finding.evidence.ref, finding.claim.strip().lower())


def dedup_findings(findings: list[Finding]) -> list[Finding]:
    """
    Dedup findings by (pack, lens, evidence.ref, claim), keeping the highest severity.
    Insertion-order stable (first occurrence wins on ties).
    """
    seen: dict[tuple, Finding] = {}
    for f in findings:
        k = _dedup_key(f)
        if k not in seen:
            seen[k] = f
        else:
            existing = seen[k]
            if _SEV_ORDER.get(f.severity, -1) > _SEV_ORDER.get(existing.severity, -1):
                seen[k] = f
    return list(seen.values())


# ---------------------------------------------------------------------------
# Verdict merging
# ---------------------------------------------------------------------------

def merge_verdicts(
    reports: list[ReviewerReport],
    merged_findings: list[Finding],
) -> dict:
    """
    Per-lens verdict: collect all reports' verdicts for each lens.
    - Majority HOLDS/REFUTED wins; ties → REFUTED.
    - Any lens that has at least one evidence-backed HIGH finding → REFUTED
      regardless of vote.

    Returns dict: {lens_id: {"verdict": "HOLDS|REFUTED", "probe": str}}
    """
    # Collect all lens ids mentioned in verdicts
    all_lens_ids: set[str] = set()
    for r in reports:
        all_lens_ids.update(r.verdicts.keys())

    # Also collect lens ids from findings
    for f in merged_findings:
        all_lens_ids.add(f.lens)

    result: dict = {}

    for lens_id in sorted(all_lens_ids):
        holds_count = 0
        refuted_count = 0
        probes: list[str] = []

        for r in reports:
            vobj = r.verdicts.get(lens_id)
            if vobj is None:
                continue
            v = vobj.get("verdict", "")
            if v == "HOLDS":
                holds_count += 1
                probe = vobj.get("probe", "")
                if probe and probe.strip():
                    probes.append(probe.strip())
            elif v == "REFUTED":
                refuted_count += 1

        # Majority vote; tie → REFUTED
        if holds_count > refuted_count:
            verdict = "HOLDS"
        else:
            verdict = "REFUTED"

        # Union rule: any evidence-backed HIGH finding for this lens → REFUTED
        for f in merged_findings:
            if (
                f.lens == lens_id
                and f.severity == "HIGH"
                and f.evidence.ref.strip()
                and f.evidence.literal.strip()
            ):
                verdict = "REFUTED"
                break

        entry: dict = {"verdict": verdict}
        if verdict == "HOLDS" and probes:
            entry["probe"] = probes[0]
        elif verdict == "HOLDS":
            # validate_report upstream rejects HOLDS with an empty probe, so
            # probes should never be empty here for a HOLDS verdict that
            # reached this function through the normal pipeline.
            assert probes, (
                "invariant violation: HOLDS verdict with no probe collected — "
                "validate_report should have rejected this report upstream"
            )
        result[lens_id] = entry

    return result


# ---------------------------------------------------------------------------
# Streak state
# ---------------------------------------------------------------------------

def _load_zero_streaks(state_dir: Path) -> dict:
    streak_file = state_dir / "zero_streaks.json"
    if not streak_file.exists():
        return {}
    with open(streak_file) as fh:
        return json.load(fh)


def _save_zero_streaks(state_dir: Path, streaks: dict) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    streak_file = state_dir / "zero_streaks.json"
    with open(streak_file, "w") as fh:
        json.dump(streaks, fh, indent=2)


def update_streaks(
    state_dir: Path,
    pack: str,
    merged_findings: list[Finding],
    reviewers: list[str],
) -> dict:
    """
    Update zero-finding streak for the pack.
    - If count of non-UNSUBSTANTIATED findings == 0: streak += 1
    - Else: streak = 0
    Returns the updated streaks dict.
    """
    streaks = _load_zero_streaks(state_dir)
    substantiated = [
        f for f in merged_findings if f.severity != "UNSUBSTANTIATED"
    ]
    pack_entry = streaks.get(pack, {"streak": 0, "last_reviewer": ""})

    if len(substantiated) == 0:
        pack_entry["streak"] = pack_entry.get("streak", 0) + 1
    else:
        pack_entry["streak"] = 0

    pack_entry["last_reviewer"] = ",".join(reviewers)
    streaks[pack] = pack_entry
    _save_zero_streaks(state_dir, streaks)
    return streaks


# ---------------------------------------------------------------------------
# Count helpers
# ---------------------------------------------------------------------------

def count_by_severity(findings: list[Finding]) -> dict:
    counts = {"HIGH": 0, "MED": 0, "LOW": 0, "UNSUBSTANTIATED": 0}
    for f in findings:
        sev = f.severity if f.severity in counts else "UNSUBSTANTIATED"
        counts[sev] += 1
    return counts


# ---------------------------------------------------------------------------
# Audit markdown
# ---------------------------------------------------------------------------

def _emit_audit_md(
    path: Path,
    slug: str,
    merged: dict,
    merged_json_str: str,
) -> None:
    today = date.today().strftime("%Y-%m-%d")
    prov = merged.get("provenance", {})
    findings = merged.get("findings", [])
    verdicts = merged.get("verdicts", {})
    counts = merged.get("counts", {})

    lines: list[str] = []
    lines.append(f"# AUDIT — adversarial_review {slug} {today}\n")

    # Provenance line
    reviewers = ", ".join(prov.get("reviewers", []))
    packs_str = ", ".join(
        f"{k}@{v}" for k, v in prov.get("packs", {}).items()
    )
    drafter = prov.get("drafter", "unknown")
    tier = prov.get("tier", "unknown")
    lines.append(
        f"**Provenance**: reviewers={reviewers}; packs={packs_str}; "
        f"drafter={drafter}; tier={tier}\n"
    )

    # Counts table
    lines.append("## Counts\n")
    lines.append("| Severity | Count |")
    lines.append("|---|---|")
    for sev in ["HIGH", "MED", "LOW", "UNSUBSTANTIATED"]:
        lines.append(f"| {sev} | {counts.get(sev, 0)} |")
    lines.append("")

    # Findings list
    lines.append("## Findings\n")
    if findings:
        for f in findings:
            ev = f.get("evidence", {}) if isinstance(f, dict) else {}
            if hasattr(f, "evidence"):
                ev_ref = f.evidence.ref
                ev_literal = f.evidence.literal
                fid = f.id
                fsev = f.severity
                flens = f.lens
                fclaim = f.claim
                ffix = f.cheapest_fix
            else:
                ev_ref = ev.get("ref", "")
                ev_literal = ev.get("literal", "")
                fid = f.get("id", "")
                fsev = f.get("severity", "")
                flens = f.get("lens", "")
                fclaim = f.get("claim", "")
                ffix = f.get("cheapest_fix", "")
            lines.append(f"- **{fid}** [{fsev}] (lens: {flens})")
            lines.append(f"  - Claim: {fclaim}")
            lines.append(f"  - Evidence ref: `{ev_ref}`")
            lines.append(f"  - Evidence literal: `{ev_literal}`")
            lines.append(f"  - Cheapest fix: {ffix}")
            lines.append("")
    else:
        lines.append("*(no findings)*\n")

    # Verdicts table
    lines.append("## Verdicts\n")
    lines.append("| Lens | Verdict |")
    lines.append("|---|---|")
    for lens_id, vobj in sorted(verdicts.items()):
        v = vobj.get("verdict", "")
        lines.append(f"| {lens_id} | {v} |")
    lines.append("")

    # Overall
    lines.append(f"**Overall verdict**: {merged.get('overall', '?')}\n")

    # Fenced JSON block
    lines.append("```json")
    lines.append(merged_json_str)
    lines.append("```")
    lines.append("")

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as fh:
        fh.write("\n".join(lines))


# ---------------------------------------------------------------------------
# Core merge function (importable)
# ---------------------------------------------------------------------------

def merge(
    report_paths: list[Path],
    state_dir: Path | None = None,
    slug: str = "run",
    emit_audit_md: Path | None = None,
) -> dict:
    """
    Validate, demote, dedup, vote, and return merged result dict.
    On schema error: prints to stderr and calls sys.exit(1).
    """
    if state_dir is None:
        state_dir = _DEFAULT_STATE_DIR

    reports: list[ReviewerReport] = []
    for rp in report_paths:
        try:
            with open(rp) as fh:
                raw = json.load(fh)
        except Exception as exc:
            print(f"ERROR reading {rp}: {exc}", file=sys.stderr)
            sys.exit(1)
        try:
            report = validate_report(raw)
        except SchemaError as exc:
            print(f"INVALID report {rp}: {exc}", file=sys.stderr)
            sys.exit(1)
        reports.append(report)

    if not reports:
        print("ERROR: no valid reports provided", file=sys.stderr)
        sys.exit(1)

    # Collect all findings, attach pack from report, demote
    all_findings: list[Finding] = []
    for report in reports:
        for f in report.findings:
            # attach pack from the report
            f_with_pack = Finding(
                id=f.id,
                lens=f.lens,
                severity=f.severity,
                claim=f.claim,
                evidence=f.evidence,
                cheapest_fix=f.cheapest_fix,
                pack=f.pack if f.pack else report.pack,
            )
            demoted = demote_unsubstantiated(f_with_pack)
            all_findings.append(demoted)

    # Dedup
    merged_findings = dedup_findings(all_findings)

    # Verdicts
    verdicts = merge_verdicts(reports, merged_findings)

    # Overall verdict:
    # REFUTED if any HIGH finding (after dedup/demotion) OR any REFUTED lens verdict
    overall = "HOLDS"
    for f in merged_findings:
        if f.severity == "HIGH":
            overall = "REFUTED"
            break
    if overall == "HOLDS":
        for lens_id, vobj in verdicts.items():
            if vobj.get("verdict") == "REFUTED":
                overall = "REFUTED"
                break

    counts = count_by_severity(merged_findings)

    # Build provenance
    reviewers = list(dict.fromkeys(r.reviewer for r in reports))  # deduplicated, ordered
    packs_prov: dict[str, str] = {}
    for r in reports:
        packs_prov[r.pack] = r.pack_version

    drafter_vals = list(dict.fromkeys(r.drafter for r in reports))
    drafter_str = ",".join(drafter_vals) if drafter_vals else "unknown"

    tiers = list(dict.fromkeys(r.tier for r in reports))
    tier_str = ",".join(tiers)

    provenance = {
        "reviewers": reviewers,
        "packs": packs_prov,
        "drafter": drafter_str,
        "tier": tier_str,
    }

    # Streak update (per pack)
    for pack_name in packs_prov:
        pack_findings = [f for f in merged_findings if f.pack == pack_name]
        update_streaks(state_dir, pack_name, pack_findings, reviewers)

    merged: dict = {
        "findings": [f.to_dict() for f in merged_findings],
        "verdicts": verdicts,
        "overall": overall,
        "counts": counts,
        "provenance": provenance,
    }

    # Audit MD
    if emit_audit_md:
        merged_json_str = json.dumps(merged, indent=2)
        _emit_audit_md(emit_audit_md, slug, merged, merged_json_str)

    return merged


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main():
    parser = argparse.ArgumentParser(
        description="Merge adversarial reviewer reports into a combined verdict."
    )
    parser.add_argument(
        "reports", nargs="+", metavar="report.json",
        help="One or more reviewer-report JSON files"
    )
    parser.add_argument(
        "--emit-audit-md", metavar="PATH",
        help="Write an audit markdown file at this path"
    )
    parser.add_argument(
        "--slug", default="run",
        help="Short slug for the audit (used in the audit MD header)"
    )
    parser.add_argument(
        "--state-dir", metavar="DIR", default=None,
        help=f"Directory for state files (default: {_DEFAULT_STATE_DIR})"
    )
    args = parser.parse_args()

    state_dir = Path(args.state_dir) if args.state_dir else None
    emit_path = Path(args.emit_audit_md) if args.emit_audit_md else None

    report_paths = [Path(r) for r in args.reports]

    result = merge(
        report_paths=report_paths,
        state_dir=state_dir,
        slug=args.slug,
        emit_audit_md=emit_path,
    )

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    _main()

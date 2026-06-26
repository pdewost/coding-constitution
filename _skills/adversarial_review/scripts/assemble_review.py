"""
assemble_review.py — Assemble reviewer prompt(s) for an adversarial review run.

CLI:
    python3.12 scripts/assemble_review.py \\
        --pack <name> \\
        --artifact <path> [--artifact <path> ...] \\
        --tier skeptic|panel|workflow \\
        --drafter <model|unknown> \\
        --reviewer <model> \\
        [--render-evidence <path> ...] \\
        [--rotated <note>] \\
        [--packs-dir <dir>] \\
        [--state-dir <dir>] \\
        [--out <path>]

Exit codes:
    0  success
    2  reviewer == drafter (independence violation)
    3  pack requires_rendering=true and no --render-evidence provided
    4  zero-finding streak >= 3 and neither a changed reviewer nor a
       --lens-emphasis override is supplied (a --rotated note alone does
       not count as a real rotation)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import yaml

_SKILL_DIR = Path(__file__).parent.parent
_DEFAULT_PACKS_DIR = _SKILL_DIR / "lenses"
_DEFAULT_STATE_DIR = _SKILL_DIR / "state"

# Model families used for family-warning detection
_FAMILIES = [
    "fable", "opus", "sonnet", "haiku",
    "gpt", "gemini", "qwen", "llama", "mistral", "deepseek",
]

# The reviewer-report JSON contract (embedded in output as findings_schema)
_FINDINGS_SCHEMA = {
    "type": "object",
    "description": "Reviewer report — return ONLY this JSON, no prose wrapper.",
    "required": ["reviewer", "pack", "pack_version", "tier", "drafter",
                 "findings", "verdicts", "overall"],
    "properties": {
        "reviewer": {"type": "string"},
        "pack": {"type": "string"},
        "pack_version": {"type": "string"},
        "tier": {"type": "string", "enum": ["skeptic", "panel", "workflow"]},
        "drafter": {"type": "string"},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["id", "lens", "severity", "claim", "evidence", "cheapest_fix"],
                "properties": {
                    "id": {"type": "string"},
                    "lens": {"type": "string"},
                    "severity": {
                        "type": "string",
                        "enum": ["HIGH", "MED", "LOW"]
                    },
                    "claim": {"type": "string"},
                    "evidence": {
                        "type": "object",
                        "required": ["type", "ref", "literal"],
                        "properties": {
                            "type": {
                                "type": "string",
                                "enum": ["file_line", "command_output", "render_capture"]
                            },
                            "ref": {"type": "string"},
                            "literal": {"type": "string"}
                        }
                    },
                    "cheapest_fix": {"type": "string"}
                }
            }
        },
        "verdicts": {
            "type": "object",
            "description": "Map of lens_id -> {verdict: HOLDS|REFUTED, probe: string (required when HOLDS)}",
            "additionalProperties": {
                "type": "object",
                "required": ["verdict"],
                "properties": {
                    "verdict": {"type": "string", "enum": ["HOLDS", "REFUTED"]},
                    "probe": {"type": "string", "description": "Required and non-empty when verdict=HOLDS"}
                }
            }
        },
        "overall": {"type": "string"}
    }
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalize(model: str) -> str:
    return model.strip().lower()


def _family_of(model: str) -> str | None:
    m = _normalize(model)
    for fam in _FAMILIES:
        if fam in m:
            return fam
    return None


def _load_pack(pack_name: str, packs_dir: Path) -> dict:
    name = pack_name if pack_name.endswith(".yaml") else pack_name + ".yaml"
    pack_path = packs_dir / name
    if not pack_path.exists():
        print(f"ERROR: pack file not found: {pack_path}", file=sys.stderr)
        sys.exit(1)
    with open(pack_path) as fh:
        data = yaml.safe_load(fh)
    return data


def _load_zero_streaks(state_dir: Path) -> dict:
    streak_file = state_dir / "zero_streaks.json"
    if not streak_file.exists():
        return {}
    with open(streak_file) as fh:
        return json.load(fh)


def _round_robin_split(items: list, n: int) -> list[list]:
    """Split items into n buckets using round-robin assignment."""
    buckets: list[list] = [[] for _ in range(n)]
    for i, item in enumerate(items):
        buckets[i % n].append(item)
    return buckets


def _build_prompt_text(
    pack: dict,
    drafter: str,
    reviewer: str,
    artifacts: list[str],
    render_evidence: list[str],
    primary_lens_ids: list[str] | None,
    tier: str,
    is_panel_prompt: bool,
) -> str:
    """
    Assemble the full prompt text for a single reviewer prompt.

    Order (per spec):
    1. pack refutation_prompt
    2. independence preamble
    3. artifact list + render evidence
    4. evidence_gatherer instructions
    5. lens list (primary subset marked for panel)
    6. severity_rubric
    7. delegate_to instruction (if present)
    8. evidence rules
    9. EXACT output instruction
    """
    parts: list[str] = []

    # (1) Refutation prompt
    parts.append("## REFUTATION FRAMING\n")
    parts.append(pack["refutation_prompt"].strip())
    parts.append("")

    # (2) Independence preamble
    if drafter.lower() == "unknown":
        parts.append("## INDEPENDENCE\n")
        parts.append(
            f"You are the reviewer ({reviewer}). The drafter of this artifact is UNKNOWN. "
            f"You must review this artifact independently, without grading your own work "
            f"or deferring to any prior self-assessment."
        )
    else:
        parts.append("## INDEPENDENCE\n")
        parts.append(
            f"You are the reviewer ({reviewer}). The drafter of this artifact is {drafter}. "
            f"You are NOT that model. Do not self-grade, do not defer to the drafter's "
            f"reasoning, do not treat the drafter's own notes as evidence. "
            f"Your job is to REFUTE — assume the artifact is wrong until evidence proves otherwise."
        )
    parts.append("")

    # (3) Artifact list + render evidence
    parts.append("## ARTIFACTS\n")
    for art in artifacts:
        parts.append(f"  - {art}")
    if render_evidence:
        parts.append("\nRender evidence / screenshots:")
        for re_path in render_evidence:
            parts.append(f"  - {re_path}")
    parts.append("")

    # (4) Evidence gatherer instructions
    parts.append("## EVIDENCE GATHERING\n")
    parts.append(pack["evidence_gatherer"].strip())
    parts.append("")

    # (5) Lens list
    lenses = pack.get("lenses", [])
    parts.append("## LENSES\n")

    # Determine effort label based on tier and whether delegate_to present
    if tier == "skeptic":
        effort_label = "medium"
    else:
        effort_label = "high"

    for lens in lenses:
        lid = lens["id"]
        is_primary = (primary_lens_ids is None) or (lid in primary_lens_ids)
        marker = " [PRIMARY — focus here first]" if (is_panel_prompt and is_primary) else ""
        parts.append(f"### Lens: {lid}{marker}\n")
        parts.append(lens["prompt"].strip())
        parts.append("")

    # (6) Severity rubric
    rubric = pack.get("severity_rubric", {})
    parts.append("## SEVERITY RUBRIC\n")
    for level in ["HIGH", "MED", "LOW"]:
        if level in rubric:
            parts.append(f"  {level}: {rubric[level]}")
    parts.append("")

    # (7) delegate_to instruction (if present)
    delegate = pack.get("delegate_to")
    if delegate:
        capability = delegate.get("capability", "<capability>")
        is_optional = delegate.get("optional", False)
        parts.append("## DELEGATION\n")
        parts.append(
            f"If '{capability}' is available in your harness, run it at effort "
            f"{effort_label} and convert its output into the findings schema. "
            f"If unavailable, apply the lenses above yourself — "
            f"never return zero findings solely because the delegate is missing."
        )
        parts.append("")

    # (8) Evidence rules
    parts.append("## EVIDENCE REQUIREMENTS\n")
    parts.append(
        "Every finding MUST cite:\n"
        "  - evidence.ref: file path + line number, command string, or capture path\n"
        "  - evidence.literal: the EXACT quoted text, command output, or visible element\n"
        "\n"
        "Findings without both ref and literal will be automatically demoted to "
        "UNSUBSTANTIATED and excluded from the verdict.\n"
        "\n"
        "For every lens where you find NO problems, you MUST still provide a verdict entry "
        "with verdict=HOLDS and a non-empty probe field showing what you checked and confirmed."
    )
    parts.append("")

    # (9) Output instruction
    parts.append("## OUTPUT INSTRUCTION\n")
    parts.append(
        "Return ONLY the reviewer-report JSON — no prose before or after it, no markdown "
        "fences, no explanation. The JSON must match this schema exactly:\n"
    )
    parts.append(json.dumps(_FINDINGS_SCHEMA, indent=2))

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Main logic (importable)
# ---------------------------------------------------------------------------

def assemble(
    pack_name: str,
    artifacts: list[str],
    tier: str,
    drafter: str,
    reviewer: str,
    render_evidence: list[str] | None = None,
    rotated: str | None = None,
    lens_emphasis: str | None = None,
    packs_dir: Path | None = None,
    state_dir: Path | None = None,
) -> dict:
    """
    Core logic for assemble_review. Returns the output dict or exits with a
    non-zero code on refusal conditions.
    """
    if packs_dir is None:
        packs_dir = _DEFAULT_PACKS_DIR
    if state_dir is None:
        state_dir = _DEFAULT_STATE_DIR
    if render_evidence is None:
        render_evidence = []

    # Exit 2: reviewer == drafter (but NOT when drafter is 'unknown')
    drafter_unknown = (_normalize(drafter) == "unknown")

    if not drafter_unknown and _normalize(reviewer) == _normalize(drafter):
        print(
            f"ERROR (exit 2): reviewer '{reviewer}' == drafter '{drafter}' — "
            f"independence violation. Use a different model as reviewer.",
            file=sys.stderr,
        )
        sys.exit(2)

    pack = _load_pack(pack_name, packs_dir)
    pack_version = str(pack.get("version", "unknown"))

    # Exit 3: pack requires_rendering but no render-evidence
    if pack.get("requires_rendering", False) and not render_evidence:
        print(
            f"ERROR (exit 3): pack '{pack_name}' requires_rendering=true but "
            f"no --render-evidence paths were provided. "
            f"Provide screenshot/capture paths or a live rendering URL.",
            file=sys.stderr,
        )
        sys.exit(3)

    # Exit 4: zero-finding streak >= 3 without a REAL rotation
    # A real rotation means: the requested reviewer differs from last_reviewer
    # for this pack (normalized), OR a --lens-emphasis override is given.
    # A --rotated note alone with the same reviewer and no lens override is
    # NOT sufficient — the gate is bypassable only by actually rotating.
    streaks = _load_zero_streaks(state_dir)
    pack_streak = streaks.get(pack_name, {}).get("streak", 0)
    if pack_streak >= 3:
        last_reviewer = streaks.get(pack_name, {}).get("last_reviewer", "")
        reviewer_changed = _normalize(reviewer) != _normalize(last_reviewer)
        rotation_real = reviewer_changed or bool(lens_emphasis)
        if not rotation_real:
            print(
                f"ERROR (exit 4): pack '{pack_name}' has {pack_streak} consecutive "
                f"zero-finding HOLDS verdicts. Last reviewer: '{last_reviewer}'. "
                f"A real rotation is required: use a different --reviewer (not '{last_reviewer}') "
                f"or supply a --lens-emphasis override. "
                f"A --rotated note alone with the same reviewer does not count.",
                file=sys.stderr,
            )
            sys.exit(4)

    # Family warning
    provenance: dict = {}
    rev_family = _family_of(reviewer)
    draft_family = _family_of(drafter) if not drafter_unknown else None

    if drafter_unknown:
        provenance["drafter_unknown"] = True
        print(
            f"WARNING: drafter is 'unknown' — cannot check for family overlap. "
            f"Proceeding with drafter_unknown=true in provenance.",
            file=sys.stderr,
        )
    else:
        if rev_family and draft_family and rev_family == draft_family:
            print(
                f"WARNING: reviewer '{reviewer}' and drafter '{drafter}' appear to be "
                f"from the same model family ('{rev_family}'). This weakens independence. "
                f"Consider a reviewer from a different family.",
                file=sys.stderr,
            )
            provenance["family_warning"] = True

    # Record rotation provenance when a real rotation occurred (streak >= 3 gate passed)
    if pack_streak >= 3:
        last_reviewer = streaks.get(pack_name, {}).get("last_reviewer", "")
        reviewer_changed = _normalize(reviewer) != _normalize(last_reviewer)
        if rotated:
            provenance["rotation"] = rotated
        elif reviewer_changed:
            provenance["rotation"] = f"reviewer rotated {last_reviewer} -> {reviewer}"
    elif rotated:
        # rotated note supplied outside a streak context — record it anyway
        provenance["rotation"] = rotated

    # Tier-specific prompt assembly
    lenses = pack.get("lenses", [])
    all_lens_ids = [lens["id"] for lens in lenses]
    prompts: list[dict] = []

    if tier == "skeptic":
        text = _build_prompt_text(
            pack=pack,
            drafter=drafter,
            reviewer=reviewer,
            artifacts=artifacts,
            render_evidence=render_evidence,
            primary_lens_ids=None,
            tier=tier,
            is_panel_prompt=False,
        )
        prompts.append({
            "label": "skeptic",
            "primary_lenses": all_lens_ids,
            "text": text,
        })

    elif tier in ("panel", "workflow"):
        # 3 prompts; round-robin split of lens ids into primary subsets
        buckets = _round_robin_split(all_lens_ids, 3)
        for panel_idx in range(3):
            primary_ids = buckets[panel_idx]
            text = _build_prompt_text(
                pack=pack,
                drafter=drafter,
                reviewer=reviewer,
                artifacts=artifacts,
                render_evidence=render_evidence,
                primary_lens_ids=primary_ids,
                tier=tier,
                is_panel_prompt=True,
            )
            prompts.append({
                "label": f"panel-{panel_idx + 1}",
                "primary_lenses": primary_ids,
                "text": text,
            })

        if tier == "workflow":
            provenance["workflow_opt_in_required"] = True

    output = {
        "pack": pack_name,
        "pack_version": pack_version,
        "tier": tier,
        "reviewer": reviewer,
        "drafter": drafter,
        "provenance": provenance,
        "prompts": prompts,
        "findings_schema": _FINDINGS_SCHEMA,
        "artifacts": artifacts,
        "render_evidence": render_evidence,
    }

    return output


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main():
    parser = argparse.ArgumentParser(
        description="Assemble reviewer prompt(s) for an adversarial review run."
    )
    parser.add_argument("--pack", required=True, help="Pack name (e.g. plan, code)")
    parser.add_argument(
        "--artifact", dest="artifacts", action="append", required=True,
        metavar="PATH",
        help="Artifact path (repeatable, at least 1 required)"
    )
    parser.add_argument(
        "--tier", required=True, choices=["skeptic", "panel", "workflow"],
        help="Review tier"
    )
    parser.add_argument(
        "--drafter", required=True,
        help="Drafter model id or 'unknown'"
    )
    parser.add_argument(
        "--reviewer", required=True,
        help="Reviewer model id"
    )
    parser.add_argument(
        "--render-evidence", dest="render_evidence", action="append",
        metavar="PATH", default=[],
        help="Render evidence path (repeatable)"
    )
    parser.add_argument(
        "--rotated", metavar="NOTE",
        help=(
            "Optional rotation note. When streak >= 3, a real rotation is still "
            "required: a different --reviewer OR a --lens-emphasis override. "
            "This note alone with the same reviewer does NOT bypass exit 4."
        )
    )
    parser.add_argument(
        "--lens-emphasis", metavar="LENS_IDS",
        help=(
            "Comma-separated lens ids to emphasise in this run. "
            "When streak >= 3, supplying this counts as a real rotation override."
        )
    )
    parser.add_argument(
        "--packs-dir", metavar="DIR", default=None,
        help=f"Directory containing lens packs (default: {_DEFAULT_PACKS_DIR})"
    )
    parser.add_argument(
        "--state-dir", metavar="DIR", default=None,
        help=f"Directory for state files (default: {_DEFAULT_STATE_DIR})"
    )
    parser.add_argument(
        "--out", metavar="PATH", default=None,
        help="Output path (default: stdout)"
    )
    args = parser.parse_args()

    packs_dir = Path(args.packs_dir) if args.packs_dir else None
    state_dir = Path(args.state_dir) if args.state_dir else None

    result = assemble(
        pack_name=args.pack,
        artifacts=args.artifacts,
        tier=args.tier,
        drafter=args.drafter,
        reviewer=args.reviewer,
        render_evidence=args.render_evidence or [],
        rotated=args.rotated,
        lens_emphasis=args.lens_emphasis,
        packs_dir=packs_dir,
        state_dir=state_dir,
    )

    out_json = json.dumps(result, indent=2)

    if args.out:
        with open(args.out, "w") as fh:
            fh.write(out_json)
            fh.write("\n")
        print(f"Written to {args.out}")
    else:
        print(out_json)


if __name__ == "__main__":
    _main()

#!/usr/bin/env python3.12
"""lint_dispatch_class.py — Art. 9's missing enforcement (lifecycle PLAN P0.3).

ART. 9 VERBATIM: *"Plans and budgets declare task classes, never model names."* Until now that was
a documented discipline with **zero mechanical enforcement** — one of five such articles measured
2026-08-27 (Arts. 3, 8, 9, 10, 14). Art. 9 is the cheapest of the five to close because its rule is
a check on FORM, not on fact: a dispatch site either names a class or it does not.

WHAT IT ASSERTS. Every skill that dispatches a SUBAGENT declares, in its own `SKILL.md`:

    DISPATCH-CLASS: <task_class>@<effort>      # e.g. adversarial@high
    DISPATCH-CLASS: none — <reason>            # explicitly does not dispatch subagents

and never a model name, and never a bare reliance on inheriting the caller's model.

THE VOCABULARY COMES FROM `routing_policy.yaml`, NOT FROM THIS FILE. That is P0.3's explicit
requirement and it is the whole point: a lint carrying its own hard-coded class list would drift
from the policy it enforces, which is the defect Art. 9 exists to prevent, reproduced inside its
own enforcement. If `routing_policy` gains a class tomorrow, this lint accepts it tomorrow.

POPULATION IS DISCOVERED, NOT FROZEN. Candidates are every `_skills/*/SKILL.md` matching
`subagent|spawn`. **That grep over-matches on purpose and the over-match must be dispositioned
rather than silently dropped:** measured 2026-08-27, it returns five files, and two of them
(`example_scraper`, `example_llm_client`) mention `spawn` only about OS PROCESSES — Chrome via CDP, an MLX
server subprocess — not about subagents. Both are dispositioned `none` in their own SKILL.md, which
is why an explicit `none` is a first-class answer here. A skill added tomorrow enters the population
automatically; a required one cannot leave it by having its wording edited.

EXIT: 0 clean · 1 findings · 2 usage error.
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
POLICY = ROOT / "governance" / "routing_policy.yaml"
SKILLS = ROOT / "_skills"

DISPATCH_HINT = re.compile(r"subagent|spawn", re.I)
DECL = re.compile(r"^DISPATCH-CLASS:\s*(.+?)\s*$", re.M)
# Model literals a declaration must never contain. Art. 9's actual prohibition.
MODEL_LITERAL = re.compile(
    r"\b(claude|opus|sonnet|haiku|fable|gpt|o\d|gemini|llama|mistral|qwen|deepseek|gemma|hermes)\b",
    re.I,
)
INHERIT = re.compile(r"\binherit\b", re.I)


class Finding:
    def __init__(self, skill: str, detail: str):
        self.skill, self.detail = skill, detail

    def __str__(self) -> str:
        return f"  [dispatch-class] _skills/{self.skill}/SKILL.md\n      {self.detail}"


def vocabulary(policy: pathlib.Path) -> tuple[set[str], set[str]]:
    """(task classes, effort levels) read from routing_policy.yaml.

    Deliberately a small hand parse rather than a YAML dependency: this lint must run from a
    pre-commit hook and a governance gate with no third-party imports available.
    """
    text = policy.read_text(encoding="utf-8")

    def block(name: str) -> list[str]:
        m = re.search(rf"^{name}:\s*$\n((?:(?:  .*)?\n)*)", text, re.M)
        return re.findall(r"^  ([a-z_]+):", m.group(1), re.M) if m else []

    classes = set(block("task_classes"))
    efforts = {e for e in block("effort") if e in {"low", "medium", "high"}}
    return classes, efforts


def candidates(skills_dir: pathlib.Path) -> list[pathlib.Path]:
    out = []
    for p in sorted(skills_dir.glob("*/SKILL.md")):
        try:
            if DISPATCH_HINT.search(p.read_text(encoding="utf-8", errors="replace")):
                out.append(p)
        except OSError:
            continue
    return out


def check(skills_dir: pathlib.Path = SKILLS,
          policy: pathlib.Path = POLICY) -> list[Finding]:
    classes, efforts = vocabulary(policy)
    if not classes or not efforts:
        raise RuntimeError(f"no task_classes/effort vocabulary in {policy}")
    out: list[Finding] = []
    for p in candidates(skills_dir):
        skill = p.parent.name
        decls = DECL.findall(p.read_text(encoding="utf-8", errors="replace"))
        if not decls:
            out.append(Finding(skill, "mentions subagent dispatch but declares no "
                                      "`DISPATCH-CLASS:` — Art. 9 requires a task class, or an "
                                      "explicit `none — <reason>` if it dispatches nothing"))
            continue
        for d in decls:
            if d.lower().startswith("none"):
                if len(d.strip()) <= len("none") + 1:
                    out.append(Finding(skill, "`DISPATCH-CLASS: none` with no reason — an "
                                              "undispositioned exemption is how a population "
                                              "silently shrinks"))
                continue
            if MODEL_LITERAL.search(d):
                out.append(Finding(skill, f"declares a MODEL, not a class: {d!r} — Art. 9: "
                                          f"'declare task classes, never model names'"))
                continue
            if INHERIT.search(d):
                out.append(Finding(skill, f"relies on inheriting the caller's model: {d!r} — "
                                          f"a worker that inherits declares nothing"))
                continue
            # ART. 8, verbatim: "never delegate destructive operations." A site declaring a
            # `destructive` class is not a routing choice to be validated — it is the prohibited
            # act, stated. Art. 9's vocabulary check would happily PASS it, because `destructive`
            # is a legitimate task class; it is legitimate for the MAIN LOOP and forbidden for a
            # SUBAGENT. Checked here rather than in a second lint because the declaration this
            # reads is the same one, and a separate file would mean two populations to keep in
            # step — which is how one of them stops being maintained.
            if d.strip().split("@")[0].lower() == "destructive":
                out.append(Finding(skill, f"declares {d.strip()!r} — Art. 8: 'NEVER delegate "
                                          f"destructive operations'. A destructive act is run by "
                                          f"the main loop under the founder's gate, never handed "
                                          f"to a subagent."))
                continue
            m = re.fullmatch(r"([a-z_]+)@([a-z]+)", d.strip())
            if not m:
                out.append(Finding(skill, f"malformed declaration {d!r} — expected "
                                          f"`<class>@<effort>`"))
                continue
            cls, eff = m.group(1), m.group(2)
            if cls not in classes:
                out.append(Finding(skill, f"unknown task class {cls!r} — routing_policy.yaml "
                                          f"defines: {', '.join(sorted(classes))}"))
            if eff not in efforts:
                out.append(Finding(skill, f"unknown effort {eff!r} — routing_policy.yaml "
                                          f"defines: {', '.join(sorted(efforts))}"))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--skills", default=str(SKILLS))
    ap.add_argument("--policy", default=str(POLICY))
    a = ap.parse_args(argv)
    sd, pol = pathlib.Path(a.skills), pathlib.Path(a.policy)
    if not sd.is_dir() or not pol.is_file():
        print(f"missing skills dir or policy: {sd} / {pol}", file=sys.stderr)
        return 2
    try:
        found = check(sd, pol)
    except Exception as e:                                   # noqa: BLE001
        print(f"lint_dispatch_class: ERROR {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    classes, efforts = vocabulary(pol)
    print(f"lint_dispatch_class: {len(candidates(sd))} candidate skill(s); vocabulary from "
          f"{pol.name}: {len(classes)} class(es), {len(efforts)} effort(s)")
    if not found:
        print("  clean")
        return 0
    for f in found:
        print(f)
    print(f"\n{len(found)} finding(s).")
    return 1


if __name__ == "__main__":
    sys.exit(main())

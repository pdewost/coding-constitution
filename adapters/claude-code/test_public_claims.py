#!/usr/bin/env python3
"""test_public_claims.py — checkable invariants about what the PUBLISHED tree claims and ships.

Runs on the extracted public tree, where it ships as `adapters/claude-code/test_public_claims.py` (the author's
extraction script and this file's source are private, not shipped). The tree root is two
folders up from this file there, or `PUBLIC_TREE` when set (to run it from the private workspace against a clone).
A root that is not a published tree (no `DECLARATION.md` or no `adapters/claude-code/`) skips the module.

FS-3 (PLAN_fix_external_reader_claims_verify_gate_2026-10-30, claim (iii)): no shipped `.py` may import
`_skills.<name>` for a `<name>` absent from the shipped `_skills/` unless the import sits in a `try` whose handler
catches ImportError and raises naming the remedy, and `<name>` is in the README's not-shipped sentence; no shipped
hook may name `_skills/<name>/` for such a `<name>` that the sentence omits. Evidence: the one import of 9a0cc77
(`_skills/verify_gate/scripts/judges.py:112`) met the first half only by care.

Run (in the published tree):
    python3.12 -m pytest adapters/claude-code/test_public_claims.py -q -p no:cacheprovider
(from elsewhere, set PUBLIC_TREE=<the clone> and give this file's path)
"""
import ast
import os
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(os.environ.get("PUBLIC_TREE") or Path(__file__).resolve().parents[2])
if not ((ROOT / "DECLARATION.md").is_file() and (ROOT / "adapters" / "claude-code").is_dir()):
    pytest.skip(f"not a published tree: {ROOT} (set PUBLIC_TREE)", allow_module_level=True)

_SKIP_PARTS = {".git", "__pycache__"}


def _files() -> list:
    """The shipped files: tracked ones in a clone (it may hold untracked worktrees), every file in an extracted copy."""
    if (ROOT / ".git").exists():
        out = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z"], capture_output=True, text=True, check=True)
        return sorted(ROOT / p for p in out.stdout.split("\0") if p)
    return sorted(p for p in ROOT.rglob("*") if p.is_file() and not (_SKIP_PARTS & set(p.relative_to(ROOT).parts)))


def _shipped(suffix: str = ""):
    return [p for p in _files() if p.name.endswith(suffix)]


def _shipped_skills() -> set:
    return {p.relative_to(ROOT).parts[1] for p in _files() if p.relative_to(ROOT).parts[0] == "_skills"
            and len(p.relative_to(ROOT).parts) > 2}


def _not_shipped_names() -> set:
    """The backticked names of the README list item that says what is cited but not shipped."""
    items = re.split(r"\n(?=- )|\n\s*\n", (ROOT / "README.md").read_text(encoding="utf-8"))
    hits = [it for it in items if "they are not shipped" in it]
    assert len(hits) == 1, f"README.md: expected one not-shipped sentence, found {len(hits)}"
    return set(re.findall(r"`([^`\s]+)`", hits[0]))


def _skill_of(module: str):
    parts = module.split(".")
    return parts[1] if len(parts) > 1 and parts[0] == "_skills" else None


def _imports(tree):
    """(lineno, skill name, node) for each import of `_skills.<name>`."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if _skill_of(a.name):
                    yield node.lineno, _skill_of(a.name), node
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            if _skill_of(node.module):
                yield node.lineno, _skill_of(node.module), node
            elif node.module == "_skills":
                for a in node.names:
                    yield node.lineno, a.name, node


def _catches_import_error(handler: ast.ExceptHandler) -> bool:
    t = handler.type
    names = t.elts if isinstance(t, ast.Tuple) else [t]
    return any(isinstance(n, ast.Name) and n.id in ("ImportError", "ModuleNotFoundError") for n in names)


def _raises_with_remedy(handler: ast.ExceptHandler) -> bool:
    """The handler raises an error whose message says what to do: it contains 'install' or 'pass '."""
    for node in ast.walk(handler):
        if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call):
            text = " ".join(c.value for a in node.exc.args for c in ast.walk(a)
                            if isinstance(c, ast.Constant) and isinstance(c.value, str))
            if re.search(r"\binstall\b|\bpass\s", text):
                return True
    return False


def _guarded(tree, target) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Try) and any(target is n for b in node.body for n in ast.walk(b)):
            if any(_catches_import_error(h) and _raises_with_remedy(h) for h in node.handlers):
                return True
    return False


def _hits():
    """Every import of an unshipped skill: (relpath:line, name, guarded)."""
    shipped = _shipped_skills()
    out = []
    for f in _shipped(".py"):
        tree = ast.parse(f.read_text(encoding="utf-8"), filename=str(f))
        for line, name, node in _imports(tree):
            if name not in shipped:
                out.append((f"{f.relative_to(ROOT)}:{line}", name, _guarded(tree, node)))
    return out


def _hook_hits():
    """Every `_skills/<name>/` named by a shipped hook, for an unshipped <name>: (relpath:line, name)."""
    shipped = _shipped_skills()
    out = []
    for f in _shipped():
        if f.relative_to(ROOT).parts[:3] != ("adapters", "claude-code", "hooks"):
            continue
        for i, ln in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            for name in re.findall(r"_skills/([A-Za-z0-9_]+)/", ln):
                if name not in shipped:
                    out.append((f"{f.relative_to(ROOT)}:{i}", name))
    return out


def test_fs3_an_unshipped_skill_import_is_guarded_and_declared():
    declared = _not_shipped_names()
    bad = [(where, name) for where, name, guarded in _hits() if not guarded or name not in declared]
    assert not bad, f"unguarded or undeclared imports of unshipped skills: {bad}"


def test_fs3_a_hook_names_only_shipped_or_declared_skills():
    declared = _not_shipped_names()
    bad = [(where, name) for where, name in _hook_hits() if name not in declared]
    assert not bad, f"hooks name unshipped skills the README omits: {bad}"


def test_fs3_the_hits_are_listed(capsys):
    """Not an assertion on the tree: prints each hit, so the run's output is the hits log (PLAN §9)."""
    with capsys.disabled():
        for where, name, guarded in _hits():
            print(f"\n  FS-3 import hit: {where} _skills.{name} guarded={guarded}")
        for where, name in _hook_hits():
            print(f"\n  FS-3 hook hit:   {where} _skills/{name}/")


# --- FS-2 (PLAN_fix_external_reader_claims v0.6.2, claim (ii)): verify_fires proves SessionStart only ------------------------
# Sites are located by section heading (README), by the PROVEN block (bootstrap.sh), by file (POLICY_CORE, the hook docstring),
# never by a bare mention of the tool's name: a broad claim that drops the name must still be caught.
_LIVE = "Live proof: SessionStart only"
_README_SECTIONS = ("Governing more than one project", "See it work", "Adopt it")


def _section(text: str, heading_prefix: str) -> str:
    """The body of the `## <heading_prefix>...` section, up to the next `## ` heading (fenced `#` comments are not headings)."""
    out, take, fence = [], False, False
    for ln in text.splitlines():
        if ln.startswith("```"):
            fence = not fence
        if not fence and ln.startswith("## "):
            if take:
                break
            take = ln[3:].startswith(heading_prefix)
            continue
        if take:
            out.append(ln)
    assert take or out, f"README.md: no `## {heading_prefix}` section"
    return "\n".join(out)


def _paragraphs(text: str):
    return [p for p in re.split(r"\n\s*\n", text) if p.strip()]


@pytest.mark.parametrize("heading", _README_SECTIONS)
def test_fs2_readme_section_that_cites_verify_fires_says_sessionstart_only(heading):
    body = _section((ROOT / "README.md").read_text(encoding="utf-8"), heading)
    assert "verify_fires" in body, f"README `## {heading}...` no longer cites verify_fires (re-locate the site)"
    assert _LIVE in body, f"README `## {heading}...` cites verify_fires without: {_LIVE!r}"


def test_fs2_readme_keeps_no_superseded_wording():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    for para in (" ".join(p.split()) for p in _paragraphs(text)):   # unwrapped: a hard line break must not hide a phrase
        if "its enforcement is a plan" in para:
            assert "SessionStart" in para, f"'its enforcement is a plan' without SessionStart: {para[:160]!r}"
        if "0 of 146" in para:
            assert re.search(r"(counted|taken|read|measured) from|\bsource\b", para), \
                f"'0 of 146' without its source: {para[:160]!r}"


def _bootstrap_proven_block() -> str:
    lines = (ROOT / "adapters" / "claude-code" / "bootstrap.sh").read_text(encoding="utf-8").splitlines()
    idx = [i for i, ln in enumerate(lines) if "PROVEN" in ln]
    assert idx, "bootstrap.sh: no PROVEN block (re-locate the site)"
    return "\n".join(lines[idx[0]:idx[-1] + 1])


def test_fs2_bootstrap_proven_block_says_sessionstart_only():
    block = _bootstrap_proven_block()
    assert _LIVE in block, f"bootstrap.sh PROVEN block lacks {_LIVE!r}"
    # every line that says PROVEN names the event, so a broad claim cannot hide next to a narrow one
    # a wrapped sentence may carry the event on the previous line: accept only when the block's line before has it
    lines = block.splitlines()
    bad = [ln for i, ln in enumerate(lines) if "PROVEN" in ln and "SessionStart" not in ln
           and not (i and "SessionStart" in lines[i - 1])]
    assert not bad, f"bootstrap.sh PROVEN lines that do not say SessionStart: {bad}"
    assert "enforcement is a plan, not a control" not in block


def test_fs2_policy_core_has_no_self_proving():
    text = (ROOT / "spec" / "POLICY_CORE.md").read_text(encoding="utf-8")
    assert "self-proving" not in text.lower()
    assert "SessionStart" in text


def test_fs2_verify_advisory_docstring_has_no_verify_armed_claim():
    f = ROOT / "adapters" / "claude-code" / "hooks" / "verify_advisory.py"
    doc = ast.get_docstring(ast.parse(f.read_text(encoding="utf-8"))) or ""
    assert doc, "verify_advisory.py has no module docstring"
    assert "not yet verify-armed" not in doc


# --- FS-4 and D1 (PLAN_fix_external_reader_claims_verify_gate_2026-10-30 v0.6, constitution-publish): the README says what
# the v0.13.1 code does. Each check names the README block it reads; a block is a paragraph or list item, with blockquote
# markers removed and whitespace collapsed, so a re-wrap does not break it and a lost clause does.

def _readme() -> str:
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    return "\n".join(re.sub(r"^> ?", "", ln) for ln in text.splitlines())


def _blocks() -> list:
    return [" ".join(b.split()) for b in re.split(r"\n\s*\n|\n(?=- )|\n(?=\| )", _readme()) if b.strip()]


def _block(marker: str) -> str:
    hits = [b for b in _blocks() if marker in b]
    assert len(hits) == 1, f"README.md: expected one block holding {marker!r}, found {len(hits)}"
    return hits[0]


def _readme_section(heading: str) -> str:
    m = re.search(rf"^## {re.escape(heading)}\n(.*?)(?=^## |\Z)", _readme(), re.S | re.M)
    assert m, f"README.md: no section {heading!r}"
    return " ".join(m.group(1).split())


def _missing(block: str, clauses) -> list:
    return [c for c in clauses if c not in block]


def test_fs4h_the_verify_gate_row_states_the_receipt_contract_and_its_limits():
    row = _block("| **VERIFY-GATE** |")
    gone = _missing(row, (
        "the CLI spends once under a locked ledger",           # FS-1: locked, atomic ledger (D2 a)
        "the hooks check it without spending",                 # D2 a
        "until it is spent once — by the CLI included",        # FS-1.6, s142 F5-2
        "the next Stop refuses it",                            # F5-2
        "a corrupt ledger invalidates every receipt",          # FS-1: fails closed
        "does not yet record whether its review was independent",          # FS-4h clause 1
        "does not check that its verdict file still hashes to `verdict_sha256`",  # clause 2
        "the CLI's ledger follows the receipt's folder",       # clause 3
        "`CI=1` counts as an anchor",                          # clause 4
    ))
    assert not gone, f"VERIFY-GATE row lacks: {gone}"
    assert "single-use receipt" not in row, "VERIFY-GATE row still calls the receipt single-use without its conditions"


def test_fs4d_the_known_limits_name_run_time_interpreter_code():
    limits = _block("known limits in this release")
    gone = _missing(limits, ("only at run time", "a computed name", "a glob", "a script file it writes and runs"))
    assert not gone, f"known-limits block lacks: {gone}"


def test_fs4b_the_quickstart_names_the_unit_suite_and_its_prerequisites():
    run = _readme_section("See it work (60 seconds)")
    gone = _missing(run, ("`python3.12 -m pytest adapters/claude-code _skills`", "pytest", "PyYAML",
                          "zsh", "node", "osacompile"))
    assert not gone, f"quickstart lacks: {gone}"


def test_fs4f_unrecorded_figures_are_marked_author_reported():
    step6 = _block("**Step 6 is not a formality.**")
    assert "author-reported" in step6 or "reviews/" in step6, "Step 6 figures (76 days, 0 of 146) are neither marked nor linked"
    ratified = _block("adversarially reviewed before ratification")
    assert "author-reported" in ratified, "the pre-ratification '10+ HIGH' has no record and is not marked author-reported"


def test_fs3_the_readme_says_how_the_default_backend_ends():
    item = _block("they are not shipped")
    gone = _missing(item, ("`produce_review.py --backend stateless_llm`", "exit 4", "`judge_unreachable`", "`call_fn`"))
    assert not gone, f"not-shipped item lacks: {gone}"


def test_d1_the_status_list_names_release_v0131():
    status = _readme_section("Status & maturity")
    assert "- **Release v0.13.1**" in status, "Status & maturity has no v0.13.1 release bullet"
    bullet = _block("- **Release v0.13.1**")
    gone = _missing(bullet, ("a corrupt ledger", "a missing or malformed nonce", "more than 300 s ahead", "now invalid"))
    assert not gone, f"v0.13.1 bullet does not name the outcome changes (consumer note 2026-11-07): {gone}"
    gone = _missing(bullet, ("a non-finite timestamp", "a hard-linked ledger", "a symlinked ledger folder",
                             "a lock wait past 30 s", "a lock file replaced while held", "`<non-regular>`", "verify_gate 0.3.1"))
    assert not gone, f"v0.13.1 bullet does not name the 0.3.1 outcome changes (consumer note, 0.3.1 section): {gone}"

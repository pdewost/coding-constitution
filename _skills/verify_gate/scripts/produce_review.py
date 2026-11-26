#!/usr/bin/env python3
"""verify_gate.produce_review — run an independent review end to end and gate it.

seal evidence -> spawn/run the judge -> orchestrator attests out-of-band -> verify_gate
evaluates -> on PASS, emit a change-set-bound receipt.

Two ways in:
  - harness_subagent (PRIMARY, agent-driven): the agent spawns a fresh reviewer subagent and
    passes its verdict via `judge_fn` (or `agent_verdict=`). The orchestrator (the agent/harness)
    is the attestor; the subagent is the producer => attestor != producer => can block.
  - stateless_llm (FALLBACK): omit judge_fn and pass call_fn=<your model call>; without call_fn the
    default needs a local_llm-style client, which is NOT shipped, and the review ends judge_unreachable
    (exit 4) with a reason that says so.

CLI (stateless path, for CI / hooks; it takes no call_fn, so it needs that client installed):
  produce_review.py --artifact X --evidence-json E.json --backend stateless_llm
    --producer-id reviewer --drafter-id author --orchestrator-id ci --anchor-id <run-id>
    --changed f1 f2 --outdir DIR [--require-blocking]
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core            # noqa: E402
import evidence_bundle # noqa: E402
import review_runner   # noqa: E402
import receipt as receipt_mod  # noqa: E402
import judges          # noqa: E402
import review_package  # noqa: E402


def prepare(*, changed_paths: list[str], outdir: str) -> dict:
    """RV3-7, step 1 of the harness_subagent hand-over: build the package and return the reviewer brief and a suggested
    JOURNAL line that lists the package's paths (RV3-10: a reader can compare them with the session's edits)."""
    pkg = review_package.build([receipt_mod.qualify_path(p) for p in changed_paths], outdir)
    paths = [f["path"] for f in pkg["files"]]
    brief = "\n".join([
        "REVIEWER BRIEF (verify_gate receipt-v3)",
        f"snapshot folder: {pkg['snapshot']}",
        "read only the snapshot: its files are read-only copies of the change-set, numbered as in package.json:",
        *[f"  {f['copy']} = {f['path']}" for f in pkg["files"]],
        f"package_sha256: {pkg['package_sha256']}",
        "Return a verify_gate.verdict.v2: the v1 shape (schema, producer_id, drafter_id, lenses) plus a top-level",
        f'"package_sha256": "{pkg["package_sha256"]}". Never include attestation, provenance, anchor_kind or context_isolated.',
    ])
    journal = (f"JOURNAL: receipt-v3 package {pkg['package_sha256'][:12]} over {len(paths)} file(s): "
               + ", ".join(paths))
    return {"package": pkg, "brief": brief, "journal": journal}


def finish(*, verdict_json: str, outdir: str, orchestrator_id: str, anchor_id: str,
           require_blocking: bool = False) -> dict:
    """RV3-7, step 2: attest the subagent's verdict (stamping the package from outdir/package.json), evaluate, and on an
    issuable PASS write outdir/receipt.json over the package's paths. The verdict's bytes are kept as returned (copied
    verbatim), so its sha256 is the one the ledger spends. Every refusal is reported, never raised."""
    pkg_path = os.path.join(os.path.realpath(outdir), "package.json")
    # s329 F-3: the verdict's parent folders are resolved (a symlinked /tmp or artifact folder is fine); the leaf is not,
    # so the safe read still refuses a verdict file that is itself a link.
    # s519 F2: the parent is qualified WITHOUT normpath (qualify_path) and then realpath'd, so `link/..` is resolved as the
    # kernel does (the link first, then `..`), never folded lexically into another folder.
    v_q = receipt_mod.qualify_path(verdict_json)
    v_src = os.path.join(os.path.realpath(os.path.dirname(v_q) or os.sep), os.path.basename(v_q))
    try:
        files, _sha = receipt_mod.read_package(pkg_path)
        raw = receipt_mod._safe_read_bytes(v_src)
    except (receipt_mod.UnsafeFile, OSError) as e:
        return _refusal(f"no receipt issued: {e}")
    verdict = receipt_mod._json_obj(raw) if raw is not None else None
    if verdict is None:
        return {"exit": core.ExitCode.TAMPER, "decision": "tamper", "receipt_path": None,
                "reasons": [f"verdict unreadable or not a JSON object: {verdict_json}"], "receipt_refused": None}
    verdict_path = os.path.join(os.path.realpath(outdir), "verdict.json")
    try:
        if v_src != verdict_path:
            _write_no_follow(verdict_path, raw)          # s519 F3: never through a link planted in outdir
        attestation_path = review_runner.attest(
            verdict=verdict, backend="harness_subagent", orchestrator_id=orchestrator_id, anchor_id=anchor_id,
            context_isolated=True, outdir=outdir, package={"package_path": pkg_path})
        attestation = core.load_json(attestation_path)
    except (receipt_mod.UnsafeFile, OSError, ValueError) as e:
        return _refusal(f"no receipt issued: {e}")
    d = core.evaluate(verdict, attestation, require_blocking=require_blocking)
    receipt_path, refused = None, None
    if d.exit_code == core.ExitCode.PASS:
        ok, why = receipt_mod.issuable(verdict, attestation)
        if not ok:
            refused = f"no receipt issued: {why}"
        else:
            try:
                rec = receipt_mod.write_receipt(changed_paths=[f["path"] for f in files], verdict_path=verdict_path,
                                                gate_exit=0, attestation_ref=attestation_path, package_path=pkg_path)
            except receipt_mod.UnsafeFile as e:
                refused = f"no receipt issued: {e}"
            else:
                # s520 M2 / s519 F3: a failed or redirected write is a reported refusal, never a raise
                refused = _write_receipt_file(os.path.join(os.path.realpath(outdir), "receipt.json"), rec)
                if refused is None:
                    receipt_path = os.path.join(os.path.realpath(outdir), "receipt.json")
    code = core.ExitCode.RECEIPT_INVALID if (refused and d.exit_code == core.ExitCode.PASS) else d.exit_code
    return {"exit": code, "decision": _name(code), "verdict_path": verdict_path,
            "attestation_path": attestation_path, "receipt_path": receipt_path,
            "reasons": d.reasons + ([refused] if refused else []), "receipt_refused": refused}


def produce(*, artifact: str, evidence: list[dict], drafter_id: str, producer_id: str,
            backend: str, orchestrator_id: str, anchor_id: str, outdir: str,
            changed_paths=None, judge_fn=None, call_fn=None, agent_verdict: dict | None = None,
            require_blocking: bool = False) -> dict:
    # RV3-1/RV3-2: the package is built (and fingerprinted) BEFORE the judge runs; the judge sees only its snapshot.
    try:
        package = review_package.build([receipt_mod.qualify_path(p) for p in (changed_paths or [])], outdir)
    except (receipt_mod.UnsafeFile, OSError) as e:
        # R-2 kept under v3: an unsafe changed path is a reported refusal (exit 6), never a raise; no judge runs.
        # s329 F-3: so is a reused outdir (a package is built once, never refreshed in place), and s519 F3 any other
        # OSError from the outdir (a folder under a file, no permission).
        why = (f"{os.path.join(os.path.realpath(outdir), 'package')} already exists: use a new --outdir"
               if isinstance(e, FileExistsError) else str(e))
        refused = f"no receipt issued: {why}"
        return {"exit": core.ExitCode.RECEIPT_INVALID, "decision": _name(core.ExitCode.RECEIPT_INVALID),
                "verdict_path": None, "attestation_path": None, "receipt_path": None, "blocking": False,
                "advisory": False, "block_findings": [], "reasons": [refused], "receipt_refused": refused}
    bundle = evidence_bundle.seal(artifact=artifact, evidence=evidence, package=package)
    evidence_bundle.assert_no_intent(bundle)

    if agent_verdict is not None:
        judge_fn = lambda _b: agent_verdict  # noqa: E731 (the agent already ran the subagent)
    if judge_fn is None:
        if backend == "stateless_llm":
            _call = call_fn or judges.default_call_fn
            judge_fn = lambda b: judges.stateless_llm_judge(  # noqa: E731
                b, producer_id=producer_id, drafter_id=drafter_id, call_fn=_call)
        else:
            raise ValueError(
                f"backend {backend!r} is agent-driven: pass the spawned subagent's verdict via "
                "judge_fn= or agent_verdict=")

    res = review_runner.run_review(
        bundle=bundle, backend=backend, judge_fn=judge_fn,
        orchestrator_id=orchestrator_id, anchor_id=anchor_id, context_isolated=True, outdir=outdir, package=package)

    if res["judge_unreachable"]:
        return {"exit": core.ExitCode.JUDGE_UNREACHABLE, "decision": "judge_unreachable",
                "reason": res.get("reason", ""), "verdict_path": None, "attestation_path": None, "receipt_path": None,
                "blocking": False, "advisory": False, "block_findings": []}

    verdict = core.load_json(res["verdict_path"])
    attestation = core.load_json(res["attestation_path"])
    d = core.evaluate(verdict, attestation, require_blocking=require_blocking)

    receipt_path, receipt_refused, no_receipt = None, None, None
    issue_ok, issue_why = receipt_mod.issuable(verdict, attestation)
    if d.exit_code == core.ExitCode.PASS and changed_paths and not issue_ok:
        # s519 F1, founder card 11-26 "Keep exit 0, fix wording": the exit stays the gate's decision (as the CLI, F-5, and
        # the 0.4.0 t9/t10 pins), but the reason no receipt was written is reported
        no_receipt = f"no receipt written: {issue_why}"
    if d.exit_code == core.ExitCode.PASS and changed_paths and issue_ok:  # R-1
        try:
            # R-2: one path-qualification rule (absolute as spelled; relative joined to the cwd, no folding), and issuance
            # reads every file the way validation does: an UnsafeFile means no receipt, with its reason.
            rec = receipt_mod.write_receipt(changed_paths=[receipt_mod.qualify_path(p) for p in changed_paths],
                                            verdict_path=res["verdict_path"], gate_exit=0,
                                            attestation_ref=res["attestation_path"],
                                            package_path=package["package_path"])
        except receipt_mod.UnsafeFile as e:
            receipt_refused = f"no receipt issued: {e}"
        else:
            receipt_refused = _write_receipt_file(os.path.join(outdir, "receipt.json"), rec)   # s520 M2 / s519 F3
            if receipt_refused is None:
                receipt_path = os.path.join(outdir, "receipt.json")

    # s329 F-1: a receipt refused on a PASS is exit 6 (RECEIPT_INVALID), as in `finish`, never a silent 0.
    code = core.ExitCode.RECEIPT_INVALID if (receipt_refused and d.exit_code == core.ExitCode.PASS) else d.exit_code
    return {"exit": code, "decision": _name(code),
            "verdict_path": res["verdict_path"], "attestation_path": res["attestation_path"],
            "receipt_path": receipt_path, "blocking": d.blocking, "advisory": d.advisory,
            "block_findings": d.block_findings, "anchor_kind": d.anchor_kind,
            "reasons": d.reasons + [r for r in (receipt_refused, no_receipt) if r], "receipt_refused": receipt_refused}


def _write_no_follow(path: str, data: bytes) -> None:
    """Create or truncate `path` and write `data`, refusing a link at the leaf (O_NOFOLLOW: ELOOP raises OSError)."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o644)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)


def _write_receipt_file(path: str, rec) -> str | None:
    """Write the receipt JSON without following a link; None on success, else the refusal text (s520 M2, s519 F3)."""
    try:
        _write_no_follow(path, json.dumps(rec.as_dict(), indent=2).encode("utf-8"))
    except OSError as e:
        return f"no receipt issued: receipt not written: {e}"
    return None


def _refusal(why: str) -> dict:
    """A reported refusal before any decision (s329 F-3): exit 6, no receipt, the reason kept."""
    return {"exit": core.ExitCode.RECEIPT_INVALID, "decision": _name(core.ExitCode.RECEIPT_INVALID),
            "verdict_path": None, "attestation_path": None, "receipt_path": None, "reasons": [why],
            "receipt_refused": why}


def _name(code: int) -> str:
    return {0: "pass_or_advisory", 1: "block", 2: "identity", 3: "unanchored",
            4: "judge_unreachable", 5: "tamper", 6: "receipt_invalid"}.get(code, str(code))


def main(argv=None) -> int:  # pragma: no cover (CLI; the stateless local_llm path needs a model)
    import argparse
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["prepare"]:
        ap = argparse.ArgumentParser(prog="produce_review.py prepare")
        ap.add_argument("--changed", nargs="+", required=True)
        ap.add_argument("--outdir", required=True)
        a = ap.parse_args(argv[1:])
        try:
            out = prepare(changed_paths=a.changed, outdir=a.outdir)
        except (receipt_mod.UnsafeFile, OSError) as e:
            print(json.dumps({"decision": "package not built", "reason": str(e)}))
            return core.ExitCode.RECEIPT_INVALID
        print(out["brief"])
        print(out["journal"])
        return 0
    if argv[:1] == ["finish"]:
        ap = argparse.ArgumentParser(prog="produce_review.py finish")
        ap.add_argument("--verdict-json", required=True)
        ap.add_argument("--outdir", required=True)
        ap.add_argument("--orchestrator-id", required=True)
        ap.add_argument("--anchor-id", required=True)
        ap.add_argument("--require-blocking", action="store_true")
        a = ap.parse_args(argv[1:])
        try:
            out = finish(verdict_json=a.verdict_json, outdir=a.outdir, orchestrator_id=a.orchestrator_id,
                         anchor_id=a.anchor_id, require_blocking=a.require_blocking)
        except (receipt_mod.UnsafeFile, OSError) as e:
            print(json.dumps({"decision": "receipt not issued", "reason": str(e)}))
            return core.ExitCode.RECEIPT_INVALID
        print(json.dumps(out, indent=2))
        return out["exit"]
    ap = argparse.ArgumentParser(description="produce + gate an independent review")
    ap.add_argument("--artifact", required=True)
    ap.add_argument("--evidence-json", required=True, help="path to a JSON list of evidence items")
    ap.add_argument("--backend", default="stateless_llm",
                    help="stateless_llm (default) needs a local_llm-style model client, which is not shipped: "
                         "without it the run ends at exit 4 (judge_unreachable) with a reason naming call_fn")
    ap.add_argument("--producer-id", required=True)
    ap.add_argument("--drafter-id", required=True)
    ap.add_argument("--orchestrator-id", required=True)
    ap.add_argument("--anchor-id", required=True)
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--changed", nargs="*", default=[])
    ap.add_argument("--require-blocking", action="store_true")
    a = ap.parse_args(argv)
    evidence = core.load_json(a.evidence_json)
    out = produce(artifact=a.artifact, evidence=evidence, drafter_id=a.drafter_id,
                  producer_id=a.producer_id, backend=a.backend, orchestrator_id=a.orchestrator_id,
                  anchor_id=a.anchor_id, outdir=a.outdir, changed_paths=a.changed,
                  require_blocking=a.require_blocking)
    print(json.dumps(out, indent=2))
    return out["exit"]


if __name__ == "__main__":
    sys.exit(main())

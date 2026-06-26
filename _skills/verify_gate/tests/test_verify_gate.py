"""Acceptance suite for the VERIFY-GATE — proves the converged design (3 §3 review rounds).
Run: python3 -m pytest _skills/verify_gate/tests/ -q
"""
import json
import os
import sys
import time

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))

import core            # noqa: E402
import receipt as R    # noqa: E402
import review_runner   # noqa: E402
import evidence_bundle # noqa: E402
import verdict_form    # noqa: E402
import verify_gate     # noqa: E402


# ---- builders --------------------------------------------------------------
def hi_finding(ref="src/x.py:10", literal="def f(:"):
    return {"severity": "high", "claim": "syntax error", "cheapest_fix": "fix paren",
            "evidence": {"type": "file", "ref": ref, "literal": literal}}


def mk_verdict(producer="reviewer", drafter="author", refuted=False, finding=None, holds_probe="checked"):
    lens = {"lens": "correctness", "verdict": "refuted" if refuted else "holds",
            "probe": holds_probe, "findings": [finding] if finding else []}
    return {"schema": "verify_gate.verdict.v1", "artifact": "art", "drafter_id": drafter,
            "producer_id": producer, "lenses": [lens]}


def mk_att(anchor_kind="harness_subagent", attestor="orchestrator", producer="reviewer", iso=True):
    return {"schema": "verify_gate.attestation.v1", "anchor_kind": anchor_kind,
            "anchor_id": "sub-123", "attestor": attestor, "producer_id": producer,
            "context_isolated": iso}


# ---- core gate exit codes --------------------------------------------------
def test_pass_clean_anchored():
    d = core.evaluate(mk_verdict(), mk_att())
    assert d.exit_code == core.ExitCode.PASS and not d.blocking


def test_block_high_refutation_anchored():
    d = core.evaluate(mk_verdict(refuted=True, finding=hi_finding()), mk_att())
    assert d.exit_code == core.ExitCode.BLOCK and d.blocking
    assert len(d.block_findings) == 1


def test_identity_producer_is_drafter():
    v = mk_verdict(producer="same", drafter="same", refuted=True, finding=hi_finding())
    d = core.evaluate(v, mk_att(producer="same"))
    assert d.exit_code == core.ExitCode.IDENTITY


def test_unanchored_self_with_require_blocking():
    # real HIGH refutation but anchor=self => advisory; caller requires blocking => exit 3
    d = core.evaluate(mk_verdict(refuted=True, finding=hi_finding()),
                      mk_att(anchor_kind="self", attestor="reviewer"),
                      require_blocking=True)
    assert d.exit_code == core.ExitCode.UNANCHORED and d.advisory


def test_self_anchor_advisory_not_block():
    # same HIGH refutation, self anchor, NOT require_blocking => pass(advisory), never blocks
    d = core.evaluate(mk_verdict(refuted=True, finding=hi_finding()),
                      mk_att(anchor_kind="self", attestor="reviewer"))
    assert d.exit_code == core.ExitCode.PASS and d.advisory and not d.blocking


def test_faked_harness_self_attest_collapses_to_advisory():
    # The lie that doesn't help: claim harness_subagent but attestor == producer (one agent).
    d = core.evaluate(mk_verdict(refuted=True, finding=hi_finding()),
                      mk_att(anchor_kind="harness_subagent", attestor="reviewer", producer="reviewer"),
                      require_blocking=True)
    assert d.exit_code == core.ExitCode.UNANCHORED  # collapses to advisory, cannot block


def test_tamper_inline_attestation():
    v = mk_verdict()
    v["lenses"][0]["attestation"] = {"anchor_kind": "harness_subagent"}  # judge self-attests
    d = core.evaluate(v, mk_att())
    assert d.exit_code == core.ExitCode.TAMPER


def test_judge_unreachable():
    d = core.evaluate(mk_verdict(), mk_att(), judge_unreachable=True)
    assert d.exit_code == core.ExitCode.JUDGE_UNREACHABLE


def test_demotion_evidenceless_high_does_not_block():
    bad = {"severity": "high", "claim": "vibes", "evidence": {"type": "file", "ref": "", "literal": ""}}
    d = core.evaluate(mk_verdict(refuted=True, finding=bad), mk_att())
    assert d.exit_code == core.ExitCode.PASS and not d.blocking and len(d.demoted) == 1


def test_holds_without_probe_is_malformed():
    v = mk_verdict(holds_probe="")
    d = core.evaluate(v, mk_att())
    assert d.exit_code == core.ExitCode.TAMPER  # schema problem path


# ---- the human path can BLOCK (coder-neutrality) ---------------------------
def test_human_path_blocks_with_evidence():
    v = verdict_form.build_verdict(
        producer_id="human:alice", drafter_id="author", artifact="art",
        lenses=[{"lens": "ux", "verdict": "refuted",
                 "findings": [{"severity": "high", "claim": "contrast fails",
                               "evidence": {"type": "render", "ref": "home.png:btn",
                                            "literal": "#999 on #aaa = 1.6:1"}}]}])
    att = mk_att(anchor_kind="human_external", attestor="ci:reviewer-id", producer="human:alice")
    d = core.evaluate(v, att)
    assert d.exit_code == core.ExitCode.BLOCK and d.blocking


def test_human_prose_only_is_demoted_no_block():
    v = verdict_form.build_verdict(
        producer_id="human:alice", drafter_id="author", artifact="art",
        lenses=[{"lens": "ux", "verdict": "refuted",
                 "findings": [{"severity": "high", "claim": "feels off", "evidence": {}}]}])
    att = mk_att(anchor_kind="human_external", attestor="ci:reviewer-id", producer="human:alice")
    d = core.evaluate(v, att)
    assert d.exit_code == core.ExitCode.PASS and len(d.demoted) == 1


# ---- receipt: change-set bound, single-use, fresh --------------------------
def test_receipt_roundtrip_and_changeset_binding(tmp_path):
    f = tmp_path / "code.py"; f.write_text("x = 1\n")
    vfile = tmp_path / "verdict.json"; vfile.write_text(json.dumps(mk_verdict()))
    ledger = str(tmp_path / "ledger.json")
    rec = R.write_receipt(changed_paths=[str(f)], verdict_path=str(vfile), gate_exit=0,
                          attestation_ref="att.json")
    ok, why = R.validate_receipt(rec.as_dict(), changed_paths=[str(f)], ledger_path=ledger)
    assert ok, why
    # edit the file => change-set hash flips => receipt no longer valid
    time.sleep(0.01); f.write_text("x = 2\n")
    ok2, why2 = R.validate_receipt(rec.as_dict(), changed_paths=[str(f)], ledger_path=ledger)
    assert not ok2 and "mismatch" in why2


def test_receipt_single_use_replay(tmp_path):
    f = tmp_path / "code.py"; f.write_text("x = 1\n")
    vfile = tmp_path / "verdict.json"; vfile.write_text(json.dumps(mk_verdict()))
    ledger = str(tmp_path / "ledger.json")
    rec = R.write_receipt(changed_paths=[str(f)], verdict_path=str(vfile), gate_exit=0,
                          attestation_ref="att.json")
    ok, _ = R.validate_receipt(rec.as_dict(), changed_paths=[str(f)], ledger_path=ledger)
    assert ok
    R.consume(rec.nonce, ledger)
    ok2, why2 = R.validate_receipt(rec.as_dict(), changed_paths=[str(f)], ledger_path=ledger)
    assert not ok2 and "replay" in why2


def test_receipt_stale(tmp_path):
    f = tmp_path / "code.py"; f.write_text("x = 1\n")
    rec = R.Receipt(changeset_sha256=R.changeset_sha256([str(f)]), verdict_sha256="v",
                    gate_exit=0, ts=time.time() - 99999, attestation_ref="a", nonce="n1")
    ok, why = R.validate_receipt(rec.as_dict(), changed_paths=[str(f)],
                                 ledger_path=str(tmp_path / "l.json"))
    assert not ok and "stale" in why


# ---- orchestrator (review_runner) ------------------------------------------
def test_runner_attests_out_of_band(tmp_path):
    bundle = evidence_bundle.seal(artifact="art", evidence=[{"type": "file", "ref": "x:1"}])
    out = review_runner.run_review(
        bundle=bundle, backend="harness_subagent",
        judge_fn=lambda b: mk_verdict(refuted=True, finding=hi_finding()),
        orchestrator_id="parent-harness", anchor_id="sub-9", context_isolated=True,
        outdir=str(tmp_path))
    assert not out["judge_unreachable"]
    att = json.load(open(out["attestation_path"]))
    assert att["attestor"] == "parent-harness" and att["producer_id"] == "reviewer"
    d = core.evaluate(json.load(open(out["verdict_path"])), att)
    assert d.exit_code == core.ExitCode.BLOCK  # external orchestrator => can block


def test_runner_self_invoked_is_advisory(tmp_path):
    # The bare local case: the same agent is orchestrator AND judge => attestor == producer.
    bundle = evidence_bundle.seal(artifact="art", evidence=[])
    out = review_runner.run_review(
        bundle=bundle, backend="harness_subagent",
        judge_fn=lambda b: mk_verdict(producer="agent", refuted=True, finding=hi_finding()),
        orchestrator_id="agent", anchor_id="sub-9", context_isolated=True, outdir=str(tmp_path))
    att = json.load(open(out["attestation_path"]))
    d = core.evaluate(json.load(open(out["verdict_path"])), att, require_blocking=True)
    assert d.exit_code == core.ExitCode.UNANCHORED  # attestor==producer => advisory


def test_runner_judge_unreachable(tmp_path):
    out = review_runner.run_review(
        bundle={"schema": "verify_gate.bundle.v1", "artifact": "a", "evidence": []},
        backend="stateless_llm", judge_fn=lambda b: None,
        orchestrator_id="o", anchor_id="c1", context_isolated=True, outdir=str(tmp_path))
    assert out["judge_unreachable"] and out["verdict_path"] is None


# ---- evidence bundle seals intent ------------------------------------------
def test_seal_rejects_intent():
    with pytest.raises(ValueError):
        evidence_bundle.seal(artifact="a", evidence=[{"type": "file", "answer_key": "secret"}])


# ---- CLI: override downgrades a block, audited -----------------------------
def test_cli_override_downgrades_block(tmp_path):
    v = tmp_path / "v.json"; v.write_text(json.dumps(mk_verdict(refuted=True, finding=hi_finding())))
    a = tmp_path / "a.json"; a.write_text(json.dumps(mk_att()))
    rc = verify_gate.main(["--verdict", str(v), "--attestation", str(a), "--override", "founder waiver"])
    assert rc == core.ExitCode.PASS


def test_cli_block_exit_code(tmp_path):
    v = tmp_path / "v.json"; v.write_text(json.dumps(mk_verdict(refuted=True, finding=hi_finding())))
    a = tmp_path / "a.json"; a.write_text(json.dumps(mk_att()))
    rc = verify_gate.main(["--verdict", str(v), "--attestation", str(a)])
    assert rc == core.ExitCode.BLOCK

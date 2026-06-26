"""Acceptance suite for the review PRODUCER (judges + produce_review) — proves the end-to-end
flow: seal -> judge -> out-of-band attest -> gate -> receipt, for both backends."""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))

import core            # noqa: E402
import judges          # noqa: E402
import produce_review  # noqa: E402


def _verdict_json(producer="reviewer", drafter="author", refuted=True, with_evidence=True):
    f = {"severity": "high", "claim": "syntax error", "cheapest_fix": "fix paren",
         "evidence": {"type": "file", "ref": "x.py:10" if with_evidence else "",
                      "literal": "def f(:" if with_evidence else ""}}
    lens = {"lens": "correctness", "verdict": "refuted" if refuted else "holds",
            "probe": "" if refuted else "compiled", "findings": [f] if refuted else []}
    return json.dumps({"schema": "verify_gate.verdict.v1", "artifact": "art",
                       "drafter_id": drafter, "producer_id": producer, "lenses": [lens]})


# ---- judges.parse_verdict --------------------------------------------------
def test_parse_plain_json():
    v = judges.parse_verdict(_verdict_json(), producer_id="r", drafter_id="d", artifact="a")
    assert v and v["producer_id"] == "r" and v["lenses"][0]["verdict"] == "refuted"


def test_parse_fenced_json():
    raw = "Here is my review:\n```json\n" + _verdict_json() + "\n```\nthanks"
    v = judges.parse_verdict(raw, producer_id="r", drafter_id="d", artifact="a")
    assert v is not None and v["producer_id"] == "r"


def test_parse_strips_self_attestation():
    obj = json.loads(_verdict_json())
    obj["attestation"] = {"anchor_kind": "harness_subagent"}  # a model tries to self-attest
    v = judges.parse_verdict(json.dumps(obj), producer_id="r", drafter_id="d", artifact="a")
    assert "attestation" not in v and "anchor_kind" not in v


def test_parse_garbage_returns_none():
    assert judges.parse_verdict("no json here", producer_id="r", drafter_id="d", artifact="a") is None


def test_stateless_judge_with_fake_call():
    v = judges.stateless_llm_judge({"schema": "verify_gate.bundle.v1", "artifact": "art", "evidence": []},
                                   producer_id="rev", drafter_id="auth",
                                   call_fn=lambda prompt: _verdict_json(producer="rev", drafter="auth"))
    assert v and v["producer_id"] == "rev"


# ---- produce() end-to-end --------------------------------------------------
def test_produce_stateless_blocks(tmp_path):
    out = produce_review.produce(
        artifact="art", evidence=[{"type": "file", "ref": "x:1"}], drafter_id="author",
        producer_id="reviewer", backend="stateless_llm", orchestrator_id="ci", anchor_id="run-7",
        outdir=str(tmp_path), call_fn=lambda p: _verdict_json())
    assert out["exit"] == core.ExitCode.BLOCK and out["blocking"]
    assert len(out["block_findings"]) == 1


def test_produce_harness_subagent_agent_verdict_blocks(tmp_path):
    out = produce_review.produce(
        artifact="art", evidence=[], drafter_id="author", producer_id="reviewer",
        backend="harness_subagent", orchestrator_id="parent-agent", anchor_id="sub-3",
        outdir=str(tmp_path), agent_verdict=json.loads(_verdict_json()))
    assert out["exit"] == core.ExitCode.BLOCK
    att = json.load(open(out["attestation_path"]))
    assert att["attestor"] == "parent-agent" and att["anchor_kind"] == "harness_subagent"


def test_produce_self_invoked_is_advisory(tmp_path):
    # orchestrator == producer (one agent) => attestor==producer => advisory, cannot block
    out = produce_review.produce(
        artifact="art", evidence=[], drafter_id="author", producer_id="agent",
        backend="harness_subagent", orchestrator_id="agent", anchor_id="sub-3",
        outdir=str(tmp_path), agent_verdict=json.loads(_verdict_json(producer="agent")),
        require_blocking=True)
    assert out["exit"] == core.ExitCode.UNANCHORED and out["advisory"]


def test_produce_pass_writes_receipt(tmp_path):
    code = tmp_path / "c.py"; code.write_text("x=1\n")
    out = produce_review.produce(
        artifact="art", evidence=[], drafter_id="author", producer_id="reviewer",
        backend="stateless_llm", orchestrator_id="ci", anchor_id="run-7", outdir=str(tmp_path),
        changed_paths=[str(code)], call_fn=lambda p: _verdict_json(refuted=False))
    assert out["exit"] == core.ExitCode.PASS and out["receipt_path"] and os.path.exists(out["receipt_path"])


def test_produce_evidenceless_does_not_block(tmp_path):
    out = produce_review.produce(
        artifact="art", evidence=[], drafter_id="author", producer_id="reviewer",
        backend="stateless_llm", orchestrator_id="ci", anchor_id="run-7", outdir=str(tmp_path),
        call_fn=lambda p: _verdict_json(with_evidence=False))
    assert out["exit"] == core.ExitCode.PASS and not out["blocking"]


def test_produce_judge_unreachable(tmp_path):
    out = produce_review.produce(
        artifact="art", evidence=[], drafter_id="author", producer_id="reviewer",
        backend="stateless_llm", orchestrator_id="ci", anchor_id="run-7", outdir=str(tmp_path),
        call_fn=lambda p: "not json at all")
    assert out["exit"] == core.ExitCode.JUDGE_UNREACHABLE

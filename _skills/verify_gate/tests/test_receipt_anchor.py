"""Receipt v2 (PLAN_verify_gate_receipt_v2_2026-11-02 v0.7): T9-T17. A receipt says how it was earned.

Every negative test changes only its target condition (X4-1): a control case in the same fixture issues and accepts
a receipt, so a red result names the one thing that changed. Hermetic: everything lives under pytest's tmp_path.
Run: python3.12 -m pytest _skills/verify_gate/tests/test_receipt_anchor.py -q

T17 MATRIX (R-3 v0.7, s239 H-4): case x channel -> expected value, red (R) or green (G) on 9c6bca0, and the mutant it kills there.
Channels: PR = receipt.project_root, VP = hook_support.vg_probe, LP = the gate's module-free probe (these three are tested in
this module and in .claude/hooks/test_stop_closeout_verify_gate.py by returned value), CLI = `verify_gate.py --receipt`,
SH = the Stop fixture with the helper loaded, SN = the Stop fixture without it (hook_support tampered). "-" = not run on that
channel (allow/block or the value does not differ there). Values: armed | unreadable | none. `+` = column added in s241 F-6 (green on 30de4ae; red on 9c6bca0 not measured). `d` = diagnostic only: the helper-less Stop path blocks either way (armed or
unreadable), so only the block TEXT differs, not allow/block (PLAN R-3; s241 F-6). The Stop fixture runs the hook through the command line
settings.json configures (`Fx.run`).

  case  expected   PR VP LP CLI SH SN  mutant killed
  (a)   armed       R  R  R  -   R  d  resolution failure always unreadable (lexical armed root ignored)
  (b)   unreadable  R  R  R  R   R  R  resolution failure read as absent  [CLI: exit 6, decision "armed state unreadable"]
  (c)   unreadable  R  R  R  -   R  R  resolution failure read as absent
  (d1)  armed       G  G  G  -   G  G  ENOENT read as failure   (guard)
  (d2)  none        G  G  G  -   G  G  ENOENT read as failure   (guard)
  (e1)  unreadable  R  G  G  R   R  G  wrong-type entry (FIFO `armed`) read as absent
  (e2)  unreadable  R  G  G  -   R  G  walk passes over a malformed candidate instead of stopping at it
  (e3)  unreadable  R  G  G  -   R  G  dangling `armed` link read as unarmed
  (e4)  unreadable  R  G  G  -   R  G  looping `NEOCORTEX` link read as absent
  (f)   unreadable  R  G  G  -   R  G  file `.verify` at a candidate read as absent (the cost, section 6)
  (g)   armed       R  R  R  -   R  R  lexical placement dropped (only the resolved path placed)
  (h)   unreadable  R  G  G  R   R  -  order-dependent or wrong precedence; Stop only on SH: the valid receipt over both readable
                                       files means only precedence (unreadable beats root) refuses; SN answers armed/not only
  (i)   armed       -  -  -  -   R  -  a second marker read with OSError -> not armed (helper-loaded Stop path only)
  (j)   armed       G  G  G  -   G  G  stop at the first candidate (an empty nested `.verify/`)   (guard)
  (k1)  unreadable  R  R  R  R   R  R  plain-file `NEOCORTEX` read as absent
  (k2)  none        G  G  G  -   G  G  a dangling `NEOCORTEX` link with no `.verify` read as unreadable (guard)
  (l1)  unreadable  R  G  G  -   R  R  only ELOOP a failure (EACCES injected; SH/SN with permission bits, skipped as root)
  (l2)  unreadable  R  G  G  -   R  R  same, outside every root
  (m1)  armed       -  -  -  -   R  +  event cwd dropped / joined to the wrong cwd (Stop with a valid receipt; positive evidence)
  (m2)  armed       G  G  G  -   G  G  resolve the already-normalised string   (guard: Path.resolve resolved links before `..`)
  (n)   unreadable  R  R  R  -   +  +  no link budget (41 links, external 10 s timeout, no lexical armed root)
  (o1)  dropped     -  -  -  -   G  -  guard: a relative write and a relative `rm` from the same cwd leave the change-set
  (o2)  kept        -  -  -  -   R  -  `rm` targets matched unqualified (same basename, cwd A vs cwd B)
  (m3)  unreadable  -  -  -  -   R  -  the hook's own cwd used for a relative edit with no usable cwd

Other T17 tests here: precedence across paths (unreadable > ambiguous > root > none), one root spelled two ways is one root, hostile
input never raises, the three channels agree on every case, and the AST parity of the placement text in the three files.
"""
import json
import os
import subprocess
import sys
import time

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, "..", "scripts")
sys.path.insert(0, SCRIPTS)

import core            # noqa: E402
import produce_review  # noqa: E402
import receipt as R    # noqa: E402
import review_runner   # noqa: E402
import verify_gate     # noqa: E402

RECEIPT_INVALID = 6
VG = os.path.join(SCRIPTS, "verify_gate.py")


def hi_finding():
    return {"severity": "high", "claim": "syntax error", "cheapest_fix": "fix",
            "evidence": {"type": "file", "ref": "src/x.py:10", "literal": "def f(:"}}


def mk_verdict(producer="reviewer", drafter="author", refuted=False):
    lens = {"lens": "correctness", "verdict": "refuted" if refuted else "holds", "probe": "checked",
            "findings": [hi_finding()] if refuted else []}
    return {"schema": "verify_gate.verdict.v1", "artifact": "art", "drafter_id": drafter,
            "producer_id": producer, "lenses": [lens]}


def mk_att(anchor_kind="harness_subagent", attestor="orchestrator", producer="reviewer", iso=True):
    return {"schema": "verify_gate.attestation.v1", "anchor_kind": anchor_kind, "anchor_id": "sub-123",
            "attestor": attestor, "producer_id": producer, "context_isolated": iso}


def mk_root(tmp_path, name="proj"):
    """A marked project root holding one changed file."""
    root = tmp_path / name
    (root / "NEOCORTEX").mkdir(parents=True)
    (root / ".verify").mkdir()
    (root / ".verify" / "armed").write_text("")
    code = root / "code.py"
    code.write_text("x = 1\n")
    return root, code


def put(path, obj):
    path.write_text(json.dumps(obj, indent=2))
    return path


def issue(root, code, *, verdict=None, att=None):
    """Write a verdict + attestation and issue a v2 receipt through write_receipt; returns (receipt dict, vpath, apath)."""
    v = put(root / "verdict.json", verdict or mk_verdict())
    a = put(root / "attestation.json", att or mk_att())
    rec = R.write_receipt(changed_paths=[str(code)], verdict_path=str(v), gate_exit=0, attestation_ref=str(a))
    return rec.as_dict(), v, a


def validate(rec, code, root, **kw):
    return R.validate_receipt(rec, changed_paths=[str(code)],
                              ledger_path=str(root / ".verify" / "_consumed_nonces.json"), **kw)


def test_control_anchored_clean_receipt_is_valid(tmp_path):
    root, code = mk_root(tmp_path)
    rec, _v, _a = issue(root, code)
    assert rec["schema"] == "verify_gate.receipt.v2"
    assert os.path.isabs(rec["verdict_ref"]) and os.path.isabs(rec["attestation_ref"])
    assert rec["attestation_sha256"]
    assert validate(rec, code, root) == (True, "receipt valid for the current change-set")


# ---- T9 / T10 (R-1): issue only what an armed gate may accept ----------------------------------------------------------
def _cli_issue(tmp_path, verdict, att):
    v = put(tmp_path / "v.json", verdict)
    a = put(tmp_path / "a.json", att)
    out = tmp_path / "receipt.json"
    code = tmp_path / "code.py"
    code.write_text("x = 1\n")
    rc = verify_gate.main(["--verdict", str(v), "--attestation", str(a), "--changed", str(code),
                           "--write-receipt", str(out)])
    return rc, out


def _produce_issue(tmp_path, verdict, orchestrator_id):
    code = tmp_path / "code.py"
    code.write_text("x = 1\n")
    outdir = tmp_path / "out"
    res = produce_review.produce(artifact="art", evidence=[{"type": "file", "ref": "x:1"}], drafter_id="author",
                                 producer_id="reviewer", backend="harness_subagent", orchestrator_id=orchestrator_id,
                                 anchor_id="sub-9", outdir=str(outdir), changed_paths=[str(code)],
                                 agent_verdict=verdict)
    return res, outdir / "receipt.json"


def test_t9_control_clean_anchored_pass_writes_a_receipt_at_both_entry_points(tmp_path):
    rc, out = _cli_issue(tmp_path, mk_verdict(), mk_att())
    assert rc == core.ExitCode.PASS and out.exists()
    (tmp_path / "p").mkdir()
    res, rpath = _produce_issue(tmp_path / "p", mk_verdict(), "orchestrator")
    assert res["exit"] == core.ExitCode.PASS and rpath.exists()


def test_t9_self_attested_high_refutation_writes_no_receipt_cli(tmp_path):
    """An un-anchored HIGH refutation is PASS (advisory) for require_blocking=False; it must still earn no receipt."""
    rc, out = _cli_issue(tmp_path, mk_verdict(refuted=True), mk_att(anchor_kind="self", attestor="reviewer"))
    assert rc == core.ExitCode.PASS
    assert not out.exists()


def test_t9_self_attested_high_refutation_writes_no_receipt_produce(tmp_path):
    res, rpath = _produce_issue(tmp_path, mk_verdict(refuted=True), "reviewer")   # orchestrator == producer
    assert res["exit"] == core.ExitCode.PASS and res["advisory"] is True
    assert res["receipt_path"] is None and not rpath.exists()


def test_t10_unanchored_clean_verdict_writes_no_receipt_cli(tmp_path):
    rc, out = _cli_issue(tmp_path, mk_verdict(), mk_att(anchor_kind="self", attestor="reviewer"))
    assert rc == core.ExitCode.PASS
    assert not out.exists()


def test_t10_unanchored_clean_verdict_writes_no_receipt_produce(tmp_path):
    res, rpath = _produce_issue(tmp_path, mk_verdict(), "reviewer")
    assert res["exit"] == core.ExitCode.PASS and res["receipt_path"] is None and not rpath.exists()


# ---- T11 (R-2): validation re-derives the decision ----------------------------------------------------------------------
def test_t11_reserialised_verdict_is_invalid(tmp_path):
    root, code = mk_root(tmp_path)
    rec, v, _a = issue(root, code)
    before = json.loads(v.read_text())
    v.write_text(json.dumps(before, indent=4) + "\n")     # whitespace only: same parsed content
    assert json.loads(v.read_text()) == before
    ok, why = validate(rec, code, root)
    assert not ok and "verdict_sha256" in why, why


def test_t11b_attestation_for_another_producer_is_invalid_though_hashes_agree(tmp_path):
    root, code = mk_root(tmp_path)
    rec, _v, a = issue(root, code)
    import hashlib
    new = mk_att(producer="someone-else")                    # same anchor_kind / attestor / context_isolated
    a.write_text(json.dumps(new))
    rec["attestation_sha256"] = hashlib.sha256(a.read_bytes()).hexdigest()   # only evaluate can refuse it now
    ok, why = validate(rec, code, root)
    assert not ok and "re-derived" in why, why


def test_t11c_only_the_attestor_changed_is_invalid(tmp_path):
    root, code = mk_root(tmp_path)
    rec, _v, a = issue(root, code)
    att = json.loads(a.read_text())
    att["attestor"] = "someone-else"
    a.write_text(json.dumps(att, indent=2))
    ok, why = validate(rec, code, root)
    assert not ok and "attestation_sha256" in why, why


def test_t11d_receipt_over_a_refuting_verdict_is_invalid(tmp_path, monkeypatch):
    """A hand-made receipt whose artifacts are self-attested and refuting: hashes agree, the decision does not.
    (Issuance itself now refuses these bytes, s241 F-2: the forger bypasses `issuable` inside write_receipt only.)"""
    root, code = mk_root(tmp_path)
    monkeypatch.setattr(R, "issuable", lambda v, a: (True, "forged"))
    rec, _v, _a = issue(root, code, verdict=mk_verdict(refuted=True), att=mk_att(anchor_kind="self", attestor="reviewer"))
    monkeypatch.undo()
    ok, why = validate(rec, code, root)
    assert not ok and "re-derived" in why, why


# ---- T13: a v1 receipt ----------------------------------------------------------------------------------------------------
def test_t13_fresh_v1_receipt_is_invalid_with_a_reissue_reason(tmp_path):
    root, code = mk_root(tmp_path)
    rec, _v, _a = issue(root, code)
    rec["schema"] = "verify_gate.receipt.v1"
    ok, why = validate(rec, code, root)
    assert not ok and "re-issue under v2" in why, why


# ---- T15: safe reads, each within 2 s ---------------------------------------------------------------------------------
# The bound is enforced from OUTSIDE (s232 F-6): the validation runs in a child process under subprocess `timeout`, so a
# read that blocks on a FIFO fails the test with TimeoutExpired instead of hanging it, and the 2 s is measured in the child.
_CHILD = (
    "import json, sys, time\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "import receipt as R\n"
    "rec, paths, ledger = json.loads(sys.argv[2]), json.loads(sys.argv[3]), sys.argv[4]\n"
    "t0 = time.monotonic()\n"
    "ok, why = R.validate_receipt(rec, changed_paths=paths, ledger_path=ledger)\n"
    "print(json.dumps([ok, why, time.monotonic() - t0]))\n"
)


def _validate_isolated(rec, paths, root, wall=15):
    r = subprocess.run([sys.executable, "-c", _CHILD, SCRIPTS, json.dumps(rec), json.dumps([str(p) for p in paths]),
                        str(root / ".verify" / "_consumed_nonces.json")],
                       capture_output=True, text=True, timeout=wall)       # TimeoutExpired = the test fails, never hangs
    assert r.returncode == 0, r.stderr[-400:]
    ok, why, elapsed = json.loads(r.stdout.strip().splitlines()[-1])
    return ok, why, elapsed


def _timed_invalid(rec, paths, root, needle=None):
    ok, why, elapsed = _validate_isolated(rec, paths, root)
    assert elapsed < 2, "validation took %.2f s (more than 2 s)" % elapsed
    assert not ok, why
    if needle:
        assert needle in why, why
    return why


@pytest.mark.parametrize("which", ["verdict_ref", "attestation_ref"])
@pytest.mark.parametrize("kind", ["missing", "symlink", "fifo"])
def test_t15_artifact_refs_must_be_plain_files(tmp_path, which, kind):
    root, code = mk_root(tmp_path)
    rec, v, a = issue(root, code)
    assert _validate_isolated(rec, [code], root)[0], "control: the untouched fixture validates"
    path = v if which == "verdict_ref" else a
    saved = path.read_bytes()
    path.unlink()
    if kind == "symlink":
        (tmp_path / "real.json").write_bytes(saved)          # identical bytes at the identical path: only the link differs
        os.symlink(tmp_path / "real.json", path)
    elif kind == "fifo":
        os.mkfifo(path)
    why = _timed_invalid(rec, [code], root, which)
    assert ("missing" in why) if kind == "missing" else ("unsafe" in why), why


@pytest.mark.parametrize("kind", ["fifo", "symlink"])
def test_t15_changed_paths_must_be_plain_files(tmp_path, kind):
    """The receipt is issued over [code, extra] with `extra` a plain file; then ONLY the kind of `extra` changes (the same
    path, the same bytes for the symlink), so a validator that followed the link would accept it and only the unsafe-path
    refusal can fail this."""
    root, code = mk_root(tmp_path)
    extra = root / "extra.py"
    extra.write_text("y = 1\n")
    target = tmp_path / "t.py"                               # s239 H-6: the link target exists BEFORE issuance, so its mtime
    target.write_bytes(extra.read_bytes())                   # is older than the receipt and only the no-follow check can refuse
    v = put(root / "verdict.json", mk_verdict())
    a = put(root / "attestation.json", mk_att())
    rec = R.write_receipt(changed_paths=[str(code), str(extra)], verdict_path=str(v), gate_exit=0,
                          attestation_ref=str(a)).as_dict()
    ok, why, _ = _validate_isolated(rec, [code, extra], root)
    assert ok, "control: the plain-file change-set validates: " + why
    saved = extra.read_bytes()
    extra.unlink()
    if kind == "fifo":
        os.mkfifo(extra)
    else:
        assert target.read_bytes() == saved
        os.symlink(target, extra)
    why = _timed_invalid(rec, [code, extra], root)
    assert "changed path unsafe" in why, why


@pytest.mark.parametrize("kind", ["fifo", "symlink"])
def test_t15_ledger_must_be_a_plain_file(tmp_path, kind):
    root, code = mk_root(tmp_path)
    rec, _v, _a = issue(root, code)
    assert _validate_isolated(rec, [code], root)[0], "control: no ledger yet, the receipt validates"
    ledger = root / ".verify" / "_consumed_nonces.json"
    if kind == "fifo":
        os.mkfifo(ledger)
    else:
        (tmp_path / "l.json").write_text("[]")
        os.symlink(tmp_path / "l.json", ledger)
    _timed_invalid(rec, [code], root, "ledger")


def test_t15_validation_from_another_working_directory_with_absolute_refs_is_valid(tmp_path, monkeypatch):
    root, code = mk_root(tmp_path)
    rec, _v, _a = issue(root, code)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    ok, why = validate(rec, code, root)
    assert ok, why


def test_t15_receipt_issued_through_a_symlinked_artifact_folder_stays_valid(tmp_path):
    root, code = mk_root(tmp_path)
    real = tmp_path / "real_art"
    real.mkdir()
    os.symlink(real, tmp_path / "linked_art")
    v = put(real / "verdict.json", mk_verdict())
    a = put(real / "attestation.json", mk_att())
    rec = R.write_receipt(changed_paths=[str(code)], verdict_path=str(tmp_path / "linked_art" / "verdict.json"),
                          gate_exit=0, attestation_ref=str(tmp_path / "linked_art" / "attestation.json")).as_dict()
    assert "linked_art" not in rec["verdict_ref"] and "linked_art" not in rec["attestation_ref"]
    ok, why = validate(rec, code, root)
    assert ok, why


# ---- T12 / T16 / T16b (R-3): one ledger per project, the gate's -------------------------------------------------------
def _run_cli(*args):
    return subprocess.run([sys.executable, VG, *args], capture_output=True, text=True, timeout=30)


def test_t12_a_spent_receipt_copied_elsewhere_is_a_replay(tmp_path):
    root, code = mk_root(tmp_path)
    rec, _v, _a = issue(root, code)
    first = root / ".verify" / "receipt.json"
    put(first, rec)
    r1 = _run_cli("--receipt", str(first), "--changed", str(code))
    assert r1.returncode == 0, r1.stdout + r1.stderr
    copy_dir = tmp_path / "copy"
    copy_dir.mkdir()
    copy = put(copy_dir / "receipt.json", rec)
    r2 = _run_cli("--receipt", str(copy), "--changed", str(code))
    assert r2.returncode == RECEIPT_INVALID and "replay" in r2.stdout, r2.stdout
    assert json.loads((root / ".verify" / "_consumed_nonces.json").read_text()) == [rec["nonce"]]
    assert not (copy_dir / "_consumed_nonces.json").exists()


def test_t16_the_cli_spends_into_the_project_ledger_not_a_decoy(tmp_path):
    root, code = mk_root(tmp_path)
    sub = root / "sub"
    (sub / ".verify").mkdir(parents=True)                       # a decoy `.verify/` without NEOCORTEX/
    inner = sub / "inner.py"
    inner.write_text("y = 2\n")
    v = put(root / "verdict.json", mk_verdict())
    a = put(root / "attestation.json", mk_att())
    rec = R.write_receipt(changed_paths=[str(inner)], verdict_path=str(v), gate_exit=0,
                          attestation_ref=str(a)).as_dict()
    rp = put(sub / ".verify" / "receipt.json", rec)
    r = _run_cli("--receipt", str(rp), "--changed", str(inner))
    assert r.returncode == 0, r.stdout + r.stderr
    assert json.loads((root / ".verify" / "_consumed_nonces.json").read_text()) == [rec["nonce"]]
    assert not (sub / ".verify" / "_consumed_nonces.json").exists()
    # the gate reads <project>/.verify/_consumed_nonces.json: the same receipt is now a replay for it
    ok, why = R.validate_receipt(rec, changed_paths=[str(inner)],
                                 ledger_path=str(root / ".verify" / "_consumed_nonces.json"))
    assert not ok and "replay" in why, why


def test_t16b_no_marked_root_and_two_marked_roots(tmp_path):
    bare = tmp_path / "bare"
    bare.mkdir()
    f = bare / "f.py"
    f.write_text("x = 1\n")
    rp = put(tmp_path / "r.json", {"schema": "verify_gate.receipt.v2"})
    r = _run_cli("--receipt", str(rp), "--changed", str(f))
    assert r.returncode == RECEIPT_INVALID and json.loads(r.stdout)["decision"] == "no project root"
    r1, c1 = mk_root(tmp_path, "one")
    r2, c2 = mk_root(tmp_path, "two")
    assert R.project_root([str(c1), str(c2)]) == (None, "ambiguous project root")
    r = _run_cli("--receipt", str(rp), "--changed", str(c1), str(c2))
    assert r.returncode == RECEIPT_INVALID and json.loads(r.stdout)["decision"] == "ambiguous project root"
    assert R.project_root([str(c1)])[0] == r1 and R.project_root([str(c1), str(f)])[0] == r1   # outside paths ignored


def test_t16_project_root_requires_both_marker_and_neocortex(tmp_path):
    p = tmp_path / "p"
    (p / ".verify").mkdir(parents=True)
    (p / ".verify" / "armed").write_text("")
    f = p / "f.py"
    f.write_text("")
    assert R.project_root([str(f)]) == (None, "no project root")      # a marker without NEOCORTEX/
    (p / "NEOCORTEX").mkdir()
    assert R.project_root([str(f)])[0] == p


# ---- T14 (R-4): no anchor from the environment ---------------------------------------------------------------------------
@pytest.mark.parametrize("var", ["CI", "GITHUB_ACTIONS", "GITLAB_CI", "BUILDKITE"])
def test_t14_ci_environment_does_not_anchor(monkeypatch, tmp_path, var):
    monkeypatch.setenv(var, "1")
    assert review_runner._anchor_kind_for("self", "orchestrator", "run-1") == "self"
    assert review_runner._anchor_kind_for("stateless_llm", "orchestrator", "run-1") == "stateless_api"
    out = review_runner.run_review(bundle={"schema": "verify_gate.bundle.v1", "artifact": "a", "evidence": []},
                                   backend="self", judge_fn=lambda b: mk_verdict(), orchestrator_id="orchestrator",
                                   anchor_id="run-1", context_isolated=True, outdir=str(tmp_path))
    att = json.loads(open(out["attestation_path"]).read())
    assert att["anchor_kind"] != "ci"


# ---- s232 F-5 (written before the fix): _safe_read has a running byte cap and detects a file that changes under it ---------
def _intercept_read(monkeypatch, target, on_read):
    """Route os.read for the fd of `target` through on_read(real_read, fd, n, call_no); other fds are untouched."""
    real_read, real_fstat = os.read, os.fstat
    ino = os.stat(target).st_ino
    state = {"n": 0}

    def fake(fd, n):
        try:
            mine = real_fstat(fd).st_ino == ino
        except OSError:
            mine = False
        if not mine:
            return real_read(fd, n)
        state["n"] += 1
        return on_read(real_read, fd, n, state["n"])
    monkeypatch.setattr(R.os, "read", fake)
    return state


def test_f5_control_a_stable_small_file_reads(tmp_path):
    p = tmp_path / "f.bin"
    p.write_bytes(b"abc")
    data, mtime = R._safe_read(str(p))
    assert data == b"abc"


def test_f5_a_file_that_grows_past_the_cap_during_the_read_is_refused(tmp_path, monkeypatch):
    p = tmp_path / "grow.bin"
    p.write_bytes(b"a" * 10)
    monkeypatch.setattr(R, "_MAX_READ", 100_000)
    # fstat says 10 bytes; the reads then deliver 64 KiB chunks 50 times (3.2 MB) and only then EOF
    _intercept_read(monkeypatch, p, lambda real, fd, n, k: b"x" * 65536 if k <= 50 else b"")
    with pytest.raises(R.UnsafeFile):
        R._safe_read(str(p))


def test_f5_an_endless_read_stops_at_the_running_cap(tmp_path, monkeypatch):
    """The running cap alone (slot (85) survivor F-5 (a), 2026-11-10): reads never reach EOF, so the after-read fstat check is never
    reached; only the cap ends the loop. Past cap + 2 chunks the interceptor raises a different error, so a missing cap fails at once."""
    p = tmp_path / "endless.bin"
    p.write_bytes(b"a" * 10)
    monkeypatch.setattr(R, "_MAX_READ", 100_000)
    limit = 100_000 // 65536 + 3

    def on_read(real, fd, n, k):
        if k > limit:
            raise AssertionError("the read ran past the running cap")
        return b"x" * 65536
    _intercept_read(monkeypatch, p, on_read)
    with pytest.raises(R.UnsafeFile, match="grew past"):
        R._safe_read(str(p))


def test_f5_a_file_modified_during_the_read_is_refused(tmp_path, monkeypatch):
    p = tmp_path / "mod.bin"
    p.write_bytes(b"a" * 10)

    def on_read(real, fd, n, k):
        if k == 1:
            with open(p, "ab") as fh:              # an append between the fstat and the end of the read
                fh.write(b"b")
        return real(fd, n)
    _intercept_read(monkeypatch, p, on_read)
    with pytest.raises(R.UnsafeFile):
        R._safe_read(str(p))


def test_f5_an_in_place_rewrite_with_the_same_size_is_refused(tmp_path, monkeypatch):
    p = tmp_path / "same.bin"
    p.write_bytes(b"a" * 10)
    st0 = os.stat(p)

    def on_read(real, fd, n, k):
        if k == 1:
            with open(p, "r+b") as fh:             # same size, different bytes and mtime
                fh.write(b"b" * 10)
            os.utime(p, ns=(st0.st_atime_ns, st0.st_mtime_ns + 5_000_000_000))
        return real(fd, n)
    _intercept_read(monkeypatch, p, on_read)
    with pytest.raises(R.UnsafeFile):
        R._safe_read(str(p))


# ======================================================================================================================
# v0.7 (PLAN_verify_gate_receipt_v2 v0.7): R-2 issuance, T15 issuance cases, F-9, F-11a/b. Written BEFORE the code.
# ======================================================================================================================
import hashlib  # noqa: E402
import pathlib  # noqa: E402


def _issue_args(tmp_path, changed, out="receipt.json"):
    v = put(tmp_path / "v.json", mk_verdict())
    a = put(tmp_path / "a.json", mk_att())
    return ["--verdict", str(v), "--attestation", str(a), "--changed", *[str(c) for c in changed],
            "--write-receipt", str(tmp_path / out)]


def _decision(capsys):
    return json.loads(capsys.readouterr().out)


# ---- changeset_sha256 reads through _safe_read: the raising contract (R-2 v0.7, s239 H-9) -------------------------------
def test_changeset_sha256_control_plain_and_absent_files(tmp_path):
    f, gone = str(tmp_path / "f.py"), str(tmp_path / "gone.py")
    (tmp_path / "f.py").write_text("x = 1\n")
    h = hashlib.sha256()
    for p, body in sorted([(f, hashlib.sha256(b"x = 1\n").digest()), (gone, b"<missing>")]):
        h.update(p.encode() + b"\0" + body + b"\0")
    assert R.changeset_sha256([f, gone]) == h.hexdigest()


@pytest.mark.parametrize("kind", ["symlink-leaf", "symlink-component", "fifo", "folder", "hardlink"])
def test_changeset_sha256_raises_unsafefile_for_every_shape_validation_refuses(tmp_path, kind):
    real = tmp_path / "real"
    real.mkdir()
    (real / "a.py").write_text("x = 1\n")
    if kind == "symlink-leaf":
        os.symlink(real / "a.py", tmp_path / "l.py")
        target = tmp_path / "l.py"
    elif kind == "symlink-component":
        os.symlink(real, tmp_path / "lnk")
        target = tmp_path / "lnk" / "a.py"
    elif kind == "fifo":
        os.mkfifo(tmp_path / "p.py")
        target = tmp_path / "p.py"
    elif kind == "folder":
        (tmp_path / "d.py").mkdir()
        target = tmp_path / "d.py"
    else:
        os.link(real / "a.py", tmp_path / "h.py")
        target = tmp_path / "h.py"
    with pytest.raises(R.UnsafeFile):
        R.changeset_sha256([str(target)])


# ---- T15 issuance cases (red on 9c6bca0): write_receipt refuses what validation refuses ------------------------------------
def test_t15_issuance_control_plain_changed_file_issues_a_receipt(tmp_path, capsys):
    code = tmp_path / "code.py"
    code.write_text("x = 1\n")
    rc = verify_gate.main(_issue_args(tmp_path, [code]))
    assert rc == 0 and (tmp_path / "receipt.json").exists()


def test_t15_issuance_changed_path_through_a_symlinked_folder_issues_no_receipt(tmp_path, capsys):
    real = tmp_path / "real"
    real.mkdir()
    (real / "x.py").write_text("x = 1\n")
    os.symlink(real, tmp_path / "lnk")
    path = tmp_path / "lnk" / "x.py"
    rc = verify_gate.main(_issue_args(tmp_path, [path]))
    d = _decision(capsys)
    assert rc == RECEIPT_INVALID and not (tmp_path / "receipt.json").exists(), d
    assert str(path) in d["decision"], d


def test_t15_issuance_hard_linked_changed_file_issues_no_receipt(tmp_path, capsys):
    code = tmp_path / "code.py"
    code.write_text("x = 1\n")
    os.link(code, tmp_path / "second.py")
    rc = verify_gate.main(_issue_args(tmp_path, [code]))
    d = _decision(capsys)
    assert rc == RECEIPT_INVALID and not (tmp_path / "receipt.json").exists(), d
    assert str(code) in d["decision"], d


def test_t15_issuance_changed_file_grown_during_write_receipt_issues_no_receipt(tmp_path, monkeypatch, capsys):
    code = tmp_path / "code.py"
    code.write_text("x = 1\n")

    def on_read(real, fd, n, k):
        if k == 1:
            with open(code, "ab") as fh:               # an append between the fstat and the end of the read
                fh.write(b"y = 2\n")
        return real(fd, n)
    _intercept_read(monkeypatch, code, on_read)
    rc = verify_gate.main(_issue_args(tmp_path, [code]))
    d = _decision(capsys)
    assert rc == RECEIPT_INVALID and not (tmp_path / "receipt.json").exists(), d
    assert str(code) in d["decision"], d


def test_t15_issuance_relative_changed_path_is_qualified_and_valid_at_the_gate(tmp_path, monkeypatch):
    root, code = mk_root(tmp_path)
    monkeypatch.chdir(root)
    rc = verify_gate.main(_issue_args(root, ["code.py"]))
    assert rc == 0
    rec = json.loads((root / "receipt.json").read_text())
    ok, why = validate(rec, str(root) + "/code.py", root)
    assert ok, why


def test_t15_issuance_dot_slash_path_is_kept_in_the_hash_and_valid_at_the_gate(tmp_path, monkeypatch):
    root, code = mk_root(tmp_path)
    monkeypatch.chdir(root)
    rc = verify_gate.main(_issue_args(root, ["./code.py"]))
    assert rc == 0
    rec = json.loads((root / "receipt.json").read_text())
    ok, why = validate(rec, os.path.join(str(root), "./code.py"), root)
    assert ok, why
    assert not validate(rec, str(root) + "/code.py", root)[0], "qualification does not fold `./`: the plain path hashes differently"


def test_t15_issuance_link_dotdot_path_issues_no_receipt(tmp_path, monkeypatch, capsys):
    root, code = mk_root(tmp_path)
    (root / "sub").mkdir()
    (root / "x.py").write_text("y = 1\n")
    os.symlink(root / "sub", root / "link")
    monkeypatch.chdir(root)
    rc = verify_gate.main(_issue_args(root, ["link/../x.py"]))
    d = _decision(capsys)
    assert rc == RECEIPT_INVALID and not (root / "receipt.json").exists(), d
    assert "link/../x.py" in d["decision"], d


def test_t15_produce_review_refuses_an_unsafe_changed_path_with_its_reason(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    (real / "x.py").write_text("x = 1\n")
    os.symlink(real, tmp_path / "lnk")
    path = tmp_path / "lnk" / "x.py"
    outdir = tmp_path / "out"
    res = produce_review.produce(artifact="art", evidence=[{"type": "file", "ref": "x:1"}], drafter_id="author",
                                 producer_id="reviewer", backend="harness_subagent", orchestrator_id="orchestrator",
                                 anchor_id="sub-9", outdir=str(outdir), changed_paths=[str(path)], agent_verdict=mk_verdict())
    assert res["receipt_path"] is None and not (outdir / "receipt.json").exists(), res
    assert str(path) in res["receipt_refused"], res


def test_t15_produce_review_qualifies_a_relative_changed_path(tmp_path, monkeypatch):
    root, code = mk_root(tmp_path)
    monkeypatch.chdir(root)
    res = produce_review.produce(artifact="art", evidence=[{"type": "file", "ref": "x:1"}], drafter_id="author",
                                 producer_id="reviewer", backend="harness_subagent", orchestrator_id="orchestrator",
                                 anchor_id="sub-9", outdir=str(root / "out"), changed_paths=["code.py"],
                                 agent_verdict=mk_verdict())
    rec = json.loads(open(res["receipt_path"]).read())
    ok, why = validate(rec, str(root) + "/code.py", root)
    assert ok, why


# ---- T15 guard (green on 9c6bca0): the fstat comparison, isolated: only metadata differs -----------------------------------
def test_t15_fstat_comparison_alone_refuses_an_identical_rewrite_with_a_new_mtime(tmp_path, monkeypatch):
    p = tmp_path / "big.bin"
    data = bytes(range(256)) * 400                         # 102,400 bytes: two chunks
    p.write_bytes(data)
    st0 = os.stat(p)

    def on_read(real, fd, n, k):
        if k == 2:                                         # the first chunk is already read
            with open(p, "r+b") as fh:
                fh.write(data[:4096])                      # identical bytes over the already-read region, same size
            os.utime(p, ns=(st0.st_atime_ns, st0.st_mtime_ns + 7_000_000_000))
        return real(fd, n)
    _intercept_read(monkeypatch, p, on_read)
    with pytest.raises(R.UnsafeFile, match="modified while being read"):
        R._safe_read(str(p))


# ---- F-9 (s238): one try around evaluate and block_eligible ---------------------------------------------------------------
def test_f9_a_raising_block_eligible_is_a_refusal_not_a_raise(monkeypatch):
    """core.evaluate calls block_eligible itself (inside issuable's first try); only the SECOND call, issuable's own, is outside it
    on 9c6bca0. So the stub passes the first call through and raises on the second."""
    assert R.issuable(mk_verdict(), mk_att())[0], "control"
    real, calls = core.block_eligible, []

    def second_raises(*a, **k):
        calls.append(1)
        if len(calls) >= 2:
            raise TypeError("block_eligible exploded")
        return real(*a, **k)
    monkeypatch.setattr(core, "block_eligible", second_raises)
    ok, why = R.issuable(mk_verdict(), mk_att())
    assert len(calls) >= 2 and ok is False and "not evaluable" in why, (calls, why)


# ---- F-11a (s238, s239 H-10): defensive contract; the real evaluator never returns PASS with block findings ---------------
def test_f11a_issuable_refuses_a_pass_decision_that_carries_block_findings(monkeypatch):
    real = core.evaluate(mk_verdict(), mk_att(), require_blocking=False)
    fake = core.Decision(exit_code=core.ExitCode.PASS, blocking=False, advisory=False, reasons=[],
                         block_findings=[hi_finding()], demoted=[], anchor_kind=real.anchor_kind)
    monkeypatch.setattr(core, "evaluate", lambda *a, **k: fake)
    assert core.block_eligible(mk_att(), mk_verdict())[0], "the anchor is eligible: only the findings can refuse"
    ok, why = R.issuable(mk_verdict(), mk_att())
    assert not ok and "HIGH refutation" in why, why


# ---- F-11b (s238): T13 variant with unreadable artifacts proves the schema check runs first ---------------------------------
@pytest.mark.parametrize("kind", ["missing", "fifo"])
def test_t13b_v1_receipt_with_unreadable_artifacts_is_refused_on_its_schema_first(tmp_path, kind):
    root, code = mk_root(tmp_path)
    rec, v, a = issue(root, code)
    rec["schema"] = "verify_gate.receipt.v1"
    v.unlink()
    a.unlink()
    if kind == "fifo":
        os.mkfifo(v)
        os.mkfifo(a)
    why = _timed_invalid(rec, [code], root)
    assert "re-issue under v2" in why, why


# ======================================================================================================================
# T17 (R-3 v0.7): one placement rule for project_root, vg_probe and the module-free probe. The case builders live HERE and
# are reused by .claude/hooks/test_stop_closeout_verify_gate.py (which adds the probes and the Stop fixture).
# A builder takes a fresh `base` folder (off the gate's scratch/temp exemptions when the caller says so) and returns a Case.
# ======================================================================================================================
class Case:
    def __init__(self, paths, expect, root=None, deny=None, slow=False):
        self.paths, self.expect, self.root, self.deny, self.slow = [str(p) for p in paths], expect, root, deny, slow


def _armed_root(base, name="P"):
    p = pathlib.Path(base) / name
    (p / "NEOCORTEX").mkdir(parents=True)
    (p / ".verify").mkdir()
    (p / ".verify" / "armed").write_text("")
    (p / "src").mkdir()
    return p


def _candidate(base, name, neocortex="dir", verify="dir", armed=None):
    """A folder with a `NEOCORTEX` and a `.verify` entry of the given kinds; `armed` None, "file", "fifo" or "dangling"."""
    d = pathlib.Path(base) / name
    d.mkdir(parents=True)
    for entry, kind in (("NEOCORTEX", neocortex), (".verify", verify)):
        p = d / entry
        if kind == "dir":
            p.mkdir()
        elif kind == "file":
            p.write_text("")
        elif kind == "selfloop":
            os.symlink(entry, p)
        elif kind == "dangling":
            os.symlink("does-not-exist", p)
    if armed and verify == "dir":
        a = d / ".verify" / "armed"
        {"file": lambda: a.write_text(""), "fifo": lambda: os.mkfifo(a),
         "dangling": lambda: os.symlink("does-not-exist", a)}[armed]()
    return d


def _b_a(base):    # (a) a self-looping symlink under an armed root
    p = _armed_root(base)
    os.symlink("loop.py", p / "src" / "loop.py")
    return Case([p / "src" / "loop.py"], "armed", root=p)


def _b_b(base):    # (b) outside/link.py -> a loop inside an armed root
    p = _armed_root(base)
    os.symlink("loop", p / "src" / "loop")
    out = pathlib.Path(base) / "outside"
    out.mkdir()
    os.symlink(p / "src" / "loop", out / "link.py")
    return Case([out / "link.py"], "unreadable")


def _b_c(base):    # (c) a loop outside every root (the cost, R-3 / section 6)
    out = pathlib.Path(base) / "outside2"
    out.mkdir()
    os.symlink("loop.py", out / "loop.py")
    return Case([out / "loop.py"], "unreadable")


def _b_d1(base):   # (d) guard: a deleted parent under an armed root
    p = _armed_root(base)
    return Case([p / "gone" / "x.py"], "armed", root=p)


def _b_d2(base):   # (d) guard: a deleted parent under no root
    return Case([pathlib.Path(base) / "nowhere" / "gone" / "x.py"], "none")


def _b_e1(base):   # (e) a FIFO `armed`
    q = _candidate(base, "Q", armed="fifo")
    return Case([q / "x.py"], "unreadable")


def _b_e2(base):   # (e) a file `.verify` at a candidate inside an armed root: the walk stops, it does not pass over
    p = _armed_root(base)
    inner = _candidate(p, "inner", verify="file")
    return Case([inner / "x.py"], "unreadable")


def _b_e3(base):   # (e) a dangling `armed` link
    q = _candidate(base, "Q3", armed="dangling")
    return Case([q / "x.py"], "unreadable")


def _b_e4(base):   # (e) a looping `NEOCORTEX` link
    q = _candidate(base, "Q4", neocortex="selfloop", armed="file")
    return Case([q / "x.py"], "unreadable")


def _b_f(base):    # (f) an unarmed project with a file `.verify` (the cost, section 6)
    u = _candidate(base, "U", verify="file")
    return Case([u / "x.py"], "unreadable")


def _b_g(base):    # (g) src/a.py in an armed root linked to a file elsewhere: armed by the lexical placement
    p = _armed_root(base)
    el = pathlib.Path(base) / "elsewhere"
    el.mkdir()
    (el / "a.py").write_text("x = 1\n")
    os.symlink(el / "a.py", p / "src" / "a.py")
    return Case([p / "src" / "a.py"], "armed", root=p)


def _b_h1(base):   # (h) an armed project's path + an unreadable marker of another project
    p = _armed_root(base)
    (p / "code.py").write_text("x = 1\n")
    q = _candidate(base, "Qh", armed="fifo")
    (q / "x.py").write_text("y = 1\n")
    return Case([p / "code.py", q / "x.py"], "unreadable")


def _b_h2(base):   # (h) the same two paths in the other order
    c = _b_h1(base)
    c.paths.reverse()
    return c


def _b_j(base):    # (j) guard: a nested NEOCORTEX/ + empty .verify/ under an armed root, the edit inside it
    p = _armed_root(base)
    inner = _candidate(p, "inner")
    return Case([inner / "f.py"], "armed", root=p)


def _b_k1(base):   # (k) a plain-file NEOCORTEX beside a valid .verify/armed
    r = _candidate(base, "R1", neocortex="file", armed="file")
    return Case([r / "x.py"], "unreadable")


def _b_k2(base):   # (k) a dangling NEOCORTEX link with no .verify: no root, no block
    r = pathlib.Path(base) / "R2"
    r.mkdir()
    os.symlink("nothing", r / "NEOCORTEX")
    return Case([r / "x.py"], "none")


def _b_l1(base):   # (l) a component with no search permission (EACCES, injected) under an armed root
    p = _armed_root(base)
    (p / "locked").mkdir()
    return Case([p / "locked" / "x.py"], "unreadable", deny=str(p / "locked"))


def _b_l2(base):   # (l) the same outside every root
    d = pathlib.Path(base) / "lk" / "locked"
    d.mkdir(parents=True)
    return Case([d / "x.py"], "unreadable", deny=str(d))


def _b_m2(base):   # (m) outside/link/../x.py with link -> P/src: the kernel applies `..` after the link
    p = _armed_root(base)
    out = pathlib.Path(base) / "outside"
    out.mkdir()
    os.symlink(p / "src", out / "link")
    return Case([str(out / "link") + "/../x.py"], "armed", root=p)


def _b_n(base):    # (n) a chain of 41 links, no lexical armed root; run under an external 10 s timeout
    d = pathlib.Path(base) / "chain"
    d.mkdir()
    (d / "target.py").write_text("x = 1\n")
    for i in range(41):
        os.symlink("l%d" % (i + 1) if i < 40 else "target.py", d / ("l%d" % i))
    return Case([d / "l0"], "unreadable", slow=True)


T17_BUILDERS = {"a": _b_a, "b": _b_b, "c": _b_c, "d1": _b_d1, "d2": _b_d2, "e1": _b_e1, "e2": _b_e2, "e3": _b_e3,
                "e4": _b_e4, "f": _b_f, "g": _b_g, "h1": _b_h1, "h2": _b_h2, "j": _b_j, "k1": _b_k1, "k2": _b_k2,
                "l1": _b_l1, "l2": _b_l2, "m2": _b_m2, "n": _b_n}


@pytest.fixture
def deny_lstat(monkeypatch):
    """Deterministic EACCES (s239 H-4): every lstat/stat strictly under `prefix` raises PermissionError."""
    def apply(prefix):
        if not prefix:
            return
        for name in ("lstat", "stat"):
            real = getattr(os, name)

            def fake(p, *a, _real=real, **k):
                s = os.fspath(p)
                if s.startswith(prefix + "/"):
                    raise PermissionError(13, "Permission denied (injected)", s)
                return _real(p, *a, **k)
            monkeypatch.setattr(os, name, fake)
    return apply


def root_outcome(paths):
    root, why = R.project_root(paths)
    if root is not None or why == "ambiguous project root":
        return "armed", root
    return {"armed state unreadable": "unreadable", "no project root": "none"}[why], None


_CHILD_ROOT = (
    "import json, sys\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "import receipt as R\n"
    "print(json.dumps(list(R.project_root(json.loads(sys.argv[2])))[1:]))\n"
)


@pytest.mark.parametrize("name", sorted(T17_BUILDERS))
def test_t17_project_root(tmp_path, deny_lstat, name):
    case = T17_BUILDERS[name](tmp_path)
    if case.slow:                                           # (n): the external deadline, not a hang the test waits out
        r = subprocess.run([sys.executable, "-c", _CHILD_ROOT, SCRIPTS, json.dumps(case.paths)],
                           capture_output=True, text=True, timeout=10)
        assert json.loads(r.stdout.strip().splitlines()[-1]) == ["armed state unreadable"], (r.stdout, r.stderr[-300:])
        return
    deny_lstat(case.deny)
    got, root = root_outcome(case.paths)
    assert got == case.expect, (name, got, R.project_root(case.paths))
    if case.root is not None:
        assert os.path.realpath(root) == os.path.realpath(case.root), (name, root)


def test_t17_project_root_never_raises_on_hostile_input(tmp_path):
    for bad in ([None], [""], ["rel/x.py"], [b"/x"], [12], ["/a\0b"], None):
        r = R.project_root(bad)
        assert r[0] is None and r[1] in ("no project root", "armed state unreadable"), (bad, r)
    assert R.project_root(["rel/x.py"]) == (None, "armed state unreadable")        # a relative path is a resolution failure
    assert R.project_root([]) == (None, "no project root")


def test_t17_precedence_across_paths_is_unreadable_then_ambiguous_then_root_then_none(tmp_path):
    p = _armed_root(tmp_path, "P1")
    p2 = _armed_root(tmp_path, "P2")
    bad = _candidate(tmp_path, "Qx", armed="fifo")
    plain = tmp_path / "plain"
    plain.mkdir()
    both = [str(p / "a.py"), str(p2 / "b.py")]
    assert R.project_root(both + [str(bad / "x.py")]) == (None, "armed state unreadable")
    assert R.project_root([str(bad / "x.py")] + both) == (None, "armed state unreadable")
    assert R.project_root(both + [str(plain / "z.py")]) == (None, "ambiguous project root")
    assert R.project_root([str(p / "a.py"), str(p / "src" / "b.py"), str(plain / "z.py")])[1] == "ok"
    assert R.project_root([str(plain / "z.py")]) == (None, "no project root")


def test_t17_one_root_spelled_two_ways_is_one_root(tmp_path):
    """The lexical and the resolved placement of a path through a linked folder reach the SAME root: not ambiguous."""
    p = _armed_root(tmp_path)
    os.symlink(p, tmp_path / "alias")
    root, why = R.project_root([str(tmp_path / "alias" / "src" / "a.py")])
    assert why == "ok" and os.path.realpath(root) == os.path.realpath(p), (root, why)
    root, why = R.project_root([str(tmp_path / "alias" / "src" / "a.py"), str(p / "src" / "b.py")])
    assert why == "ok", why


# ---- the CLI (`--receipt`): exit 6 and a decision naming the unreadable state ---------------------------------------------
@pytest.mark.parametrize("name", ["b", "h1", "h2", "e1", "k1"])
def test_t17_cli_receipt_mode_exits_6_with_armed_state_unreadable(tmp_path, name):
    case = T17_BUILDERS[name](tmp_path)
    rp = put(tmp_path / "r.json", {"schema": "verify_gate.receipt.v2"})
    r = _run_cli("--receipt", str(rp), "--changed", *case.paths)
    assert r.returncode == RECEIPT_INVALID, (r.stdout, r.stderr[-300:])
    assert json.loads(r.stdout)["decision"] == "armed state unreadable", r.stdout


def test_t17_cli_relative_changed_path_without_a_root_is_qualified_by_the_cwd(tmp_path):
    """T17 (m): a relative path under an armed cwd is qualified by the CLI's cwd (not left to the resolver)."""
    root, code = mk_root(tmp_path)
    rp = put(tmp_path / "r.json", {"schema": "verify_gate.receipt.v2"})
    r = subprocess.run([sys.executable, VG, "--receipt", str(rp), "--changed", "code.py"], capture_output=True, text=True,
                       timeout=30, cwd=str(root))
    d = json.loads(r.stdout)
    assert d["decision"] == "receipt_invalid", d          # a root WAS found (armed cwd); the dummy receipt then fails validation


# ======================================================================================================================
# R-5 (words and versions that live in this tree): SKILL.md 0.4.0, the two canonical limit sentences, the CLI's usage and its
# `receipt_valid` line. (The public README and governance/publish_kit/test_public_claims.py are another role's, at landing.)
# ======================================================================================================================
LIMIT_1 = ("on a local box a determined agent can still forge the whole evidence set (attestation, verdict, receipt), and "
           "single use is per project root: moving the ledger aside reopens every spent nonce. R-1 to R-4 close the "
           "accidental paths named above; they do not raise the cryptographic floor.")
LIMIT_2 = ("A receipt does not prove what was reviewed: a genuine clean verdict and attestation, whatever they reviewed "
           "and however old, can be bound to any later change-set, any number of times, and an edit made while a review runs "
           "is not detected (planned: receipt-v3). Validation reads the changed files one after another, so an edit made "
           "during validation can pass. The Stop hook sees edits as the transcript spells them: a write under a scratch or "
           "temp exemption (an alias or a `..` escape into the project included), a `cd` inside a command, an unexpanded "
           "variable, or a link changed before the turn ends can escape the armed check. The hooks' import guard does not "
           "authenticate modules already in sys.modules; it refuses only an unverified module cached under a scripts-file "
           "name whose file lies in the scripts folder. The CLIs have no import guard.")


def _skill_text():
    return " ".join(open(os.path.join(HERE, "..", "SKILL.md"), encoding="utf-8").read().split())


def test_r5_skill_md_is_0_4_0_and_carries_both_canonical_limit_sentences_verbatim():
    t = _skill_text()
    assert "version: 0.4.0" in t
    assert LIMIT_1 in t and LIMIT_2 in t


def test_r5_skill_md_usage_has_no_ledger_option_and_says_receipt_mode_serves_armed_roots():
    t = _skill_text()
    assert "--ledger" not in t.split("## Changelog")[0], "the usage no longer takes --ledger"
    assert "armed roots" in t.split("## Changelog")[0]
    assert "can't forge" not in t


def test_r5_skill_md_changelog_lists_every_shape_that_turns_invalid():
    t = _skill_text()
    log = t.split("## Changelog")[1].split("- **0.3.1**")[0]
    assert "**0.4.0**" in log
    for shape in ("v1", "symlink", "FIFO", "hard link", "64 MiB", "attestation_sha256", "verdict_ref", "ambiguous project root",
                  "armed state unreadable", "no project root", "CI", "--ledger", "UnsafeFile", "exit 6"):
        assert shape in log, shape


def test_r5_verify_gate_usage_block_drops_ledger_and_says_emit_on_an_anchored_clean_pass():
    doc = verify_gate.__doc__
    assert "--ledger" not in doc and "emit one on an anchored, clean PASS" in doc and "armed roots" in doc


def test_r5_the_receipt_valid_line_says_the_receipt_is_spent(tmp_path):
    root, code = mk_root(tmp_path)
    rec, _v, _a = issue(root, code)
    rp = put(root / ".verify" / "receipt.json", rec)
    r = _run_cli("--receipt", str(rp), "--changed", str(code))
    assert r.returncode == 0, r.stdout
    d = json.loads(r.stdout)
    assert d["decision"] == "receipt_valid" and "spent: the Stop gate no longer accepts this receipt" in d["reason"], d


# ======================================================================================================================
# s241 (Codex review of 30de4ae) F-2 and F-4. Written BEFORE the fixes.
# ======================================================================================================================
def _swap_after_load(monkeypatch, basename, new_obj):
    """After core.load_json has returned the clean `basename`, replace that file with `new_obj` (an atomic replace between the
    eligibility decision and the issuance read)."""
    real = core.load_json

    def load(path):
        out = real(path)
        if os.path.basename(path) == basename:
            tmp = str(path) + ".swap"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(new_obj, fh)
            os.replace(tmp, path)
        return out
    monkeypatch.setattr(core, "load_json", load)


def test_s241_f2_write_receipt_refuses_an_artifact_the_gate_would_not_accept(tmp_path):
    root, code = mk_root(tmp_path)
    v = put(root / "verdict.json", mk_verdict())
    a = put(root / "attestation.json", mk_att(anchor_kind="self", attestor="reviewer"))     # un-anchored: not issuable
    with pytest.raises(R.UnsafeFile):
        R.write_receipt(changed_paths=[str(code)], verdict_path=str(v), gate_exit=0, attestation_ref=str(a))


def test_s241_f2_cli_artifact_replaced_between_load_and_issuance_issues_no_receipt(tmp_path, monkeypatch):
    v = put(tmp_path / "v.json", mk_verdict())
    a = put(tmp_path / "attestation.json", mk_att())
    out, code = tmp_path / "receipt.json", tmp_path / "code.py"
    code.write_text("x = 1\n")
    _swap_after_load(monkeypatch, "attestation.json", mk_att(anchor_kind="self", attestor="reviewer"))
    rc = verify_gate.main(["--verdict", str(v), "--attestation", str(a), "--changed", str(code), "--write-receipt", str(out)])
    assert not out.exists(), "a receipt was issued over bytes the gate would refuse"
    assert rc != core.ExitCode.PASS


def test_s241_f2_produce_artifact_replaced_between_load_and_issuance_issues_no_receipt(tmp_path, monkeypatch):
    code = tmp_path / "code.py"
    code.write_text("x = 1\n")
    outdir = tmp_path / "out"
    _swap_after_load(monkeypatch, "attestation.json", mk_att(anchor_kind="self", attestor="reviewer"))
    res = produce_review.produce(artifact="art", evidence=[{"type": "file", "ref": "x:1"}], drafter_id="author",
                                 producer_id="reviewer", backend="harness_subagent", orchestrator_id="orchestrator",
                                 anchor_id="sub-9", outdir=str(outdir), changed_paths=[str(code)], agent_verdict=mk_verdict())
    assert res["receipt_path"] is None and not (outdir / "receipt.json").exists(), res
    assert res["receipt_refused"], res


def test_s241_f4_safe_read_wraps_a_read_oserror_as_unsafefile(tmp_path, monkeypatch):
    p = tmp_path / "f.bin"
    p.write_bytes(b"abc")

    def on_read(real, fd, n, k):
        raise OSError(5, "I/O error")
    _intercept_read(monkeypatch, p, on_read)
    with pytest.raises(R.UnsafeFile):
        R._safe_read(str(p))


def test_s241_f4_safe_read_wraps_an_fstat_oserror_as_unsafefile(tmp_path, monkeypatch):
    p = tmp_path / "f.bin"
    p.write_bytes(b"abc")
    real_fstat, ino, n = os.fstat, os.stat(p).st_ino, {"k": 0}

    def fake(fd):
        st = real_fstat(fd)
        if st.st_ino == ino:
            n["k"] += 1
            if n["k"] >= 2:                         # the after-read fstat
                raise OSError(5, "I/O error")
        return st
    monkeypatch.setattr(R.os, "fstat", fake)
    with pytest.raises(R.UnsafeFile):
        R._safe_read(str(p))


def test_s241_f4_a_vanished_artifact_refuses_at_both_entry_points(tmp_path, monkeypatch):
    v = put(tmp_path / "v.json", mk_verdict())
    a = put(tmp_path / "attestation.json", mk_att())
    out, code = tmp_path / "receipt.json", tmp_path / "code.py"
    code.write_text("x = 1\n")
    real = core.load_json

    def load(path):
        o = real(path)
        if os.path.basename(path) == "attestation.json":
            os.unlink(path)
        return o
    monkeypatch.setattr(core, "load_json", load)
    rc = verify_gate.main(["--verdict", str(v), "--attestation", str(a), "--changed", str(code), "--write-receipt", str(out)])
    assert rc != core.ExitCode.PASS and not out.exists()
    outdir = tmp_path / "out"
    res = produce_review.produce(artifact="art", evidence=[{"type": "file", "ref": "x:1"}], drafter_id="author",
                                 producer_id="reviewer", backend="harness_subagent", orchestrator_id="orchestrator",
                                 anchor_id="sub-9", outdir=str(outdir), changed_paths=[str(code)], agent_verdict=mk_verdict())
    assert res["receipt_path"] is None and res["receipt_refused"], res


# ---- s241 F-6: the schema check runs before ANY artifact read (a reader sentinel, not only the outcome) --------------------
@pytest.mark.parametrize("schema", ["verify_gate.receipt.v1", "verify_gate.receipt.v9", None])
def test_s241_f6_a_bad_schema_is_refused_before_any_artifact_is_read(tmp_path, monkeypatch, schema):
    root, code = mk_root(tmp_path)
    rec, _v, _a = issue(root, code)
    if schema is None:
        rec.pop("schema")
    else:
        rec["schema"] = schema

    def sentinel(*a, **k):
        raise AssertionError("an artifact was read before the schema was rejected")
    monkeypatch.setattr(R, "_safe_read", sentinel)
    monkeypatch.setattr(R, "_safe_read_bytes", sentinel)
    ok, why = validate(rec, code, root)
    assert not ok and ("re-issue under v2" in why or "bad receipt schema" in why), why


# ---- s241 survivors of the slot run on 30de4ae (R-3) -----------------------------------------------------------------------------
def test_t16_a_candidate_with_both_folders_and_no_armed_marker_is_not_a_root(tmp_path):
    """Kills "nearest .verify/ as the root": NEOCORTEX/ and .verify/ both present, `armed` absent: unarmed, so no root (both
    placements keep walking, and with no armed ancestor the answer is "no project root")."""
    p = tmp_path / "p"
    (p / "NEOCORTEX").mkdir(parents=True)
    (p / ".verify").mkdir()
    f = p / "f.py"
    f.write_text("")
    assert R.project_root([str(f)]) == (None, "no project root")
    (p / ".verify" / "armed").write_text("")
    assert R.project_root([str(f)])[0] == p                          # control: only the marker changed


def test_t16_an_unmarked_nested_candidate_does_not_become_the_root(tmp_path):
    outer = _armed_root(tmp_path)
    inner = _candidate(outer, "inner")                               # NEOCORTEX/ + .verify/, no marker
    root, why = R.project_root([str(inner / "f.py")])
    assert why == "ok" and os.path.realpath(root) == os.path.realpath(outer), (root, why)


def test_t16b_one_path_whose_lexical_and_resolved_roots_differ_is_ambiguous(tmp_path):
    """Kills "first root taken on ambiguity": P1/src/a.py is a link to a file in P2; both are armed roots."""
    p1 = _armed_root(tmp_path, "P1")
    p2 = _armed_root(tmp_path, "P2")
    (p2 / "b.py").write_text("x = 1\n")
    os.symlink(p2 / "b.py", p1 / "src" / "a.py")
    assert R.project_root([str(p1 / "src" / "a.py")]) == (None, "ambiguous project root")
    assert R.project_root([str(p2 / "b.py")])[0] == p2               # control: the plain path has one root

"""receipt-v3 (PLAN_verify_gate_receipt_v3_2026-11-13.md v0.4, §3 T1-T19): a receipt proves what was reviewed.

G3-1 (any later change-set), G3-2 (any number of times) and G3-3 (edits during the review) each have a test here
(T3, T7, T2). Their 0.4.0 comparison runs use 0.4.0's own v1 verdict and v1 attestation; they are recorded by
`governance/NEOCORTEX/_evidence/2026-11-13_receipt_v3_040_gaps/demo_040_gaps.py` (this file's API does not exist on 0.4.0).
Every negative test changes only its target condition (v2's X4-1 rule). Hermetic: every file lives under tmp_path.
Run: python3.12 -m pytest _skills/verify_gate/tests/test_receipt_v3.py -q
"""
import hashlib
import json
import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, "..", "scripts")
sys.path.insert(0, SCRIPTS)

import core                      # noqa: E402
import evidence_bundle           # noqa: E402
import receipt as R              # noqa: E402
import review_package as P       # noqa: E402
import produce_review            # noqa: E402
import judges                    # noqa: E402

V2, A2 = "verify_gate.verdict.v2", "verify_gate.attestation.v2"
LEDGER = "_consumed_nonces.json"


# ---------------------------------------------------------------- helpers

def _proj(tmp_path, armed=True):
    """A project folder; armed = NEOCORTEX/ + .verify/armed, so project_root() finds it (the real CLI's ledger rule)."""
    proj = tmp_path / "proj"
    (proj / "NEOCORTEX").mkdir(parents=True)
    (proj / ".verify").mkdir()
    if armed:
        (proj / ".verify" / "armed").write_text("")
    return proj


def _files(proj, names=("f1.py", "f2.py")):
    out = []
    for n in names:
        p = proj / n
        p.write_text(f"# {n}\nx = 1\n")
        out.append(str(p))
    return out


def _verdict(sha, *, schema=V2, producer="reviewer"):
    v = {"schema": schema, "producer_id": producer, "drafter_id": "author",
         "lenses": [{"lens": "correctness", "verdict": "holds", "probe": "read every snapshot file", "findings": []}]}
    if sha is not None:
        v["package_sha256"] = sha
    return v


def _att(pkg, *, schema=A2, producer="reviewer"):
    a = {"schema": schema, "anchor_kind": "harness_subagent", "anchor_id": "sub-1", "attestor": "orch",
         "producer_id": producer, "context_isolated": True}
    if schema == A2:
        a["package_sha256"] = pkg["package_sha256"]
        a["package_ref"] = os.path.realpath(pkg["package_path"])
    return a


def _review(tmp_path, paths, *, tag="r", verdict_sha="pkg", att_schema=A2, verdict_schema=V2):
    """Build a package over `paths`, then write a clean verdict + attestation for it. Returns (pkg, vpath, apath)."""
    out = tmp_path / f"review_{tag}"
    pkg = P.build(paths, str(out))
    sha = pkg["package_sha256"] if verdict_sha == "pkg" else verdict_sha
    vpath, apath = out / "verdict.json", out / "attestation.json"
    vpath.write_text(json.dumps(_verdict(sha, schema=verdict_schema)))
    apath.write_text(json.dumps(_att(pkg, schema=att_schema)))
    return pkg, str(vpath), str(apath)


def _issue(paths, pkg, vpath, apath, **kw):
    return R.write_receipt(changed_paths=paths, verdict_path=vpath, gate_exit=0, attestation_ref=apath,
                           package_path=pkg["package_path"], **kw).as_dict()


def _ledger(proj):
    return str(proj / ".verify" / LEDGER)


def _forced(monkeypatch, paths, pkg, vpath, apath):
    """Force a receipt through issuance with the three package checks switched off, to test that VALIDATION refuses it."""
    with monkeypatch.context() as m:
        m.setattr(R, "_package_checks", lambda *a, **k: None)
        return _issue(paths, pkg, vpath, apath)


def _run(script, *args, cwd=None):
    return subprocess.run([sys.executable, os.path.join(SCRIPTS, script), *args],
                          capture_output=True, text=True, cwd=cwd, timeout=60)


# ---------------------------------------------------------------- T1-T19

def test_t1_clean_v2_verdict_issues_valid_v3_receipt(tmp_path):
    proj = _proj(tmp_path)
    paths = _files(proj)
    pkg, v, a = _review(tmp_path, paths)
    rec = _issue(paths, pkg, v, a)
    assert rec["schema"] == "verify_gate.receipt.v3"
    assert rec["package_sha256"] == pkg["package_sha256"]
    assert rec["package_ref"] == os.path.realpath(pkg["package_path"])
    ok, why = R.validate_receipt(rec, changed_paths=paths, ledger_path=_ledger(proj))
    assert ok, why


def test_t2_file_edited_after_build_refuses_issuance(tmp_path):
    """G3-3: bytes edited between the judge's read (the package) and issuance."""
    proj = _proj(tmp_path)
    paths = _files(proj)
    pkg, v, a = _review(tmp_path, paths)
    with open(paths[0], "a") as fh:
        fh.write("y = 2\n")
    with pytest.raises(R.UnsafeFile, match="package"):
        _issue(paths, pkg, v, a)


def test_t2b_same_size_edit_after_build_refuses_issuance(tmp_path):
    """G3-3 with the size unchanged: only the live sha256 can see it (s329 F-6 added a size check beside it)."""
    proj = _proj(tmp_path)
    paths = _files(proj)
    pkg, v, a = _review(tmp_path, paths)
    before = open(paths[0]).read()
    with open(paths[0], "w") as fh:
        fh.write(before.replace("x = 1", "x = 2"))
    assert len(open(paths[0]).read()) == len(before)
    with pytest.raises(R.UnsafeFile, match="package file changed"):
        _issue(paths, pkg, v, a)


@pytest.mark.parametrize("case", ["edited", "outside"])
def test_t3_clean_verdict_for_package_a_never_binds_other_bytes(tmp_path, monkeypatch, case):
    """G3-1: a clean verdict for package A, then (i) a package file edited or (ii) a changed path outside the package."""
    proj = _proj(tmp_path)
    paths = _files(proj, ("f1.py", "f2.py", "f3.py"))
    in_pkg = paths[:2]
    pkg, v, a = _review(tmp_path, in_pkg)
    if case == "edited":
        with open(in_pkg[0], "a") as fh:
            fh.write("y = 2\n")
        changed = in_pkg
    else:
        changed = paths          # f3.py is not in the package
    with pytest.raises(R.UnsafeFile):
        _issue(changed, pkg, v, a)
    forced = _forced(monkeypatch, changed, pkg, v, a)
    ok, why = R.validate_receipt(forced, changed_paths=changed, ledger_path=_ledger(proj))
    assert not ok, why


def test_t4_superset_package_validates_narrower_list_and_refuses_wider(tmp_path):
    """RV3-10 / R-01: v2's change-set equality is gone; checks 1-3 replace it."""
    proj = _proj(tmp_path)
    f1, f2, f3 = _files(proj, ("f1.py", "f2.py", "f3.py"))
    pkg, v, a = _review(tmp_path, [f1, f2])
    rec = _issue([f1, f2], pkg, v, a)
    ok, why = R.validate_receipt(rec, changed_paths=[f1], ledger_path=_ledger(proj))
    assert ok, why
    ok, why = R.validate_receipt(rec, changed_paths=[f1, f3], ledger_path=_ledger(proj))
    assert not ok and "package" in why, why
    with open(f2, "a") as fh:
        fh.write("y = 2\n")
    ok, why = R.validate_receipt(rec, changed_paths=[f1], ledger_path=_ledger(proj))
    assert not ok, why


def test_t5_verdict_package_differs_from_attestation_is_tamper(tmp_path):
    proj = _proj(tmp_path)
    pkg = P.build(_files(proj), str(tmp_path / "out"))
    d = core.evaluate(_verdict("0" * 64), _att(pkg))
    assert d.exit_code == core.ExitCode.TAMPER == 5
    assert any("verdict reviewed another package" in r for r in d.reasons)


def _rewrite_package(pkg, edit):
    data = json.loads(open(pkg["package_path"]).read())
    edit(data)
    os.chmod(pkg["package_path"], 0o644)
    with open(pkg["package_path"], "w") as fh:
        json.dump(data, fh)


def test_t6_package_json_edited_after_the_verdict_refuses(tmp_path):
    """s329 F-7: one condition only, a fingerprinted field (`size` of an entry)."""
    proj = _proj(tmp_path)
    paths = _files(proj)
    pkg, v, a = _review(tmp_path, paths)
    rec = _issue(paths, pkg, v, a)
    _rewrite_package(pkg, lambda d: d["files"][0].__setitem__("size", d["files"][0]["size"] + 1))
    with pytest.raises(R.UnsafeFile):
        _issue(paths, pkg, v, a)
    ok, why = R.validate_receipt(rec, changed_paths=paths, ledger_path=_ledger(proj))
    assert not ok, why


def test_t6b_built_ts_alone_is_outside_the_fingerprint(tmp_path):
    """s329 F-7, pinned as it is: `built_ts` is not in the fingerprinted `files` list, so editing it alone keeps the
    package_sha256, the issued receipt and a new issuance valid (the snapshot and the live bytes are what is bound)."""
    proj = _proj(tmp_path)
    paths = _files(proj)
    pkg, v, a = _review(tmp_path, paths)
    rec = _issue(paths, pkg, v, a)
    _rewrite_package(pkg, lambda d: d.__setitem__("built_ts", d["built_ts"] + 1))
    assert P.read(pkg["package_path"])[1] == pkg["package_sha256"]
    ok, why = R.validate_receipt(rec, changed_paths=paths, ledger_path=_ledger(proj))
    assert ok, why
    assert _issue(paths, pkg, v, a)["package_sha256"] == pkg["package_sha256"]


def test_t7_one_verdict_one_receipt_once_spent(tmp_path):
    """G3-2: before the first spend a second receipt issues; after it, issue, validate and consume all refuse."""
    proj = _proj(tmp_path)
    paths = _files(proj)
    pkg, v, a = _review(tmp_path, paths)
    ledger = _ledger(proj)
    first, second = _issue(paths, pkg, v, a), _issue(paths, pkg, v, a)
    assert first["nonce"] != second["nonce"]
    ok, why = R.validate_and_consume(first, changed_paths=paths, ledger_path=ledger)
    assert ok, why
    with pytest.raises(R.UnsafeFile, match="spent"):
        _issue(paths, pkg, v, a)
    ok, why = R.validate_receipt(second, changed_paths=paths, ledger_path=ledger)
    assert not ok and "spent" in why, why
    ok, why = R.validate_and_consume(second, changed_paths=paths, ledger_path=ledger)
    assert not ok and "spent" in why, why


NONCE, VSHA = "a" * 32, "b" * 64


def test_t8_list_ledger_migrates_on_consume(tmp_path):
    ledger = tmp_path / LEDGER
    ledger.write_text(json.dumps([NONCE]))
    nonces, verdicts = R._load_ledger(str(ledger))
    assert nonces == {NONCE} and verdicts == set()
    R.consume("c" * 32, str(ledger))
    data = json.loads(ledger.read_text())
    assert set(data) == {"nonces", "verdicts"}
    assert sorted(data["nonces"]) == [NONCE, "c" * 32] and data["verdicts"] == []


@pytest.mark.parametrize("bad", [
    {"nonces": [NONCE], "verdicts": [VSHA], "extra": []},   # an extra key
    {"nonces": [NONCE]},                                     # a missing key
    {"nonces": ["not-a-nonce"], "verdicts": [VSHA]},         # a bad nonce
    {"nonces": [NONCE], "verdicts": ["xyz"]},                # a non-hex verdict
])
def test_t8_malformed_object_ledger_fails_closed(tmp_path, bad):
    ledger = tmp_path / LEDGER
    ledger.write_text(json.dumps(bad))
    with pytest.raises(R.LedgerError):
        R._load_ledger(str(ledger))


def test_t8_good_object_ledger_reads(tmp_path):
    ledger = tmp_path / LEDGER
    ledger.write_text(json.dumps({"nonces": [NONCE], "verdicts": [VSHA]}))
    assert R._load_ledger(str(ledger)) == ({NONCE}, {VSHA})


def test_t9_v2_receipt_is_invalid_reissue_under_v3(tmp_path):
    proj = _proj(tmp_path)
    paths = _files(proj)
    pkg, v, a = _review(tmp_path, paths)
    rec = _issue(paths, pkg, v, a)
    rec["schema"] = "verify_gate.receipt.v2"
    ok, why = R.validate_receipt(rec, changed_paths=paths, ledger_path=_ledger(proj))
    assert not ok and "re-issue under v3" in why, why


def test_t10_v1_verdict_evaluates_but_is_never_issuable(tmp_path):
    v1 = _verdict(None, schema="verify_gate.verdict.v1")
    a1 = _att(None, schema="verify_gate.attestation.v1")
    assert core.evaluate(v1, a1).exit_code == core.ExitCode.PASS
    ok, why = R.issuable(v1, a1)
    assert not ok, why


def test_t11_prepare_finish_end_to_end(tmp_path):
    proj = _proj(tmp_path)
    paths = _files(proj)
    d = tmp_path / "D"
    r = _run("produce_review.py", "prepare", "--changed", *paths, "--outdir", str(d))
    assert r.returncode == 0, r.stderr
    pkg_sha = P.read(str(d / "package.json"))[1]
    assert pkg_sha in r.stdout and str(d / "package") in r.stdout and "read only the snapshot" in r.stdout
    journal = [ln for ln in r.stdout.splitlines() if ln.startswith("JOURNAL:")]
    assert journal and all(p in journal[0] for p in paths), r.stdout
    # the stub judge: a clean v2 verdict that repeats the fingerprint
    vj = tmp_path / "stub_verdict.json"
    vj.write_text(json.dumps(_verdict(pkg_sha)))
    r = _run("produce_review.py", "finish", "--verdict-json", str(vj), "--outdir", str(d),
             "--orchestrator-id", "orch", "--anchor-id", "sub-1")
    assert r.returncode == 0, r.stdout + r.stderr
    rec = json.loads((d / "receipt.json").read_text())
    att = json.loads((d / "attestation.json").read_text())
    assert att["schema"] == A2 and att["package_sha256"] == pkg_sha
    ok, why = R.validate_receipt(rec, changed_paths=paths, ledger_path=_ledger(proj))
    assert ok, why


def test_t12_build_refuses_links_and_copies_read_only(tmp_path):
    proj = _proj(tmp_path)
    paths = _files(proj)
    pkg = P.build(paths, str(tmp_path / "ok"))
    for f in pkg["files"]:
        mode = os.stat(os.path.join(os.path.dirname(pkg["package_path"]), f["copy"])).st_mode & 0o777
        assert mode == 0o444, oct(mode)
    link = proj / "link.py"
    link.symlink_to(paths[0])
    with pytest.raises(R.UnsafeFile):
        P.build([str(link)], str(tmp_path / "sym"))
    hard = proj / "hard.py"
    os.link(paths[1], hard)                 # paths[1] now has nlink == 2
    with pytest.raises(R.UnsafeFile):
        P.build([paths[1]], str(tmp_path / "hard"))


def test_t13_seal_requires_a_package():
    with pytest.raises(TypeError):
        evidence_bundle.seal(artifact="x", evidence=[])


def test_t14_v2_verdict_without_package_sha_is_refused_by_schema():
    v = _verdict(None)
    assert any("package_sha256" in p for p in core.validate_verdict_schema(v))
    v["package_sha256"] = "XYZ"
    assert any("package_sha256" in p for p in core.validate_verdict_schema(v))


def test_t15_v1_attestation_with_v2_verdict_is_not_issuable(tmp_path):
    proj = _proj(tmp_path)
    pkg = P.build(_files(proj), str(tmp_path / "out"))
    ok, why = R.issuable(_verdict(pkg["package_sha256"]), _att(pkg, schema="verify_gate.attestation.v1"))
    assert not ok, why


def test_t16_cli_write_receipt_without_package_writes_nothing(tmp_path):
    proj = _proj(tmp_path)
    paths = _files(proj)
    pkg, v, a = _review(tmp_path, paths)
    out = tmp_path / "rec.json"
    r = _run("verify_gate.py", "--verdict", v, "--attestation", a, "--changed", *paths, "--write-receipt", str(out))
    assert r.returncode != 0
    assert not out.exists()
    assert "--package" in r.stdout + r.stderr


@pytest.mark.parametrize("field", ["package_ref", "package_sha256"])
def test_t17_edited_package_fields_in_receipt_refuse(tmp_path, field):
    proj = _proj(tmp_path)
    paths = _files(proj)
    pkg, v, a = _review(tmp_path, paths)
    rec = _issue(paths, pkg, v, a)
    if field == "package_ref":
        other = P.build(paths, str(tmp_path / "other"))      # a real package, same bytes, another place
        rec[field] = os.path.realpath(other["package_path"])
    else:
        rec[field] = "0" * 64
    ok, why = R.validate_receipt(rec, changed_paths=paths, ledger_path=_ledger(proj))
    assert not ok, why


def test_t18_spent_verdict_and_bad_ledger_raise_unsafefile_and_clis_say_so(tmp_path):
    proj = _proj(tmp_path)
    paths = _files(proj)
    pkg, v, a = _review(tmp_path, paths)
    first = _issue(paths, pkg, v, a)                        # default ledger: the armed root's
    assert R.validate_and_consume(first, changed_paths=paths, ledger_path=_ledger(proj))[0]
    with pytest.raises(R.UnsafeFile, match="spent"):
        _issue(paths, pkg, v, a)
    # the CLI, which passes no ledger_path: no receipt, reason printed, no traceback
    out = tmp_path / "rec.json"
    r = _run("verify_gate.py", "--verdict", v, "--attestation", a, "--changed", *paths,
             "--write-receipt", str(out), "--package", pkg["package_path"])
    assert r.returncode != 0 and not out.exists() and "spent" in r.stdout and "Traceback" not in r.stderr
    # produce_review.finish: same verdict, same package folder
    d = os.path.dirname(pkg["package_path"])
    r = _run("produce_review.py", "finish", "--verdict-json", v, "--outdir", d,
             "--orchestrator-id", "orch", "--anchor-id", "sub-1")
    assert not os.path.exists(os.path.join(d, "receipt.json"))
    assert r.returncode == core.ExitCode.RECEIPT_INVALID, r.stdout + r.stderr       # s329 F-7
    assert "spent" in r.stdout and "Traceback" not in r.stderr


def test_t18b_malformed_ledger_raises_unsafefile_and_clis_say_so(tmp_path):
    """T18's second condition on its own (s520 L2: one condition per test): a malformed armed-root ledger, nothing spent."""
    proj = _proj(tmp_path)
    paths = _files(proj)
    with open(_ledger(proj), "w") as fh:
        fh.write("{not json")
    pkg2, v2, a2 = _review(tmp_path, paths, tag="r2")
    with pytest.raises(R.UnsafeFile):                       # UnsafeFile, not LedgerError
        _issue(paths, pkg2, v2, a2)
    # s329 F-7: both CLIs on the malformed ledger, default ledger resolution: exit 6, no receipt, no traceback
    out2 = tmp_path / "rec2.json"
    r = _run("verify_gate.py", "--verdict", v2, "--attestation", a2, "--changed", *paths,
             "--write-receipt", str(out2), "--package", pkg2["package_path"])
    assert r.returncode == core.ExitCode.RECEIPT_INVALID and not out2.exists(), r.stdout + r.stderr
    assert "Traceback" not in r.stderr
    d2 = os.path.dirname(pkg2["package_path"])
    r = _run("produce_review.py", "finish", "--verdict-json", v2, "--outdir", d2,
             "--orchestrator-id", "orch", "--anchor-id", "sub-1")
    assert r.returncode == core.ExitCode.RECEIPT_INVALID, r.stdout + r.stderr
    assert not os.path.exists(os.path.join(d2, "receipt.json")) and "Traceback" not in r.stderr


def test_t19_package_other_than_attestations_is_refused(tmp_path):
    proj = _proj(tmp_path)
    paths = _files(proj)
    pkg, v, a = _review(tmp_path, paths)
    other = P.build(paths, str(tmp_path / "other"))          # same bytes, same package_sha256, another package.json
    assert other["package_sha256"] == pkg["package_sha256"]
    with pytest.raises(R.UnsafeFile, match="package_ref"):
        R.write_receipt(changed_paths=paths, verdict_path=v, gate_exit=0, attestation_ref=a,
                        package_path=other["package_path"])
    out = tmp_path / "rec.json"
    r = _run("verify_gate.py", "--verdict", v, "--attestation", a, "--changed", *paths,
             "--write-receipt", str(out), "--package", other["package_path"])
    assert r.returncode != 0 and not out.exists()


def test_no_ledger_receipt_says_none(tmp_path):
    """RV3-5 interface: with no armed root and no ledger_path, the spent check is skipped and the receipt says so."""
    proj = _proj(tmp_path, armed=False)
    paths = _files(proj)
    pkg, v, a = _review(tmp_path, paths)
    assert _issue(paths, pkg, v, a)["ledger"] == "none"


def test_package_sha_is_derived_not_stored(tmp_path):
    proj = _proj(tmp_path)
    pkg = P.build(_files(proj), str(tmp_path / "out"))
    data = json.loads(open(pkg["package_path"]).read())
    assert "package_sha256" not in data and data["schema"] == "verify_gate.package.v1"
    canon = json.dumps(data["files"], sort_keys=True, separators=(",", ":")).encode()
    assert hashlib.sha256(canon).hexdigest() == pkg["package_sha256"]
    assert [f["path"] for f in data["files"]] == sorted(f["path"] for f in data["files"])


# ---------------------------------------------------------------- s329 fixes (NOTE_triage_s329_..._2026-11-14.md)
# Each test was written before its fix and recorded RED on the s329-pinned tree.

def _produce(tmp_path, paths, tag):
    """The one-shot producer with a stub judge that repeats the bundle's fingerprint (a clean, anchored v2 verdict)."""
    return produce_review.produce(
        artifact="art", evidence=[], drafter_id="author", producer_id="reviewer", backend="harness_subagent",
        orchestrator_id="orch", anchor_id="sub-1", outdir=str(tmp_path / f"out_{tag}"), changed_paths=paths,
        judge_fn=lambda b: _verdict(b["package"]["package_sha256"]))


def test_s329_f1_produce_maps_a_refused_receipt_on_pass_to_6_corrupt_ledger(tmp_path):
    proj = _proj(tmp_path)
    paths = _files(proj)
    with open(_ledger(proj), "w") as fh:
        fh.write("{corrupt")
    res = _produce(tmp_path, paths, "a")
    assert res["exit"] == core.ExitCode.RECEIPT_INVALID == 6, res
    assert res["decision"] == "receipt_invalid" and res["receipt_path"] is None and res["receipt_refused"]


def test_s329_f1_produce_maps_a_spent_verdict_to_6(tmp_path):
    proj = _proj(tmp_path)
    paths = _files(proj)
    first = _produce(tmp_path, paths, "a")
    assert first["exit"] == 0 and first["receipt_path"], first
    rec = json.loads(open(first["receipt_path"]).read())
    assert R.validate_and_consume(rec, changed_paths=paths, ledger_path=_ledger(proj))[0]
    second = _produce(tmp_path, paths, "b")            # same files, same stub verdict bytes: the verdict is spent
    assert second["exit"] == core.ExitCode.RECEIPT_INVALID, second
    assert second["receipt_path"] is None and "spent" in second["receipt_refused"]


def test_s329_f2_write_receipt_refuses_an_empty_change_set(tmp_path):
    proj = _proj(tmp_path)
    paths = _files(proj)
    pkg, v, a = _review(tmp_path, paths)
    with pytest.raises(R.UnsafeFile, match="no changed path"):
        R.write_receipt(changed_paths=[], verdict_path=v, gate_exit=0, attestation_ref=a,
                        package_path=pkg["package_path"])
    out = tmp_path / "rec.json"                       # the CLI without --changed (its default is [])
    r = _run("verify_gate.py", "--verdict", v, "--attestation", a, "--write-receipt", str(out),
             "--package", pkg["package_path"])
    assert r.returncode == core.ExitCode.RECEIPT_INVALID and not out.exists(), r.stdout + r.stderr


def _loose(tmp_path):
    d = tmp_path / "loose"                             # no NEOCORTEX/.verify above it: no project root
    d.mkdir()
    p = d / "u.py"
    p.write_text("u = 1\n")
    return str(p)


def test_s329_f2_ledger_resolves_over_the_package_not_a_changed_subset(tmp_path):
    proj = _proj(tmp_path)
    (f1,) = _files(proj, ("f1.py",))
    u = _loose(tmp_path)
    pkg, v, a = _review(tmp_path, [u, f1])
    first = _issue([u, f1], pkg, v, a)
    assert R.validate_and_consume(first, changed_paths=[u, f1], ledger_path=_ledger(proj))[0]
    with pytest.raises(R.UnsafeFile, match="spent"):
        _issue([u], pkg, v, a)                         # [u] alone has no root; the package's f1 has the armed one


def test_s329_f2_unreadable_armed_state_of_a_package_path_refuses(tmp_path):
    proj = _proj(tmp_path)
    (f1,) = _files(proj, ("f1.py",))
    u = _loose(tmp_path)
    pkg, v, a = _review(tmp_path, [u, f1])
    os.unlink(proj / ".verify" / "armed")
    (proj / ".verify" / "armed").mkdir()               # a folder named armed: the armed state is unreadable
    with pytest.raises(R.UnsafeFile, match="ledger not resolvable"):
        _issue([u], pkg, v, a)


def _prepared(tmp_path):
    proj = _proj(tmp_path)
    paths = _files(proj)
    d = tmp_path / "D"
    produce_review.prepare(changed_paths=paths, outdir=str(d))
    return proj, paths, d, P.read(str(d / "package.json"))[1]


def test_s329_f3_finish_reads_a_verdict_through_a_symlinked_parent(tmp_path):
    proj, paths, d, sha = _prepared(tmp_path)
    real = tmp_path / "real_v"
    real.mkdir()
    (tmp_path / "link_v").symlink_to(real)
    (real / "verdict.json").write_text(json.dumps(_verdict(sha)))
    out = produce_review.finish(verdict_json=str(tmp_path / "link_v" / "verdict.json"), outdir=str(d),
                                orchestrator_id="orch", anchor_id="sub-1")
    assert out["exit"] == 0 and out["receipt_path"], out


def test_s329_f3_finish_reports_a_leaf_link_verdict(tmp_path):
    proj, paths, d, sha = _prepared(tmp_path)
    vj = tmp_path / "verdict_real.json"
    vj.write_text(json.dumps(_verdict(sha)))
    (tmp_path / "verdict_link.json").symlink_to(vj)
    out = produce_review.finish(verdict_json=str(tmp_path / "verdict_link.json"), outdir=str(d),
                                orchestrator_id="orch", anchor_id="sub-1")
    assert out["exit"] == core.ExitCode.RECEIPT_INVALID and out["receipt_path"] is None, out


def test_s329_f3_finish_reports_a_missing_package(tmp_path):
    _proj(tmp_path)
    d = tmp_path / "empty"
    d.mkdir()
    vj = tmp_path / "v.json"
    vj.write_text(json.dumps(_verdict("0" * 64)))
    out = produce_review.finish(verdict_json=str(vj), outdir=str(d), orchestrator_id="orch", anchor_id="sub-1")
    assert out["exit"] == core.ExitCode.RECEIPT_INVALID and out["receipt_path"] is None, out


@pytest.mark.parametrize("how", ["missing", "not-json"])
def test_s520_m1_finish_missing_or_non_json_verdict_is_tamper_5(tmp_path, how):
    """s520 M1, pinned as designed (SKILL.md wording fixed instead): a verdict that is absent or not a JSON object is
    TAMPER (exit 5), as `verify_gate.py` treats an unreadable verdict; an UNSAFE verdict file is a refusal (exit 6)."""
    proj, paths, d, sha = _prepared(tmp_path)
    vj = tmp_path / "v.json"
    if how == "not-json":
        vj.write_text("not json")
    out = produce_review.finish(verdict_json=str(vj), outdir=str(d), orchestrator_id="orch", anchor_id="sub-1")
    assert out["exit"] == core.ExitCode.TAMPER == 5 and out["receipt_path"] is None, out


def test_s520_m2_finish_reports_a_failed_receipt_write(tmp_path):
    """s520 M2: the receipt.json write is a refusal too (exit 6, reported), never a raise. A folder named receipt.json
    makes the write fail with IsADirectoryError after every check passed."""
    proj, paths, d, sha = _prepared(tmp_path)
    (d / "receipt.json").mkdir()
    vj = tmp_path / "v.json"
    vj.write_text(json.dumps(_verdict(sha)))
    out = produce_review.finish(verdict_json=str(vj), outdir=str(d), orchestrator_id="orch", anchor_id="sub-1")
    assert out["exit"] == core.ExitCode.RECEIPT_INVALID and out["receipt_path"] is None, out


def test_s520_m2_produce_reports_a_failed_receipt_write(tmp_path):
    proj = _proj(tmp_path)
    paths = _files(proj)
    (tmp_path / "out_w").mkdir()
    (tmp_path / "out_w" / "receipt.json").mkdir()
    res = _produce(tmp_path, paths, "w")
    assert res["exit"] == core.ExitCode.RECEIPT_INVALID and res["receipt_path"] is None, res


def test_s519_f1_produce_unissuable_pass_keeps_exit_0_and_says_why(tmp_path):
    """s519 F1, founder card 11-26 "Keep exit 0, fix wording": a PASS that cannot earn a receipt (here unanchored:
    orchestrator == producer) keeps exit 0, as the CLI (F-5) and t10_produce pin, but the reason is reported."""
    proj = _proj(tmp_path)
    paths = _files(proj)
    res = produce_review.produce(
        artifact="art", evidence=[], drafter_id="author", producer_id="reviewer", backend="harness_subagent",
        orchestrator_id="reviewer", anchor_id="sub-1", outdir=str(tmp_path / "out_u"), changed_paths=paths,
        judge_fn=lambda b: _verdict(b["package"]["package_sha256"]))
    assert res["exit"] == core.ExitCode.PASS and res["receipt_path"] is None, res
    assert any(r.startswith("no receipt written:") for r in res["reasons"]), res["reasons"]


def test_s519_f2_finish_resolves_a_parent_link_before_dotdot(tmp_path):
    """s519 F2: `link/../verdict.json` is the kernel's path (the link's target's parent), not the lexical fold."""
    proj, paths, d, sha = _prepared(tmp_path)
    (tmp_path / "nested" / "sub").mkdir(parents=True)
    (tmp_path / "link_sub").symlink_to(tmp_path / "nested" / "sub")
    blocking = {"schema": V2, "package_sha256": sha, "artifact": "art", "producer_id": "reviewer",
                "drafter_id": "author",
                "lenses": [{"lens": "correctness", "verdict": "refuted", "probe": "checked snapshot",
                            "findings": [{"severity": "high", "claim": "defect", "cheapest_fix": "fix",
                                          "evidence": {"type": "file", "ref": "f1.py:2", "literal": "x = 1"}}]}]}
    nested_v = tmp_path / "nested" / "verdict.json"
    nested_v.write_text(json.dumps(blocking))
    (tmp_path / "verdict.json").write_text(json.dumps(_verdict(sha)))
    via_link = str(tmp_path / "link_sub") + "/../verdict.json"
    assert os.path.realpath(via_link) == os.path.realpath(str(nested_v))
    out = produce_review.finish(verdict_json=via_link, outdir=str(d), orchestrator_id="orch", anchor_id="sub-1",
                                require_blocking=True)
    assert out["exit"] == core.ExitCode.BLOCK == 1 and out["receipt_path"] is None, out


def test_s519_f3_produce_reports_an_outdir_under_a_file(tmp_path):
    proj = _proj(tmp_path)
    paths = _files(proj)
    (tmp_path / "plain").write_text("not a folder\n")
    res = produce_review.produce(
        artifact="art", evidence=[], drafter_id="author", producer_id="reviewer", backend="harness_subagent",
        orchestrator_id="orch", anchor_id="sub-1", outdir=str(tmp_path / "plain" / "out"), changed_paths=paths,
        judge_fn=lambda b: _verdict(b["package"]["package_sha256"]))
    assert res["exit"] == core.ExitCode.RECEIPT_INVALID and res["receipt_path"] is None, res


@pytest.mark.parametrize("name", ["verdict.json", "receipt.json"])
def test_s519_f3_finish_never_writes_through_a_link_in_outdir(tmp_path, name):
    proj, paths, d, sha = _prepared(tmp_path)
    target = tmp_path / "elsewhere.json"
    target.write_text("untouched\n")
    (d / name).symlink_to(target)
    vj = tmp_path / "v.json"
    vj.write_text(json.dumps(_verdict(sha)))
    out = produce_review.finish(verdict_json=str(vj), outdir=str(d), orchestrator_id="orch", anchor_id="sub-1")
    assert out["exit"] == core.ExitCode.RECEIPT_INVALID and out["receipt_path"] is None, out
    assert target.read_text() == "untouched\n"


def test_s519_f4_same_size_edit_of_a_snapshot_copy_refuses(tmp_path):
    """s519 F4: one condition, the copy's sha256 alone (its size unchanged)."""
    proj = _proj(tmp_path)
    paths = _files(proj)
    pkg, v, a = _review(tmp_path, paths)
    rec = _issue(paths, pkg, v, a)
    c = _copy0(pkg)
    before = open(c).read()
    os.chmod(c, 0o644)
    with open(c, "w") as fh:
        fh.write(before.replace("x = 1", "x = 2"))
    assert len(open(c).read()) == len(before)
    with pytest.raises(R.UnsafeFile, match="snapshot copy differs"):
        _issue(paths, pkg, v, a)
    ok, why = R.validate_receipt(rec, changed_paths=paths, ledger_path=_ledger(proj))
    assert not ok and "snapshot copy differs" in why, why


def test_s519_f4_hard_linked_snapshot_copy_refuses(tmp_path):
    proj = _proj(tmp_path)
    paths = _files(proj)
    pkg, v, a = _review(tmp_path, paths)
    os.link(_copy0(pkg), tmp_path / "second_name")      # the copy now has nlink == 2
    with pytest.raises(R.UnsafeFile, match="snapshot copy unsafe"):
        _issue(paths, pkg, v, a)


def test_s519_f4_an_entry_size_that_lies_refuses(tmp_path):
    """One condition: the entry's size differs from the bytes, while its sha256 matches them and the verdict and the
    attestation repeat the fingerprint of that (lying) package.json."""
    proj = _proj(tmp_path)
    paths = _files(proj)
    out = tmp_path / "review_lie"
    pkg = P.build(paths, str(out))
    _rewrite_package(pkg, lambda d: d["files"][0].__setitem__("size", d["files"][0]["size"] + 1))
    sha = P.read(pkg["package_path"])[1]
    pkg = {**pkg, "package_sha256": sha}
    (out / "verdict.json").write_text(json.dumps(_verdict(sha)))
    (out / "attestation.json").write_text(json.dumps(_att(pkg)))
    with pytest.raises(R.UnsafeFile, match="snapshot copy differs"):
        _issue(paths, pkg, str(out / "verdict.json"), str(out / "attestation.json"))


def test_s329_f3_produce_reports_a_reused_outdir(tmp_path):
    proj = _proj(tmp_path)
    paths = _files(proj)
    assert _produce(tmp_path, paths, "same")["exit"] == 0
    res = _produce(tmp_path, paths, "same")
    assert res["exit"] == core.ExitCode.RECEIPT_INVALID and res["receipt_path"] is None, res
    assert str(tmp_path / "out_same") in res["receipt_refused"]


_HOLDS = [{"lens": "c", "verdict": "holds", "probe": "p", "findings": []}]


def _parsed(obj):
    return judges.parse_verdict(json.dumps(obj), producer_id="r", drafter_id="d", artifact="a")


@pytest.mark.parametrize("sha", ["<absent>", None, "", "abc"])
def test_s329_f4_a_declared_v2_stays_v2_and_core_refuses_its_bad_sha(sha):
    obj = {"schema": V2, "lenses": _HOLDS}
    if sha != "<absent>":
        obj["package_sha256"] = sha
    v = _parsed(obj)
    assert v["schema"] == V2
    assert any("package_sha256" in p for p in core.validate_verdict_schema(v))


@pytest.mark.parametrize("sha", [None, ""])
def test_s329_f4_an_undeclared_non_sha_is_not_relabelled_v2(sha):
    v = _parsed({"package_sha256": sha, "lenses": _HOLDS})
    assert v["schema"] == "verify_gate.verdict.v1"


def test_s329_f4_an_undeclared_repeated_sha_is_v2():
    assert _parsed({"package_sha256": "a" * 64, "lenses": _HOLDS})["schema"] == V2


def test_s329_f4_an_upper_case_sha_is_not_normalised():
    """s520 L1: split from the test above, one condition each."""
    v = _parsed({"schema": V2, "package_sha256": "A" * 64, "lenses": _HOLDS})
    assert v["schema"] == V2 and v["package_sha256"] == "A" * 64
    assert any("package_sha256" in p for p in core.validate_verdict_schema(v))


def _copy0(pkg):
    return os.path.join(os.path.dirname(pkg["package_path"]), pkg["files"][0]["copy"])


@pytest.mark.parametrize("how", ["missing", "altered", "link"])
def test_s329_f6_the_snapshot_copy_is_checked(tmp_path, how):
    proj = _proj(tmp_path)
    paths = _files(proj)
    pkg, v, a = _review(tmp_path, paths)
    rec = _issue(paths, pkg, v, a)
    c = _copy0(pkg)
    os.chmod(c, 0o644)
    if how == "missing":
        os.unlink(c)
    elif how == "altered":
        with open(c, "a") as fh:
            fh.write("# not the reviewed bytes\n")
    else:
        os.unlink(c)
        os.symlink(pkg["files"][0]["path"], c)          # same bytes as the live file, but a link
    with pytest.raises(R.UnsafeFile, match="snapshot"):
        _issue(paths, pkg, v, a)
    ok, why = R.validate_receipt(rec, changed_paths=paths, ledger_path=_ledger(proj))
    assert not ok and "snapshot" in why, why

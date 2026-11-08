"""FS-1 (PLAN_fix_external_reader_claims_verify_gate_2026-10-30): the consumed-nonce ledger is locked, atomic, and
fails closed. Hermetic: every file lives under pytest's tmp_path.

Invariants, each with its evidence on 0.2.0:
  - of 8 concurrent CLI runs on one receipt, at most one exits 0 (verify_gate.py validated, then consumed, unlocked);
  - no nonce is lost by 8 concurrent consumes (receipt.consume read-modify-wrote the whole file);
  - a present ledger that does not parse invalidates every receipt and is never rewritten (it read as empty);
  - the final ledger path is never opened for writing (consume rewrote it in place);
  - an absent ledger is empty (the one armed project has none).
Run: python3.12 -m pytest _skills/verify_gate/tests/ -q
"""
import builtins
import json
import os
import stat
import subprocess
import sys
import time

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, "..", "scripts")
sys.path.insert(0, SCRIPTS)

import receipt as R    # noqa: E402
import verify_gate     # noqa: E402

RECEIPT_INVALID = 6
N = 8

# T0, the controlled interleaving probe (0.3.1: two probes, at _load_ledger exit and at _write_ledger entry, plus a start
# barrier whose arrival count the parent asserts: all N children reached the lock together, else the run is inconclusive).
# Original text: Each child wraps receipt._load_ledger so that, after reading, it waits on a
# file barrier that every reader of the same round reaches before any writes. The wait ends when all N arrived, or
# when no new reader arrived for IDLE_S (bounded by CAP_S): it never raises, and the run goes on. On 0.2.0 each run
# reads twice (validate, then consume), so the barrier serves two rounds and every reader sees the same state. Under
# FS-1's lock only one reader reaches it at a time; the idle timeout releases it and the run proceeds.
IDLE_S, CAP_S = 0.75, 6.0
_CHILD = r'''
import glob, os, sys, time
scripts, bdir, n, idle, cap = sys.argv[1], sys.argv[2], int(sys.argv[3]), float(sys.argv[4]), float(sys.argv[5])
sys.path.insert(0, scripts)
import receipt
_orig_load, _orig_write, _round = receipt._load_ledger, receipt._write_ledger, [0]
def _barrier():
    _round[0] += 1
    open(os.path.join(bdir, f"r{_round[0]}_{os.getpid()}"), "w").close()
    t0 = last = time.monotonic(); seen = 0
    while time.monotonic() - t0 < cap:
        now = len(glob.glob(os.path.join(bdir, f"r{_round[0]}_*")))
        if now >= n:
            break
        if now != seen:
            seen, last = now, time.monotonic()
        elif time.monotonic() - last > idle:
            break
        time.sleep(0.005)
def _probe_load(*a, **k):
    out = _orig_load(*a, **k)
    if bdir != "-":
        _barrier()
    return out
def _probe_write(*a, **k):
    if bdir != "-":
        _barrier()
    return _orig_write(*a, **k)
def _arrival(fn):
    # B8: every child reports how many of the N children had arrived at the lock when it was released.
    def wrapper(*a, **k):
        if bdir != "-":
            open(os.path.join(bdir, f"s_{os.getpid()}"), "w").close()
            t0 = time.monotonic()
            while time.monotonic() - t0 < 30 and len(glob.glob(os.path.join(bdir, "s_*"))) < n:
                time.sleep(0.005)
            with open(os.path.join(bdir, f"count_{os.getpid()}"), "w") as fh:
                fh.write(str(len(glob.glob(os.path.join(bdir, "s_*")))))
        return fn(*a, **k)
    return wrapper
receipt._load_ledger = _probe_load
receipt._write_ledger = _probe_write
receipt.validate_and_consume = _arrival(receipt.validate_and_consume)
receipt.consume = _arrival(receipt.consume)
import verify_gate
if sys.argv[7] == "CONSUME":
    try:
        receipt.consume(sys.argv[8], sys.argv[9]); sys.exit(0)
    except receipt.LedgerError as e:
        print(e); sys.exit(6)
sys.exit(verify_gate.main(sys.argv[7:]))
'''


def _setup(tmp_path):
    code = tmp_path / "code.py"; code.write_text("x = 1\n")
    verdict = tmp_path / "verdict.json"; verdict.write_text("{}")
    return code, verdict, tmp_path / "ledger.json"


def _receipt(tmp_path, code, verdict, name="receipt.json", **override):
    rec = R.write_receipt(changed_paths=[str(code)], verdict_path=str(verdict), gate_exit=0,
                          attestation_ref="att.json").as_dict()
    rec.update(override)
    for k in [k for k, v in override.items() if v is _DROP]:
        del rec[k]
    p = tmp_path / name; p.write_text(json.dumps(rec))
    return p, rec


_DROP = object()


def _cli(receipt_path, code, ledger):
    return ["--receipt", str(receipt_path), "--changed", str(code), "--ledger", str(ledger)]


def _spawn(tmp_path, argvs, *, forced=True):
    child = tmp_path / "child.py"; child.write_text(_CHILD)
    bdir = tmp_path / "barrier"
    if forced:
        bdir.mkdir(exist_ok=True)
        for f in bdir.iterdir():
            f.unlink()
    procs = [subprocess.Popen([sys.executable, str(child), SCRIPTS, str(bdir) if forced else "-", str(N),
                               str(IDLE_S), str(CAP_S), "--", *argv],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for argv in argvs]
    outs = [p.communicate(timeout=120) for p in procs]
    for (_out, err) in outs:
        assert "Traceback" not in err, err
    if forced:
        counts = [int(f.read_text()) for f in bdir.glob("count_*")]
        assert len(counts) == len(argvs) and all(c == len(argvs) for c in counts), \
            f"INCONCLUSIVE: arrival counts {counts}, expected {len(argvs)} children x {len(argvs)}"
    return [p.returncode for p in procs]


# ---- T1 / T2: concurrency ---------------------------------------------------------------------------------------
def test_t1_eight_runs_on_one_receipt_exactly_one_passes(tmp_path):
    code, verdict, ledger = _setup(tmp_path)
    rp, rec = _receipt(tmp_path, code, verdict)
    rcs = _spawn(tmp_path, [_cli(rp, code, ledger)] * N)
    assert sorted(rcs) == [0] + [RECEIPT_INVALID] * (N - 1), rcs
    assert json.loads(ledger.read_text()) == [rec["nonce"]]


def test_t1_stress_twenty_unforced_rounds(tmp_path):
    """Stress evidence only (no forced interleaving): 20 rounds of 8 concurrent runs, each on a fresh receipt."""
    tally = []
    for i in range(20):
        code, verdict, ledger = _setup(tmp_path)
        rp, _rec = _receipt(tmp_path, code, verdict, name=f"r{i}.json")
        rcs = _spawn(tmp_path, [_cli(rp, code, ledger)] * N, forced=False)
        tally.append(rcs.count(0))
        ledger.unlink(missing_ok=True)
    print(f"\n  T1 stress: exits 0 per round of {N}: {tally}")
    assert all(c <= 1 for c in tally), tally


def test_t2_eight_distinct_receipts_all_recorded(tmp_path):
    code, verdict, ledger = _setup(tmp_path)
    recs = [_receipt(tmp_path, code, verdict, name=f"r{i}.json") for i in range(N)]
    rcs = _spawn(tmp_path, [_cli(rp, code, ledger) for rp, _ in recs])
    assert rcs == [0] * N, rcs
    assert sorted(json.loads(ledger.read_text())) == sorted(r["nonce"] for _, r in recs)


# ---- T3 / T4: the ledger's own state ----------------------------------------------------------------------------
@pytest.mark.parametrize("content", ['["0123', '{"a": 1}', '["not-a-hex-nonce"]'],
                         ids=["truncated", "json-object", "non-hex-entry"])
def test_t3_corrupt_ledger_invalidates_and_is_never_rewritten(tmp_path, content):
    code, verdict, ledger = _setup(tmp_path)
    ledger.write_text(content)
    before = ledger.read_bytes()
    rp, rec = _receipt(tmp_path, code, verdict)
    assert R.validate_receipt(rec, changed_paths=[str(code)], ledger_path=str(ledger)) == \
        (False, f"nonce ledger unreadable: {ledger}")
    assert verify_gate.main(_cli(rp, code, ledger)) == RECEIPT_INVALID
    assert ledger.read_bytes() == before


def test_t4_absent_ledger_is_empty_then_holds_one_nonce(tmp_path):
    code, verdict, ledger = _setup(tmp_path)
    rp, rec = _receipt(tmp_path, code, verdict)
    assert not ledger.exists()
    assert verify_gate.main(_cli(rp, code, ledger)) == 0
    assert json.loads(ledger.read_text()) == [rec["nonce"]]


# ---- T5 / T5b: the commit point ---------------------------------------------------------------------------------
def test_t5_replace_failure_leaves_the_ledger_unchanged_and_no_temp(tmp_path, monkeypatch):
    code, verdict, ledger = _setup(tmp_path)
    ledger.write_text(json.dumps(["0" * 32]))
    before = ledger.read_bytes()
    rp, _rec = _receipt(tmp_path, code, verdict)
    listing = set(os.listdir(tmp_path))

    def boom(*a, **k):
        raise OSError("replace refused (test)")
    monkeypatch.setattr(os, "replace", boom)
    assert verify_gate.main(_cli(rp, code, ledger)) == RECEIPT_INVALID
    assert ledger.read_bytes() == before
    assert set(os.listdir(tmp_path)) - listing <= {"ledger.json.lock"}, os.listdir(tmp_path)


def test_t5b_folder_fsync_failure_after_replace_is_distinct_and_spends(tmp_path, monkeypatch):
    code, verdict, ledger = _setup(tmp_path)
    _rp, rec = _receipt(tmp_path, code, verdict)
    real_fsync = os.fsync

    def fsync(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError("folder fsync refused (test)")
        return real_fsync(fd)
    monkeypatch.setattr(os, "fsync", fsync)
    monkeypatch.setattr(sys, "platform", "linux")  # 0.3.1: on Darwin F_FULLFSYNC (T15) syncs the folder first
    ok, why = R.validate_and_consume(rec, changed_paths=[str(code)], ledger_path=str(ledger))
    assert not ok and "spent" in why and "unchanged" not in why, why
    assert json.loads(ledger.read_text()) == [rec["nonce"]]


# ---- T6: no code path opens the final ledger for writing; the F5-11 open flags ----------------------------------
_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND


def test_t6_final_ledger_never_opened_for_writing(tmp_path, monkeypatch):
    code, verdict, ledger = _setup(tmp_path)
    ledger.write_text(json.dumps(["0" * 32]))
    _rp, rec = _receipt(tmp_path, code, verdict)
    calls = []
    real_open, real_os_open = builtins.open, os.open

    def rec_open(file, mode="r", *a, **k):
        calls.append(("open", os.fspath(file) if not isinstance(file, int) else file, mode))
        return real_open(file, mode, *a, **k)

    def rec_os_open(path, flags, *a, **k):
        calls.append(("os.open", os.fspath(path), flags))
        return real_os_open(path, flags, *a, **k)
    monkeypatch.setattr(builtins, "open", rec_open)
    monkeypatch.setattr(os, "open", rec_os_open)
    ok, why = R.validate_and_consume(rec, changed_paths=[str(code)], ledger_path=str(ledger))
    R.consume("f" * 32, str(ledger))
    monkeypatch.undo()
    assert ok, why
    assert sorted(json.loads(ledger.read_text())) == sorted(["0" * 32, rec["nonce"], "f" * 32])

    final, lock = os.path.realpath(ledger), os.path.realpath(str(ledger) + ".lock")
    for kind, path, how in calls:
        if isinstance(path, str) and os.path.realpath(path) == final:
            assert (kind == "open" and not set(how) & set("wax+")) or \
                   (kind == "os.open" and not how & _WRITE_FLAGS), (kind, path, how)
    tmp_flags = os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_NONBLOCK
    lock_flags = os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK
    temps = [f for k, p, f in calls if k == "os.open" and isinstance(p, str)
             and os.path.dirname(os.path.realpath(p)) == os.path.dirname(final)
             and os.path.realpath(p) not in (final, lock) and f & os.O_CREAT]
    locks = [f for k, p, f in calls if k == "os.open" and isinstance(p, str) and os.path.realpath(p) == lock]
    assert len(temps) == 2 and all(f & tmp_flags == tmp_flags for f in temps), temps
    assert len(locks) == 2 and all(f & lock_flags == lock_flags and not f & (os.O_EXCL | os.O_TRUNC) for f in locks), locks


def test_t6_linked_sidecar_is_refused(tmp_path):
    code, verdict, ledger = _setup(tmp_path)
    _rp, rec = _receipt(tmp_path, code, verdict)
    (tmp_path / "elsewhere").write_text("")
    os.symlink(tmp_path / "elsewhere", str(ledger) + ".lock")
    ok, why = R.validate_and_consume(rec, changed_paths=[str(code)], ledger_path=str(ledger))
    assert not ok, why
    assert not ledger.exists() and (tmp_path / "elsewhere").read_text() == ""


# ---- T7: the nonce's shape and the future bound -----------------------------------------------------------------
@pytest.mark.parametrize("override", [{"nonce": _DROP}, {"nonce": "n1"}, {"nonce": "A" * 32},
                                      {"ts": time.time() + 3600}],
                         ids=["no-nonce", "short-nonce", "uppercase-nonce", "future-ts"])
def test_t7_bad_nonce_or_future_ts_is_invalid_without_traceback(tmp_path, override):
    code, verdict, ledger = _setup(tmp_path)
    rp, rec = _receipt(tmp_path, code, verdict, **override)
    ok, why = R.validate_receipt(rec, changed_paths=[str(code)], ledger_path=str(ledger))
    assert not ok, why
    r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "verify_gate.py"), *_cli(rp, code, ledger)],
                       capture_output=True, text=True)
    assert r.returncode == RECEIPT_INVALID and "Traceback" not in r.stderr, (r.returncode, r.stderr)
    assert not ledger.exists()


def test_t7_stale_with_a_malformed_nonce_still_reads_stale(tmp_path):
    code, verdict, ledger = _setup(tmp_path)
    _rp, rec = _receipt(tmp_path, code, verdict, ts=time.time() - 99999, nonce="n1")
    ok, why = R.validate_receipt(rec, changed_paths=[str(code)], ledger_path=str(ledger))
    assert not ok and "stale" in why, why


# ---- T8: without fcntl the gate refuses -------------------------------------------------------------------------
def test_t8_no_fcntl_refuses_and_leaves_the_ledger(tmp_path, monkeypatch):
    code, verdict, ledger = _setup(tmp_path)
    ledger.write_text(json.dumps(["0" * 32]))
    before = ledger.read_bytes()
    rp, rec = _receipt(tmp_path, code, verdict)
    monkeypatch.setitem(sys.modules, "fcntl", None)
    ok, why = R.validate_and_consume(rec, changed_paths=[str(code)], ledger_path=str(ledger))
    assert not ok and "fcntl" in why, why
    assert verify_gate.main(_cli(rp, code, ledger)) == RECEIPT_INVALID
    assert ledger.read_bytes() == before


# =====================================================================================================================
# 0.3.1 (FS-1b, PLAN_verify_gate_031_s218_fixes): the s218 findings VG-1 .. VG-9. Anything that could hang on 0.3.0 runs
# in a subprocess under a timeout, so the suite itself never hangs.
# =====================================================================================================================
VG = os.path.join(SCRIPTS, "verify_gate.py")


def _run(cmd, timeout, **kw):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, **kw)
    except subprocess.TimeoutExpired:
        pytest.fail(f"HUNG: no return within {timeout}s: {cmd[-4:]}")


# ---- T7c: a non-finite ts (VG-6, B1) ----------------------------------------------------------------------------
@pytest.mark.parametrize("ts", [float("nan"), float("inf"), float("-inf")], ids=["nan", "inf", "-inf"])
def test_t7c_non_finite_ts_is_invalid_and_exits_6(tmp_path, ts):
    code, verdict, ledger = _setup(tmp_path)
    rp, rec = _receipt(tmp_path, code, verdict, ts=ts)
    assert R.validate_receipt(rec, changed_paths=[str(code)], ledger_path=str(ledger)) == (False, "bad receipt ts")
    r = _run([sys.executable, VG, *_cli(rp, code, ledger)], 20)
    assert r.returncode == RECEIPT_INVALID and "Traceback" not in r.stderr, (r.returncode, r.stderr)
    assert not ledger.exists()


# ---- T9: a hard-linked ledger is refused (VG-2, B2) -------------------------------------------------------------
def test_t9_hard_linked_ledger_is_invalid_under_both_names(tmp_path):
    code, verdict, ledger = _setup(tmp_path)
    ledger.write_text(json.dumps(["0" * 32]))
    other = tmp_path / "ledger_alias.json"
    os.link(ledger, other)
    before = ledger.read_bytes()
    rp, rec = _receipt(tmp_path, code, verdict)
    for name in (ledger, other):
        ok, why = R.validate_receipt(rec, changed_paths=[str(code)], ledger_path=str(name))
        assert (ok, why) == (False, f"nonce ledger unreadable: {name}"), why
        assert R.validate_and_consume(rec, changed_paths=[str(code)], ledger_path=str(name))[0] is False
        with pytest.raises(R.LedgerError):
            R.consume("f" * 32, str(name))
    rcs = _spawn(tmp_path, [_cli(rp, code, ledger), _cli(rp, code, other)] * 2, forced=False)
    assert rcs == [RECEIPT_INVALID] * 4, rcs
    assert ledger.read_bytes() == before and other.read_bytes() == before


# ---- T10: a ledger or path that raised something other than LedgerError (VG-4, B3) ------------------------------
def test_t10_deeply_nested_ledger_is_invalid_without_traceback(tmp_path):
    code, verdict, ledger = _setup(tmp_path)
    ledger.write_text("[" * 100000)
    before = ledger.read_bytes()
    rp, rec = _receipt(tmp_path, code, verdict)
    assert R.validate_receipt(rec, changed_paths=[str(code)], ledger_path=str(ledger)) == \
        (False, f"nonce ledger unreadable: {ledger}")
    r = _run([sys.executable, VG, *_cli(rp, code, ledger)], 30)
    assert r.returncode == RECEIPT_INVALID and "Traceback" not in r.stderr, (r.returncode, r.stderr[-300:])
    assert ledger.read_bytes() == before


def test_t10_ledger_under_a_regular_file_parent_exits_6_without_traceback(tmp_path):
    code, verdict, _ledger = _setup(tmp_path)
    (tmp_path / "afile").write_text("x")
    ledger = tmp_path / "afile" / "sub" / "ledger.json"
    rp, rec = _receipt(tmp_path, code, verdict)
    ok, why = R.validate_and_consume(rec, changed_paths=[str(code)], ledger_path=str(ledger))
    assert not ok, why
    r = _run([sys.executable, VG, *_cli(rp, code, ledger)], 30)
    assert r.returncode == RECEIPT_INVALID and "Traceback" not in r.stderr, (r.returncode, r.stderr[-300:])


# ---- T11 / T12: nothing blocks forever (VG-5, B4) ---------------------------------------------------------------
def test_t11_fifo_in_changed_does_not_hang(tmp_path):
    import hashlib
    code, verdict, ledger = _setup(tmp_path)
    fifo = tmp_path / "pipe"
    os.mkfifo(fifo)
    rp, _rec = _receipt(tmp_path, code, verdict)
    r = _run([sys.executable, VG, "--receipt", str(rp), "--changed", str(fifo), "--ledger", str(ledger)], 10)
    assert r.returncode == RECEIPT_INVALID and "Traceback" not in r.stderr, (r.returncode, r.stderr[-300:])
    # the digest of a non-regular file is the marker, computed without reading it
    h = hashlib.sha256()
    h.update(str(fifo).encode() + b"\0" + b"<non-regular>" + b"\0")
    r2 = _run([sys.executable, "-c", "import sys; sys.path.insert(0, sys.argv[1]); import receipt; "
               "print(receipt.changeset_sha256([sys.argv[2]]))", SCRIPTS, str(fifo)], 10)
    assert r2.stdout.strip() == h.hexdigest(), (r2.stdout, r2.stderr[-300:])


_HOLDER = r'''
import fcntl, os, sys
fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT, 0o666)
fcntl.flock(fd, fcntl.LOCK_EX)
print("ready", flush=True)
sys.stdin.read()
'''
_SHORT_TIMEOUT = r'''
import sys
sys.path.insert(0, sys.argv[1])
import receipt
receipt.LOCK_TIMEOUT_S = 1
import verify_gate
sys.exit(verify_gate.main(sys.argv[2:]))
'''


def test_t12_lock_held_by_another_process_times_out_with_exit_6(tmp_path):
    code, verdict, ledger = _setup(tmp_path)
    rp, _rec = _receipt(tmp_path, code, verdict)
    holder = subprocess.Popen([sys.executable, "-c", _HOLDER, str(ledger) + ".lock"], stdin=subprocess.PIPE,
                              stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "ready"
        t0 = time.monotonic()
        r = _run([sys.executable, "-c", _SHORT_TIMEOUT, SCRIPTS, *_cli(rp, code, ledger)], 5)
        assert r.returncode == RECEIPT_INVALID and "Traceback" not in r.stderr, (r.returncode, r.stderr[-300:])
        assert "lock timeout" in r.stdout, r.stdout
        assert time.monotonic() - t0 < 5
        assert not ledger.exists()
    finally:
        holder.kill()
        holder.wait()


# ---- T13: the lock file replaced while held (VG-1, B5) ----------------------------------------------------------
def test_t13_lock_file_replaced_while_held_refuses_the_write(tmp_path, monkeypatch):
    code, verdict, ledger = _setup(tmp_path)
    ledger.write_text(json.dumps(["0" * 32]))
    before = ledger.read_bytes()
    _rp, rec = _receipt(tmp_path, code, verdict)
    lock = str(ledger) + ".lock"
    real_write = R._write_ledger

    def swap_lock(*a, **k):
        os.unlink(lock)
        open(lock, "w").close()
        return real_write(*a, **k)
    monkeypatch.setattr(R, "_write_ledger", swap_lock)
    ok, why = R.validate_and_consume(rec, changed_paths=[str(code)], ledger_path=str(ledger))
    assert not ok and "lock" in why, why
    assert ledger.read_bytes() == before
    with pytest.raises(R.LedgerError):
        R.consume("f" * 32, str(ledger))
    assert ledger.read_bytes() == before
    assert [f for f in os.listdir(tmp_path) if f.endswith(".tmp")] == []
    # control: with no swap the same call spends the nonce
    monkeypatch.setattr(R, "_write_ledger", real_write)
    assert R.validate_and_consume(rec, changed_paths=[str(code)], ledger_path=str(ledger))[0] is True


# ---- T14: one folder for makedirs, temp and fsync; a symlinked ledger folder is refused (VG-3, B6) ---------------
def test_t14_symlinked_ledger_folder_is_refused_and_nothing_is_written(tmp_path):
    code, verdict, _l = _setup(tmp_path)
    target = tmp_path / "target"
    target.mkdir()
    os.symlink(target, tmp_path / ".verify")
    ledger = tmp_path / ".verify" / "ledger.json"
    _rp, rec = _receipt(tmp_path, code, verdict)
    ok, why = R.validate_and_consume(rec, changed_paths=[str(code)], ledger_path=str(ledger))
    assert not ok, why
    with pytest.raises(R.LedgerError):
        R.consume("f" * 32, str(ledger))
    assert os.listdir(target) == []


def test_t14_dotdot_through_a_symlink_puts_the_temp_in_the_ledgers_own_folder(tmp_path, monkeypatch):
    code, verdict, _l = _setup(tmp_path)
    home = tmp_path / "elsewhere"
    (home / "sub").mkdir(parents=True)
    os.symlink(home / "sub", tmp_path / "jump")
    ledger = f"{tmp_path}/jump/../ledger.json"          # resolves to <elsewhere>/ledger.json
    _rp, rec = _receipt(tmp_path, code, verdict)
    opened, real_os_open = [], os.open

    def rec_open(path, flags, *a, **k):
        opened.append((os.fspath(path), flags))
        return real_os_open(path, flags, *a, **k)
    monkeypatch.setattr(os, "open", rec_open)
    ok, why = R.validate_and_consume(rec, changed_paths=[str(code)], ledger_path=ledger)
    monkeypatch.undo()
    assert ok, why
    temps = [p for p, f in opened if f & os.O_EXCL]
    assert len(temps) == 1 and os.path.dirname(os.path.realpath(temps[0])) == os.path.realpath(home), temps
    assert json.loads((home / "ledger.json").read_text()) == [rec["nonce"]]


# ---- T15: F_FULLFSYNC on Darwin (VG-8, B7) ----------------------------------------------------------------------
def _fullfsync_recorder(monkeypatch, *, fail):
    import fcntl
    monkeypatch.setattr(fcntl, "F_FULLFSYNC", 51, raising=False)
    calls, fsyncs, real_fsync = [], [], os.fsync

    def fake_fcntl(fd, cmd, *a):
        if cmd == 51:
            calls.append("dir" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file")
            if fail:
                raise OSError("F_FULLFSYNC refused (test)")
        return 0

    def fsync(fd):
        fsyncs.append("dir" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file")
        return real_fsync(fd)
    monkeypatch.setattr(fcntl, "fcntl", fake_fcntl)
    monkeypatch.setattr(os, "fsync", fsync)
    return calls, fsyncs


def test_t15_darwin_full_fsyncs_the_temp_file_and_the_folder(tmp_path, monkeypatch):
    code, verdict, ledger = _setup(tmp_path)
    _rp, rec = _receipt(tmp_path, code, verdict)
    calls, _fsyncs = _fullfsync_recorder(monkeypatch, fail=False)
    monkeypatch.setattr(sys, "platform", "darwin")
    ok, why = R.validate_and_consume(rec, changed_paths=[str(code)], ledger_path=str(ledger))
    assert ok, why
    assert sorted(calls) == ["dir", "file"], calls


def test_t15_darwin_falls_back_to_fsync_when_full_fsync_fails(tmp_path, monkeypatch):
    code, verdict, ledger = _setup(tmp_path)
    _rp, rec = _receipt(tmp_path, code, verdict)
    calls, fsyncs = _fullfsync_recorder(monkeypatch, fail=True)
    monkeypatch.setattr(sys, "platform", "darwin")
    ok, why = R.validate_and_consume(rec, changed_paths=[str(code)], ledger_path=str(ledger))
    assert ok, why
    assert sorted(calls) == ["dir", "file"] and sorted(fsyncs) == ["dir", "file"], (calls, fsyncs)


def test_t15_other_platforms_use_plain_fsync(tmp_path, monkeypatch):
    code, verdict, ledger = _setup(tmp_path)
    _rp, rec = _receipt(tmp_path, code, verdict)
    calls, fsyncs = _fullfsync_recorder(monkeypatch, fail=False)
    monkeypatch.setattr(sys, "platform", "linux")
    ok, why = R.validate_and_consume(rec, changed_paths=[str(code)], ledger_path=str(ledger))
    assert ok, why
    assert calls == [] and sorted(fsyncs) == ["dir", "file"], (calls, fsyncs)


# ---- T2b: eight concurrent consume() calls (B8) -----------------------------------------------------------------
def test_t2b_eight_concurrent_consumes_all_recorded(tmp_path):
    _code, _verdict, ledger = _setup(tmp_path)
    nonces = [f"{i:032x}" for i in range(1, N + 1)]
    rcs = _spawn(tmp_path, [["CONSUME", n, str(ledger)] for n in nonces])
    assert rcs == [0] * N, rcs
    assert sorted(json.loads(ledger.read_text())) == sorted(nonces)


# ---- T3b: symlink, FIFO and directory ledgers, each refused promptly (B8) ---------------------------------------
_ONE_CALL = r'''
import json, sys
sys.path.insert(0, sys.argv[1])
import receipt
rec = json.load(open(sys.argv[2]))
print(json.dumps(receipt.validate_and_consume(rec, changed_paths=[sys.argv[3]], ledger_path=sys.argv[4])))
'''


@pytest.mark.parametrize("kind", ["symlink", "fifo", "directory"])
def test_t3b_non_regular_ledgers_are_refused_within_5_seconds(tmp_path, kind):
    code, verdict, ledger = _setup(tmp_path)
    rp, _rec = _receipt(tmp_path, code, verdict)
    if kind == "symlink":
        real = tmp_path / "real.json"
        real.write_text("[]")
        os.symlink(real, ledger)
    elif kind == "fifo":
        os.mkfifo(ledger)
    else:
        ledger.mkdir()
    r = _run([sys.executable, "-c", _ONE_CALL, SCRIPTS, str(rp), str(code), str(ledger)], 5)
    ok, why = json.loads(r.stdout)
    assert ok is False and "unreadable" in why, (r.stdout, r.stderr[-300:])
    if kind == "symlink":
        assert (tmp_path / "real.json").read_text() == "[]"

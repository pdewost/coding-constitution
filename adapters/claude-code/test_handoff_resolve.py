#!/usr/bin/env python3.12
"""test_handoff_resolve.py — the fleet handoff convention and its `cold start` resolver.

PLAN_cold_start_handoff_harmonization_2026-10-26.md v0.2 (§1 invariants I1-I4, §2 closing convention, §3 opening, §10 gate fold;
record AUDIT_adversarial_handoff_harmonization_plan_r1_2026-10-26.md, Rn = its findings).

Fixture workspaces are built under the system temp dir (tempfile.gettempdir(), never a real project path or the home
folder). HANDOFF_RESOLVE_SRC overrides the module under test; ANCHOR_RENDER_SRC the reader it reuses.
Run: python3.12 -m pytest test_handoff_resolve.py -q
"""
import datetime as dt
import importlib.util
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

import pytest

HERE = pathlib.Path(__file__).resolve().parent
SRC = pathlib.Path(os.environ.get("HANDOFF_RESOLVE_SRC") or HERE / "handoff_resolve.py")
UTC = dt.timezone.utc
NOW = dt.datetime(2026, 10, 27, 1, 0, tzinfo=UTC)          # fixed clock for the unit cases
CONVENTION = re.compile(r"^HANDOFF_[a-z0-9]+(?:-[a-z0-9]+)*_\d{4}-\d{2}-\d{2}_\d{4}Z\.md$")


def _load():
    spec = importlib.util.spec_from_file_location("handoff_resolve_under_test", str(SRC))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


hr = _load()


@pytest.fixture
def ws():
    base = pathlib.Path(os.environ.get("HANDOFF_TEST_BASE")
                        or pathlib.Path(tempfile.gettempdir()) / "hooks_batch2_tests" / "_tmp_handoff")
    base.mkdir(parents=True, exist_ok=True)
    tmp = pathlib.Path(tempfile.mkdtemp(dir=base)).resolve()
    w = tmp / "ws"
    (w / "governance" / "NEOCORTEX").mkdir(parents=True)
    yield w
    shutil.rmtree(tmp, ignore_errors=True)


def handoff(nc, role, stamp, status="Status: ACTIVE", supersedes="none", cold="- First: run the preflight.\n- COLD-SENTINEL-%(role)s\n",
            title=None, sections=True, sub=None, mtime=None, extra=""):
    """Write a convention handoff into NEOCORTEX dir `nc` (or nc/_archive/<sub>) and return its path."""
    d = pathlib.Path(nc) / "_archive" / sub if sub else pathlib.Path(nc)
    d.mkdir(parents=True, exist_ok=True)
    p = d / ("HANDOFF_%s_%sZ.md" % (role, stamp))
    body = [status, title or '# HANDOFF — %s — "fixture session" [abc123] — 2026-10-26 20:00 EDT' % role,
            "Supersedes: %s" % supersedes, ""]
    if cold is not None:
        body += ["## Cold start", cold % {"role": role}, ""]
    if sections:
        body += ["## State at close", "- fact `cmd`", "", "## Open items", "1. owner -> step", ""]
    p.write_text("\n".join(body) + extra, encoding="utf-8")
    if mtime is not None:
        os.utime(p, (mtime, mtime))
    return p


def ans(ws, cwd, trigger=("bare", None), now=NOW):
    return hr.answer(str(cwd), str(ws), trigger, now=now)


# --- trigger -------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("prompt,want", [
    ("cold start", ("bare", None)),
    ("  COLD   Start \n", ("bare", None)),
    ("Cold-Start.", ("bare", None)),
    ("coldstart!", ("bare", None)),
    ("cold start governance", ("role", "governance")),
    ("Cold start finance-console", ("role", "finance-console")),
    ("cold start drill", ("role", "drill")),            # one token always triggers (R9): NOT-FOUND if no such role
    ("the cold start costs", None),
    ("cold start the server now", None),
    ("cold start Gouvernance!", ("role", "gouvernance")),
    ("cold start a_b", None),                            # not a slug
    ("", None),
    ("cold", None),
])
def test_parse_trigger(prompt, want):
    assert hr.parse_trigger(prompt) == want


# --- paths ---------------------------------------------------------------------------------------------------------------

def test_canonical_maps_worktrees_to_main_checkout():
    assert hr.canonical("/w/.claude/worktrees/x") == "/w"
    assert hr.canonical("/w/.claude/worktrees/x/governance") == "/w/governance"
    assert hr.canonical("/w/P/.claude/worktrees/y/src") == "/w/P/src"
    assert hr.canonical("/w/.claude/worktrees/x/P/.claude/worktrees/y") == "/w/P"
    assert hr.canonical("/w/P/src") == "/w/P/src"


def test_project_chain(ws):
    gov = ws / "governance"
    (ws / "ProjA" / "NEOCORTEX").mkdir(parents=True)
    (ws / "ProjA" / "src" / "deep").mkdir(parents=True)
    (ws / "ProjP" / "sub" / "NEOCORTEX").mkdir(parents=True)
    (ws / "ProjP" / "NEOCORTEX").mkdir(parents=True)
    (ws / "ProjP" / "sub" / "x").mkdir()
    (ws / "NoProj").mkdir()
    c = lambda p: hr.project_chain(str(p), str(ws))
    assert c(ws) == [str(gov)]                                             # the root maps to governance
    assert c(ws / ".claude" / "worktrees" / "x") == [str(gov)]             # a root worktree too
    assert c(gov) == [str(gov)]
    assert c(ws / "ProjA" / "src" / "deep") == [str(ws / "ProjA")]         # S6 walk
    assert c(ws / "ProjA" / ".claude" / "worktrees" / "y" / "src") == [str(ws / "ProjA")]
    assert c(ws / "ProjP" / "sub" / "x") == [str(ws / "ProjP" / "sub"), str(ws / "ProjP")]   # R8 nested, nearest first
    assert c(ws / "ProjA") == [str(ws / "ProjA")]                          # governance is NOT added to a project's chain
    assert c(ws / "NoProj") == []


# --- RESOLVED / supersession by name -------------------------------------------------------------------------------------

def test_resolved_single_role(ws):
    nc = ws / "governance" / "NEOCORTEX"
    p = handoff(nc, "governance", "2026-10-26_2330")
    kind, text = ans(ws, ws)
    assert kind == "RESOLVED"
    assert text.startswith("[Cold start] RESOLVED")
    assert str(p) in text
    assert "COLD-SENTINEL-governance" in text
    first_do = text.index("preflight")
    assert first_do < text.index("Read that file IN FULL")                 # R4: preflight before the handoff
    assert "founder's own go" in text                                       # R6
    assert len(text.encode()) <= 6144


def test_newest_by_name_wins_over_mtime_and_old_status(ws):
    nc = ws / "governance" / "NEOCORTEX"
    later = NOW.timestamp() - 600
    old = handoff(nc, "governance", "2026-10-25_1200", mtime=later)        # older by name, touched later, still says ACTIVE
    mid = handoff(nc, "governance", "2026-10-25_2300", mtime=later - 90000)
    new = handoff(nc, "governance", "2026-10-26_2330", supersedes=mid.name, mtime=later - 7200)
    kind, text = ans(ws, ws)
    assert kind == "RESOLVED" and str(new) in text and "Handoff: " + str(old) not in text
    assert "COLD-SENTINEL" in text
    assert "name order and file times disagree" in text and old.name in text   # R2 warning


def test_supersedes_mismatch_warns(ws):
    nc = ws / "governance" / "NEOCORTEX"
    a = handoff(nc, "governance", "2026-10-26_1000")
    b = handoff(nc, "governance", "2026-10-26_1001", supersedes="none")    # a concurrent closer never saw `a`
    kind, text = ans(ws, ws)
    assert kind == "RESOLVED" and str(b) in text
    assert "WARNING" in text and a.name in text                             # R1: the dropped file is named


# --- CHOOSE: the ambiguous root ------------------------------------------------------------------------------------------

def test_ambiguous_root_chooses(ws):
    nc = ws / "governance" / "NEOCORTEX"
    handoff(nc, "governance", "2026-10-26_2330")
    handoff(nc, "constitution-review", "2026-10-24_1133")
    kind, text = ans(ws, ws)
    assert kind == "CHOOSE"
    assert text.startswith("[Cold start] CHOOSE")
    assert text.index("governance —") < text.index("constitution-review —")   # newest first
    assert "COLD-SENTINEL" not in text                                      # no handoff content when ambiguous
    assert "Do not pick one yourself" in text and "cold start <role>" in text


def test_role_argument_resolves_in_ambiguous_folder(ws):
    nc = ws / "governance" / "NEOCORTEX"
    handoff(nc, "governance", "2026-10-26_2330")
    p = handoff(nc, "constitution-review", "2026-10-24_1133")
    kind, text = ans(ws, ws, ("role", "constitution-review"))
    assert kind == "RESOLVED" and str(p) in text and "COLD-SENTINEL-constitution-review" in text


# --- NONE / CLOSED / NOT-FOUND -------------------------------------------------------------------------------------------

def test_none_when_no_handoff(ws):
    (ws / "ProjA" / "NEOCORTEX").mkdir(parents=True)
    kind, text = ans(ws, ws / "ProjA")
    assert kind == "NONE" and text.startswith("[Cold start] NONE")
    assert "0 files" in text and "Handoff: " not in text


def test_none_lists_old_format_candidates_never_serves(ws):
    proj = ws / "ProjA"
    (proj / "NEOCORTEX").mkdir(parents=True)
    (proj / "docs").mkdir()
    t = NOW.timestamp()
    for i, (d, n) in enumerate([("docs", "HANDOFF_session_2026-10-26_pt2.md"), ("NEOCORTEX", "NOTE_handoff_2026-10-22.md"),
                                ("NEOCORTEX", "NOTE_session_handoff_2026-10-25.md"), ("docs", "HANDOFF_session_2026-10-01.md")]):
        f = proj / d / n
        f.write_text("Status: ACTIVE\n## Cold start\nLEGACY-SENTINEL\n")
        os.utime(f, (t - 100 * (i + 1), t - 100 * (i + 1)))
    for n in ("DECISION_cross_llm_handoff_2026-08-31.md", "PLAN_x_handoff_2026-10-26.md"):
        (proj / "NEOCORTEX" / n).write_text("x\n")
        os.utime(proj / "NEOCORTEX" / n, (t, t))                            # newest of all: a broad match would list them
    kind, text = ans(ws, proj)
    assert kind == "NONE"
    assert "HANDOFF_session_2026-10-26_pt2.md" in text and "NOTE_handoff_2026-10-22.md" in text
    assert "NOTE_session_handoff_2026-10-25.md" in text
    assert "HANDOFF_session_2026-10-01.md" not in text                     # newest 3 only
    assert "DECISION_cross_llm" not in text and "PLAN_x_handoff" not in text   # R10: not handoffs
    assert "LEGACY-SENTINEL" not in text                                    # listed, never served
    assert text.index("2026-10-26_pt2") < text.index("NOTE_handoff_2026-10-22")


def test_none_outside_any_project(ws):
    (ws / "NoProj").mkdir()
    kind, text = ans(ws, ws / "NoProj")
    assert kind == "NONE" and "not a NEOCORTEX project" in text


@pytest.mark.parametrize("status", ["Status: DONE — role closed", "Status: CLOSED 2026-10-26 07:33 EDT by [f9bf79]",
                                    "Status: SUPERSEDED-BY HANDOFF_x.md", "Status: ARCHIVED"])
def test_closed_role(ws, status):
    nc = ws / "governance" / "NEOCORTEX"
    handoff(nc, "constitution-review", "2026-10-24_1100")
    done = handoff(nc, "constitution-review", "2026-10-26_1133", status=status)
    kind, text = ans(ws, ws)
    assert kind == "NONE" and "COLD-SENTINEL" not in text
    assert "Closed in the last 14 days" in text and "constitution-review" in text
    kind, text = ans(ws, ws, ("role", "constitution-review"))
    assert kind == "CLOSED" and done.name in text and "COLD-SENTINEL" not in text


def test_archived_tombstone_keeps_role_closed(ws):
    nc = ws / "governance" / "NEOCORTEX"
    handoff(nc, "constitution-review", "2026-10-20_0900")                  # ACTIVE predecessor left at top level (I3)
    handoff(nc, "constitution-review", "2026-10-26_1133", status="Status: DONE", sub="2026-10-27")
    assert ans(ws, ws)[0] == "NONE"                                          # R3: the archived DONE still closes the role


def test_archived_newest_counts_as_closed(ws):
    nc = ws / "governance" / "NEOCORTEX"
    handoff(nc, "governance", "2026-10-26_1133", sub="2026-10-27")
    assert ans(ws, ws)[0] == "NONE"


def test_unknown_status_line_served_with_warning(ws):
    nc = ws / "governance" / "NEOCORTEX"
    handoff(nc, "governance", "2026-10-26_1133", status="# HANDOFF without a status line")
    kind, text = ans(ws, ws)
    assert kind == "RESOLVED" and "not a recognised Status line" in text


def test_not_found_role(ws):
    nc = ws / "governance" / "NEOCORTEX"
    handoff(nc, "governance", "2026-10-26_1133")
    kind, text = ans(ws, ws, ("role", "drill"))
    assert kind == "NOT-FOUND" and "drill" in text and "governance" in text and "COLD-SENTINEL" not in text


# --- nested projects (R8) ------------------------------------------------------------------------------------------------

def test_nested_union(ws):
    top = ws / "ProjP"
    sub = top / "sub"
    (sub / "NEOCORTEX").mkdir(parents=True)
    p = handoff(top / "NEOCORTEX", "project-b", "2026-10-24_1340")
    kind, text = ans(ws, sub)
    assert kind == "RESOLVED" and str(p) in text                            # the parent's role is not masked
    handoff(sub / "NEOCORTEX", "style", "2026-10-25_1000")
    kind, text = ans(ws, sub)
    assert kind == "CHOOSE" and "project-b" in text and "style" in text and "ProjP/sub" in text


# --- stamps (R2) and names -----------------------------------------------------------------------------------------------

def test_future_and_invalid_stamps_refused(ws):
    nc = ws / "governance" / "NEOCORTEX"
    good = handoff(nc, "governance", "2026-10-26_1133")
    handoff(nc, "governance", "2099-01-01_0000", cold="- FUTURE-SENTINEL\n")
    (nc / "HANDOFF_governance_2026-13-01_0000Z.md").write_text("Status: ACTIVE\n## Cold start\nBADMONTH-SENTINEL\n")
    (nc / "HANDOFF_governance_2026-10-26_2599Z.md").write_text("Status: ACTIVE\n## Cold start\nBADTIME-SENTINEL\n")
    kind, text = ans(ws, ws)
    assert kind == "RESOLVED" and str(good) in text
    for s in ("FUTURE-SENTINEL", "BADMONTH-SENTINEL", "BADTIME-SENTINEL"):
        assert s not in text
    assert "refused" in text and "2099-01-01_0000Z" in text


@pytest.mark.parametrize("name", [
    "HANDOFF_governance_session_2026-10-26.md", "HANDOFF_coding_constitution_review_r3_2026-10-24.md",
    "HANDOFF_session_2026-09-29_8becb0.md", "HANDOFF_session_close_2026-10-16.md", "HANDOFF_finance_console_f2f_2026-10-07.md",
    "HANDOFF_session_2026-10-26_pt2.md", "HANDOFF_session_2026-09-28.md", "NOTE_handoff_2026-10-22.md",
    "NOTE_handoff_session_2026-10-24_a47421.md", "NOTE_session_handoff_2026-10-25.md", "HANDOFF_governance_2026-10-26_1133.md",
])
def test_old_name_shapes_never_conform(name):
    assert hr.parse_name(name) is None


def test_parse_name_conforming():
    r = hr.parse_name("HANDOFF_finance-console_2026-10-26_2354Z.md")
    assert r[0] == "finance-console" and r[1] == dt.datetime(2026, 10, 26, 23, 54, tzinfo=UTC)


# --- safety (reader reuse) -----------------------------------------------------------------------------------------------

def test_symlink_hardlink_fifo_refused(ws):
    nc = ws / "governance" / "NEOCORTEX"
    secret = ws / "secret.txt"
    secret.write_text("SECRET-SENTINEL\n")
    os.symlink(secret, nc / "HANDOFF_governance_2026-10-26_1200Z.md")
    hl = ws / "hl.md"
    hl.write_text("Status: ACTIVE\n## Cold start\nHARDLINK-SENTINEL\n")
    os.link(hl, nc / "HANDOFF_governance_2026-10-26_1100Z.md")
    os.mkfifo(nc / "HANDOFF_governance_2026-10-26_1300Z.md")
    ok = handoff(nc, "governance", "2026-10-26_1000")
    kind, text = ans(ws, ws)
    assert "SECRET-SENTINEL" not in text and "HARDLINK-SENTINEL" not in text
    assert kind == "RESOLVED" and str(ok) in text
    assert text.count("refused") >= 1


def test_oversized_refused(ws):
    nc = ws / "governance" / "NEOCORTEX"
    big = nc / "HANDOFF_governance_2026-10-26_1200Z.md"
    with open(big, "wb") as fh:
        fh.write(b"Status: ACTIVE\n## Cold start\nBIG-SENTINEL\n")
        fh.write(b"x" * (4 * 1024 * 1024 + 1))
    ok = handoff(nc, "governance", "2026-10-26_1000")
    kind, text = ans(ws, ws)
    assert kind == "RESOLVED" and str(ok) in text and "BIG-SENTINEL" not in text


# --- bounds (I4, R11) and sanitising -------------------------------------------------------------------------------------

def test_cold_start_over_cap_is_cut_loudly(ws):
    nc = ws / "governance" / "NEOCORTEX"
    handoff(nc, "governance", "2026-10-26_1133", cold="HEAD-SENTINEL\n" + ("y" * 79 + "\n") * 70 + "TAIL-SENTINEL\n")
    kind, text = ans(ws, ws)
    assert kind == "RESOLVED" and "HEAD-SENTINEL" in text and "TAIL-SENTINEL" not in text
    assert "over the 4,000 B cap" in text
    assert len(text.encode()) <= 6144


def test_missing_cold_start_section_warns(ws):
    nc = ws / "governance" / "NEOCORTEX"
    p = handoff(nc, "governance", "2026-10-26_1133", cold=None)
    kind, text = ans(ws, ws)
    assert kind == "RESOLVED" and str(p) in text and "no \"## Cold start\" section" in text


def test_choose_lines_are_sanitised_and_bounded(ws):
    nc = ws / "governance" / "NEOCORTEX"
    handoff(nc, "governance", "2026-10-26_1133", title="# HANDOFF \x1b[31mRED\x07 " + "z" * 300)
    handoff(nc, "constitution-review", "2026-10-24_1133")
    kind, text = ans(ws, ws)
    assert kind == "CHOOSE" and "\x1b" not in text and "\x07" not in text
    assert all(len(line) <= 400 for line in text.splitlines())
    assert "z" * 200 not in text


# --- check (I1, R5) ------------------------------------------------------------------------------------------------------

def test_check_file_good_and_bad(ws):
    nc = ws / "governance" / "NEOCORTEX"
    a = handoff(nc, "governance", "2026-10-25_1133")
    b = handoff(nc, "governance", "2026-10-26_1133", supersedes=a.name)
    assert hr.check_file(str(b), now=NOW) == []
    assert any("newest" in m for m in hr.check_file(str(a), now=NOW))       # not the role's newest
    c = handoff(nc, "governance", "2026-10-26_1200", supersedes="none")
    assert any("Supersedes" in m for m in hr.check_file(str(c), now=NOW))   # R1 concurrent-close catch
    d = handoff(nc, "other", "2026-10-26_1133", sections=False)
    msgs = hr.check_file(str(d), now=NOW)
    assert any("State at close" in m for m in msgs) and any("Open items" in m for m in msgs)
    e = handoff(nc, "big", "2026-10-26_1133", cold=("y" * 99 + "\n") * 41)
    assert any("4,000" in m for m in hr.check_file(str(e), now=NOW))
    f = handoff(nc, "weird", "2026-10-26_1133", status="Status: HANDOFF — read this")
    assert any("Status" in m for m in hr.check_file(str(f), now=NOW))
    g = handoff(nc, "closing", "2026-10-26_1133", status="Status: DONE — role closed", cold=None, sections=False)
    assert hr.check_file(str(g), now=NOW) == []
    bad = nc / "HANDOFF_governance_session_2026-10-26.md"
    bad.write_text("x\n")
    assert any("name" in m for m in hr.check_file(str(bad), now=NOW))


def test_check_dir_flags_stale_open(ws):
    nc = ws / "governance" / "NEOCORTEX"
    handoff(nc, "old-role", "2026-09-01_1000")
    handoff(nc, "governance", "2026-10-26_1133")
    problems, warnings = hr.check_dir(str(ws / "governance"), now=NOW)
    assert problems == []
    assert any("old-role" in w and "stale" in w.lower() for w in warnings)


# --- name (closing helper, R7) -------------------------------------------------------------------------------------------

def test_make_name(ws):
    nc = ws / "governance" / "NEOCORTEX"
    prev = handoff(nc, "governance", "2026-10-26_1133")
    path, sup = hr.make_name("governance", str(ws), str(ws), now=NOW)
    assert path == str(nc / "HANDOFF_governance_2026-10-27_0100Z.md") and sup == prev.name
    with pytest.raises(hr.NameRefused) as e:                                # typo guard
        hr.make_name("governence", str(ws), str(ws), now=NOW)
    assert "governance" in str(e.value) and e.value.code == 1
    path, sup = hr.make_name("governence", str(ws), str(ws), new_role=True, now=NOW)
    assert sup == "none"
    with pytest.raises(hr.NameRefused) as e:
        hr.make_name("Bad_Slug", str(ws), str(ws), now=NOW)
    assert e.value.code == 64
    (ws / "Python").mkdir()
    with pytest.raises(hr.NameRefused) as e:
        hr.make_name("x", str(ws / "Python"), str(ws), new_role=True, now=NOW)
    assert e.value.code == 2 and "--project" in str(e.value)
    (ws / "ProjA" / "NEOCORTEX").mkdir(parents=True)
    path, _ = hr.make_name("proja", str(ws / "Python"), str(ws), project=str(ws / "ProjA"), new_role=True, now=NOW)
    assert path.startswith(str(ws / "ProjA" / "NEOCORTEX") + os.sep)
    wt = ws / ".claude" / "worktrees" / "w1"
    wt.mkdir(parents=True)
    path, _ = hr.make_name("governance", str(wt), str(wt), now=NOW)        # from a worktree: the MAIN checkout
    assert path.startswith(str(nc) + os.sep)


# --- CLI -----------------------------------------------------------------------------------------------------------------

def _cli(args, env=None, cwd=None):
    e = dict(os.environ)
    e.update(env or {})
    return subprocess.run([sys.executable, "-I", str(SRC)] + args, capture_output=True, text=True, env=e, cwd=cwd, timeout=20)


def test_cli_hook_resolve_check_name(ws):
    nc = ws / "governance" / "NEOCORTEX"
    p = handoff(nc, "governance", "2026-10-26_1133")
    r = _cli(["hook", "--root", str(ws), "--cwd", str(ws)], env={"_CS_PROMPT": "hello there"})
    assert r.returncode == 0 and r.stdout == ""
    r = _cli(["hook", "--root", str(ws), "--cwd", str(ws)], env={"_CS_PROMPT": "Cold start"})
    assert r.returncode == 0 and r.stdout.startswith("[Cold start] RESOLVED") and str(p) in r.stdout
    r = _cli(["resolve", "--root", str(ws), "--cwd", str(ws)])
    assert r.returncode == 0 and r.stdout.startswith("[Cold start] RESOLVED")
    r = _cli(["check", "--file", str(p)])
    assert r.returncode == 0, r.stdout + r.stderr
    r = _cli(["check", str(ws / "governance")])
    assert r.returncode == 0, r.stdout + r.stderr
    r = _cli(["name", "Bad_Slug", "--root", str(ws), "--cwd", str(ws)])
    assert r.returncode == 64
    r = _cli(["name", "governance", "--root", str(ws), "--cwd", str(ws)])
    assert r.returncode == 0
    lines = r.stdout.splitlines()
    assert CONVENTION.match(os.path.basename(lines[0])) and lines[1] == "Supersedes: " + p.name
    r = _cli(["bogus"])
    assert r.returncode == 64

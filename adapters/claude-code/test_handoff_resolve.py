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


def test_check_file_state_at_close_read_time(ws):
    """PROTOCOL step 2 (2026-11-18): each State-at-close bullet carries its read time, on handoffs stamped from STATE_TIME_FROM."""
    nc = ws / "governance" / "NEOCORTEX"
    later = dt.datetime(2026, 11, 19, 12, 0, tzinfo=UTC)
    old = handoff(nc, "legacy", "2026-11-18_0859")                          # before the cut: untimed bullet tolerated
    assert hr.check_file(str(old), now=later) == []
    bad = handoff(nc, "fresh", "2026-11-18_0900")                           # at the cut: the fixture's "- fact `cmd`" fails
    msgs = hr.check_file(str(bad), now=later)
    assert len(msgs) == 1 and "read time" in msgs[0] and "fact" in msgs[0]
    good = handoff(nc, "timed", "2026-11-18_0900",
                   extra="")
    good.write_text(good.read_text().replace("- fact `cmd`", "- 10:05 fact `cmd`\n  continuation line, no time\n* 23:59 other `cmd`"))
    assert hr.check_file(str(good), now=later) == []
    mixed = handoff(nc, "mixed", "2026-11-18_0900")
    mixed.write_text(mixed.read_text().replace("- fact `cmd`", "- 09:10 ok `cmd`\n- no time `cmd`\n- 24:00 not a clock"))
    msgs = hr.check_file(str(mixed), now=later)
    assert len(msgs) == 2 and all("read time" in m for m in msgs)
    done = handoff(nc, "ended", "2026-11-18_0900", status="Status: DONE — role closed")
    assert hr.check_file(str(done), now=later) == []                        # DONE handoffs: sections not required


# --- role-status (PLAN_governance_charter_and_genesis_2026-11-19 P2a + D-5) ------------------------------------------------
# One fixture per numbered rule; JOURNAL lines use the shapes found in the live governance JOURNAL (2026-11-19).

def journal(nc, lines, name="JOURNAL.md", sub=None):
    d = pathlib.Path(nc) / "_archive" if sub else pathlib.Path(nc)
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.write_text("# Journal\n\n" + "\n".join(lines) + "\n", encoding="utf-8")
    return p


def draft(nc, role, stamp):
    p = pathlib.Path(nc) / ("DRAFT_HANDOFF_%s_%sZ.md" % (role, stamp))
    p.write_text("Status: ACTIVE\n# draft\n", encoding="utf-8")
    return p


def rs(ws, role="governance", now=NOW):
    return hr.role_status(role, str(ws), str(ws), now=now)


def took(title, stem, shape=0):
    """Real line shapes: writer-field TAKEN OVER, dated COLD START, quoted TAKEN OVER by, lower-case cold start of role."""
    return [
        "- 2026-10-26 21:00 CET — %s [local_abc] (Opus 5.5 @ medium): TAKEN OVER from %s.md. Read at source 21:00." % (title, stem),
        "2026-10-26 21:00 CET — %s [local_abc]: COLD START (Opus@Medium) on %s.md (sha256 db8d4816 verified)." % (title, stem),
        '### 21:00 — TAKEN OVER by "%s" [local_abc] (cold start from %s.md, H check OK)' % (title, stem),
        "- 10-26 21:00 — %s (cold start of role governance, %s.md; predecessor archived)" % (title, stem),
    ][shape]


S1 = "HANDOFF_governance_2026-10-26_1200Z"
S2 = "HANDOFF_governance_2026-10-26_1800Z"


def test_rs_rule1_unknown_newest_unreadable_never_falls_back(ws):
    nc = ws / "governance" / "NEOCORTEX"
    handoff(nc, "governance", "2026-10-26_1200")
    secret = ws / "s.txt"
    secret.write_text("x")
    os.symlink(secret, nc / (S2 + ".md"))
    journal(nc, [took("Governance 26 oct", S1)])
    r = rs(ws)
    assert (r["state"], r["rule"]) == ("UNKNOWN", 1) and S2 in r["notes"][0]


def test_rs_rule1_unknown_journal_unreadable(ws):
    nc = ws / "governance" / "NEOCORTEX"
    handoff(nc, "governance", "2026-10-26_1200")
    other = ws / "j.md"
    other.write_text("- x — Governance 26 oct: TAKEN OVER from %s.md\n" % S1)
    os.symlink(other, nc / "JOURNAL.md")
    assert rs(ws)["state"] == "UNKNOWN"


def test_rs_rule2_conflict_draft_only(ws):
    draft(ws / "governance" / "NEOCORTEX", "governance", "2026-10-26_1200")
    r = rs(ws)
    assert (r["state"], r["rule"]) == ("CONFLICT", 2) and "no handoff" in r["notes"][0]


def test_rs_rule2_conflict_newer_draft_without_takeover(ws):
    nc = ws / "governance" / "NEOCORTEX"
    handoff(nc, "governance", "2026-10-26_1200")
    draft(nc, "governance", "2026-10-26_1500")
    assert rs(ws)["state"] == "CONFLICT"


def test_rs_rule2_conflict_two_titles_on_newest_only(ws):
    nc = ws / "governance" / "NEOCORTEX"
    handoff(nc, "governance", "2026-10-26_1200")
    journal(nc, [took("Governance 26 oct", S1, 0), took("Governance 26 oct - 2", S1, 2)])
    r = rs(ws)
    assert r["state"] == "CONFLICT" and "Governance 26 oct" in r["notes"][0] and "Governance 26 oct - 2" in r["notes"][0]
    handoff(nc, "governance", "2026-10-26_1800")                  # a newer handoff taken over once: the old duplicate no longer blocks
    journal(nc, [took("Governance 26 oct", S1, 0), took("Governance 26 oct - 2", S1, 2), took("Governance 26 oct - 3", S2, 1)])
    r = rs(ws)
    assert (r["state"], r["title"]) == ("HELD", "Governance 26 oct - 3")


def test_rs_rule3_vacant(ws):
    r = rs(ws)
    assert (r["state"], r["rule"]) == ("VACANT", 3)


def test_rs_rule4_first_holder(ws):
    journal(ws / "governance" / "NEOCORTEX", ["- 2026-10-26 22:00 CET — Governance 25 oct [local_x]: TAKEN OVER governance from none"])
    r = rs(ws)
    assert (r["state"], r["rule"], r["title"]) == ("HELD", 4, "Governance 25 oct")


@pytest.mark.parametrize("setup,why", [
    ("legacy", "legacy-named"),
    ("journal", "journal events"),
    ("stray", "not on disk"),
    ("equal", "same stamp"),
    ("future", "refused handoff name"),
    ("status", "not recognised"),
    ("superseded", "SUPERSEDED"),
])
def test_rs_rule5_unresolved(ws, setup, why):
    nc = ws / "governance" / "NEOCORTEX"
    if setup == "legacy":
        (nc / "HANDOFF_governance_session_2026-10-06.md").write_text("old\n")
    elif setup == "journal":
        journal(nc, [took("Governance 26 oct", S1)])
    elif setup == "stray":
        handoff(nc, "governance", "2026-10-26_1200")
        journal(nc, [took("Governance 26 oct", S1), took("Governance 26 oct - 2", "HANDOFF_governance_2026-10-25_0900Z")])
    elif setup == "equal":
        handoff(nc, "governance", "2026-10-26_1200")
        handoff(nc, "governance", "2026-10-26_1200", sub="old")
    elif setup == "future":
        handoff(nc, "governance", "2026-10-26_1200")
        handoff(nc, "governance", "2026-10-27_0300")              # NOW is 01:00Z: two hours ahead, over FUTURE (60 min)
    elif setup == "status":
        handoff(nc, "governance", "2026-10-26_1200", status="Statut: actif")
    elif setup == "superseded":
        handoff(nc, "governance", "2026-10-26_1200", status="Status: SUPERSEDED by X")
    r = rs(ws)
    assert (r["state"], r["rule"]) == ("UNRESOLVED", 5) and why in r["notes"][0]


@pytest.mark.parametrize("status", ["Status: DONE — role ended", "Status: CLOSED"])
def test_rs_rule6_closed(ws, status):
    handoff(ws / "governance" / "NEOCORTEX", "governance", "2026-10-26_1200", status=status)
    assert rs(ws)["state"] == "CLOSED"


@pytest.mark.parametrize("shape", [0, 1, 2, 3])
def test_rs_rule7_held_each_real_shape(ws, shape):
    nc = ws / "governance" / "NEOCORTEX"
    handoff(nc, "governance", "2026-10-26_1200")
    journal(nc, [took("Governance 26 oct - 2", S1, shape)])
    r = rs(ws)
    assert (r["state"], r["rule"], r["title"]) == ("HELD", 7, "Governance 26 oct - 2")


def test_rs_rule7_d5_newer_draft_with_takeover_is_held_with_note(ws):
    nc = ws / "governance" / "NEOCORTEX"
    handoff(nc, "governance", "2026-10-26_1200")
    draft(nc, "governance", "2026-10-26_2200")
    journal(nc, [took("Governance 26 oct", S1), "- 2026-10-26 23:00 CET — Governance 26 oct: SLOT 12 GO"])
    r = rs(ws)
    assert r["state"] == "HELD" and hr.NOTE_DRAFT in r["notes"]
    assert any(n.startswith("last seen") and "SLOT 12 GO" in n for n in r["notes"])


def test_rs_rule8_open_and_non_events(ws):
    nc = ws / "governance" / "NEOCORTEX"
    handoff(nc, "governance", "2026-10-26_1200")
    journal(nc, [
        "- 2026-10-26 21:00 CET — Gov X: TAKEN OVER sent to PROJECT-A 26 oct, Gov Y (queued). %s.md" % S1,          # relay
        "- 2026-10-26 21:01 CET — Gov X: TAKEN OVER received from 'PROJECT-A 26 oct' (%s.md)" % S1,                 # relay
        "- 2026-10-26 21:02 CET — Gov X: handoff written %s.md (Cold start 1.6 KB)" % S1,                         # not an event
        "- 2026-10-26 21:03 CET — Gov X: TAKEN OVER pre-written DRAFT_%s.md" % S1,                                # draft name only
        "> quoted in a note: 21:04 — Gov Q: TAKEN OVER from %s.md (not an entry)" % S1,
        "- 17:04 — (after close) FOUNDER (typed): 'Cold start now'. Started 'Gov Z' (cold start from %s.md)" % S1,
    ])
    r = rs(ws)
    assert (r["state"], r["rule"]) == ("OPEN", 8)


def test_rs_same_line_in_two_volumes_counts_once(ws):
    nc = ws / "governance" / "NEOCORTEX"
    handoff(nc, "governance", "2026-10-26_1200")
    line = took("Governance 26 oct", S1)
    journal(nc, [line], name="JOURNAL_2026-10-26_0800_to_2026-10-26_2000.md", sub=True)
    journal(nc, [line])
    r = rs(ws)
    assert r["state"] == "HELD" and any("1 takeover event(s)" in n for n in r["notes"])


@pytest.mark.parametrize("case", ["open", "held", "closed", "archived"])
def test_rs_agrees_with_resolve_on_newest(ws, case):
    nc = ws / "governance" / "NEOCORTEX"
    handoff(nc, "governance", "2026-10-26_1000")
    if case == "archived":
        handoff(nc, "governance", "2026-10-26_1200", sub="2026")
    else:
        handoff(nc, "governance", "2026-10-26_1200", status="Status: DONE" if case == "closed" else "Status: ACTIVE")
    if case == "held":
        journal(nc, [took("Governance 26 oct", S1)])
    r = rs(ws)
    assert r["state"] in ("OPEN", "HELD", "CLOSED")
    roles, _ = hr.scan(hr.project_chain(str(ws), str(ws)), NOW)
    served = [x for x in roles if x.role == "governance"][0].served
    assert r["newest"] == served.path


def test_rs_cli(ws):
    nc = ws / "governance" / "NEOCORTEX"
    handoff(nc, "governance", "2026-10-26_1200")
    journal(nc, [took("Governance 26 oct", S1)])
    r = _cli(["role-status", "governance", "--root", str(ws), "--cwd", str(ws)])
    assert r.returncode == 0 and r.stdout.startswith('ROLE-STATUS governance: HELD by "Governance 26 oct"')
    r = _cli(["role-status", "Bad_Role", "--root", str(ws), "--cwd", str(ws)])
    assert r.returncode == 64
    r = _cli(["role-status"])
    assert r.returncode == 64


# --- VERIFY-GATE 2026-11-19 findings F1-F6, F10 ---------------------------------------------------------------------------

@pytest.mark.parametrize("line", [
    "- 2026-10-26 21:00 CET — Governance 26 oct: wrote the handoff; successor to COLD START from %s.md" % S1,       # F1
    "- 2026-10-26 21:00 CET — Governance 26 oct - 5: checked that nobody TAKEN OVER %s.md yet" % S1,
    "- 2026-10-26 21:00 CET — Governance 26 oct - 5: not TAKEN OVER %s.md" % S1,
    "- 2026-10-26 21:00 CET — Governance 26 oct - 5: closing; the successor will say TAKEN OVER of %s.md" % S1,
    "- 2026-10-26 21:00 CET — Governance 26 oct: SUCCESSOR launched; next session: open governance, TAKEN OVER expected of %s.md" % S1,
    "- 2026-10-26 21:00 CET — slot 12 — Governance 26 oct - 3: SLOT GO; Constitution cold start of %s.md" % S1,       # F2
    '- 2026-10-26 21:00 CET — Governance 26 oct - 5: wrote note; "TAKEN OVER" received from "Constitution 17 nov" for %s.md' % S1,
    "- 2026-10-26 21:00 CET — Constitution 17 nov: relayed: Governance 26 oct - 3 TAKEN OVER %s.md" % S1,
    "- 2026-10-26 21:00 CET — Governance 26 oct: TAKEN OVER from " + "x" * 130 + " %s.md" % S1,                       # too far
])
def test_rs_false_takeover_lines_stay_open(ws, line):
    nc = ws / "governance" / "NEOCORTEX"
    handoff(nc, "governance", "2026-10-26_1200")
    journal(nc, [line])
    r = rs(ws)
    assert (r["state"], r["rule"]) == ("OPEN", 8), r


def test_rs_predecessor_close_line_plus_real_takeover_is_held_not_conflict(ws):
    nc = ws / "governance" / "NEOCORTEX"
    handoff(nc, "governance", "2026-10-26_1200")
    journal(nc, ["- 2026-10-26 20:00 CET — Governance 26 oct: CLOSED; successor to COLD START from %s.md" % S1,
                 took("Governance 26 oct - 2", S1, 1)])
    r = rs(ws)
    assert (r["state"], r["title"]) == ("HELD", "Governance 26 oct - 2")


@pytest.mark.parametrize("line", [
    "- 2026-10-26 21:00 CET — Governance 26 oct: SUCCESSOR \"Governance 26 oct - 2\" started on the founder's word",   # F6
    "- 2026-10-26 21:00 CET — Governance 26 oct: next session: open governance, type cold start",
    "- 2026-10-26 21:00 CET — [Governance 26 oct] TAKEN OVER from %s.md" % S1,                                       # F4 blind
])
def test_rs_journal_mentions_block_vacant(ws, line):
    journal(ws / "governance" / "NEOCORTEX", [line])
    r = rs(ws)
    assert (r["state"], r["rule"]) == ("UNRESOLVED", 5), r


def test_rs_two_first_holders_conflict(ws):
    journal(ws / "governance" / "NEOCORTEX", ["- 2026-10-26 22:00 CET — Governance 26 oct: TAKEN OVER governance from none",
                                             "- 2026-10-26 22:05 CET — Governance 26 oct - 2: TAKEN OVER governance from none"])
    assert rs(ws)["state"] == "CONFLICT"


def test_rs_archived_newest_unrecognised_status_is_unresolved(ws):                     # F3
    handoff(ws / "governance" / "NEOCORTEX", "governance", "2026-10-26_1200", status="Statut: actif", sub="2026")
    assert rs(ws)["state"] == "UNRESOLVED"


def test_rs_rule1_unknown_on_listing_failure(ws):
    nc = ws / "governance" / "NEOCORTEX"
    handoff(nc, "governance", "2026-10-26_1200")
    locked = nc / "_archive" / "locked"
    locked.mkdir(parents=True)
    locked.chmod(0)
    try:
        if os.access(locked, os.R_OK):
            pytest.skip("running with privileges that ignore permissions")
        r = rs(ws)
        assert (r["state"], r["rule"]) == ("UNKNOWN", 1) and "listing failed" in r["notes"][0]
    finally:
        locked.chmod(0o755)


def test_rs_typo_role_says_so(ws):                                                        # F5
    r = hr.render_status(rs(ws, role="governnce"), str(ws))
    assert "VACANT" in r and "typo" in r and "role governnce" in r


# --- VERIFY-GATE round 2 (N1): position, vocabulary and retraction checks ----------------------------------------------------

@pytest.mark.parametrize("body", [
    "TAKEN OVER of %s.md?", "asked whether TAKEN OVER from %s.md happened", "TAKEN OVER cancelled for %s.md",
    "TAKEN OVER from %s.md refused by hook", "TAKEN OVER check: %s.md still OPEN", "confirmed no TAKEN OVER yet of %s.md",
    "the founder wants a COLD START from %s.md", "FOUNDER said: cold start of %s.md",
    "PROJECT-B reported: TAKEN OVER from %s.md",                                        # position only (writer field form)
])
def test_rs_n1_lines_are_not_takeovers(ws, body):
    nc = ws / "governance" / "NEOCORTEX"
    handoff(nc, "governance", "2026-10-26_1200")
    journal(nc, ["- 2026-10-26 21:00 CET — Governance 26 oct - 5: " + body % S1])
    assert rs(ws)["state"] == "OPEN"


@pytest.mark.parametrize("line", [                                   # real shapes from the live JOURNAL (2026-11-03 .. 11-19)
    '- 10:54 CET [0ee20c] **TAKEN OVER governance** by "Governance 26 oct" [0ee20c] (Claude Opus 5.5) on the founder\'s '
    '`cold start governance`; handoff %s.md sha256 4edcb679',
    '- 20:46 CET TAKEN OVER: "Governance 26 oct" [7ce696] (Claude Opus 5.5) resumes the role from %s.md; no newer DRAFT.',
    "- 18:40 — TAKEN OVER governance: 'Governance 26 oct' [local_edca0c6c] (Claude Opus 5.5, medium) resumed %s.md (n",
    "- 2026-11-18 12:17 CET — Governance 26 oct [cold start, Opus 5.5 @ medium]: took over from Governance 25 oct - 2 (%s.md). P",
    "- 2026-11-18 23:59 CET — Governance 26 oct [local_x]: COLD START. Handoff %s.md (sha 97226175…) resumed.",
    '- 2026-10-27 14:05 EDT **COLD START "Governance 26 oct" [5bd578]** from %s.md (resolver: role governance)',
])
def test_rs_real_shapes_still_count(ws, line):
    nc = ws / "governance" / "NEOCORTEX"
    handoff(nc, "governance", "2026-10-26_1200")
    journal(nc, [line % S1])
    r = rs(ws)
    assert (r["state"], r["title"]) == ("HELD", "Governance 26 oct"), r


def test_rs_quoted_title_report_inside_another_entry_is_not_a_takeover(ws):       # position only (quoted-title form)
    nc = ws / "governance" / "NEOCORTEX"
    handoff(nc, "governance", "2026-10-26_1200")
    journal(nc, ['- 11:12 — [Governance 26 oct - 5] PROJECT-B TAKEN OVER by "Governance 26 oct" from %s.md; next: PLAN' % S1])
    assert rs(ws)["state"] == "OPEN"


# --- MANIFEST coverage + PROJECT-A stamp form (founder 2026-11-25 19:38, "Build it in, have the successor draft it") -------------

LATER = dt.datetime(2026, 11, 27, 12, 0, tzinfo=UTC)


def manifest(nc, names, archive=(), extra_archive_file=()):
    import json
    m = {"spec": "x", "files": [{"file": n, "genre": "NOTE", "status": "ACTIVE", "hook": "h"} for n in names],
         "archive": {"rows": [{"file": n, "genre": "NOTE", "hook": "h"} for n in archive]}}
    (pathlib.Path(nc) / "MANIFEST.json").write_text(json.dumps(m), encoding="utf-8")
    if extra_archive_file:
        (pathlib.Path(nc) / "_archive").mkdir(exist_ok=True)
        (pathlib.Path(nc) / "_archive" / "MANIFEST_ARCHIVE.json").write_text(
            json.dumps({"rows": [{"file": n, "hook": "h"} for n in extra_archive_file]}), encoding="utf-8")


def test_check_file_manifest_row_missing_fails_and_names_each_file(ws):
    nc = ws / "governance" / "NEOCORTEX"
    h = handoff(nc, "gov", "2026-11-26_1000", extra="")
    h.write_text(h.read_text().replace("- fact `cmd`", "- 10:00 fact `cmd`"))
    (nc / "PLAN_a.md").write_text("x")
    (nc / "NOTE_b.md").write_text("x")
    (nc / "STATUS.md").write_text("x")          # standing file: no row needed (PROJECT-A neocortex_health COVERAGE_EXCLUDE)
    (nc / "JOURNAL.md").write_text("x")
    (nc / "data.txt").write_text("x")           # not .md: out of scope
    manifest(nc, [h.name, "NOTE_b.md"])
    msgs = hr.check_file(str(h), now=LATER)
    assert len(msgs) == 1 and "PLAN_a.md" in msgs[0] and "MANIFEST" in msgs[0] and "files[]" in msgs[0], msgs
    manifest(nc, [h.name, "NOTE_b.md"], archive=["PLAN_a.md"])     # archive.rows[] counts
    assert hr.check_file(str(h), now=LATER) == []
    manifest(nc, [h.name, "NOTE_b.md"], extra_archive_file=["PLAN_a.md"])   # _archive/MANIFEST_ARCHIVE.json rows[] counts
    assert hr.check_file(str(h), now=LATER) == []
    manifest(nc, ["NOTE_b.md"], archive=["PLAN_a.md"])               # the handoff itself needs its row
    msgs = hr.check_file(str(h), now=LATER)
    assert len(msgs) == 1 and h.name in msgs[0]


def test_check_file_manifest_absent_or_unreadable_fails(ws):
    nc = ws / "governance" / "NEOCORTEX"
    h = handoff(nc, "gov", "2026-11-26_1000")
    h.write_text(h.read_text().replace("- fact `cmd`", "- 10:00 fact `cmd`"))
    msgs = hr.check_file(str(h), now=LATER)
    assert len(msgs) == 1 and "no MANIFEST.json" in msgs[0]
    (nc / "MANIFEST.json").write_text("{not json")
    msgs = hr.check_file(str(h), now=LATER)
    assert len(msgs) == 1 and "MANIFEST.json" in msgs[0] and "unreadable" in msgs[0]


def test_check_file_manifest_rule_starts_at_its_cut(ws):
    nc = ws / "governance" / "NEOCORTEX"
    old = handoff(nc, "legacy", "2026-11-25_1800")                  # before MANIFEST_FROM: no coverage check
    old.write_text(old.read_text().replace("- fact `cmd`", "- 10:00 fact `cmd`"))
    (nc / "PLAN_a.md").write_text("x")
    assert hr.check_file(str(old), now=LATER) == []


def test_cli_check_dir_reports_manifest_gap(ws):
    nc = ws / "governance" / "NEOCORTEX"
    h = handoff(nc, "gov", "2026-11-26_1000")
    h.write_text(h.read_text().replace("- fact `cmd`", "- 10:00 fact `cmd`"))
    (nc / "PLAN_a.md").write_text("x")
    manifest(nc, [h.name])
    problems, _ = hr.check_dir(str(ws / "governance"), now=LATER)
    assert any("PLAN_a.md" in p for p in problems)


def test_rs_time_first_stamp_form_time_then_dated_parenthesis(ws):
    """PROJECT-A writes `- HH:MM CET (YYYY-MM-DD) ...`; a quoted-title TAKEN OVER in that form is the writer's own event."""
    nc = ws / "governance" / "NEOCORTEX"
    handoff(nc, "governance", "2026-10-26_1200")
    journal(nc, ['- 21:00 CET (2026-10-26) TAKEN OVER "Governance 26 oct" from %s.md (cold start, H check OK)' % S1])
    r = rs(ws)
    assert r["state"] == "HELD" and "Governance 26 oct" in r["notes"][0], r


def test_check_file_manifest_s508_fixes(ws):
    """s508 seat B: F1 a row naming another path does not cover; F2 errors name the failing file; F4 every gap listed;
    F7 files[] not a list is a malformed MANIFEST; F8 the cut is at 2026-11-26 00:00Z exactly."""
    import json
    nc = ws / "governance" / "NEOCORTEX"
    h = handoff(nc, "gov", "2026-11-26_0000")
    h.write_text(h.read_text().replace("- fact `cmd`", "- 10:00 fact `cmd`"))
    (nc / "NOTE_d.md").write_text("x")
    (nc / "NOTE_e.md").write_text("x")
    (nc / "MANIFEST.json").write_text(json.dumps({"files": [{"file": h.name}, {"file": "sub/NOTE_d.md"}]}))
    msgs = hr.check_file(str(h), now=LATER)
    assert len(msgs) == 2 and any("NOTE_d.md" in m for m in msgs) and any("NOTE_e.md" in m for m in msgs), msgs
    (nc / "MANIFEST.json").write_text(json.dumps({"files": {"a": 1}}))
    msgs = hr.check_file(str(h), now=LATER)
    assert len(msgs) == 1 and "files[]" in msgs[0] and "not a list" in msgs[0], msgs
    manifest(nc, [h.name, "NOTE_d.md", "NOTE_e.md"])
    arc = nc / "_archive" / "MANIFEST_ARCHIVE.json"
    arc.parent.mkdir(exist_ok=True)
    arc.write_text("{bad")
    msgs = hr.check_file(str(h), now=LATER)
    assert len(msgs) == 1 and "MANIFEST_ARCHIVE.json" in msgs[0], msgs
    arc.write_text(json.dumps({"rows": []}))
    assert hr.check_file(str(h), now=LATER) == []
    early = handoff(nc, "edge", "2026-11-25_2359")
    early.write_text(early.read_text().replace("- fact `cmd`", "- 10:00 fact `cmd`"))
    assert hr.check_file(str(early), now=LATER) == []                          # one minute before the cut: not checked
    assert any(early.name in m for m in hr.check_file(str(h), now=LATER))      # ...but it needs its row under a later handoff


@pytest.mark.parametrize("line,ok", [
    ("- 21:00 CET (2026-10-26) ", True),
    ("- 21:00 (2026-10-26) ", True),
    ("- 2026-10-26 21:00 CET — ", True),                  # the form that already worked
    ("- (2026-10-26) ", False),                           # s508 F3: a date with no time is not the PROJECT-A stamp
    ("(2026-10-26) ", False),
    ("- 21:00 CET (note) ", False),
    ("- 21:00 CET ((2026-10-26)) ", False),
])
def test_stamp_re_time_first_form_and_negatives(line, ok):
    assert bool(hr.STAMP_RE.match(line)) is ok


def test_rs_time_first_form_negated_or_refused_is_not_a_takeover(ws):
    nc = ws / "governance" / "NEOCORTEX"
    handoff(nc, "governance", "2026-10-26_1200")
    journal(nc, ['- 21:00 CET (2026-10-26) NOT TAKEN OVER "Governance 26 oct" from %s.md' % S1,
                 '- 21:05 CET (2026-10-26) TAKEN OVER "Governance 26 oct" from %s.md — refused' % S1])
    assert rs(ws)["state"] != "HELD"

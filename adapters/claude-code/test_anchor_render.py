#!/usr/bin/env python3
"""
test_anchor_render.py — tests for anchor_render.py (PLAN_cold_start_context_reduction v0.3 §11, S1).

Cases 1-20 follow BUILD_SPEC_anchor_render_2026-10-26.md §A.7 (the test name carries the case number), plus the
--capture / --table smoke tests. Fixture files are small flat regular files under fixtures/anchor_render/; symlinks,
hardlinks, FIFOs, directories, unreadable files and every large input are created at run time under tmp_path (the tree
is governed by hook_support.py: special files must not live in it).

The CLI is always run as a subprocess, `python -I anchor_render.py ...`, exactly as the hook runs it.

Run:
    python3.12 -m pytest adapters/claude-code/test_anchor_render.py -q
"""

import hashlib
import importlib.util
import io
import json
import os
import pathlib
import re
import shlex
import stat
import subprocess
import sys

import pytest

HERE = pathlib.Path(__file__).resolve().parent
RENDERER = HERE / "anchor_render.py"
FIX = HERE / "fixtures" / "anchor_render"
BUDGET = 16 * 1024
MAX_INPUT = 4 * 1024 * 1024
PY_SYSTEM = "/usr/bin/python3"


# ── helpers ────────────────────────────────────────────────────────────────────────────────────────────────────────

def cli(*args, timeout=30):
    return subprocess.run([sys.executable, "-I", str(RENDERER)] + [str(a) for a in args],
                          capture_output=True, timeout=timeout)


def render(project, timeout=30):
    return cli("render", project, timeout=timeout)


def text_of(proc):
    return proc.stdout.decode("utf-8")


def sha12(data):
    return hashlib.sha256(data).hexdigest()[:12]


def nb(lines):
    """UTF-8 bytes of lines as the renderer emits them: every line ends with one newline."""
    return sum(len(l.encode("utf-8")) + 1 for l in lines)


def make_project(base, name="proj", status=None, manifest=None):
    """<base>/<name>/NEOCORTEX/{STATUS.md, MANIFEST.json}; a value may be bytes, str or a fixture Path."""
    proj = pathlib.Path(base) / name
    nc = proj / "NEOCORTEX"
    nc.mkdir(parents=True)
    for fname, content in (("STATUS.md", status), ("MANIFEST.json", manifest)):
        if content is None:
            continue
        if isinstance(content, pathlib.Path):
            content = content.read_bytes()
        elif isinstance(content, str):
            content = content.encode("utf-8")
        (nc / fname).write_bytes(content)
    return proj


def element(text, name):
    """One rendered element: from its `--- NEOCORTEX/<name> ` header line to the next such header or the end."""
    got, grabbing = [], False
    for ln in io.StringIO(text):
        if ln.startswith("--- NEOCORTEX/"):
            grabbing = ln.startswith("--- NEOCORTEX/%s " % name)
        if grabbing:
            got.append(ln)
    return "".join(got)


def entry_lines(el):
    """The entry lines of a MANIFEST element (header, archive line, notices removed; `no ACTIVE entries` kept)."""
    body = el.split("\n")[1:-1]
    return [l for l in body if not l.startswith(("archive: ", "… ", "projection: "))]


def manifest_doc(entries, **top):
    return json.dumps(dict(top, files=entries))


def reference_cat(path):
    """The loop of session_anchor.sh::_safe_neocortex_cat, written out again here: 64 Ki CHARACTERS, universal newlines."""
    out, n = [], 0
    with open(str(path), encoding="utf-8", errors="replace") as fh:
        for ln in fh:
            n += len(ln)
            if n > 64 * 1024:
                break
            out.append(ln)
    return "".join(out)


def _load():
    spec = importlib.util.spec_from_file_location("anchor_render", RENDERER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


STATUS_MARKED = FIX / "status_marked.md"
S1_LEAD = ["# Project Fixture — STATUS", "Lead line one (binding by default).", "Lead line two.", ""]
S1_A = ["Block A line 1", "Block A line 2"]
S1_B = ["Block B line 1", "", "Block B line 3 after a blank"]
S1_REST = ["## Section Alpha", "Remainder alpha 1", "Remainder alpha 2", "", "## Section Beta", "Remainder beta 1"]


# ── 1-5: the rendered STATUS view ──────────────────────────────────────────────────────────────────────────────────

def test_01_marked_status_counts_order_and_markers_not_emitted(tmp_path):
    proj = make_project(tmp_path, status=STATUS_MARKED)
    p = render(proj)
    assert p.returncode == 0 and p.stderr == b""
    rest = nb(S1_REST)
    header = ("--- NEOCORTEX/STATUS.md · rendered view (binding blocks: 3, remainder: %d of %d bytes; "
              "canonical sha256 %s) ---" % (rest, rest, sha12(STATUS_MARKED.read_bytes())))
    expected = header + "\n" + "".join(l + "\n" for l in S1_LEAD + S1_A + S1_B + S1_REST)
    assert text_of(p) == expected                       # binding lines first, then the remainder, no cap notice
    assert "BINDING" not in text_of(p)                  # marker lines are neither emitted nor counted


def test_02_remainder_cut_reports_exact_bytes_and_keeps_binding(tmp_path):
    lead = ["# Cut test", ""]
    block = ["b" * 99] * 10                             # 100 B each
    tail = ["r" * 49] * 400                             # 50 B each
    src = "\n".join(lead + ["## Part", "<!-- BINDING -->"] + block + ["<!-- /BINDING -->"] + tail) + "\n"
    p = render(make_project(tmp_path, status=src))
    assert p.returncode == 0
    b_bytes = nb(lead) + nb(block)
    t_bytes = nb(["## Part"]) + nb(tail)
    fit = (BUDGET - b_bytes - nb(["## Part"])) // 50
    r_bytes = nb(["## Part"]) + 50 * fit
    assert 0 < fit < 400
    lines = element(text_of(p), "STATUS.md").split("\n")[:-1]
    assert lines[0].startswith("--- NEOCORTEX/STATUS.md · rendered view (binding blocks: 2, remainder: %d of %d bytes;"
                               % (r_bytes, t_bytes))
    assert lines.count("b" * 99) == 10 and "# Cut test" in lines       # binding bytes untouched
    assert lines.count("r" * 49) == fit and "## Part" in lines
    assert lines[-1] == "… %d bytes not shown; read NEOCORTEX/STATUS.md" % (t_bytes - r_bytes)
    assert not any(l.startswith("binding blocks exceed") for l in lines)


def test_03_binding_over_budget_prints_overflow_and_every_binding_line(tmp_path):
    block = ["z" * 99] * 200                            # 20,000 B
    src = "\n".join(["# Big", "## Mid", "<!-- BINDING -->"] + block + ["<!-- /BINDING -->", "## Rest", "rest line"]) + "\n"
    p = render(make_project(tmp_path, status=src))
    assert p.returncode == 0
    b_bytes = nb(["# Big"]) + nb(block)
    t_bytes = nb(["## Mid", "## Rest", "rest line"])
    lines = element(text_of(p), "STATUS.md").split("\n")[:-1]
    assert lines[0].startswith("--- NEOCORTEX/STATUS.md · rendered view (binding blocks: 2, remainder: 0 of %d bytes;"
                               % t_bytes)
    assert lines.count("z" * 99) == 200                 # every binding line present
    assert lines[-2:] == ["… %d bytes not shown; read NEOCORTEX/STATUS.md" % t_bytes,
                          "binding blocks exceed the 16 KiB budget by %d bytes" % (b_bytes - BUDGET)]
    assert "rest line" not in lines


def test_04_marked_block_beyond_64ki_characters_appears_in_full(tmp_path):
    filler = [("filler %05d " % i).ljust(59, ".") for i in range(2000)]      # 60 chars per line: 120,000 > 65,536
    src = "\n".join(["# Late block", "## Filler"] + filler +
               ["## Late", "<!-- BINDING -->", "LATE-BINDING-LINE-1", "LATE-BINDING-LINE-2", "<!-- /BINDING -->"]) + "\n"
    proj = make_project(tmp_path, status=src)
    assert "LATE-BINDING-LINE-1" not in reference_cat(proj / "NEOCORTEX" / "STATUS.md")    # today's reader loses it
    p = render(proj)
    assert p.returncode == 0
    el = element(text_of(p), "STATUS.md")
    assert el.startswith("--- NEOCORTEX/STATUS.md · rendered view (binding blocks: 2,")
    assert "LATE-BINDING-LINE-1\nLATE-BINDING-LINE-2\n" in el
    assert "bytes not shown; read NEOCORTEX/STATUS.md" in el          # the remainder is what was cut


def test_05a_single_line_over_64ki_inside_a_marked_block_is_emitted_whole(tmp_path):
    big = "X" * 70000
    src = "\n".join(["# L5a", "## Part", "<!-- BINDING -->", big, "<!-- /BINDING -->"]) + "\n"
    p = render(make_project(tmp_path, status=src))
    assert p.returncode == 0
    assert (big + "\n") in text_of(p)
    b_bytes = nb(["# L5a", big])
    lines = element(text_of(p), "STATUS.md").split("\n")[:-1]
    assert lines[-2:] == ["… %d bytes not shown; read NEOCORTEX/STATUS.md" % nb(["## Part"]),
                          "binding blocks exceed the 16 KiB budget by %d bytes" % (b_bytes - BUDGET)]


def test_05b_single_line_over_64ki_in_the_remainder_is_not_emitted(tmp_path):
    big = "Y" * 70000
    src = "\n".join(["# L5b", "## Part", "<!-- BINDING -->", "small binding", "<!-- /BINDING -->", "## Rest", big]) + "\n"
    p = render(make_project(tmp_path, status=src))
    assert p.returncode == 0
    assert "YYYYYYYYYY" not in text_of(p)
    lines = element(text_of(p), "STATUS.md").split("\n")[:-1]
    assert lines[0].startswith("--- NEOCORTEX/STATUS.md · rendered view (binding blocks: 2, remainder: %d of %d bytes;"
                               % (nb(["## Part", "## Rest"]), nb(["## Part", "## Rest", big])))
    assert lines[1:5] == ["# L5b", "small binding", "## Part", "## Rest"]
    assert lines[-1] == "… %d bytes not shown; read NEOCORTEX/STATUS.md" % nb([big])


def test_05c_marker_inside_the_lead_is_not_double_emitted(tmp_path):
    src = ["# Lead", "<!-- BINDING -->", "inside lead", "<!-- /BINDING -->", "tail of lead", "## Rest", "remainder"]
    p = render(make_project(tmp_path, status="\n".join(src) + "\n"))
    rest = nb(["## Rest", "remainder"])
    expected_head = ("--- NEOCORTEX/STATUS.md · rendered view (binding blocks: 2, remainder: %d of %d bytes; "
                     "canonical sha256 " % (rest, rest))
    lines = text_of(p).split("\n")[:-1]
    assert lines[0].startswith(expected_head)
    assert lines[1:] == ["# Lead", "inside lead", "tail of lead", "## Rest", "remainder"]


def test_05d_heading_inside_a_fence_does_not_end_the_lead(tmp_path):
    src = ["# T", "```", "## not a heading", "```", "lead after fence", "## Real", "remainder",
           "<!-- BINDING -->", "late block", "<!-- /BINDING -->"]
    p = render(make_project(tmp_path, status="\n".join(src) + "\n"))
    lines = text_of(p).split("\n")[:-1]
    assert lines[0].startswith("--- NEOCORTEX/STATUS.md · rendered view (binding blocks: 2,")
    assert lines[1:] == ["# T", "```", "## not a heading", "```", "lead after fence", "late block", "## Real", "remainder"]


# ── 6-8: fallback ──────────────────────────────────────────────────────────────────────────────────────────────────

TRAILER = re.compile(r"^… truncated: ([0-9]+) more bytes; read NEOCORTEX/STATUS\.md\n\Z", re.MULTILINE)
FALLBACK_INPUTS = [
    pytest.param((FIX / "status_plain.md").read_bytes(), False, id="plain_fixture"),
    pytest.param(b"# CRLF doc\r\nline two\r\n\r\nline four\r\n", False, id="crlf"),
    pytest.param(b"a\rb\rc\n", False, id="lone_cr"),
    pytest.param(b"# No final newline\nlast line without newline", False, id="no_final_newline"),
    pytest.param(b"# bytes \xff\xfe here\nok\n", False, id="invalid_utf8"),
    pytest.param(b"", False, id="empty"),
    pytest.param(("é" * 100 + "\n").encode("utf-8") * 700, True, id="wide_chars_cut_by_characters_not_bytes"),
    pytest.param(b"head\n" + b"y" * 70000 + b"\nafter\n", True, id="long_line_cut"),
    pytest.param(b"y" * 70000 + b"\nafter\n", True, id="first_line_alone_over_the_rule"),
]


@pytest.mark.parametrize("raw,expect_cut", FALLBACK_INPUTS)
def test_06_no_markers_falls_back_byte_identical_to_the_legacy_reader(tmp_path, raw, expect_cut):
    proj = make_project(tmp_path, status=raw)
    path = proj / "NEOCORTEX" / "STATUS.md"
    p = render(proj)
    assert p.returncode == 0 and p.stderr == b""
    el = element(text_of(p), "STATUS.md")
    head, loud, rest = el.split("\n", 2)
    assert head == ("--- NEOCORTEX/STATUS.md · today's rendering (64 Ki-character rule; canonical sha256 %s) ---"
                    % sha12(raw))
    assert loud == "STATUS has no BINDING markers: today's rendering (64 Ki-character rule)"
    ref = reference_cat(path)
    with open(str(path), encoding="utf-8", errors="replace") as fh:
        full = fh.read()
    m = TRAILER.search(rest)
    body = rest[:m.start()] if m else rest
    assert body == (ref if (not ref or ref.endswith("\n")) else ref + "\n")        # a final newline is added, nothing else
    assert (len(ref) < len(full)) == expect_cut
    if expect_cut:
        assert m is not None and int(m.group(1)) == len(full.encode("utf-8")) - len(ref.encode("utf-8"))
    else:
        assert m is None and "truncated" not in el


MALFORMED = [
    pytest.param("# T\n<!-- BINDING -->\n<!-- BINDING -->\nx\n<!-- /BINDING -->\n", 3, "nested", id="nested"),
    pytest.param("# T\nx\n<!-- /BINDING -->\n", 3, "unopened close", id="unopened_close"),
    pytest.param("# T\n## S\n<!-- BINDING -->\nx\n", 3, "unclosed", id="unclosed"),
    pytest.param("# T\n<!-- /BINDING -->\n<!-- BINDING -->\nx\n", 2, "unopened close", id="first_fault_wins"),
]


@pytest.mark.parametrize("src,line,kind", MALFORMED)
def test_07_malformed_markers_fall_back_with_line_and_kind(tmp_path, src, line, kind):
    p = render(make_project(tmp_path, status=src))
    assert p.returncode == 0
    lines = text_of(p).split("\n")
    assert lines[0] == ("--- NEOCORTEX/STATUS.md · today's rendering (64 Ki-character rule; canonical sha256 %s) ---"
                        % sha12(src.encode("utf-8")))
    assert lines[1] == ("STATUS has malformed BINDING markers (line %d: %s): today's rendering (64 Ki-character rule)"
                        % (line, kind))
    assert "\n".join(lines[2:]) == src                  # today's rendering: the file as it is, markers included


FENCED = [
    pytest.param((FIX / "status_fenced.md").read_bytes(), id="backtick_fixture"),
    pytest.param(b"# T\n~~~\n<!-- BINDING -->\nx\n<!-- /BINDING -->\n~~~\n## S\nz\n", id="tilde"),
    pytest.param(b"# T\n  ```text\n<!-- BINDING -->\nx\n<!-- /BINDING -->\n  ```\n", id="indented_fence"),
]


@pytest.mark.parametrize("raw", FENCED)
def test_08_markers_inside_a_fence_are_inert_so_the_view_falls_back(tmp_path, raw):
    p = render(make_project(tmp_path, status=raw))
    assert p.returncode == 0
    lines = text_of(p).split("\n")
    assert lines[0].startswith("--- NEOCORTEX/STATUS.md · today's rendering (64 Ki-character rule;")
    assert lines[1] == "STATUS has no BINDING markers: today's rendering (64 Ki-character rule)"


# ── 9-14: the MANIFEST view ────────────────────────────────────────────────────────────────────────────────────────

def test_09_manifest_selection_follows_the_state_rules(tmp_path):
    p = render(make_project(tmp_path, manifest=FIX / "manifest_mixed.json"))
    assert p.returncode == 0
    el = element(text_of(p), "MANIFEST.json")
    assert el.split("\n")[0] == (
        "--- NEOCORTEX/MANIFEST.json · rendered view (ACTIVE index: 8 of 11 entries; hook, archive, provenance omitted; "
        "canonical sha256 %s) ---" % sha12((FIX / "manifest_mixed.json").read_bytes()))
    lines = entry_lines(el)
    assert len(lines) == 8
    for kept in ("A_absent", "B_active", "D_shipped_residual", "E_resolved_active", "F_done", "I_weird", "J_null_state"):
        assert any(l.startswith(kept) for l in lines), kept
    assert "(malformed entry #6)" in lines
    for dropped in ("C_shipped_", "G_superseded", "H_resolved"):
        assert not any(l.startswith(dropped) for l in lines), dropped


RESIDUAL_WORDS = ["residual", "remains", "open", "pending", "carry-over", "partial", "but", "not yet", "todo", "to do", "see",
                  "PENDING"]


def test_09b_every_residual_marker_keeps_a_closed_entry_and_word_boundaries_hold(tmp_path):
    entries = [{"file": "w%02d_2026-10-01.md" % i, "genre": "N", "status": "SHIPPED, " + w, "status_state": "shipped"}
               for i, w in enumerate(RESIDUAL_WORDS)]
    entries.append({"file": "clean_2026-10-01.md", "genre": "N", "status": "SHIPPED fully", "status_state": "shipped"})
    entries.append({"file": "bound_2026-10-01.md", "genre": "N", "status": "SHIPPED; seen, reopened, butter",
                    "status_state": "resolved"})
    p = render(make_project(tmp_path, manifest=manifest_doc(entries)))
    assert p.returncode == 0
    lines = entry_lines(element(text_of(p), "MANIFEST.json"))
    assert len(lines) == len(RESIDUAL_WORDS)
    assert not any(l.startswith(("clean_", "bound_")) for l in lines)       # \b: seen, reopened, butter are not markers


def test_09c_status_state_is_matched_case_and_space_insensitively(tmp_path):
    entries = [
        {"file": "a_2026-10-01.md", "genre": "N", "status": "closed", "status_state": " Superseded "},
        {"file": "b_2026-10-02.md", "genre": "N", "status": "live", "status_state": "ACTIVE"},
        {"file": "c_2026-10-03.md", "genre": "N", "status": "live", "status_state": "Active "},
        {"file": "d_2026-10-04.md", "genre": "N", "status": "closed", "status_state": "SHIPPED"},
        {"file": "e_2026-10-05.md", "genre": "N", "status": "x", "status_state": "terminal"},
    ]
    p = render(make_project(tmp_path, manifest=manifest_doc(entries)))
    lines = entry_lines(element(text_of(p), "MANIFEST.json"))
    assert [l.split(" · ")[0] for l in lines] == ["e_2026-10-05.md", "c_2026-10-03.md", "b_2026-10-02.md"]


def test_10_newest_first_by_filename_date_with_undated_after_in_file_order(tmp_path):
    p = render(make_project(tmp_path, manifest=FIX / "manifest_order.json"))
    assert p.returncode == 0
    assert entry_lines(element(text_of(p), "MANIFEST.json")) == [
        "NOTE_newest_2026-10-20.md · NOTE · s7",              # newest entry sits LAST in files[]
        "RANGE_2026-09-01_to_2026-10-15.md · NOTE · s6",      # the LAST date in the name counts
        "NOTE_b_2026-10-10.md · NOTE · s3",                   # tie: files[] order kept
        "TIE_x_2026-10-10.md · NOTE · s5",
        "PLAN_a_2026-10-01.md · PLAN · s1",
        "undated_one.md · NOTE · u1",                         # undated: after, in file order
        "undated_two.md · NOTE · u2",
    ]


def test_11a_status_is_trimmed_to_exactly_120_characters(tmp_path):
    entries = [{"file": "L_2026-10-03.md", "genre": "G", "status": "x" * 130},
               {"file": "E_2026-10-02.md", "genre": "G", "status": "y" * 120},
               {"file": "S_2026-10-01.md", "genre": "G", "status": "z" * 119}]
    lines = entry_lines(element(text_of(render(make_project(tmp_path, manifest=manifest_doc(entries)))), "MANIFEST.json"))
    status = lines[0].split(" · ")[2]
    assert status == "x" * 119 + "…" and len(status) == 120
    assert lines[1] == "E_2026-10-02.md · G · " + "y" * 120               # exactly 120: untouched
    assert lines[2] == "S_2026-10-01.md · G · " + "z" * 119


def test_11b_control_characters_and_newlines_never_split_a_line(tmp_path):
    entries = [{"file": "a\nb\tc\x00d e\x7ff_2026-10-01.md", "genre": "G\r\nH",
                "status": "line1\nline2\n\n\nline3 line4   spaced"}]
    p = render(make_project(tmp_path, manifest=manifest_doc(entries)))
    assert p.returncode == 0
    el = element(text_of(p), "MANIFEST.json")
    assert entry_lines(el) == ["a b c d e f_2026-10-01.md · G H · line1 line2 line3 line4 spaced"]
    assert el.count("\n") == 3                              # header, one entry, projection notice: nothing else


def test_11c_missing_and_empty_fields_render_as_a_question_mark(tmp_path):
    entries = [{"file": "z_2026-10-02.md"},
               {"file": "y_2026-10-01.md", "genre": "", "status": "   "},
               {"genre": "G"}]
    lines = entry_lines(element(text_of(render(make_project(tmp_path, manifest=manifest_doc(entries)))), "MANIFEST.json"))
    assert lines == ["z_2026-10-02.md · ? · ?", "y_2026-10-01.md · ? · ?", "? · G · ?"]


def test_11d_a_lone_surrogate_in_a_field_does_not_fail_the_view(tmp_path):
    raw = b'{"files": [{"file": "bad\\ud800.md", "genre": "G", "status": "s"}]}'
    p = render(make_project(tmp_path, manifest=raw))
    assert p.returncode == 0 and p.stderr == b""
    assert "bad" in text_of(p)                              # stdout is valid UTF-8 (text_of decoded it strictly)


def test_12_files_after_a_140kb_archive_are_all_eligible_and_the_archive_is_a_one_line_summary(tmp_path):
    archive = {"exists": True, "span": "2026-05-28 .. 2026-09-30",
               "sweeps": ["ARCHIVE-JUNK-SENTINEL " + "j" * 100] * 1400, "note": "n"}
    entries = [{"file": "N%02d_2026-10-%02d.md" % (i, i + 1), "genre": "NOTE", "status": "live %d" % i,
                "status_state": "active"} for i in range(20)]
    raw = json.dumps({"spec": "x", "project": "P", "archive": archive, "files": entries})
    assert raw.index('"archive"') < raw.index('"files"') and len(raw) > 140000          # PROJECT-A shape
    p = render(make_project(tmp_path, manifest=raw))
    assert p.returncode == 0
    out = text_of(p)
    assert "ACTIVE index: 20 of 20 entries" in out.split("\n")[0]
    assert out.split("\n")[1] == "archive: exists, 2026-05-28 .. 2026-09-30"
    assert len(entry_lines(element(out, "MANIFEST.json"))) == 20
    assert "ARCHIVE-JUNK-SENTINEL" not in out


@pytest.mark.parametrize("archive,expected", [
    ({"exists": False, "span": "2026-01 .. 2026-02"}, "archive: none"),
    ({"exists": True}, "archive: exists"),
    ({"exists": True, "span": "a\nb" + "s" * 200}, "archive: exists, a b" + "s" * 116 + "…"),
    ("a string", None),
    (None, None),
])
def test_12b_archive_line_forms(tmp_path, archive, expected):
    top = {} if archive is None else {"archive": archive}
    p = render(make_project(tmp_path, manifest=manifest_doc([{"file": "e_2026-10-01.md", "genre": "G", "status": "s"}], **top)))
    second = text_of(p).split("\n")[1]
    if expected is None:
        assert not second.startswith("archive:")
    else:
        assert second == expected


def test_13a_a_manifest_that_fits_gets_the_projection_notice_and_no_cap_notice(tmp_path):
    entries = [{"file": "ent_%04d_2026-01-01.md" % i, "genre": "G", "status": "s"} for i in range(20)]
    out = text_of(render(make_project(tmp_path, manifest=manifest_doc(entries))))
    lines = out.split("\n")[:-1]
    assert "not shown" not in out
    assert lines[-1] == ("projection: ACTIVE entries only (status_state absent, active, unknown or conflicting), newest "
                         "first by filename date, status trimmed to 120 chars; the canonical file is complete: read "
                         "NEOCORTEX/MANIFEST.json")


def _cap_entries(n, status_len):
    return [{"file": "ent_%04d_2026-01-01.md" % i, "genre": "G", "status": "s" * status_len} for i in range(n)]


def test_13b_over_the_cap_the_notice_carries_the_exact_count_and_bytes(tmp_path):
    per = len(("ent_0000_2026-01-01.md · G · " + "s" * 60).encode("utf-8")) + 1
    fit = BUDGET // per
    out = text_of(render(make_project(tmp_path, manifest=manifest_doc(_cap_entries(200, 60)))))
    lines = out.split("\n")[:-1]
    assert len(entry_lines(element(out, "MANIFEST.json"))) == fit
    assert lines[-2] == "… %d entries / %d bytes not shown; read NEOCORTEX/MANIFEST.json" % (200 - fit, (200 - fit) * per)
    assert lines[-1].startswith("projection: ")
    assert "ent_%04d_" % fit not in out and "ent_%04d_" % (fit - 1) in out


def test_13c_the_budget_is_inclusive_exactly_16384_bytes_all_shown_one_more_is_cut(tmp_path):
    base = len("ent_0000_2026-01-01.md · G · ".encode("utf-8"))
    status_len = 127 - base                                 # every line is exactly 128 B with its newline
    assert 128 * 128 == BUDGET
    out = text_of(render(make_project(tmp_path, "a", manifest=manifest_doc(_cap_entries(128, status_len)))))
    assert len(entry_lines(element(out, "MANIFEST.json"))) == 128 and "not shown" not in out
    out = text_of(render(make_project(tmp_path, "b", manifest=manifest_doc(_cap_entries(129, status_len)))))
    assert len(entry_lines(element(out, "MANIFEST.json"))) == 128
    assert "… 1 entries / 128 bytes not shown; read NEOCORTEX/MANIFEST.json" in out


def test_13d_emission_stops_at_the_first_line_that_does_not_fit_no_skip_ahead(tmp_path):
    e1 = {"file": "f" * 16000 + "_2026-10-09.md", "genre": "G", "status": "s"}
    e2 = {"file": "g" * 500 + "_2026-10-08.md", "genre": "G", "status": "s"}
    e3 = {"file": "small_2026-10-07.md", "genre": "G", "status": "s"}
    size = lambda e: len(("%s · G · s" % e["file"]).encode("utf-8")) + 1
    assert size(e1) + size(e2) > BUDGET and size(e1) + size(e3) <= BUDGET               # e3 would fit if we skipped ahead
    out = text_of(render(make_project(tmp_path, manifest=manifest_doc([e3, e2, e1]))))
    assert entry_lines(element(out, "MANIFEST.json")) == ["%s · G · s" % e1["file"]]
    assert "… 2 entries / %d bytes not shown; read NEOCORTEX/MANIFEST.json" % (size(e2) + size(e3)) in out


def test_14a_empty_files_is_a_valid_empty_view_rc_2(tmp_path):
    p = render(make_project(tmp_path, manifest=FIX / "manifest_empty.json"))
    assert p.returncode == 2 and p.stderr == b""
    lines = text_of(p).split("\n")[:-1]
    assert "ACTIVE index: 0 of 0 entries" in lines[0]
    assert lines[1] == "no ACTIVE entries" and lines[2].startswith("projection: ") and len(lines) == 3


def test_14b_entries_that_are_all_closed_give_rc_2_and_a_status_still_renders(tmp_path):
    entries = [{"file": "a_2026-10-01.md", "genre": "N", "status": "SHIPPED", "status_state": "shipped"},
               {"file": "b_2026-10-02.md", "genre": "N", "status": "RESOLVED", "status_state": "resolved"}]
    p = render(make_project(tmp_path, status=STATUS_MARKED, manifest=manifest_doc(entries)))
    assert p.returncode == 2
    out = text_of(p)
    assert "ACTIVE index: 0 of 2 entries" in out and "no ACTIVE entries" in out
    assert "--- NEOCORTEX/STATUS.md · rendered view" in out


MANIFEST_FAILURES = [
    pytest.param((FIX / "manifest_nofiles.json").read_bytes(), "no files[] list", id="files_missing"),
    pytest.param((FIX / "manifest_notlist.json").read_bytes(), "no files[] list", id="files_not_a_list"),
    pytest.param(b"[]", "no files[] list", id="top_level_not_an_object"),
    pytest.param((FIX / "manifest_malformed.json").read_bytes(), "does not parse", id="malformed_json"),
    pytest.param(b"", "does not parse", id="empty_file"),
    pytest.param(b"[" * 200000, "does not parse", id="nesting_too_deep"),
]


@pytest.mark.parametrize("raw,cls", MANIFEST_FAILURES)
def test_14c_manifest_failures_are_rc_1_with_nothing_on_stdout(tmp_path, raw, cls):
    p = render(make_project(tmp_path, status=STATUS_MARKED, manifest=raw))      # the STATUS alone would render fine
    assert p.returncode == 1
    assert p.stdout == b""
    assert p.stderr == ("anchor_render: MANIFEST.json: %s\n" % cls).encode("utf-8")


# ── 15-17: input bound, hostile files, absent files ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("element_name", ["STATUS.md", "MANIFEST.json"])
def test_15a_input_over_4mib_is_refused(tmp_path, element_name):
    proj = make_project(tmp_path)
    with open(str(proj / "NEOCORTEX" / element_name), "wb") as fh:
        fh.truncate(MAX_INPUT + 1)                          # sparse: nothing is written
    p = render(proj)
    assert p.returncode == 1 and p.stdout == b""
    assert p.stderr == ("anchor_render: %s: input bound exceeded\n" % element_name).encode("utf-8")


def test_15b_input_of_exactly_4mib_is_accepted(tmp_path):
    prefix, suffix = b'{"files": [], "pad": "', b'"}'
    raw = prefix + b"x" * (MAX_INPUT - len(prefix) - len(suffix)) + suffix
    assert len(raw) == MAX_INPUT
    p = render(make_project(tmp_path, manifest=raw))
    assert p.returncode == 2 and "no ACTIVE entries" in text_of(p)


def _plant(kind, tmp_path, target, secret):
    if kind == "symlink":
        victim = tmp_path / "victim_secret.txt"
        victim.write_text(secret)
        target.symlink_to(victim)
    elif kind == "dangling":
        target.symlink_to(tmp_path / "does_not_exist")
    elif kind == "hardlink":
        victim = tmp_path / "victim_secret.txt"
        victim.write_text(secret)
        os.link(str(victim), str(target))
    elif kind == "fifo":
        os.mkfifo(str(target))
    elif kind == "directory":
        target.mkdir()
        (target / "inner.txt").write_text(secret)
    elif kind == "unreadable":
        if os.geteuid() == 0:
            pytest.skip("root reads unreadable files")
        target.write_text(secret)
        target.chmod(0)
    else:
        raise AssertionError(kind)


HOSTILE = [("symlink", "symbolic link refused"), ("dangling", "symbolic link refused"), ("hardlink", "hardlinked"),
           ("fifo", "not a regular file"), ("directory", "not a regular file"), ("unreadable", "unreadable (EACCES)")]


@pytest.mark.parametrize("element_name", ["STATUS.md", "MANIFEST.json"])
@pytest.mark.parametrize("kind,cls", HOSTILE)
def test_16_hostile_files_are_refused_by_class_and_never_leak(tmp_path, kind, cls, element_name):
    secret = "SECRET-TOKEN-5f1c9d2e"
    other = {"STATUS.md": ("MANIFEST.json", FIX / "manifest_order.json"), "MANIFEST.json": ("STATUS.md", STATUS_MARKED)}
    other_name, other_src = other[element_name]
    proj = make_project(tmp_path)
    nc = proj / "NEOCORTEX"
    (nc / other_name).write_bytes(other_src.read_bytes())            # the other element is fine: nothing may print
    target = nc / element_name
    _plant(kind, tmp_path, target, secret)
    try:
        p = render(proj, timeout=10)                                 # a FIFO must not hang the anchor
    finally:
        if kind == "unreadable":
            target.chmod(0o600)
    assert p.returncode == 1
    assert p.stdout == b""
    assert p.stderr == ("anchor_render: %s: %s\n" % (element_name, cls)).encode("utf-8")
    assert secret.encode("utf-8") not in p.stdout + p.stderr


def test_17a_an_absent_element_has_no_header(tmp_path):
    out = text_of(render(make_project(tmp_path, "m", manifest=FIX / "manifest_order.json")))
    assert out.startswith("--- NEOCORTEX/MANIFEST.json ") and "NEOCORTEX/STATUS.md" not in out
    out = text_of(render(make_project(tmp_path, "s", status=STATUS_MARKED)))
    assert out.startswith("--- NEOCORTEX/STATUS.md ") and "NEOCORTEX/MANIFEST.json" not in out


def test_17b_both_absent_is_rc_3_with_nothing_printed(tmp_path):
    proj = tmp_path / "empty_nc"
    (proj / "NEOCORTEX").mkdir(parents=True)
    for target in (proj, tmp_path / "no_neocortex_at_all"):
        p = render(target)
        assert p.returncode == 3 and p.stdout == b"" and p.stderr == b""


# ── 18: presence (gate G1) ─────────────────────────────────────────────────────────────────────────────────────────

def _presence_setup(tmp_path):
    proj = make_project(tmp_path, status=STATUS_MARKED)
    rendered = tmp_path / "rendered.txt"
    rendered.write_bytes(render(proj).stdout)
    return proj / "NEOCORTEX" / "STATUS.md", rendered


def test_18a_presence_present_on_a_real_render(tmp_path):
    status, rendered = _presence_setup(tmp_path)
    p = cli("--presence", status, rendered)
    assert p.returncode == 0 and p.stderr == b""
    assert text_of(p).split("\n")[:-1] == [
        "presence report — STATUS sha256 %s (%s B) vs rendered.txt sha256 %s"
        % (hashlib.sha256(status.read_bytes()).hexdigest(), "{:,}".format(status.stat().st_size),
           hashlib.sha256(rendered.read_bytes()).hexdigest()),
        "marked blocks: 2   (lead block: PRESENT)",
        "block 1  lines 8-9  %d B  PRESENT" % nb(S1_A),
        "block 2  lines 15-17  %d B  PRESENT" % nb(S1_B),
        "missing: 0",
    ]


def test_18b_presence_reports_missing_and_exits_1_when_a_block_line_is_gone(tmp_path):
    status, rendered = _presence_setup(tmp_path)
    kept = [l for l in rendered.read_text(encoding="utf-8").split("\n") if l != "Block B line 3 after a blank"]
    assert len(kept) == len(rendered.read_text(encoding="utf-8").split("\n")) - 1
    rendered.write_text("\n".join(kept), encoding="utf-8")
    p = cli("--presence", status, rendered)
    assert p.returncode == 1
    lines = text_of(p).split("\n")[:-1]
    assert lines[2] == "block 1  lines 8-9  %d B  PRESENT" % nb(S1_A)
    assert lines[3] == "block 2  lines 15-17  %d B  MISSING" % nb(S1_B)
    assert lines[-1] == "missing: 1"


def test_18c_presence_is_vacuous_without_markers(tmp_path):
    proj = make_project(tmp_path, status=FIX / "status_plain.md")
    rendered = tmp_path / "rendered.txt"
    rendered.write_bytes(render(proj).stdout)
    p = cli("--presence", proj / "NEOCORTEX" / "STATUS.md", rendered)
    assert p.returncode == 0
    lines = text_of(p).split("\n")[:-1]
    assert lines[1:] == ["marked blocks: 0 — presence vacuous (no BINDING markers)", "missing: 0"]


def test_18d_presence_with_malformed_markers_fails(tmp_path):
    proj = make_project(tmp_path, status="# T\n<!-- BINDING -->\n<!-- BINDING -->\nx\n<!-- /BINDING -->\n")
    rendered = tmp_path / "rendered.txt"
    rendered.write_bytes(b"anything\n")
    p = cli("--presence", proj / "NEOCORTEX" / "STATUS.md", rendered)
    assert p.returncode == 1
    lines = text_of(p).split("\n")[:-1]
    assert lines[1] == "malformed markers: line 3: nested" and lines[-1].startswith("missing: n/a")


def test_18e_presence_compares_a_block_ending_in_blank_lines_as_one_newline(tmp_path):
    proj = make_project(tmp_path, status="# T\n## S\n<!-- BINDING -->\ntext\n\n\n<!-- /BINDING -->\n")
    rendered = tmp_path / "rendered.txt"
    rendered.write_bytes(b"# T\ntext\n")                    # what the hook's $(...) leaves at the very end of the view
    p = cli("--presence", proj / "NEOCORTEX" / "STATUS.md", rendered)
    assert p.returncode == 0 and "block 1  lines 4-6" in text_of(p) and "PRESENT" in text_of(p)


def test_18f_presence_refuses_a_symlinked_rendered_file(tmp_path):
    status, rendered = _presence_setup(tmp_path)
    link = tmp_path / "link.txt"
    link.symlink_to(rendered)
    p = cli("--presence", status, link)
    assert p.returncode == 1 and p.stdout == b""
    assert p.stderr == b"anchor_render: link.txt: symbolic link refused\n"


# ── 19: Python 3.9 ─────────────────────────────────────────────────────────────────────────────────────────────────

def _system_python_runs():
    if not os.path.exists(PY_SYSTEM):
        return False
    try:
        return subprocess.run([PY_SYSTEM, "--version"], capture_output=True, timeout=30).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


needs_system_python = pytest.mark.skipif(not _system_python_runs(), reason="/usr/bin/python3 absent or not runnable")


@needs_system_python
def test_19a_the_renderer_compiles_under_the_system_python():
    code = "import sys; p = sys.argv[1]; compile(open(p, encoding='utf-8').read(), p, 'exec')"
    r = subprocess.run([PY_SYSTEM, "-c", code, str(RENDERER)], capture_output=True, timeout=60)
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")


@needs_system_python
def test_19b_render_gives_the_same_stdout_under_the_system_python(tmp_path):
    proj = make_project(tmp_path, status=STATUS_MARKED, manifest=FIX / "manifest_mixed.json")
    a = render(proj)
    b = subprocess.run([PY_SYSTEM, "-I", str(RENDERER), "render", str(proj)], capture_output=True, timeout=60)
    assert a.returncode == b.returncode == 0
    assert b.stdout == a.stdout and b.stderr == b""


# ── 20: usage ──────────────────────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("args", [
    [], ["frobnicate"], ["render"], ["render", "a", "b"], ["--presence"], ["--presence", "only_one"],
    ["--capture"], ["--capture", "base"], ["--capture", "base", "--workspace", "w"],
    ["--capture", "base", "--workspace", "w", "--workspace", "w", "--hooks-before", "x", "--hooks-after", "y",
     "--renderer", "r"],
    ["--table"], ["--table", "base", "--out", "f.md"], ["--table", "base", "--presence-dir", "d", "--out"],
])
def test_20_usage_errors_are_rc_64_never_2(args):
    p = cli(*args)
    assert p.returncode == 64
    assert p.stdout == b"" and p.stderr.startswith(b"usage: anchor_render.py")


# ── unit checks through the module ────────────────────────────────────────────────────────────────────────────────

def test_unit_parse_status_reports_the_first_fault_and_counts_blocks():
    m = _load()
    lines = ["# T\n", "## S\n", "<!-- BINDING -->\n", "a\n", "<!-- /BINDING -->\n", "<!-- /BINDING -->\n", "<!-- BINDING -->\n"]
    p = m.parse_status(lines)
    assert p["fault"] == (6, "unopened close") and p["has_markers"] is True and p["blocks"] == [(3, 4)]
    p = m.parse_status(["# T\n", "x\n"])
    assert p["fault"] is None and p["has_markers"] is False and p["lead_end"] == 2


def test_unit_safe_read_signals_absent_and_never_returns_a_partial_file(tmp_path):
    m = _load()
    with pytest.raises(m.Absent):
        m.safe_read(str(tmp_path / "nope.md"))
    (tmp_path / "ok.md").write_bytes(b"abc")
    assert m.safe_read(str(tmp_path / "ok.md")) == b"abc"


# ── --capture and --table smoke tests ──────────────────────────────────────────────────────────────────────────────

BEFORE_HOOK = """#!/bin/bash
echo "anchor for $PWD"
if [ -d "$PWD/NEOCORTEX" ]; then
  [ -f "$PWD/NEOCORTEX/MANIFEST.json" ] && { echo "--- NEOCORTEX/MANIFEST.json ---"; cat "$PWD/NEOCORTEX/MANIFEST.json"; }
  [ -f "$PWD/NEOCORTEX/STATUS.md" ] && { echo "--- NEOCORTEX/STATUS.md ---"; cat "$PWD/NEOCORTEX/STATUS.md"; }
fi
exit 0
"""
AFTER_HOOK = """#!/bin/bash
echo "anchor for $PWD"
if [ -d "$PWD/NEOCORTEX" ]; then
  %s -I "$(cd "$(dirname "$0")/../.." && pwd)/governance/adapters/claude-code/anchor_render.py" render "$PWD"
fi
exit 0
""" % shlex.quote(sys.executable)
META_KEYS = {"hook_rc_before", "hook_rc_after", "renderer_rc", "bytes_before", "bytes_after", "status_bytes_before",
             "status_bytes_after", "manifest_bytes_before", "manifest_bytes_after", "status_mode", "overflow_bytes",
             "binding_blocks", "renderer_failed_line"}
MINI = [("governance", "governance", STATUS_MARKED, FIX / "manifest_mixed.json"),
        ("Design Projects__Hotter", "Design Projects/Hotter", FIX / "status_plain.md", FIX / "manifest_empty.json")]


def _mini_baseline(tmp_path):
    base = tmp_path / "baseline"
    (base / "_shared").mkdir(parents=True)
    (base / "_shared" / "OPEN_WORK.md").write_text("open work\n")
    (base / "_shared" / "SKILLS_INDEX.md").write_text("skills\n")
    rows = ["slug\trelpath\tSTATUS.md sha256\tSTATUS bytes\tBINDING open markers\tMANIFEST.json sha256\tMANIFEST bytes"]
    for slug, rel, status, manifest in MINI:
        (base / slug).mkdir()
        (base / slug / "STATUS.md").write_bytes(status.read_bytes())
        (base / slug / "MANIFEST.json").write_bytes(manifest.read_bytes())
        rows.append("\t".join([slug, rel, "x", "0", "0", "x", "0"]))
    (base / "INDEX.tsv").write_text("\n".join(rows) + "\n")
    return base


def _hooks(tmp_path, name, script):
    d = tmp_path / name
    d.mkdir()
    (d / "session_anchor.sh").write_text(script)
    (d / "session_anchor.sh").chmod(0o755)
    (d / "helper.txt").write_text("helper\n")
    (d / "__pycache__").mkdir()
    (d / "__pycache__" / "junk.pyc").write_bytes(b"\0")
    return d


def _capture(tmp_path):
    base = _mini_baseline(tmp_path)
    ws = tmp_path / "real_root"
    (ws / "governance").mkdir(parents=True)
    (ws / "governance" / "machine_config.yaml").write_text("interpreters: {}\n")
    hb, ha = _hooks(tmp_path, "hooks_before", BEFORE_HOOK), _hooks(tmp_path, "hooks_after", AFTER_HOOK)
    scratch = tmp_path / "scratch"
    p = cli("--capture", base, "--workspace", ws, "--hooks-before", hb, "--hooks-after", ha, "--renderer", RENDERER,
            "--scratch", scratch, timeout=120)
    return base, ws, hb, ha, scratch, p


def test_capture_smoke_layout_normalisation_and_meta(tmp_path):
    base, ws, hb, ha, scratch, p = _capture(tmp_path)
    assert p.returncode == 0, p.stderr.decode("utf-8", "replace")
    # scratch layout
    for arm in ("before", "after"):
        root = scratch / arm
        hook = root / ".claude" / "hooks" / "session_anchor.sh"
        assert hook.is_file() and stat.S_IMODE(hook.stat().st_mode) == 0o755
        assert (root / ".claude" / "hooks" / "helper.txt").is_file()
        assert not (root / ".claude" / "hooks" / "__pycache__").exists()
        assert (root / ".claude" / "settings.json").read_text() == "{}\n"
        assert (root / "governance" / "OPEN_WORK.md").read_text() == "open work\n"
        assert (root / "_skills" / "SKILLS_INDEX.md").read_text() == "skills\n"
        assert (root / "governance" / "machine_config.yaml").read_text() == "interpreters: {}\n"
        assert not (root / "governance" / "routing_policy.yaml").exists()
        for slug, rel, status, manifest in MINI:
            assert (root / rel / "NEOCORTEX" / "STATUS.md").read_bytes() == status.read_bytes()
            assert (root / rel / "NEOCORTEX" / "MANIFEST.json").read_bytes() == manifest.read_bytes()
    assert (scratch / "after" / "governance" / "adapters" / "claude-code" / "anchor_render.py").read_bytes() == RENDERER.read_bytes()
    assert not (scratch / "before" / "governance" / "adapters").exists()          # today's tree has no renderer
    # outputs and path normalisation
    for slug, rel, status, manifest in MINI + [("_workspace_root", ".", None, None)]:
        today = (base / slug / "anchor_today.txt").read_text(encoding="utf-8")
        view = (base / slug / "anchor_view.txt").read_text(encoding="utf-8")
        cwd = str(ws) if rel == "." else "%s/%s" % (ws, rel)
        assert today.split("\n")[0] == "anchor for " + cwd and view.split("\n")[0] == "anchor for " + cwd
        assert str(scratch) not in today + view
        meta = json.loads((base / slug / "render_meta.json").read_text(encoding="utf-8"))
        assert set(meta) == META_KEYS
        assert meta["bytes_before"] == len(today.encode("utf-8")) and meta["bytes_after"] == len(view.encode("utf-8"))
        assert meta["hook_rc_before"] == 0 and meta["hook_rc_after"] == 0 and meta["renderer_failed_line"] is False
    # governance row: a marked STATUS and a mixed manifest
    meta = json.loads((base / "governance" / "render_meta.json").read_text(encoding="utf-8"))
    status_raw, manifest_raw = STATUS_MARKED.read_bytes(), (FIX / "manifest_mixed.json").read_bytes()
    reference = tmp_path / "reference"
    ref_text = text_of(render(make_project(reference, status=status_raw, manifest=manifest_raw)))
    assert meta["status_mode"] == "rendered" and meta["binding_blocks"] == 3 and meta["renderer_rc"] == 0
    assert meta["status_bytes_before"] == len(b"--- NEOCORTEX/STATUS.md ---\n") + len(status_raw)
    assert meta["manifest_bytes_before"] == len(b"--- NEOCORTEX/MANIFEST.json ---\n") + len(manifest_raw)
    assert meta["status_bytes_after"] == len(element(ref_text, "STATUS.md").encode("utf-8"))
    assert meta["manifest_bytes_after"] == len(element(ref_text, "MANIFEST.json").encode("utf-8"))
    # plain STATUS + empty manifest: fallback, renderer rc 2
    meta = json.loads((base / "Design Projects__Hotter" / "render_meta.json").read_text(encoding="utf-8"))
    assert meta["status_mode"] == "fallback" and meta["binding_blocks"] == 0 and meta["renderer_rc"] == 2
    # the workspace root row: no NEOCORTEX, no renderer run
    meta = json.loads((base / "_workspace_root" / "render_meta.json").read_text(encoding="utf-8"))
    assert meta["status_mode"] == "absent" and meta["renderer_rc"] is None
    assert meta["status_bytes_before"] == meta["status_bytes_after"] == meta["manifest_bytes_after"] == 0
    # provenance sits inside the baseline dir
    prov = json.loads((base / "capture_provenance.json").read_text(encoding="utf-8"))
    assert prov["renderer_sha256"] == hashlib.sha256(RENDERER.read_bytes()).hexdigest()


def test_capture_refuses_an_existing_scratch_and_a_second_run_changes_nothing(tmp_path):
    base, ws, hb, ha, scratch, p = _capture(tmp_path)
    assert p.returncode == 0
    before = (base / "governance" / "anchor_view.txt").read_bytes()
    again = cli("--capture", base, "--workspace", ws, "--hooks-before", hb, "--hooks-after", ha, "--renderer", RENDERER,
                "--scratch", scratch)
    assert again.returncode == 1 and again.stdout == b""
    assert again.stderr.startswith(b"anchor_render: scratch already exists:")
    assert (base / "governance" / "anchor_view.txt").read_bytes() == before


def test_table_smoke_rows_gates_are_computed(tmp_path):
    base, ws, hb, ha, scratch, p = _capture(tmp_path)
    assert p.returncode == 0
    pdir = tmp_path / "presence"
    pdir.mkdir()
    for slug, rel, status, manifest in MINI:
        r = cli("--presence", base / slug / "STATUS.md", base / slug / "anchor_view.txt")
        assert r.returncode == 0
        (pdir / ("presence_%s.txt" % slug)).write_bytes(r.stdout)
    out_md = tmp_path / "table.md"
    t = cli("--table", base, "--presence-dir", pdir, "--out", out_md)
    assert t.returncode == 0, t.stderr.decode("utf-8", "replace")
    doc = out_md.read_text(encoding="utf-8")
    rows = [l for l in doc.split("\n") if l.startswith("| ") and "---" not in l]
    assert len(rows) == 1 + 3                                         # header + 2 projects + the workspace root
    assert all(len(l.strip("|").split(" | ")) == 13 for l in rows)
    assert any(l.startswith("| **governance** |") for l in rows)     # the heavy seven are bold
    assert any(l.startswith("| Design Projects/Hotter |") for l in rows)

    metas = {slug: json.loads((base / slug / "render_meta.json").read_text(encoding="utf-8"))
             for slug in ("governance", "Design Projects__Hotter", "_workspace_root")}
    rels = {"governance": "governance", "Design Projects__Hotter": "Design Projects/Hotter", "_workspace_root": "."}
    g2a = [l for l in doc.split("\n") if "**G2(a)**" in l][0]
    today, view = metas["governance"]["bytes_before"], metas["governance"]["bytes_after"]
    assert "%d of %d B = %.1f%%" % (today - view, today, 100.0 * (today - view) / today) in g2a
    assert "1 of 7 heavy rows" in g2a and g2a.endswith("FAIL")        # only one of the seven is in the mini INDEX
    g2b = [l for l in doc.split("\n") if "**G2(b)**" in l][0]
    for slug, meta in metas.items():
        if slug != "_workspace_root":
            assert (rels[slug] in g2b) == (meta["bytes_after"] > meta["bytes_before"])
    g1 = [l for l in doc.split("\n") if "**G1**" in l][0]
    assert "Σ missing = 0 over 2 projects (1 with BINDING markers, 1 vacuous)" in g1 and g1.endswith("PASS")
    for key, label in (("bytes_before", "today"), ("bytes_after", "view")):
        lo, hi = sorted(m[key] for s, m in metas.items() if s != "_workspace_root")
        mid = (lo + hi) / 2.0
        mid_text = "%d" % mid if mid == int(mid) else "%.1f" % mid
        assert "- %s anchor bytes: min %d / median %s / max %d" % (label, lo, mid_text, hi) in doc
    assert "renderer sha256: " + hashlib.sha256(RENDERER.read_bytes()).hexdigest() in doc
    assert "baseline INDEX.tsv sha256: " + hashlib.sha256((base / "INDEX.tsv").read_bytes()).hexdigest() in doc

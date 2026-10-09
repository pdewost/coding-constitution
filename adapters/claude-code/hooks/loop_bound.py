#!/usr/bin/env python3
"""loop_bound.py — Art. 14 loop bound, mechanized (H9 of PLAN_hooks_outcome_tamper_loopbound_2026-10-05).

Called by pretool_guard.py on every Bash PreToolUse. Returns an `ask` reason, or None.

Art. 14: "After three full fix-verify cycles on a persistent failure, STOP: report the blocker ...".
Until now that sentence was prose. OpenAI's Hugging Face incident report (26 Aug 2026) names
persistence on impossible tasks as a root cause of the escalation, and answers it with a SAFE STOP.
This module makes the stop mechanical and makes it a question to the founder, not a refusal:

- Only when the INCOMING command runs a verifier (verifier_recognizer) is the transcript read at all.
- Scope = the current turn (after the founder's last prompt): a founder message resets everything.
- A run FAILS if its tool_result is_error, or its output carries a runner's failure summary.
  Its SIGNATURE = sha1 of the first 40 output lines after normalising digits, hex, paths, times.
- A CYCLE = a failing run with signature S preceded, since the previous failure with S, by >= 1 edit.
  The first red run is free (TDD). A changed signature starts its own count (Art. 14: re-diagnose).
- Ask when one signature reaches CYCLE_LIMIT (3, the L0 number) or the turn reaches TOTAL_CAP failing
  runs without a single passing one (signature churn). TOTAL_CAP = PLAN decision D4, default 6.
- A run that happened while the threshold was already reached was approved by the founder: counts
  reset there. A run the founder DENIED leaves an is_error result whose text says so; it is ignored.

Fails OPEN: pretool_guard imports this inside its own try; any error here is logged, never asked.
Stdlib only, Python 3.9-compatible.
"""
from __future__ import annotations

import hashlib
import json
import os
import re

import sys as _sys
# B4-35 (reviewer A round 4): use the verifier_recognizer the hook loaded FROM SOURCE; never fall back to the normal
# importer (which would accept an unchecked cached .pyc).
vr = _sys.modules.get("verifier_recognizer")
if vr is None:
    raise ImportError("verifier_recognizer must be loaded by the hook before loop_bound")

CYCLE_LIMIT = 3
TOTAL_CAP = 6          # PLAN D4
TAIL_BYTES = 8 * 1024 * 1024
EDIT_TOOLS = ("Edit", "Write", "NotebookEdit", "MultiEdit")
BLIND_CAP = TOTAL_CAP  # B2-27: verification runs whose outcome was hidden, without a pass


def _is_edit_tool(name: str) -> bool:
    """B2-28: MCP file tools (`mcp__filesystem__edit_file`, `…__write_file`) are fixes too."""
    if name in EDIT_TOOLS:
        return True
    return name.startswith("mcp__") and bool(re.search(r"(edit|write)", name.split("__")[-1], re.I))
# Seat B B-22: a refusal is recognised only by how the harness STARTS its refusal text, and never when
# the text carries a runner's failure summary (a test asserting "permission to use" is still a failure).
DENIAL_PREFIXES = ("the user doesn't want to proceed", "user denied", "permission to use", "hook denied",
                   "operation blocked by hook", "pretooluse:")
DENIAL_MARKERS = DENIAL_PREFIXES                     # kept for the replay script's import


def _is_denial(text: str) -> bool:
    t = text.lstrip().lower()
    if t.startswith("exit code"):
        return False
    return t.startswith(DENIAL_PREFIXES) and vr.output_verdict(text) != "fail"

_NORM = [
    (re.compile(r"/[^\s:'\"()]+"), "<path>"),
    (re.compile(r"\b0x[0-9a-fA-F]+\b|\b[0-9a-f]{7,}\b"), "<hex>"),
    (re.compile(r"\d+(\.\d+)?\s*(ms|s|sec|seconds)\b"), "<dur>"),
    (re.compile(r"\d{1,4}[-:/]\d{1,2}[-:/]\d{1,4}([ T]\d{1,2}:\d{2}(:\d{2})?)?"), "<time>"),
    (re.compile(r"\d+"), "<n>"),
]


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content if isinstance(b, dict))
    return ""


def signature(output: str) -> tuple:
    """(sha1, first meaningful line) of a failing run's output."""
    lines = vr._normalise(output).splitlines()       # B2-28: colour codes do not split a signature
    code = ""
    if lines and re.match(r"^Exit code \d+", lines[0]):
        code = lines[0].strip()               # Seat A F14: Art. 14 lists the exit code in the signature
        lines = lines[1:]
    body = [code] if code else []
    for ln in lines[:40]:
        for rx, rep in _NORM:
            ln = rx.sub(rep, ln)
        ln = ln.strip()
        if ln:
            body.append(ln)
    first = next((l for l in body if re.search(r"(error|fail|assert|exception)", l, re.I)), body[0] if body else "")
    return hashlib.sha1("\n".join(body).encode()).hexdigest()[:12], first[:160]


def _read_tail(tpath: str) -> list:
    """Parsed JSON entries of the last TAIL_BYTES of the transcript (first partial line dropped)."""
    if not tpath or not isinstance(tpath, str):
        return []
    fd = os.open(tpath, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as fh:
        size = os.fstat(fh.fileno()).st_size
        start = max(0, size - TAIL_BYTES)
        fh.seek(start)
        data = fh.read()
    lines = data.split(b"\n")
    if start > 0:
        fh_prev = os.open(tpath, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            os.lseek(fh_prev, start - 1, os.SEEK_SET)
            at_boundary = os.read(fh_prev, 1) == b"\n"
        finally:
            os.close(fh_prev)
        if not at_boundary:
            lines = lines[1:]                  # Seat A F15: drop only a genuinely partial first line
    out = []
    for ln in lines:
        try:
            out.append(json.loads(ln))
        except Exception:
            continue
    return out


def _is_human_prompt(entry: dict) -> bool:
    if entry.get("type") != "user" or entry.get("isMeta") or entry.get("isSidechain"):   # Seat A F15
        return False
    c = (entry.get("message") or {}).get("content")
    if isinstance(c, str):
        return bool(c.strip())
    if isinstance(c, list):
        return any(isinstance(b, dict) and b.get("type") == "text" for b in c) and \
            not any(isinstance(b, dict) and b.get("type") == "tool_result" for b in c)
    return False


def _last_outcome(text: str) -> str:
    """'pass' / 'fail' / 'empty' / '' — whichever runner outcome line comes LAST in the output."""
    t = vr._normalise(text)
    # B5-22 (reviewer A round 5): unittest's `Ran N tests` + `OK` and TAP's `# pass N` + `# fail 0` are passes too
    extra = [m.end() for m in vr._UNITTEST_OK.finditer(t) if vr._UNITTEST_RAN.search(t, 0, m.start())]
    extra += [m.end() for m in vr._TAP_FAIL0.finditer(t) if vr._TAP_PASS.search(t, 0, m.start())]
    pos = {"pass": max([m.end() for rx in vr._SUMMARY_PASS for m in rx.finditer(t)] + extra + [-1]),
           "fail": max([m.end() for m in vr._STRONG_FAIL.finditer(t)] + [-1]),
           "empty": max([m.end() for m in vr._EMPTY_RE.finditer(t)] + [-1])}
    best = max(pos, key=pos.get)
    return best if pos[best] >= 0 else ""


_SELECT_RE = None


_FAILING_FILE_RE = None


def _failing_files(text: str) -> tuple:
    """G6-03 (Gemini round 6): the test files a red run NAMES as failing (`FAILED tests/x.py::t`, `ERROR tests/x.py`,
    jest/vitest `FAIL src/a.test.ts`)."""
    import re
    global _FAILING_FILE_RE
    if _FAILING_FILE_RE is None:
        _FAILING_FILE_RE = re.compile(r"^(?:FAILED|ERROR|FAIL)\s+([^\s:]+\.(?:py|js|jsx|ts|tsx|mjs|cjs|rb|go|rs|swift))"
                                      r"(?=::|\s|$)", re.M)
    return tuple(sorted(set(_FAILING_FILE_RE.findall(vr._normalise(text or "")))))


def _identity(cmd: str, runs: list, text: str = "") -> tuple:
    """B5-12 (reviewer A round 5): which tests a run covers: (runner kind, explicit paths, selections such as `-k`, `-m`,
    `--grep`, `-t`, `-run`, `--filter` or a `::node` id). No paths and no selection = the runner's whole suite."""
    import re
    global _SELECT_RE
    if _SELECT_RE is None:
        _SELECT_RE = re.compile(r"(?:^|\s)(?:-k|-m|--grep|-g|-t|--testNamePattern|-run|--filter|--test-name-pattern)"
                                r"(?:\s+|=)(\S+)|(\S+::\S+)")
    last = runs[-1] if runs else {"kind": "", "paths": []}
    kind = last["kind"].split(":")[-1]
    kind = {"py-pytest": "pytest"}.get(kind, kind)
    # replay 15: `python3 -m pytest` names the runner, it is not pytest's `-m MARKER` selection
    bare = re.sub(r"(?:^|\s)\S*python[\d.]*(?:\s+-[^m\s]\S*)*\s+-m\s+\S+", " ", cmd)
    sel = tuple(sorted(a or b for a, b in _SELECT_RE.findall(bare)))
    return (kind, tuple(sorted(str(p).split("::")[0] for p in last.get("paths") or [])), sel,
            _failing_files(text))                # G6-03: empty for a pass


def run_events(cmd: str, runs: list, is_err: bool, text: str, extra_prefixes=()) -> list:
    """Events for one finished Bash call that ran verifier(s) (shared with replay_batch2.py)."""
    masked = not all(r["observable"] for r in runs)
    verdict = vr.output_verdict(text, masked=masked)
    if not is_err and verdict not in ("fail", "empty"):
        if all(r["kind"].split(":")[-1] in vr.SYNTAX_KINDS for r in runs):
            return [("call",)]                           # B4-20: a syntax check passing is no progress on a test failure
        plain, _w = vr.plain_verifier(cmd, extra_prefixes)
        if plain is not None:
            ok = vr.plain_verdict(plain["kind"], text) == "pass"
        else:
            # B2-26: a pass is a runner's VISIBLE complete summary (masked or not: the loop bound needs evidence of
            # progress, not a clearing proof), or exit 0 of unmasked checks that print none. Replay 8: counting
            # visible-pass masked runs as blind produced most of the new asks.
            # B3-22: `shell-c:pytest` is still pytest (a summary-printing runner)
            ok = verdict == "pass" or (not masked and all(r["kind"].split(":")[-1] not in vr.SUMMARY_KINDS
                                                          for r in runs))
        # ("call",) marks a new tool call: only a LATER call can be the founder-approved run past a threshold
        # (B3-19). B3-20: every hidden run is charged, not one per call.
        return [("call",)] + ([("run", True, "", "", _identity(cmd, runs))] if ok else [("blind",)] * len(runs))
    sig, first = signature(text)
    ident = _identity(cmd, runs, text)
    nfail = sum(1 for _ in vr._STRONG_FAIL.finditer(vr._normalise(text)))
    if not is_err and runs[-1]["observable"] and len(runs) > 1:
        # B3-21: exit 0 with an observable LAST run (`pytest; fix; pytest`): earlier runs failed as many times as the
        # output shows. B4-21: the last run is a PASS only when the last runner outcome in the output is a pass.
        tail = [("run", True, "", "", ident)] if _last_outcome(text) == "pass" else []
        return [("call",)] + [("run", False, sig, first, ident)] * max(1, min(len(runs) - 1, nfail)) + tail
    # B-33 / B3-19: every verifier run of a failing call counts as failed (the output may show fewer summaries)
    return [("call",)] + [("run", False, sig, first, ident)] * len(runs)


def turn_events(entries: list, write_targets=None, extra_prefixes=()) -> list:
    """Ordered events of the current turn: ('edit',), ('run', ok, sig, first_line)."""
    start = 0
    for i, e in enumerate(entries):
        if _is_human_prompt(e):
            start = i + 1
    results = {}
    for e in entries[start:]:
        for b in ((e.get("message") or {}).get("content") or []):
            if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("tool_use_id"):
                results[b["tool_use_id"]] = (b.get("is_error") is True, _text_of(b.get("content")))
    events = []
    for e in entries[start:]:
        if e.get("type") != "assistant":
            continue
        for b in ((e.get("message") or {}).get("content") or []):
            if not isinstance(b, dict) or b.get("type") != "tool_use":
                continue
            name, ti, tid = b.get("name", ""), b.get("input") or {}, b.get("id")
            res = results.get(tid)
            if _is_edit_tool(name):
                if res is None or not res[0]:
                    events.append(("edit",))
                continue
            if name != "Bash":
                continue
            cmd = str(ti.get("command", ""))
            runs = vr.analyse(cmd, extra_prefixes)
            if res is not None and res[0] and _is_denial(res[1]):
                continue                          # B3-23: refused before it ran: neither an edit nor a run
            if runs and write_targets is not None:   # even when the test then failed: the write happened
                try:
                    if write_targets(cmd):
                        events.append(("edit",))   # Seat A F8: the write half of `cat > x … && pytest`
                except Exception:
                    pass
            if runs:
                if res is None:
                    continue                      # no result yet (this very call, or interrupted)
                is_err, text = res
                if is_err and _is_denial(text):
                    continue                      # the founder or a hook refused it: never ran
                events.extend(run_events(cmd, runs, is_err, text, extra_prefixes))
            elif write_targets is not None:
                try:
                    if write_targets(cmd):
                        events.append(("edit",))
                except Exception:
                    pass
    return events


COVER_MODE = "middle"     # founder ruling 2026-10-06 (B5-12): replay 16 asks any 116 / kind 155 / middle 225 / strict 267


def _covers(p, f, mode: str) -> bool:
    """B5-12: does a pass with identity `p` end a failure with identity `f`? (identity = runner, paths, selection)
    "strict": same identity, or `p` is that runner's whole suite. "middle": also when `f` was the whole suite (a
    targeted green after a suite red), or `p` runs the same or more paths without a narrower selection. "kind": any
    green of the same runner. "any": any green (the pre-round-5 rule). Unknown identities always cover."""
    if p is None or f is None or mode == "any" or p[:3] == f[:3]:
        return True
    if p[0] != f[0]:
        return False
    if mode == "kind" or (not p[1] and not p[2]):
        return True
    if mode == "middle":
        # G6-03 (founder ruling "refine middle"): a targeted green ends a WHOLE-SUITE red only if it runs every test
        # file that red run named as failing; a red that names none is ended only by its suite (or the same run)
        named = f[3] if len(f) > 3 else ()
        # N3 (v0.12, Codex re-check seat C2): a green that SELECTS inside those files (`file.py::other`, `-k x`) is not it
        return (not f[1] and not f[2] and bool(named) and not p[2] and set(p[1]) >= set(named)) or \
            (bool(f[1]) and set(p[1]) >= set(f[1]) and (not p[2] or p[2] == f[2]))
    return False


def evaluate(events: list, mode: str = None) -> tuple:
    """(reason or None) for a NEW verifier run about to start after `events`."""
    mode = mode or COVER_MODE
    counts, last_fail_edit = {}, {}
    total_fail, edits_since, blind = 0, {}, 0
    first_line = {}
    threshold = None
    fail_ident = {}                           # B5-12: the identity of the verifier each failure came from

    def covers(p, f):
        return _covers(p, f, mode)
    for ev in events:
        if ev[0] == "edit":
            for s in edits_since:
                edits_since[s] += 1
            continue
        if ev[0] == "call":
            if threshold is not None:             # a LATER call ran past the threshold: the founder approved it
                counts, edits_since, total_fail, blind, threshold = {}, {}, 0, 0, None
            continue
        if ev[0] == "blind":
            blind += 1
            if blind >= BLIND_CAP:
                threshold = ("blind", "")
            continue
        _, ok, sig, first = ev[:4]
        ident = ev[4] if len(ev) > 4 else None
        if ok:
            # Seat B B-24: a green run ends the failure episode — earlier cycles must not count
            # against the next, unrelated red (Art. 14 bounds a PERSISTENT failure). B4-37: including a threshold
            # reached earlier in the same call. B5-12 (reviewer A round 5): only for the failures that green COVERS (same
            # runner and selection, or that runner's whole suite); another test passing ends nothing.
            if all(covers(ident, f) for f in fail_ident.values()):
                counts, edits_since, total_fail, blind, threshold = {}, {}, 0, 0, None
                fail_ident = {}
            else:
                for s2 in [s2 for s2, f in fail_ident.items() if covers(ident, f)]:
                    counts.pop(s2, None)
                    edits_since.pop(s2, None)
                    fail_ident.pop(s2, None)
                    if threshold is not None and threshold[1] == s2:
                        threshold = None
            continue
        fail_ident[sig] = ident
        total_fail += 1
        first_line[sig] = first
        if sig in edits_since and edits_since[sig] > 0:
            counts[sig] = counts.get(sig, 0) + 1
        edits_since[sig] = 0
        if counts.get(sig, 0) >= CYCLE_LIMIT:
            threshold = ("cycles", sig)
        elif total_fail >= TOTAL_CAP:
            threshold = ("cap", sig)
    if threshold is None:
        return None
    kind, sig = threshold
    if kind == "blind":
        return (f"Art. 14 STOP (loop bound): {BLIND_CAP} verification runs in this turn whose outcome could not "
                f"be seen (`| tail`, `|| true`, `; echo`, output sent to a file) and not one visible pass. Run the "
                f"tests on their own so the result shows, or write the blocker report (a line starting BLOCKED:). "
                f"Approve only if the agent should keep trying.")
    what = (f"{CYCLE_LIMIT} fix-verify cycles on the same failure" if kind == "cycles"
            else f"{TOTAL_CAP} failing verification runs in this turn without a single pass")
    return (f"Art. 14 STOP (loop bound): {what} -- \"{first_line.get(sig, '')}\". Before another test "
            f"run the agent writes the blocker report (a line starting BLOCKED: then blocker, diagnosis, "
            f"what was tried with evidence, recommendation). Approve only if the agent should keep trying.")


def check(data: dict, write_targets=None) -> str | None:
    ti = data.get("tool_input") or {}
    cmd = str(ti.get("command", ""))
    cwd = data.get("cwd") or ""
    extra = vr.load_declared([cwd]) if cwd else ()
    if not vr.analyse(cmd, extra):
        return None
    entries = _read_tail(data.get("transcript_path", ""))
    return evaluate(turn_events(entries, write_targets, extra))

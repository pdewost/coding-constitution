#!/usr/bin/env python3
"""verifier_recognizer.py — is this Bash command a VERIFICATION, and is its outcome observable?

Shared by stop_closeout_gate.py (CLOSEOUT-GATE, H1/H2) and loop_bound.py (Art. 14 loop bound, H9).
PLAN_hooks_outcome_tamper_loopbound_2026-10-05.md §3 H1.

WHY THIS EXISTS. The gate used to clear on a SUBSTRING (`VERIFY_MARKERS`): `cat tests/test_x.py`
contains "test_" and cleared it (reviewer A S4-20); so did `ls --dry-run`. And a real verifier whose exit
status is thrown away (`pytest | tail`, `pytest || true`, `pytest; echo done`) produced a tool_result
that is never `is_error`, so a FAILING run cleared the gate too. This module answers three questions
from the command text alone, by tokens (the same shlex(posix=False) technique as pretool_guard.py's
L0 write-guard, for the same reason: a regex cannot tell an operator from a quoted character):
  1. which segments run a verifier (a closed verb list + the project's `.verify/commands`);
  2. whether the command's exit status reflects that verifier (`observable`);
  3. which explicit paths the verifier was pointed at (for the project-root relatedness check).
And one question from the tool OUTPUT: `output_verdict()` (pass / fail / empty / unknown), used when the
exit status was masked, and to refuse a run that collected no tests.

Pure functions, stdlib only, Python 3.9-compatible (hooks may run under /usr/bin/python3).
"""
from __future__ import annotations

import fnmatch
import re
import shlex
from pathlib import Path

SEP_OPS = (";", "|", "||", "&&", "&", "|&")
MAX_OPEN_QUOTE_LINES = 200
_PUNCT_RUN = re.compile(r"&&|\|\||\|&|;;|&>>|&>|>>|>&|<&|>\||<<<|<<|[;&|()<>]")


def _split_punct(tok: str) -> list:
    """shlex(punctuation_chars=True) glues adjacent operators into one token (`);`, `)&&`): split them
    back (Seat B B-07/B-12 — `(echo x); cp …` hid the `;`)."""
    if len(tok) > 1 and all(c in "();<>|&" for c in tok):
        return _PUNCT_RUN.findall(tok)
    return [tok]
REDIRECT_OPS = (">", ">>", ">|", "&>", "&>>", "<", "<<", "<<<", ">&", "<&", "2>", "2>>")
WRAPPERS = {"sudo", "doas", "env", "command", "builtin", "exec", "nohup", "time", "nice", "ionice",
            "stdbuf", "caffeinate"}
TIMEOUT_WRAPPERS = {"timeout", "gtimeout"}          # take one positional DURATION before the command
RUNNER_PREFIXES = {"uv", "poetry", "pipenv", "pdm", "hatch", "rye"}   # `<x> run <cmd>`
# B2-40 (reviewer A round 2b): options that TAKE A VALUE, per wrapper, so `nice -n 10 pytest` reaches pytest
# (the value `10` used to be read as the command). Same table shape as pretool_guard's _WRAPPER_OPTS.
WRAPPER_VALUE_OPTS = {
    "sudo": {"-u", "-g", "-C", "-h", "-p", "-r", "-t", "-U", "-D", "-T", "-R"}, "doas": {"-u", "-C"},
    "env": {"-u", "--unset", "-C", "--chdir", "-S", "--split-string", "-P"}, "nice": {"-n", "--adjustment"},
    "ionice": {"-c", "-n", "-p"}, "stdbuf": {"-i", "-o", "-e"}, "time": {"-f", "-o", "--format", "--output"},
    "timeout": {"-s", "--signal", "-k", "--kill-after"}, "gtimeout": {"-s", "--signal", "-k", "--kill-after"},
    "caffeinate": {"-t", "-w"}, "exec": {"-a"}, "command": set(), "builtin": set(), "nohup": set(),
    "npx": {"-p", "--package", "-c", "--call"}, "bunx": {"-p", "--package"},
    "run": {"--with", "--with-editable", "--with-requirements", "--python", "-p", "--project", "--directory",
            "--package", "--extra", "--group", "--env-file", "--index", "--from", "-e", "--env"},
}
TEST_SCRIPT_RE = re.compile(r"^(test_[\w.-]*|[\w.-]*_test|run_tests?|selftest)\.(py|sh|bash|zsh)$")
PY_RE = re.compile(r"^python(\d+(\.\d+)?)?$")
PY_TEST_MODULES = {"pytest", "unittest", "py_compile", "compileall", "doctest"}
NON_TEST_FLAGS = {"--version", "-V", "--help", "-h", "--collect-only", "--co", "--fixtures", "--markers",
                  "--setup-plan", "--setup-only", "--cache-show", "--list-tests", "--listTests", "--showConfig",
                  "-list", "--no-run", "--list", "--fixtures-per-test", "--if-present",   # B4-03
                  "-showBuildSettings", "-dry-run", "-showdestinations", "-showTestPlans", "-showsdks"}   # B5-38
# B2-16: make modes that run no recipe (dry run / question / touch); checked per runner, never globally
# (pytest -n is xdist, pytest -q is quiet).
MAKE_NO_RUN_LONG = {"--dry-run", "--just-print", "--recon", "--question", "--touch", "--ignore-errors"}   # B4-04
MAKE_VALUE_SHORT = set("CfIoWjl")
SHELLS = {"bash", "sh", "zsh", "dash", "ksh"}
PATHLIKE_SUFFIXES = (".py", ".sh", ".bash", ".zsh", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".applescript")


def _unquote(tok: str) -> str:
    if len(tok) >= 2 and tok[0] == tok[-1] and tok[0] in "\"'":
        return tok[1:-1]
    return tok


def tokenize(cmd: str) -> list:
    """shlex tokens with an explicit ';' after each physical line (copied from pretool_guard.py's
    _tokenize: a newline ends a command like ';', a quoted span may cross lines). Raises ValueError
    on an unterminated quote."""
    toks, buf = [], ""
    cmd = re.sub(r"\\\r?\n", " ", cmd)
    for line in cmd.replace("\r", "\n").split("\n"):
        buf = line if not buf else buf + "\n" + line
        lx = shlex.shlex(buf, posix=False, punctuation_chars=True)
        lx.whitespace_split = True
        try:
            toks.extend(list(lx))
        except ValueError:
            if buf.count("\n") > MAX_OPEN_QUOTE_LINES or len(buf) > 8_000:
                raise ValueError("open quote spans too many lines")   # Seat B B-19: no quadratic re-parse
            continue
        toks.append(";")
        buf = ""
    if buf:
        raise ValueError("unterminated quote")
    return [p for t in toks for p in _split_punct(t)]


# B4-15 (reviewer A round 4): the delimiter is the whole shell word (`<<SQL-END`), not its first `\w` run
_HEREDOC_RE = re.compile(r"(?<!<)<<-?\s*(['\"]?)([^\s'\"<>|;&()]+)\1")   # Seat A F12: `<<$E` too


# B2-07 (reviewer A round 2b): `/bin/bash <<EOF`, `bash --noprofile <<EOF`, `bash -s -- <<EOF` run their body too.
_SHELL_FED_RE = re.compile(r"(?:^|[;&|(]\s*|\s)(?:[^\s;&|()<>]*/)?(?:bash|sh|zsh|dash|ksh)(?:\s+-{1,2}[A-Za-z-]*)*\s*<<")


def _heredoc_word(line: str, i: int):
    """B5-05 (reviewer A round 5): the delimiter after `<<`/`<<-` at index i, AFTER shell quote removal, with the index
    past it; None if there is no word. `<<\\EOF`, `<<E"OF"`, `<<'EOF'` and `<< EOF` all end at a line `EOF`."""
    j, n = i + 2, len(line)
    if j < n and line[j] == "-":
        j += 1
    while j < n and line[j] in " \t":
        j += 1
    out = []
    while j < n:
        c = line[j]
        if c in " \t<>|;&()":
            break
        if c == "\\":
            out.append(line[j + 1:j + 2])
            j += 2
            continue
        if c in "'\"":
            k = line.find(c, j + 1)
            if k < 0:
                return None
            out.append(line[j + 1:k])
            j = k + 1
            continue
        out.append(c)
        j += 1
    d = "".join(out)
    return (d, j) if d else None


def _heredoc_delims(line: str, q: str) -> tuple:
    """(delimiters of the REAL `<<` operators of one physical line, quote state after it). reviewer A B3-16: a `<<EOF`
    inside quotes or a comment is text, and must not make the next (executable) lines vanish as heredoc data."""
    out, i, n, word_start = [], 0, len(line), True
    while i < n:
        c = line[i]
        if q:
            if c == "\\" and q == '"':
                i += 2
                continue
            if c == q:
                q = ""
            i += 1
            continue
        if c == "\\":
            i, word_start = i + 2, False
            continue
        if c in "'\"":
            q, i, word_start = c, i + 1, False
            continue
        if c == "#" and word_start:
            break
        if line.startswith("$((", i) or line.startswith("((", i):
            depth, j = 0, i                             # B4-15: `<<` inside arithmetic is a shift, not a heredoc
            while j < n:
                if line[j] == "(":
                    depth += 1
                elif line[j] == ")":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            i, word_start = j + 1, False
            continue
        if line.startswith("<<", i) and not line.startswith("<<<", i) and (i == 0 or line[i - 1] != "<"):
            w = _heredoc_word(line, i)
            if w:
                out.append(w[0])
                i, word_start = w[1], False
                continue
        word_start = c in " \t;&|()"
        i += 1
    return out, q


def strip_heredocs(cmd: str) -> str:
    """Drop heredoc BODIES (the lines up to each delimiter): they are data fed to a command, never
    commands. Without this, a `python3 - <<EOF` body that merely mentions `pytest` or `trash` was
    parsed as running it (P6 replay 2026-10-05: the main source of false governor asks)."""
    lines = cmd.replace("\r", "\n").split("\n")
    out, i, q = [], 0, ""
    while i < len(lines):
        ln = lines[i]
        out.append(ln)
        i += 1
        delims, q = _heredoc_delims(ln, q)
        if not delims:
            continue
        shell_fed = bool(_SHELL_FED_RE.search(ln))   # `bash <<EOF` runs its body: keep it (replay 2026-10-05)
        for d in delims:
            while i < len(lines) and lines[i].strip() != d:
                if shell_fed:
                    out.append(lines[i])
                i += 1
            i += 1
    return "\n".join(out)


def segments_full(cmd: str) -> list:
    """[(words, op_after, raw_tokens, scope_events)] for each NON-EMPTY simple command, in order.
    `words` has redirections and their targets removed; `raw_tokens` keeps them (for write detection);
    `scope_events` lists the subshell parentheses ("(" / ")") met BEFORE this segment, so a caller can
    scope shell options and `cd` to a subshell (Seat B B-12). `op_after` skips parentheses: in
    `(pytest); echo`, pytest is followed by `;`."""
    toks = tokenize(strip_heredocs(cmd))
    raw, cur, pending = [], [], []
    for t in toks:
        if t in ("(", ")"):
            if cur:
                cur.append(t)                       # keep in raw; filtered from words below
            else:
                pending.append(t)
            continue
        if t in SEP_OPS:
            raw.append((cur, t, pending))
            cur, pending = [], []
        else:
            cur.append(t)
    raw.append((cur, None, pending))
    out, carry = [], []
    for toks_, op, pre in raw:
        pre = carry + pre
        closes_inside = [t for t in toks_ if t in ("(", ")")]
        rtoks = [t for t in toks_ if t not in ("(", ")")]
        clean, skip = [], False
        for w in rtoks:
            if skip:
                skip = False
                continue
            if w in REDIRECT_OPS:
                skip = True
                if clean and clean[-1].isdigit():          # the fd number before `>&` / `>`
                    clean.pop()
                continue
            clean.append(w)
        if clean:
            out.append([clean, op, rtoks, pre])
            carry = closes_inside
        else:
            carry = pre + closes_inside
    if out and out[-1][1] != "&":
        out[-1][1] = None                                   # nothing non-empty follows ('&' = background, kept)
    return [tuple(x) for x in out]


def segments(cmd: str) -> list:
    """[(words, op_after)] — see segments_full."""
    return [(w, o) for w, o, _r, _e in segments_full(cmd)]


def _skip_options(words: list, valued: set) -> list:
    """Drop leading options; an option in `valued` also drops its separate value (B2-40).
    `--opt=value` and attached short values (`-n10`) are one word."""
    while words and words[0].startswith("-") and words[0] != "-":
        w = _unquote(words[0])
        words = words[1:]
        if w == "--":
            break
        if w in valued and "=" not in w and words:
            words = words[1:]
    return words


def _strip_wrappers(words: list) -> tuple:
    """Return (effective_words, negated)."""
    negated = False
    while words:
        w0 = _unquote(words[0])
        head = w0.split("/")[-1]
        if w0 == "!":
            negated = True
            words = words[1:]
        elif w0 in ("if", "elif", "while", "until", "then", "do", "else", "{"):
            words = words[1:]                               # analyse() marks these as conditional
        elif re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", w0):
            words = words[1:]
        elif head == "command" and len(words) > 1 and _unquote(words[1]) in ("-v", "-V"):
            return [], negated                              # B3-03: a command LOOKUP runs nothing
        elif head in WRAPPERS:
            words = words[1:]
            while words:
                before = len(words)
                words = _skip_options(words, WRAPPER_VALUE_OPTS.get(head, set()))
                if words and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", words[0]):
                    words = words[1:]
                if len(words) == before:
                    break
        elif head in TIMEOUT_WRAPPERS:
            words = _skip_options(words[1:], WRAPPER_VALUE_OPTS[head])
            words = words[1:]                               # the duration
        elif head in RUNNER_PREFIXES and len(words) > 1 and _unquote(words[1]) == "run":
            words = _skip_options(words[2:], WRAPPER_VALUE_OPTS["run"])
        elif head == "npx" or head == "bunx":
            words = _skip_options(words[1:], WRAPPER_VALUE_OPTS[head])
        else:
            break
    return words, negated


def _drop_valued(args: list, valued: set, optional_numeric=frozenset()) -> list:
    """`args` without the given options' separate VALUES (`-C dir` -> `-C`); `--opt=v` stays one word."""
    out, i = [], 0
    while i < len(args):
        a = _unquote(args[i])
        out.append(args[i])
        if a in valued and i + 1 < len(args):
            i += 2
            continue
        if a in optional_numeric and i + 1 < len(args) and re.match(r"^\d+$", _unquote(args[i + 1])):
            i += 2
            continue
        i += 1
    return out


def _nonflags(args: list) -> list:
    return [_unquote(a) for a in args if not a.startswith("-")]


def _looks_like_path(a: str) -> bool:
    a = a.split("::")[0]
    return "/" in a or a.endswith(PATHLIKE_SUFFIXES)


def _is_test_script(path: str) -> bool:
    return bool(TEST_SCRIPT_RE.match(_unquote(path).split("/")[-1]))


def verifier_kind(words: list, extra_prefixes=(), strip: bool = True) -> tuple:
    """(kind, explicit_paths) if `words` (one simple command) runs a verifier, else (None, []).
    strip=False: `words` are already the effective command (the closed grammar stripped its own wrappers)."""
    if strip:
        words, _neg = _strip_wrappers(list(words))
    if not words:
        return None, []
    joined = " ".join(_unquote(w) for w in words)
    for p in extra_prefixes:
        if p and (joined == p or joined.startswith(p + " ")):
            rest = words[len(p.split()):]
            rflags = {_unquote(a).split("=")[0] for a in rest if a.startswith("-")}
            if rflags & (NON_TEST_FLAGS | MAKE_NO_RUN_LONG) or (p.split()[0] == "make" and any(
                    re.match(r"^-[A-Za-z]*[nqti]", _unquote(a)) and not a.startswith("--") for a in rest)):
                return None, []                              # B4-04: the no-run checks apply to declared commands too
            return "declared", [a for a in _nonflags(rest) if _looks_like_path(a)]
    verb = _unquote(words[0]).split("/")[-1]
    args = words[1:]
    nf = _nonflags(args)
    flags = {_unquote(a).split("=")[0] for a in args if a.startswith("-")}
    if flags & NON_TEST_FLAGS:
        return None, []                                     # Seat B B-13: --version / --collect-only / --list-tests …
    if verb in ("pytest", "py.test"):
        return "pytest", [a.split("::")[0] for a in nf if _looks_like_path(a)]
    if PY_RE.match(verb):
        mi = args.index("-m") if "-m" in args else len(args)
        pre = [_unquote(a) for a in args[:mi]]
        if "-c" in pre:                                      # B4-31: a `-c` AFTER `-m pytest` is pytest's option
            return None, []
        if any(re.match(r"^-[A-Za-z]*O", a) and not a.startswith("--") for a in pre):
            return None, []                                  # B4-02: -O/-OO remove the assert statements
        if "-m" in args:
            i = args.index("-m")
            mod = _unquote(args[i + 1]) if i + 1 < len(args) else ""
            last = mod.split(".")[-1]
            if mod.startswith(("tests.", "test.")) or last.startswith("test_") or last.endswith("_test"):
                return "py-test-module", []                 # `python -m tests.test_api` runs a test module
            if mod in PY_TEST_MODULES:
                rest = _nonflags(args[i + 2:])
                return "py-" + mod, [a.split("::")[0] for a in rest if _looks_like_path(a)]
            return None, []
        if nf and _is_test_script(nf[0]):
            return "py-test-script", [nf[0]]                 # B4-02: Python test files need a runner summary
        return None, []
    if verb in SHELLS:
        flags_before = []
        for a in args:
            if not a.startswith("-"):
                break
            flags_before.append(a)
        if any(re.match(r"^-[a-zA-Z]*n[a-zA-Z]*$", f) for f in flags_before):
            return "shell-syntax", [a for a in nf if _looks_like_path(a)]
        if nf and _is_test_script(nf[0]):
            return "test-script", [nf[0]]
        return None, []
    if verb == "osacompile":
        return "osacompile", [a for a in nf if a.endswith(".applescript")]
    if verb in ("npm", "pnpm", "yarn", "bun"):
        # B3-30 (reviewer A round 3): global options with values (`npm --prefix web test`, `yarn --cwd web test`)
        nf = _nonflags(_drop_valued(args, {"--prefix", "-C", "--dir", "--cwd", "--workspace", "-w", "--filter", "-F"}))
        if nf[:1] in (["test"], ["t"]) or (nf[:1] == ["run"] and len(nf) > 1 and nf[1].startswith("test")):
            return "pkg-test", []                           # a package script: whatever it runs
        return None, []
    if verb == "playwright":
        return ("js-test", [a for a in nf[1:] if _looks_like_path(a)]) if nf[:1] == ["test"] else (None, [])
    if verb == "vitest" and nf[:1] == ["list"]:
        return None, []
    if verb in ("jest", "vitest", "mocha", "ava"):
        return "js-test", [a for a in nf if _looks_like_path(a)]
    if verb == "tsc" and "--noEmit" in args:
        return "tsc", []
    if verb == "node" and "--test" in args:
        return "node-test", [a for a in nf if _looks_like_path(a)]
    if verb == "node" and ("--check" in args or "-c" in args):
        return "node-check", [a for a in nf if _looks_like_path(a)]
    if verb == "swift" and nf[:2] == ["test", "list"]:
        return None, []
    if verb == "cargo" and nf[:1] == ["check"]:
        return "cargo-check", []
    if verb in ("go", "cargo", "swift") and nf[:1] == ["test"]:
        return verb + "-test", []
    if verb == "xcodebuild" and "test" in nf:
        return "xcodebuild-test", []
    if verb == "make":
        nf = _nonflags(_drop_valued(args, {"-C", "-f", "-I", "-o", "-W", "--directory", "--file", "--makefile",
                                           "--include-dir", "--old-file", "--what-if", "-l", "--load-average"},
                                    optional_numeric={"-j", "--jobs"}))
    if verb == "make" and nf[:1] in (["test"], ["check"]):
        for a in (_unquote(x) for x in args if x.startswith("-")):
            if a.split("=")[0] in MAKE_NO_RUN_LONG:
                return None, []
            if not a.startswith("--"):
                for ch in a[1:]:
                    if ch in MAKE_VALUE_SHORT:
                        break                           # the rest of the cluster is that option's value
                    if ch in "nqti":                         # B4-04: -i ignores recipe failures
                        return None, []
        return "make-test", []
    if _is_test_script(words[0]):
        # B5-03 (reviewer A round 5): `./tests/test_api.py`, `uv run tests/test_api.py`: a Python test file needs a summary
        return ("py-test-script" if _unquote(words[0]).endswith(".py") else "test-script"), [_unquote(words[0])]
    return None, []


def _apply_set(words: list, pipefail: bool, errexit: bool) -> tuple:
    """Shell options after one `set` command, in order and with polarity (Seat A F11):
    `set -o pipefail` / `+o pipefail`, `-e` / `+e`, combined flags such as `-euo pipefail`."""
    w = [_unquote(x) for x in words[1:]]
    i = 0
    while i < len(w):
        a = w[i]
        if a in ("-o", "+o") and i + 1 < len(w):
            on = a[0] == "-"
            if w[i + 1] == "pipefail":
                pipefail = on
            elif w[i + 1] == "errexit":
                errexit = on
            i += 2
            continue
        if re.match(r"^[-+][a-zA-Z]+$", a):
            on = a[0] == "-"
            if "e" in a[1:]:
                errexit = on
            if a.endswith("o") and i + 1 < len(w):
                if w[i + 1] == "pipefail":
                    pipefail = on
                elif w[i + 1] == "errexit":
                    errexit = on
                i += 2
                continue
        i += 1
    return pipefail, errexit


def _shell_c_inner(words: list, extra_prefixes=()) -> list:
    """Runs found inside `bash -c '<cmd>'` / `sh -c` / `zsh -c` (Seat A F6), one level deep."""
    w, _neg = _strip_wrappers(list(words))
    if not w or _unquote(w[0]).split("/")[-1] not in SHELLS:
        return []
    args = w[1:]
    for i, a in enumerate(args):
        if a == "-c" or (re.match(r"^-[a-zA-Z]*c$", a) and not a.startswith("--")):
            if i + 1 < len(args):
                return analyse(_unquote(args[i + 1]), extra_prefixes, _depth=1)
            return []
        if not a.startswith("-"):
            return []
    return []


def analyse(cmd: str, extra_prefixes=(), _depth: int = 0) -> list:
    """One dict per verifier segment: {kind, paths, observable, why, index}. Empty list = no verifier.
    `observable` = the whole command's exit status reflects this verifier's result; `index` = its
    position in segments_full(cmd)."""
    try:
        segs = segments_full(cmd)
    except ValueError:
        return []
    conditional = any(w and _unquote(w[0]) in ("if", "while", "until", "then", "do") for w, _o, _r, _e in segs)
    pipefail = errexit = False                # evolves in command order (Seat A F11)
    here = ""                                 # "" = the caller's cwd; None = unknown (cd -, cd $X, bare cd)
    stack = []                                # subshell scope (Seat B B-12): options and cwd do not leak out
    runs = []
    for i, (words, op, _raw, events) in enumerate(segs):
        for ev in events:
            if ev == "(":
                stack.append((pipefail, errexit, here))
            elif stack:
                pipefail, errexit, here = stack.pop()
        if words and _unquote(words[0]) == "set":
            pipefail, errexit = _apply_set(words, pipefail, errexit)
            continue
        if words and _unquote(words[0]) in ("cd", "pushd", "popd"):
            arg = [w for w in words[1:] if not w.startswith("-")]
            tgt = _unquote(arg[0]) if arg else ""
            if _unquote(words[0]) == "popd" or not tgt or tgt == "-" or "$" in tgt or "`" in tgt:
                here = None
            elif here is not None:
                here = tgt if tgt.startswith(("/", "~")) else (here + "/" + tgt if here else tgt)
            continue
        kind, paths = verifier_kind(words, extra_prefixes)
        inner_why = ""
        if not kind and _depth == 0:
            inner = _shell_c_inner(words, extra_prefixes)
            if inner:
                kind = "shell-c:" + inner[0]["kind"]
                paths = [p for r in inner for p in r["paths"]]
                if not all(r["observable"] for r in inner):
                    inner_why = "inside the -c string: " + next(r["why"] for r in inner if not r["observable"])
        if not kind:
            continue
        if here is None:
            paths = []              # unknown directory: no relatedness claim (clears like a bare run)
        elif here:
            paths = [p if p.startswith(("/", "~")) else here + "/" + p for p in paths]
        _w, negated = _strip_wrappers(list(words))
        why = ""
        if inner_why:
            why = inner_why
        elif negated:
            why = "negated with '!'"
        elif conditional:
            why = "inside if/while (exit status consumed by the condition)"
        elif i > 0 and segs[i - 1][1] == "||":
            why = "runs only if the previous command FAILED ('||' before it)"
        else:
            # Seat B B-11: errexit does NOT stop the shell for a command inside an && / || list (other
            # than the last), so `set -e; pytest && x; echo done` still swallows a failing pytest.
            in_list = i > 0 and segs[i - 1][1] in ("&&", "||")
            j = i
            while j < len(segs) and segs[j][1] is not None:
                o = segs[j][1]
                if o == "&&":
                    in_list = True
                    j += 1
                    continue
                if o in ("|", "|&") and pipefail:
                    j += 1
                    continue
                if o == ";" and errexit and not in_list:
                    j += 1
                    continue
                why = {"|": "piped (exit status of the LAST pipe stage, not the verifier)",
                       "|&": "piped (exit status of the LAST pipe stage, not the verifier)",
                       "||": "followed by '||' (failure swallowed)",
                       ";": "followed by ';' (exit status of the next command)",
                       "&": "backgrounded with '&'"}.get(o, "exit status not observable")
                break
        runs.append({"kind": kind, "paths": paths, "observable": not why, "why": why, "index": i})
    return runs


# Verdicts from tool output (Seat B B-14/B-15/B-31, 2026-10-05). Output is normalised first (ANSI
# colour codes removed, carriage returns turned into line breaks). PASS needs a COMPLETE runner summary
# line — never a fragment (`2 passed` from `grep -o`, a lone tick, `0 failed` inside an assertion text).
# STRONG failure = a runner's own summary line says something failed (trusted even on exit 0); WEAK
# failure lines are only consulted when the exit status was masked, where "not proven" is the safe answer.
_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07")
# reviewer A round 2b (2026-10-06): a pass needs a POSITIVE count of tests run (B2-19: `0/0 passed`, cargo's
# `0 passed`, Go's `[no tests to run]`, `Executed 0 tests` ran nothing), and a pytest summary line is built
# from pytest's own OUTCOME words only (B2-38: `Application log: 1 failed request in 0.3s` is app prose).
_OUT = r"(?:passed|failed|errors?|skipped|deselected|xfailed|xpassed|warnings?|reruns?)"
_PYTEST_LINE = r"^=*\s*(?:\d+ " + _OUT + r", )*"
_SUMMARY_PASS = [
    re.compile(_PYTEST_LINE + r"[1-9]\d* passed(?:, \d+ " + _OUT + r")* in [\d.]+s\b[^\n]*$", re.M),  # pytest
    re.compile(r"^RESULT: [1-9]\d* passed, 0 failed\b", re.M),                                   # test_hooks.sh
    re.compile(r"^([1-9]\d*)/\1 passed\s*$", re.M),                                              # N/N matrices
    re.compile(r"^Tests:\s+(?:\d+ \w+, )*[1-9]\d* passed, \d+ total", re.M),                      # jest
    re.compile(r"^\s*Tests\s+[1-9]\d* passed\b", re.M),                                          # vitest
    re.compile(r"^\s*[1-9]\d* passed \([\d.]+m?s\)\s*$", re.M),                                   # playwright
    re.compile(r"^\s*[1-9]\d* passing \([\d.]+m?s\)\s*$", re.M),                                  # mocha
    re.compile(r"^ok\s+\S+\s+(?:[\d.]+s|\(cached\))(?![^\n]*\[no tests to run\])", re.M),          # go test
    re.compile(r"^test result: ok\. [1-9]\d* passed", re.M),                                      # cargo
    re.compile(r"^\s*Executed [1-9]\d* tests?, with 0 failures", re.M),                           # swift / xctest
    re.compile(r"^\s*\u2714 Test run with [1-9]\d* tests? passed\b", re.M),                       # Swift Testing (B3-31)
    re.compile(r"^[1-9]\d* passed and 0 failed\.\s*$", re.M),                                    # doctest -v (B5-37)
]
_UNITTEST_RAN = re.compile(r"^Ran ([1-9]\d*) tests? in [\d.]+s\s*$", re.M)
_UNITTEST_OK = re.compile(r"^OK(?: \([^)]*\))?\s*$", re.M)
_TAP_PASS = re.compile(r"^# pass [1-9]\d*\s*$", re.M)                                              # node --test
_TAP_FAIL0 = re.compile(r"^# fail 0\s*$", re.M)
_STRONG_FAIL = re.compile(
    _PYTEST_LINE + r"[1-9]\d* (?:failed|errors?)(?:, \d+ " + _OUT + r")* in [\d.]+s\b"   # pytest summary line
    r"|^FAILED \((?:failures|errors)="                                 # unittest
    r"|^RESULT: \d+ passed, [1-9]\d* failed"                          # test_hooks.sh
    r"|^(\d+)/(?!\1\b)\d+ passed\s*$"                                  # N/M matrices
    r"|^Tests:[^\n]*\b[1-9]\d* failed"                                 # jest
    r"|^test result: FAILED"                                           # cargo
    r"|^\s*Executed \d+ tests?, with [1-9]\d* failures?"               # swift / xctest
    r"|^\s*[1-9]\d* failing\s*$"                                       # mocha
    r"|^# fail [1-9]\d*\s*$"                                           # node --test (TAP)
    r"|^FAIL\s+\S+\s+(?:[\d.]+s|\(cached\))"                           # go test
    r"|^\s*(?:Test Files|Tests)\s+[1-9]\d* failed\b"                       # vitest (B3-04)
    r"|^make(?:\[\d+\])?: \[[^\]]*\] Error \d+ \(ignored\)"                # make -i (B4-04)
    r"|^\s*[1-9]\d* failed\s*$"                                            # playwright (B4-05)
    r"|^Test Suites:[^\n]*\b[1-9]\d* failed\b"                              # jest suites (B4-05)
    r"|^\s*\u2718 Test run with \d+ tests? failed\b",                       # Swift Testing
    re.M,
)
_WEAK_FAIL = re.compile(r"^FAILED\b|^FAIL\b|^ERROR\b|^E\s{3}|Traceback \(most recent call last\)|\bSyntaxError\b"
                        r"|[✗✘❌]|\bFAIL(?:ED)?:", re.M)
_EMPTY_RE = re.compile(r"\bno tests ran\b|\bcollected 0 items\b|^Ran 0 tests\b|^\s*Executed 0 tests\b"
                       r"|^0/0 passed\s*$|^RESULT: 0 passed, 0 failed\b|^Tests:\s+0 passed, 0 total"
                       # B3-04 (reviewer A round 3): explicit no-run outputs of runners called through scripts. Only
                       # consulted when NO positive summary is present (a run with some empty packages still passes).
                       r"|^test result: ok\. 0 passed\b|\[no tests to run\]|\bNo tests found\b"
                       r"|^make(?:\[\d+\])?: (?:Nothing to be done for|'[^']*' is up to date)"
                       r"|^0 tests in \d+ items\.|^No projects matched the filters\b", re.M)   # B5-37, B5-39


def _normalise(text: str) -> str:
    return _ANSI.sub("", text).replace("\r\n", "\n").replace("\r", "\n")


def has_pass_summary(text: str) -> bool:
    t = _normalise(text)
    if any(rx.search(t) for rx in _SUMMARY_PASS):
        return True
    if _TAP_PASS.search(t) and _TAP_FAIL0.search(t):
        return True
    return bool(_UNITTEST_RAN.search(t) and _UNITTEST_OK.search(t)) and not _unittest_all_skipped(t)


def _unittest_all_skipped(t: str) -> bool:
    """B5-11 (reviewer A round 5): `Ran 3 tests` + `OK (skipped=3)`: no test body executed."""
    ran, ok = _UNITTEST_RAN.search(t), _UNITTEST_OK.search(t)
    if not (ran and ok):
        return False
    sk = re.search(r"skipped=(\d+)", ok.group(0))
    return bool(sk) and int(sk.group(1)) >= int(ran.group(1))


def output_verdict(text: str, masked: bool = False) -> str:
    """'empty' (ran but collected/ran nothing), 'fail', 'pass', or 'unknown', from a tool result.
    `masked=True` (exit status not observable) also counts WEAK failure lines; a 'pass' always means a
    complete runner summary was seen."""
    if not text:
        return "unknown"
    t = _normalise(text)
    if _STRONG_FAIL.search(t):
        return "fail"
    if masked and _WEAK_FAIL.search(t):
        return "fail"
    if _unittest_all_skipped(t):
        return "empty"                           # B5-11
    if has_pass_summary(t):                      # B3-31: a positive complete summary outranks an empty sibling suite
        return "pass"
    if _EMPTY_RE.search(t):
        return "empty"
    return "unknown"


# §3a (PLAN v0.7, founder ruling 2026-10-06, option A): H1 = PLAIN VERIFIER ONLY. Three reviewer A rounds kept
# finding new shell spellings of "this compound command's status still reflects the test" (B-11/12/16/33,
# B2-15..19, B2-21). The gate no longer reasons about shell semantics: a Bash call clears only if it is,
# after `cd <literal dir> &&` prefixes and status-preserving wrappers (option values consumed), EXACTLY
# ONE recognised verifier, with no `;` `||` `|` `&`, subshell, substitution or redirect other than 2>&1.
SYNTAX_KINDS = {"shell-syntax", "osacompile", "tsc", "node-check", "py-py_compile", "py-compileall", "cargo-check"}
SUMMARY_KINDS = {"pytest", "py-pytest", "py-unittest", "js-test", "node-test", "go-test", "cargo-test", "swift-test",
                 "py-test-script", "py-test-module",       # B4-02: a Python test file that defines tests runs none
                 "py-doctest", "xcodebuild-test"}          # B5-37 / B5-38: their summaries are recognised
_KEYWORDS = ("!", "if", "elif", "while", "until", "then", "do", "else", "{", "}", "for", "case")


# v0.8 (reviewer A round 3, founder ruling 2026-10-06 "Fix all + reviewer A round 4"): "plain" is a CLOSED GRAMMAR, not a
# reading of shell text. Logical line = the command minus blank and whole-line comment lines, with lines ending in
# `&&` or `\` joined. Words: runs of _PLAIN_SAFE characters, single-quoted text, or double-quoted text without
# `$ ` \ !`. A `#` starts a comment only at the start of a word. Operators: `&&` only after `cd DIR`, `2>&1` only
# last. Anything else (`!`, `;`, `|`, `$`, backticks, `<`, `>`, `(`, a mid-word `#`, ...) is not plain.
# Wrappers: a short fixed list with fixed option forms (_plain_wrappers); help/lookup/split-string modes are not.
_PLAIN_SAFE = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_./:=,@+%-*?[]^~")
_AND, _TWO_TO_ONE = "\x00&&", "\x002>&1"
_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_RUNNER_FLAGS = {"-q", "--quiet", "-v", "--verbose", "--offline", "--no-progress", "--frozen", "--locked",
                 "--no-sync", "--isolated", "--all-extras", "--no-dev"}
_RUNNER_VALUED = {"--directory", "--project", "--python", "-p", "--with", "--with-editable", "--with-requirements",
                  "--extra", "--group", "--env-file", "--index", "--from"}


def _code_part(line: str) -> str:
    """`line` without its comment: an unquoted `#` at the start of a word starts the comment."""
    q, i, word_start = "", 0, True
    while i < len(line):
        c = line[i]
        if q:
            if c == "\\" and q == '"':
                i += 2
                continue
            if c == q:
                q = ""
        elif c in "'\"":
            q = c
        elif c == "\\":
            i += 2
            word_start = False
            continue
        elif c == "#" and word_start:
            return line[:i]
        word_start = c in " \t"
        i += 1
    return line


def _plain_line(cmd: str):
    """The single logical line of `cmd`, or None when it has more than one."""
    lines = cmd.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    kept, cur = [], ""
    for ln in lines:
        if not cur and (not ln.strip() or ln.lstrip().startswith("#")):
            continue                                      # B3-29: blank and whole-line comment lines run nothing
        piece = ln.rstrip()
        code = _code_part(piece).rstrip()                 # B4-01: a comment never supplies a join operator
        if code.endswith("\\"):
            cur += code[:-1] + " "
            continue
        if code.endswith("&&"):
            cur += code + " "
            continue
        cur += piece
        kept.append(cur)
        cur = ""
    if cur.strip():
        kept.append(cur)
    return kept[0] if len(kept) == 1 else None


def _plain_lex(line: str):
    """Words of the closed grammar (operators as sentinels), or None for anything outside it."""
    words, cur, have, i, n = [], [], False, 0, len(line)
    while i < n:
        c = line[i]
        if c in " \t":
            if have:
                words.append("".join(cur))
                cur, have = [], False
            i += 1
        elif c == "#" and not have:
            break                                         # a comment, only at the start of a word (B3-02)
        elif c == "'":
            j = line.find("'", i + 1)
            if j == -1:
                return None
            cur.append(line[i + 1:j])
            have, i = True, j + 1
        elif c == '"':
            j = line.find('"', i + 1)
            if j == -1 or any(ch in line[i + 1:j] for ch in "$`\\!"):
                return None
            cur.append(line[i + 1:j])
            have, i = True, j + 1
        elif not have and line.startswith("&&", i):
            words.append(_AND)
            i += 2
        elif not have and line.startswith("2>&1", i) and (i + 4 == n or line[i + 4] in " \t"):
            words.append(_TWO_TO_ONE)
            i += 4
        elif c in _PLAIN_SAFE:
            cur.append(c)
            have, i = True, i + 1
        else:
            return None
    if have:
        words.append("".join(cur))
    return words


def _plain_wrappers(w: list):
    """Effective words after the fixed wrapper list, or None when a wrapper is used outside its fixed forms."""
    while w:
        h = w[0]
        base = h.split("/")[-1]
        if _ASSIGN.match(h):
            if h.split("=", 1)[0] in ("MAKEFLAGS", "MFLAGS", "GNUMAKEFLAGS"):
                return None                               # B4-04: `MAKEFLAGS=-n make test` runs nothing
            w = w[1:]
        elif base == "env":
            w = w[1:]
            while w and (_ASSIGN.match(w[0]) or w[0] in ("-i", "-u")):
                if _ASSIGN.match(w[0]) and w[0].split("=", 1)[0] in ("MAKEFLAGS", "MFLAGS", "GNUMAKEFLAGS"):
                    return None                           # B5-04: `env MAKEFLAGS=-n make test` runs nothing either
                if w[0] == "-u":
                    if len(w) < 2:
                        return None
                    w = w[2:]
                else:
                    w = w[1:]
            if w and w[0].startswith("-"):
                return None                               # B3-03: `env -S`, `env -C` & co are not plain
        elif base == "nice":
            w = w[1:]
            if w and w[0] == "-n" and len(w) > 1 and re.match(r"^-?\d+$", w[1]):
                w = w[2:]
            elif w and re.match(r"^-(?:n)?-?\d+$", w[0]):
                w = w[1:]
            if w and w[0].startswith("-"):
                return None
        elif base in ("timeout", "gtimeout"):
            w = w[1:]
            while w and w[0].startswith("-"):
                o = w[0]
                if o in ("-s", "-k", "--signal", "--kill-after") and len(w) > 1:
                    w = w[2:]
                elif o.split("=")[0] in ("--signal", "--kill-after") or o in ("--foreground", "--preserve-status"):
                    w = w[1:]
                else:
                    return None
            if not w or not re.match(r"^\d+(\.\d+)?[smhd]?$", w[0]):
                return None
            w = w[1:]
        elif base == "time":
            w = w[1:]
            if w and w[0] == "-p":
                w = w[1:]
            if w and w[0].startswith("-"):
                return None
        elif base == "sudo":
            w = w[1:]
            while w and w[0].startswith("-"):
                if w[0] == "-u" and len(w) > 1:
                    w = w[2:]
                elif w[0] in ("-E", "-n", "-H"):
                    w = w[1:]
                else:
                    return None
        elif base in RUNNER_PREFIXES:
            rest, seen_run = w[1:], False
            while rest:
                o = rest[0]
                if o == "run" and not seen_run:
                    seen_run, rest = True, rest[1:]
                    continue
                if not o.startswith("-"):
                    break
                if o in _RUNNER_FLAGS or o.split("=")[0] in _RUNNER_VALUED and "=" in o:
                    rest = rest[1:]
                elif o in _RUNNER_VALUED and len(rest) > 1:
                    rest = rest[2:]
                else:
                    return None                           # B3-03: `uv run --help` & co
            if not seen_run:
                return None
            w = rest
        elif base in ("npx", "bunx"):
            w = w[1:]
            while w and w[0].startswith("-"):
                if w[0] in ("-y", "--yes", "--no-install", "-q", "--quiet") or w[0].startswith("--package="):
                    w = w[1:]
                elif w[0] in ("-p", "--package") and len(w) > 1:
                    w = w[2:]
                else:
                    return None
        else:
            return w
    return w


def plain_verifier(cmd: str, extra_prefixes=()) -> tuple:
    """(run, why). run = {kind, paths, observable: True, why: "", index} when `cmd` is a plain verifier
    command (§3a, closed grammar v0.8); otherwise (None, short reason)."""
    line = _plain_line(cmd)
    if line is None:
        return None, "more than one command"
    words = _plain_lex(line)
    if words is None:
        bad = next((c for c in line if c not in _PLAIN_SAFE and c not in " \t'\"#&"), "")
        return None, (f"'{bad}' is outside the plain form" if bad else "characters or operators outside the plain form")
    if not words:
        return None, "empty"
    here, k = "", 0
    while k < len(words) and words[k] == "cd":
        if k + 2 >= len(words) or words[k + 2] != _AND or words[k + 1] in (_AND, _TWO_TO_ONE) \
                or words[k + 1].startswith("-"):
            return None, "more than one command"
        tgt = words[k + 1]
        here = tgt if tgt.startswith(("/", "~")) or not here else here + "/" + tgt
        k += 3
    w = words[k:]
    if w and w[-1] == _TWO_TO_ONE:
        w = w[:-1]
    if any(x in (_AND, _TWO_TO_ONE) for x in w):
        return None, "more than one command"
    eff = _plain_wrappers(w)
    if not eff:
        return None, "wrapper outside its plain forms" if eff is None else "empty"
    if eff[0].split("/")[-1] in SHELLS and any(
            a == "-c" or (re.match(r"^-[a-zA-Z]*c[a-zA-Z]*$", a) and not a.startswith("--")) for a in eff[1:]):
        return None, "shell -c string"
    kind, paths = verifier_kind(eff, extra_prefixes, strip=False)
    if not kind:
        return None, "not a recognised verifier"
    if here:
        paths = [p if p.startswith(("/", "~")) else here + "/" + p for p in paths]
    return {"kind": kind, "paths": paths, "observable": True, "why": "", "index": 0}, ""


def plain_verdict(kind: str, text: str) -> str:
    """What a plain verifier's (exit-0) output shows: 'pass', 'fail', 'empty' or 'unknown'. Summary-printing
    runners need a complete summary with a positive count; syntax checkers and repo test scripts pass on
    exit 0 unless a runner's own failure/empty line says otherwise (§3a)."""
    if text.lstrip().startswith("Command running in background"):
        return "unknown"                                     # B4-06: a launch acknowledgement, not an outcome
    v = output_verdict(text, masked=False)
    if v in ("fail", "empty"):
        return v
    if kind == "pkg-test" and not text.strip():
        return "unknown"                                     # B4-03: a real package run prints npm's `> pkg test` header
    if kind.split(":")[-1] in SUMMARY_KINDS:
        return "pass" if v == "pass" else "unknown"
    return "pass"


# Formatters: which invocations WRITE their file operands (reviewer A round 2b B2-18/B2-29/B2-35). Check, diff,
# lint and stdout modes write nothing. pretool_guard.py keeps its own copy of this table (its DENY path
# has no import dependency); test_pretool_h0_h4_h9.py checks the two copies agree.
FORMATTERS_DEFAULT_WRITE = {"black": {"--check", "--diff"}, "isort": {"--check", "--check-only", "-c", "--diff"},
                            "rustfmt": {"--check"}, "swiftformat": {"--lint", "--dryrun"}}
FORMATTERS_WRITE_FLAG = {"autopep8": {"-i", "--in-place"}, "yapf": {"-i", "--in-place"},
                         "prettier": {"--write", "-w"}, "eslint": {"--fix"}, "gofmt": {"-w"}, "goimports": {"-w"},
                         "clang-format": {"-i"}, "shfmt": {"-w", "--write"}, "autoflake": {"-i", "--in-place"},
                         "biome": {"--write", "--apply", "--fix"}}


def formatter_writes(verb: str, args: list):
    """True / False if `verb` is a known formatter (does this invocation write its operands?), else None."""
    flags = {_unquote(a).split("=")[0] for a in args if a.startswith("-")}
    nf = _nonflags(args)
    if verb == "ruff":
        if nf[:1] == ["format"]:
            return not (flags & {"--check", "--diff"})
        return bool(flags & {"--fix"}) and not (flags & {"--diff", "--no-fix"})
    if verb == "rustfmt":
        # B3-27 (reviewer A round 3): `--emit files` WRITES; only `--emit stdout` (and --check) do not
        emit = next((a.split("=", 1)[1] for a in args if a.startswith("--emit=")), None)
        if emit is None and "--emit" in args and args.index("--emit") + 1 < len(args):
            emit = args[args.index("--emit") + 1]
        if "--check" in flags:
            return False
        return emit is None or emit == "files"
    if verb == "dprint":
        return nf[:1] == ["fmt"]
    if verb in FORMATTERS_WRITE_FLAG:
        if verb == "clang-format" and flags & {"--dry-run", "-n"}:
            return False
        return bool(flags & FORMATTERS_WRITE_FLAG[verb]) and "--fix-dry-run" not in flags
    if verb in FORMATTERS_DEFAULT_WRITE:
        return not (flags & FORMATTERS_DEFAULT_WRITE[verb])
    return None


def load_declared(project_roots) -> tuple:
    """`.verify/commands` lines (one command prefix per line, '#' comments) of every given root.
    D2 of the PLAN. Missing/unreadable files contribute nothing."""
    out = []
    for r in project_roots:
        try:
            p = Path(r) / ".verify" / "commands"
            if p.is_file() and not p.is_symlink() and p.stat().st_size < 16384:
                for ln in p.read_text(encoding="utf-8", errors="replace").splitlines():
                    ln = ln.strip()
                    if ln and not ln.startswith("#"):
                        out.append(ln)
        except OSError:
            continue
    return tuple(out)


TEST_DIR_PARTS = {"tests", "test", "__tests__", "fixtures", "spec", "specs", "e2e"}
TEST_BASENAME_GLOBS = ("test_*.py", "*_test.py", "*.spec.*", "*.test.*", "conftest.py", "pytest.ini",
                       "tox.ini", "jest.config.*", "vitest.config.*", "playwright.config.*", ".coveragerc",
                       "test_*.sh", "test_*.ts", "test_*.js")
TEST_CONFIG_IF_DIFF = {"pyproject.toml": re.compile(r"pytest|\[tool\.coverage"),
                       "package.json": re.compile(r'"test[^"]*"\s*:')}


def is_test_path(fp: str) -> bool:
    """Test code, fixtures or test-runner configuration (H2). pyproject.toml / package.json are
    handled separately (only when their diff touches test configuration)."""
    p = Path(fp)
    base = p.name
    if any(fnmatch.fnmatch(base, g) for g in TEST_BASENAME_GLOBS):
        return True
    if any(part in TEST_DIR_PARTS for part in p.parts[:-1]):
        return True
    if ".github" in p.parts and "workflows" in p.parts:
        return True
    return False

#!/bin/bash
# post_push_audit.sh — PostToolUse(Bash) hook filter for the github_commit_audit gate.
#
# Fires the post-push audit instruction ONLY when the executed Bash command actually
# contains a `git push` invocation. Replaces the agent-type hook (retired 2026-06-10)
# whose `if:` condition was evaluated AFTER spawning an audit agent, causing the agent
# to fire on every Bash call (two documented false fires on `find`/`grep` commands
# during the constitution-upgrade session of 2026-06-10).
#
# Contract (Claude Code PostToolUse command hook):
#   stdin  — JSON payload; the executed command is at .tool_input.command
#   exit 0 with no stdout      → silent no-op (non-push commands)
#   stdout {"decision":"block"} → "reason" is fed back to the main agent as an instruction
#
# Detection (S4-45): `git [global options with values] push` as the command of a segment; an
# `echo 'git push'` literal no longer matches. A parse failure falls back to whitespace splitting.

PY=$(command -v python3.12 || command -v python3 || echo /usr/bin/python3)   # S4: bare python3.12 failed open when PATH lacked it
# S4-45 (final, 2026-10-05): tokenized detection. `git` must be the COMMAND of a segment (not an
# argument of echo/grep), and its global options are consumed WITH their values (`-C "a b"`, `-c k=v`,
# `--git-dir=…`) before the subcommand is read. The regex this replaces missed quoted-space paths and
# `-c` values, and fired on `echo 'git push'`.
# N29 (2026-10-26, A2 F-06 + A3 F-09): a segment may open with a shell keyword (`{ git push; }`, `then git push`), and a
# wrapper's options are consumed WITH their values (`env -C dir`, `sudo -u user`) plus timeout's duration (`timeout 60`).
# A subcommand that is not a git builtin is looked up as an alias (`git config --get alias.<name>`, in the payload's cwd
# as moved by an earlier `cd` or by `-C`); an alias that expands to push, or a `!` alias that runs `git push`, fires.
is_push=$("$PY" -c '
import json, os, re, shlex, subprocess, sys
try:
    data = json.load(sys.stdin)
    cmd = data.get("tool_input", {}).get("command", "")
except Exception:
    sys.exit(0)
VAL = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path", "--config-env"}
WRAP = {"sudo", "env", "command", "nohup", "time", "nice", "exec", "builtin", "timeout", "gtimeout", "stdbuf",
        "caffeinate", "xargs"}
WVAL = {"sudo": {"-u", "-g", "-C", "-h", "-p", "-r", "-t", "-U", "-D", "-T", "-R"}, "env": {"-u", "-C", "-S", "-P",
        "--chdir", "--unset", "--split-string"}, "nice": {"-n"}, "timeout": {"-s", "-k", "--signal", "--kill-after"},
        "gtimeout": {"-s", "-k", "--signal", "--kill-after"}, "stdbuf": {"-i", "-o", "-e"}, "time": {"-f", "-o"},
        "caffeinate": {"-t", "-w"}, "exec": {"-a"}, "xargs": {"-I", "-L", "-n", "-P", "-s", "-E", "-J", "-R", "-d", "-a"}}
KW = {"{", "!", "then", "do", "else", "elif", "if", "while", "until"}
BUILTIN = {"add", "am", "apply", "archive", "bisect", "blame", "branch", "bundle", "cat-file", "checkout", "cherry-pick",
           "clean", "clone", "commit", "config", "describe", "diff", "fetch", "format-patch", "fsck", "gc", "grep",
           "help", "init", "log", "ls-files", "ls-remote", "ls-tree", "merge", "mv", "notes", "pull", "rebase",
           "reflog", "remote", "reset", "restore", "rev-list", "rev-parse", "revert", "rm", "show", "stash", "status",
           "submodule", "switch", "tag", "version", "worktree"}
ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def head(w):
    """The segment without its leading keywords, assignments and wrappers (with their option values)."""
    while w:
        name = w[0].split("/")[-1]
        if w[0] in KW or ASSIGN.match(w[0]):
            w = w[1:]
        elif name in WRAP:
            w = w[1:]
            while w and w[0].startswith("-") and w[0] != "-":
                if w[0] == "--":
                    w = w[1:]
                    break
                w = w[2:] if w[0] in WVAL.get(name, ()) else w[1:]
            if name in ("timeout", "gtimeout") and w and re.match(r"^[0-9.]+[smhd]?$", w[0]):
                w = w[1:]
        else:
            break
    return w


def git_sub(argv):
    """(index of the subcommand, directory named by -C) in a git argv."""
    i, d = 1, None
    while i < len(argv) and argv[i].startswith("-"):
        if argv[i] == "-C" and i + 1 < len(argv):
            d = argv[i + 1]
        i += 2 if argv[i] in VAL else 1
    return i, d


def alias_pushes(name, cwd):
    if name in BUILTIN or not re.match(r"^[A-Za-z0-9][A-Za-z0-9._-]*$", name):
        return False                        # git ignores an alias that shadows a builtin
    try:
        r = subprocess.run(["git", "-C", cwd, "config", "--get", "alias." + name], capture_output=True, text=True, timeout=3)
    except Exception:
        return False
    exp = r.stdout.strip()
    if exp.startswith("!"):
        return re.search(r"(^|[\s;&|(])git(\s+-\S+(\s+\S+)?)*\s+push\b", exp[1:]) is not None
    try:
        argv = ["git"] + shlex.split(exp)
    except ValueError:
        return False
    i, _d = git_sub(argv)
    return i < len(argv) and argv[i] == "push"


cwd = str(data.get("cwd") or os.getcwd())
for line in cmd.replace("\r", "\n").split("\n"):
    try:
        lx = shlex.shlex(line, posix=True, punctuation_chars=True)
        lx.whitespace_split = True
        toks = list(lx)
    except ValueError:
        toks = line.split()
    seg = []
    for t in toks + [";"]:
        if t in (";", "&&", "||", "|", "&", "(", ")"):
            w = head(seg)
            seg = []
            if len(w) > 1 and w[0] == "cd":
                cwd = os.path.join(cwd, os.path.expanduser(w[1]))
            if w and w[0].split("/")[-1] == "git":
                i, d = git_sub(w)
                if i < len(w) and (w[i] == "push" or alias_pushes(w[i], os.path.join(cwd, os.path.expanduser(d or ".")))):
                    print("1")
                    sys.exit(0)
        else:
            seg.append(t)
' <<< "$(cat)")

if [ "$is_push" = "1" ]; then
  cat <<'JSON'
{"decision": "block", "reason": "A git push just completed (post-push audit gate — github_commit_audit). Run the TESTED audit SCRIPT (not a hand-derived checklist): `python3.12 _skills/github_commit_audit/scripts/post_audit.py --repo-path \"$(pwd)\" --repo-name <the pushed repo name>` (add `--deep` for a secrets scan). This executes the 42-test-covered audit logic — remote-sync, README-vs-reality, version-tag consistency, doc-code coherence, structural completeness, external-auditor test — instead of re-deriving those six checks by prose. Report the script's Check/Status/Evidence table and remediate any FAIL before proceeding. FALLBACK: if the script errors or is unavailable, run the six checks manually and report the error (never skip the audit). BYPASS a false-positive for one push by noting the reason and proceeding (founder-owned; set GHCA_AUDIT_BYPASS=1 to document it)."}
JSON
fi
exit 0

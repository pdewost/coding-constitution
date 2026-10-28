#!/bin/bash
# session_anchor.sh — ANCHOR policy (POLICY_CORE.md), Claude Code adapter.
# SessionStart hook (matchers: startup | resume | compact). Stdout becomes
# session context. Injects: Constitution pointer (one line: L0, reasoning craft, capability
# inventory), the skill index (bounded), the open fleet-work backlog (governance/OPEN_WORK.md,
# one line per item) at the workspace root and in governance/ only, and the NEOCORTEX manifest +
# status of the nearest project at or above the working directory — or a legacy-BRAIN notice for
# non-migrated projects (transitional clause).
# PLAN_cold_start_context_reduction_2026-10-24.md v0.3 §11 (validated 2026-10-26; S1, S2, S6):
#   S1 — the manifest and status are rendered by adapters/claude-code/anchor_render.py
#        (ACTIVE index + binding blocks, bounded, rc contract §11.B.3); any helper failure prints
#        a loud line and falls back to today's rendering (_safe_neocortex_cat, unchanged).
#   S2 — banner line deleted, L0 + craft lines folded into one, "Judgment layer" echo deleted
#        (its path sits in the L0 line), OPEN_WORK digest only at ROOT and in governance/.
#   S6 — the project is found by walking up from $PWD to ROOT (NEOCORTEX/ only), not $PWD alone.
# ROOT = the workspace root, derived from this script's own location — NOT from
# CLAUDE_PROJECT_DIR, which equals the per-project dir when a session starts
# inside a project (e.g. PROJECT-A's repo) and would break all governance paths.
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
# INTERPRETER — resolved ONCE here; never bare `python3` below. `machine_config.yaml` names this
# hazard: `python3` is PATH-DEPENDENT and `/usr/bin/python3` is 3.9, while governance scripts are
# provisioned for 3.12. Measured 2026-08-27: all four heredocs below parse under 3.9 (4/4), so
# this was a LATENT trap, not a live break. It still had to close, because every call site below
# is `2>/dev/null` — the day one of them gains 3.10+ syntax it dies SILENTLY and the cold-start
# anchor degrades with no error anywhere, which is this workspace's most expensive defect class.
# THE FALLBACK CHAIN IS THE POINT: naming `python3.12` bare would introduce the very failure mode
# it removes, because a hook whose interpreter is absent fails through that same silent redirect.
# First reachable candidate wins; bare `python3` remains the last resort, never the first choice.
PY_BIN=""
for _c in python3.12 /usr/local/bin/python3.12 /opt/homebrew/bin/python3.12 python3; do
  if command -v "$_c" >/dev/null 2>&1; then PY_BIN="$_c"; break; fi
done
[ -n "$PY_BIN" ] || PY_BIN=python3
# Escape hatch for the test harness, which must be able to prove the chain actually degrades.
[ -n "$ANCHOR_PY_BIN" ] && PY_BIN="$ANCHOR_PY_BIN"

# Heartbeat: append timestamp+PWD via an O_NOFOLLOW open of every path component, so a
# symlink/hardlink planted at the log path can't redirect or clobber another file. Silent.
_HB_ROOT="$ROOT" _HB_TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)" _HB_PWD="$PWD" "$PY_BIN" - <<'PY' 2>/dev/null || true
import os, stat
root = os.environ.get("_HB_ROOT", "")
# strip CR/LF from the cwd so a newline in $PWD can't forge a second log record
pwd = os.environ.get("_HB_PWD", "").replace("\n", " ").replace("\r", " ")
line = ("%s\t%s\n" % (os.environ.get("_HB_TS", ""), pwd)).encode()
try:
    dfd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        gfd = os.open("governance", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=dfd)
        os.close(dfd); dfd = gfd
        # O_NONBLOCK so a FIFO planted at the log path returns instead of hanging the
        # whole SessionStart hook; the S_ISREG check then rejects it.
        fd = os.open("hook_heartbeat.log",
                     os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=dfd)
        try:
            st = os.fstat(fd)
            if stat.S_ISREG(st.st_mode) and st.st_nlink == 1:
                os.write(fd, line)
        finally:
            os.close(fd)
    finally:
        os.close(dfd)
except OSError:
    pass
PY

# S2 (PLAN_cold_start v0.3 §2 S2, §11.C D-3): the "=== ... anchor ===" banner is gone, and the L0 line stands
# alone here (the reasoning-craft and capability pointers are not shipped, R34; it replaces the "Judgment
# layer beside these facts" echo that used to follow the skill index).
echo "L0 RATIFIED v1.0: ${ROOT}/PAICodeConstitution-2026.md (14 articles and one amendment)."
echo "Routing: declare task classes per governance/routing_policy.yaml; machine facts: governance/machine_config.yaml."

# H4c + H0 (PLAN_hooks_outcome_tamper_loopbound_2026-10-05): governor tamper evidence, and the count
# of hook exceptions swallowed in the last 24 h. Prints NOTHING when the live hooks match the approved
# list and no hook failed: a healthy governor costs the bounded anchor zero lines.
GOV_OUT="$(_HS_DIR="${ROOT}/.claude/hooks" "$PY_BIN" - <<'PY' 2>/dev/null
import sys
sys.path[:] = [p for p in sys.path if p not in ("", ".")]   # B4-10: no module from the cwd shadows the stdlib
import hashlib, os, types
d = os.environ["_HS_DIR"]
root = os.path.dirname(os.path.dirname(d))
# B3-10 (reviewer A round 3): the bytes of the comparator are checked WITHOUT importing it, and a comparator that
# cannot be loaded is reported, never silent. B3-11: it is loaded from source (no cached .pyc runs).
# B5-02 / B5-30 (reviewer A round 5): only a regular file of bounded size is read (no link, no FIFO, no device), and
# NO approved entry (list missing or unreadable, or no line for it) means NOT executed, like a mismatch.
import stat
own, data = None, None
try:
    with open(os.path.join(root, "governance", "hooks_approved.sha256"), encoding="utf-8", errors="replace") as fh:
        want = next((ln.split(None, 1)[0] for ln in fh if ln.rstrip("\n").endswith("  .claude/hooks/hook_support.py")), None)
except OSError:
    want = None
p = os.path.join(d, "hook_support.py")
try:
    st = os.lstat(p)
    if not stat.S_ISREG(st.st_mode) or st.st_size > 4000000:
        own = ".claude/hooks/hook_support.py: not a regular file of bounded size"
    else:
        fd = os.open(p, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        try:
            data = os.read(fd, 4000001)
        finally:
            os.close(fd)
        have = "%s:%o" % (hashlib.sha256(data).hexdigest(), st.st_mode & 0o7777)
        if want is None:
            own = ("governance/hooks_approved.sha256 is missing, unreadable or has no line for "
                   ".claude/hooks/hook_support.py")
        elif want != have:
            own = ".claude/hooks/hook_support.py: approved %s, live %s (independent check)" % (want[:12], have[:12])
except OSError as exc:
    own = ".claude/hooks/hook_support.py could not be read (%s)" % type(exc).__name__
if own:
    # B4-07 (reviewer A round 4): a comparator whose bytes are not the approved ones is reported and NEVER executed
    print("GOVERNOR MODIFIED (H4c): " + own + " -- GOVERNOR CHECK UNAVAILABLE (H4c): the comparator was not run.")
    sys.exit(0)
try:
    path = os.path.join(d, "hook_support.py")
    hs = types.ModuleType("hook_support")
    hs.__file__ = path
    sys.modules["hook_support"] = hs
    exec(compile(data, path, "exec", dont_inherit=True), hs.__dict__)   # the bytes checked above
except BaseException as exc:
    print("GOVERNOR CHECK UNAVAILABLE (H4c): .claude/hooks/hook_support.py could not be loaded (%s), so the "
          "governor cannot be compared." % type(exc).__name__ + (" GOVERNOR MODIFIED (H4c): " + own if own else ""))
    sys.exit(0)
issues = hs.integrity_report()
if own and not any("hook_support.py" in i for i in issues):
    issues.insert(0, own)
if issues:
    more = f" (+{len(issues) - 3} more)" if len(issues) > 3 else ""
    print("GOVERNOR MODIFIED (H4c): " + "; ".join(issues[:3]) + more + " -- re-approve at a founder-"
          "visible landing (.claude/hooks/hook_support.py --write-approved) or restore the approved files.")
n = hs.recent_error_count(24)
if n:
    print(f"HOOK ERRORS (24 h): {n} swallowed hook exception(s) -- see governance/hook_errors.jsonl.")
PY
)"
[ -n "$GOV_OUT" ] && printf '%s\n' "$GOV_OUT"

IDX="${ROOT}/_skills/SKILLS_INDEX.md"
# O_NOFOLLOW + regular-file-only read of the fixed, ROOT-relative skill index, first 40
# lines — a symlink planted here must not disclose another file into session context.
SKILL_IDX_OUT="$(_SI="$IDX" "$PY_BIN" - <<'PY' 2>/dev/null
import os, stat, sys
try:
    fd = os.open(os.environ["_SI"], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
except OSError:
    sys.exit(0)
try:
    if not stat.S_ISREG(os.fstat(fd).st_mode) or os.fstat(fd).st_nlink != 1:   # S4-47: hardlinks refused too
        sys.exit(0)
    for i, ln in enumerate(os.fdopen(fd, encoding="utf-8", errors="replace")):
        if i >= 40:
            break
        sys.stdout.write(ln)
except OSError:
    sys.exit(0)
PY
)"
if [ -n "$SKILL_IDX_OUT" ]; then
  echo "--- Skill index (generated; check before building — Art. 7) ---"
  printf '%s\n' "$SKILL_IDX_OUT"
else
  echo "Skill index not yet generated (plan Phase 4); registry at ${ROOT}/_skills/ — check before building (Art. 7)."
fi

# S6 (PLAN_cold_start v0.3 §10.6, F10): PROJ = the nearest of $PWD and its ancestors that holds a NEOCORTEX/
# directory, never above ROOT (inclusive). A session started in src/ or a sub-folder of a project used to get no
# manifest and no status at all (the old test looked at $PWD alone). The walk is by string (${_d%/*}), with no
# realpath, and stops at ROOT. A $PWD outside ROOT keeps today's behaviour: only $PWD is tested.
# NEOCORTEX/ alone is the marker, not CLAUDE.md: PROJECT-C has an inherited CLAUDE.md only (§10.6 over §2).
PROJ=""
case "$PWD/" in
  "$ROOT/"*)
    _d="$PWD"
    while [ -n "$_d" ]; do
      if [ -d "${_d}/NEOCORTEX" ]; then PROJ="$_d"; break; fi
      [ "$_d" = "$ROOT" ] && break
      _d="${_d%/*}"
    done
    ;;
  *)
    [ -d "${PWD}/NEOCORTEX" ] && PROJ="$PWD"
    ;;
esac

OW="${ROOT}/governance/OPEN_WORK.md"
# S2 (PLAN_cold_start v0.3 §2 S2): the backlog digest is read only when the session starts at the workspace root
# or in governance/ (PROJ resolved above); a project session carries its own STATUS instead. Taken literally this
# also drops the digest for a cwd that is a git worktree root of the workspace (ROOT/.claude/worktrees/<name>).
# O_NOFOLLOW + regular-file-only read of the fixed, ROOT-relative backlog. Path passed
# via env (never interpolated into the python source); a symlink here is refused.
OPEN_WORK_OUT=""
if [ "$PWD" = "$ROOT" ] || [ "$PROJ" = "${ROOT}/governance" ]; then
OPEN_WORK_OUT="$(_OW="$OW" "$PY_BIN" - <<'PY' 2>/dev/null
import os, re, stat, sys
try:
    fd = os.open(os.environ["_OW"], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
except OSError:
    sys.exit(0)
try:
    if not stat.S_ISREG(os.fstat(fd).st_mode) or os.fstat(fd).st_nlink != 1:   # S4-47: hardlinks refused too
        sys.exit(0)
    for l in os.fdopen(fd, encoding="utf-8", errors="replace"):
        if not l.startswith('| ') or l.startswith('| Item') or '---|' in l:
            continue
        c = [x.strip() for x in l.strip().strip('|').split('|')]
        if len(c) < 3:
            continue
        item = re.sub(r'[*`]', '', c[0]).split('—')[0].strip()
        status = re.sub(r'[*`]', '', c[2])[:62]
        print(f'  • {item} — {status}')
except OSError:
    sys.exit(0)
PY
)"
fi
if [ -n "$OPEN_WORK_OUT" ]; then
  echo "--- Open fleet work (governance/OPEN_WORK.md) ---"
  printf '%s\n' "$OPEN_WORK_OUT"
fi

# O_NOFOLLOW regular-file-only read of a PROJECT-controlled NEOCORTEX file (path via env, never
# interpolated into the python source; output capped): a symlink planted at MANIFEST/STATUS must not
# disclose another readable file into session context (Codex #3, CWE-59 — parity with the fixed reads above).
_safe_neocortex_cat() {
  _NF="$1" "$PY_BIN" - <<'PY' 2>/dev/null
import os, stat, sys
try:
    fd = os.open(os.environ["_NF"], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
except OSError:
    sys.exit(0)
try:
    if not stat.S_ISREG(os.fstat(fd).st_mode) or os.fstat(fd).st_nlink != 1:   # S4-47: hardlinks refused too
        sys.exit(0)
    n = 0
    for ln in os.fdopen(fd, encoding="utf-8", errors="replace"):
        n += len(ln)
        if n > 64 * 1024:
            break
        sys.stdout.write(ln)
except OSError:
    sys.exit(0)
PY
}

# S1 (PLAN_cold_start v0.3 §11.B.3, R2-04): the manifest + status of PROJ are rendered by the helper. Its rc is
# ALWAYS read (no 2>/dev/null on the call; its stderr is one content-free line) and its output is printed only
# for rc 0 (rendered) or rc 2 (valid empty manifest) AND only when it starts with a "--- NEOCORTEX/" header: Python
# itself exits 2 when it cannot open its script, so the header check closes that rc-2 collision. Anything else
# (helper absent, a symlink, a bad interpreter = rc 127, any other rc, no header) prints one loud line carrying the
# real rc and then today's rendering through _safe_neocortex_cat, unchanged. A partial view is never printed.
RENDERER="${ROOT}/governance/adapters/claude-code/anchor_render.py"
if [ -n "$PROJ" ]; then
  NC="${PROJ}/NEOCORTEX"
  if [ -e "$NC/MANIFEST.json" ] || [ -L "$NC/MANIFEST.json" ] || [ -e "$NC/STATUS.md" ] || [ -L "$NC/STATUS.md" ]; then
    _rc=127; _view=""
    if [ -f "$RENDERER" ] && [ ! -L "$RENDERER" ]; then
      _view="$("$PY_BIN" -I "$RENDERER" render "$PROJ")"; _rc=$?
    fi
    _ok=0
    case "$_rc" in 0|2) case "$_view" in "--- NEOCORTEX/"*) _ok=1 ;; esac ;; esac
    if [ "$_ok" = 1 ]; then
      printf '%s\n' "$_view"
    else
      echo "ANCHOR RENDERER FAILED (rc=${_rc}): today's rendering follows"
      [ -f "$NC/MANIFEST.json" ] && { echo "--- NEOCORTEX/MANIFEST.json ---"; _safe_neocortex_cat "$NC/MANIFEST.json"; }
      [ -f "$NC/STATUS.md" ] && { echo "--- NEOCORTEX/STATUS.md ---"; _safe_neocortex_cat "$NC/STATUS.md"; }
    fi
  fi
elif [ -d "${PWD}/BRAIN" ]; then
  echo "Legacy BRAIN/ project (not yet migrated): ANTIGRAVITY-2025 conventions apply here; follow this project's CLAUDE.md read order."
fi
exit 0

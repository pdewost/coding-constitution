#!/bin/bash
# session_anchor.sh — ANCHOR policy (POLICY_CORE.md), Claude Code adapter.
# SessionStart hook (matchers: startup | resume | compact). Stdout becomes
# session context. Injects: Constitution pointer, the skill index (bounded),
# the open fleet-work backlog (governance/OPEN_WORK.md, one line per item),
# and the working directory's NEOCORTEX manifest + status — or a legacy-BRAIN
# notice for non-migrated projects (transitional clause).
# ROOT = the workspace root, derived from this script's own location — NOT from
# CLAUDE_PROJECT_DIR, which equals the per-project dir when a session starts
# inside a project (e.g. a sub-project's repo) and would break all governance paths.
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
# Heartbeat: append timestamp+PWD via an O_NOFOLLOW open of every path component, so a
# symlink/hardlink planted at the log path can't redirect or clobber another file.
# (python3 is already required by this hook — see the OPEN_WORK parse below.) Silent.
_HB_ROOT="$ROOT" _HB_TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)" _HB_PWD="$PWD" python3 - <<'PY' 2>/dev/null || true
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

echo "=== PAICodeConstitution-2026 anchor (auto-injected) ==="
echo "L0 RATIFIED v1.0: ${ROOT}/PAICodeConstitution-2026.md (14 articles)."
echo "Routing: declare task classes per governance/routing_policy.yaml; machine facts: governance/machine_config.yaml."

IDX="${ROOT}/_skills/SKILLS_INDEX.md"
# O_NOFOLLOW + regular-file-only read of the fixed, ROOT-relative skill index, first 40
# lines — a symlink planted here must not disclose another file into session context.
SKILL_IDX_OUT="$(_SI="$IDX" python3 - <<'PY' 2>/dev/null
import os, stat, sys
try:
    fd = os.open(os.environ["_SI"], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
except OSError:
    sys.exit(0)
try:
    if not stat.S_ISREG(os.fstat(fd).st_mode):
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

OW="${ROOT}/governance/OPEN_WORK.md"
# O_NOFOLLOW + regular-file-only read of the fixed, ROOT-relative backlog. Path passed
# via env (never interpolated into the python source); a symlink here is refused.
OPEN_WORK_OUT="$(_OW="$OW" python3 - <<'PY' 2>/dev/null
import os, re, stat, sys
try:
    fd = os.open(os.environ["_OW"], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
except OSError:
    sys.exit(0)
try:
    if not stat.S_ISREG(os.fstat(fd).st_mode):
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
if [ -n "$OPEN_WORK_OUT" ]; then
  echo "--- Open fleet work (governance/OPEN_WORK.md) ---"
  printf '%s\n' "$OPEN_WORK_OUT"
fi

# O_NOFOLLOW regular-file-only read of a PROJECT-controlled NEOCORTEX file (path via env, never
# interpolated into the python source; output capped): a symlink planted at MANIFEST/STATUS must not
# disclose another readable file into session context (Codex #3, CWE-59 — parity with the fixed reads above).
_safe_neocortex_cat() {
  _NF="$1" python3 - <<'PY' 2>/dev/null
import os, stat, sys
try:
    fd = os.open(os.environ["_NF"], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
except OSError:
    sys.exit(0)
try:
    if not stat.S_ISREG(os.fstat(fd).st_mode):
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

if [ -d "${PWD}/NEOCORTEX" ]; then
  [ -f "${PWD}/NEOCORTEX/MANIFEST.json" ] && { echo "--- NEOCORTEX/MANIFEST.json ---"; _safe_neocortex_cat "${PWD}/NEOCORTEX/MANIFEST.json"; }
  [ -f "${PWD}/NEOCORTEX/STATUS.md" ] && { echo "--- NEOCORTEX/STATUS.md ---"; _safe_neocortex_cat "${PWD}/NEOCORTEX/STATUS.md"; }
elif [ -d "${PWD}/BRAIN" ]; then
  echo "Legacy BRAIN/ project (not yet migrated): ANTIGRAVITY-2025 conventions apply here; follow this project's CLAUDE.md read order."
fi
exit 0

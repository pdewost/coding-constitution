#!/usr/bin/env python3
"""anchor_render.py — bounded, projection-first view of a project's NEOCORTEX files for the session anchor.

PLAN_cold_start_context_reduction_2026-10-24.md v0.3 §10-§11 (S1); BUILD_SPEC_anchor_render_2026-10-26.md §A.

Today session_anchor.sh cats NEOCORTEX/MANIFEST.json and STATUS.md up to 64 Ki characters each. This helper replaces that
with a view that is smaller and cannot lose the part the owner marked as binding:

  render <project_dir>   print the anchor view of <project_dir>/NEOCORTEX/{MANIFEST.json, STATUS.md}; never writes a file.
                         rc 0 rendered · rc 2 rendered, MANIFEST has no ACTIVE entries · rc 1 failed (nothing on stdout,
                         one content-free line on stderr) · rc 3 neither file exists · rc 64 usage.
  --presence <STATUS.md> <rendered_file>        gate G1: every marked block of STATUS occurs in the rendered file.
  --capture ...  --table ...                    baseline instruments (E11): the hook is run against FROZEN copies only.

MANIFEST view: the ACTIVE entries, newest first by filename date, one `file · genre · status` line each, under a 16 KiB
budget. STATUS view: the binding blocks (the lead block before the first `## ` heading, plus every `<!-- BINDING -->` ...
`<!-- /BINDING -->` block) in full, then as much of the rest as the same budget allows; a STATUS without well-formed
markers falls back to today's rendering, announced on a loud line.

Stdlib only, Python 3.9-compatible syntax (the hook's interpreter chain can fall back to /usr/bin/python3 = 3.9).
"""
import os as _os0, sys as _sys0
# B4-10 / B5-01 (same rule as .claude/hooks/hook_support.py): Python puts this script's directory first on sys.path, so a
# file in adapters/claude-code/ named like a stdlib module (json.py, io.py, ...) would run inside the anchor
# before any check. Drop that entry FIRST: no import may precede this block. (The hook also passes -I.)
if __name__ == "__main__" and _sys0.path and \
        _os0.path.realpath(_sys0.path[0] or ".") == _os0.path.dirname(_os0.path.realpath(__file__)):
    del _sys0.path[0]

import errno
import hashlib
import io
import json
import os
import re
import shutil
import statistics
import stat
import subprocess
import sys

MAX_INPUT = 4 * 1024 * 1024          # A.1: one NEOCORTEX file above this is refused, not truncated
LEGACY_CHARS = 64 * 1024             # today's _safe_neocortex_cat cutoff, in CHARACTERS (fallback view only)
BUDGET = 16 * 1024                   # A.3 / A.4: bytes of entry lines, or of binding + remainder lines
FIELD_MAX = 120                      # A.3: longest status / span, in characters
OPEN_MARK = "<!-- BINDING -->"
CLOSE_MARK = "<!-- /BINDING -->"
ELEMENTS = ("MANIFEST.json", "STATUS.md")        # today's order
RC_OK, RC_FAIL, RC_EMPTY, RC_NONE, RC_USAGE = 0, 1, 2, 3, 64

USAGE = ("usage: anchor_render.py render <project_dir>\n"
         "       anchor_render.py --presence <STATUS.md> <rendered_file>\n"
         "       anchor_render.py --capture <baseline_dir> --workspace <dir> --hooks-before <dir> --hooks-after <dir>"
         " --renderer <file> --scratch <dir>\n"
         "       anchor_render.py --table <baseline_dir> --presence-dir <dir> --out <file.md>\n")

HEAVY_SEVEN = (   # the author measured seven project folders; the six private names are replaced. List your own.
    "governance",
    "example-project-1",
    "example-project-2",
    "example-project-3",
    "example-project-4",
    "example-project-5",
    "example-project-6",
)
ROOT_SLUG = "_workspace_root"


class RenderError(Exception):
    """A refusal that names only the element and a fixed class, never file content (A.1; R2-05)."""

    def __init__(self, element, cls):
        Exception.__init__(self, "%s: %s" % (element, cls))
        self.element = element
        self.cls = cls


class Absent(Exception):
    """The path vanished between lexists and open: treated as not there (A.2)."""


class UsageError(Exception):
    pass


class ToolError(Exception):
    """A capture/table precondition failed. The message carries paths and counts, never file content."""


# --- A.1 safe reader --------------------------------------------------------------------------------------------------

def safe_read(path):
    """R2-03 / F09 (PLAN §11.B.2): O_NOFOLLOW | O_NONBLOCK, regular single-link file, bounded. The same refusals as the
    legacy _safe_neocortex_cat (Codex #3 CWE-59 symlink, S4-47 hardlink, FIFO that would block), but each one is NAMED and
    becomes rc 1 instead of an empty read the hook cannot tell from success. The diagnostic carries the element name and
    the class only: a planted link to a secret must not leak a byte of it (A.1)."""
    element = os.path.basename(path)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as exc:
        if exc.errno == errno.ENOENT:
            raise Absent(element)
        if exc.errno == errno.ELOOP:
            raise RenderError(element, "symbolic link refused")
        raise RenderError(element, "unreadable (%s)" % errno.errorcode.get(exc.errno, "errno%s" % exc.errno))
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise RenderError(element, "not a regular file")        # FIFO, directory, device
        if st.st_nlink != 1:
            raise RenderError(element, "hardlinked")
        if st.st_size > MAX_INPUT:
            raise RenderError(element, "input bound exceeded")
        chunks, total = [], 0
        while True:                                                    # the WHOLE file: no 64 Ki cut at read time
            try:
                block = os.read(fd, 1 << 20)
            except OSError as exc:
                raise RenderError(element, "unreadable (%s)" % errno.errorcode.get(exc.errno, "errno%s" % exc.errno))
            if not block:
                break
            total += len(block)
            if total > MAX_INPUT:                                      # the file grew after fstat
                raise RenderError(element, "input bound exceeded")
            chunks.append(block)
        return b"".join(chunks)
    finally:
        os.close(fd)


def _blen(s):
    return len(s.encode("utf-8"))


def _sha12(data):
    return hashlib.sha256(data).hexdigest()[:12]


def _lines(data):
    """The legacy reader's line iteration (universal newlines: CRLF and CR become LF), never str.splitlines()."""
    return list(io.TextIOWrapper(io.BytesIO(data), encoding="utf-8", errors="replace"))


def _nl(line):
    return line if line.endswith("\n") else line + "\n"


# --- A.3 MANIFEST view ------------------------------------------------------------------------------------------------

_CLOSED = ("shipped", "superseded", "resolved")
# E9's residual markers: NEOCORTEX_SPEC §2 forces a live residual to `active`, so prose naming one contradicts a closed state.
_RESIDUAL = re.compile(r"residual|remain|\bopen\b|pending|carry|partial|\bbut\b|not yet|to ?do|\bsee\b", re.IGNORECASE)
_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_CTRL = dict((c, " ") for c in list(range(0x20)) + [0x7F, 0x2028, 0x2029])


def _field(value, limit=None):
    """One display field: control characters and line separators become spaces, whitespace runs collapse, empty is `?`.
    F06: an entry can never add a line, so the entry cap in bytes is also a cap in lines. A lone surrogate (JSON \\ud800)
    would make the final utf-8 write raise; it is replaced here so one odd character cannot fail the whole view."""
    s = "" if value is None else (value if isinstance(value, str) else str(value))
    s = " ".join(s.translate(_CTRL).split())
    s = s.encode("utf-8", "replace").decode("utf-8")
    if not s:
        return "?"
    if limit is not None and len(s) > limit:
        s = s[:limit - 1] + "…"
    return s


def _is_active(entry):
    """PLAN §10.4: absent, unknown or conflicting state is KEPT; only a clean closed state drops an entry."""
    state = entry.get("status_state")
    if not isinstance(state, str):
        return True                                   # absent, null, or not a string
    state = state.strip().lower()
    if state not in _CLOSED:
        return True                                   # `active` and every value outside the SPEC's four
    prose = entry.get("status")
    if isinstance(prose, str) and (prose.strip().upper().startswith("ACTIVE") or _RESIDUAL.search(prose)):
        return True                                   # closed state contradicted by its own prose
    return False


def _file_date(entry):
    found = _DATE.findall(str(entry.get("file", "")))
    return found[-1] if found else None


def manifest_view(data):
    """-> (text, n_active). R2-05: the view always says it is a projection and where the complete file is."""
    try:
        doc = json.loads(data.decode("utf-8", "replace"))
    except (ValueError, RecursionError):
        raise RenderError("MANIFEST.json", "does not parse")
    if not isinstance(doc, dict) or not isinstance(doc.get("files"), list):
        raise RenderError("MANIFEST.json", "no files[] list")
    files = doc["files"]
    kept = []                                          # (date or None, line)
    for i, entry in enumerate(files):
        if not isinstance(entry, dict):
            kept.append((None, "(malformed entry #%d)" % i))
        elif _is_active(entry):
            line = "%s · %s · %s" % (_field(entry.get("file")), _field(entry.get("genre")),
                                     _field(entry.get("status"), FIELD_MAX))
            kept.append((_file_date(entry), line))
    dated = sorted((t for t in kept if t[0] is not None), key=lambda t: t[0], reverse=True)   # reverse keeps stability
    ordered = [t[1] for t in dated + [t for t in kept if t[0] is None]]

    shown, used = [], 0
    for line in ordered:                               # stop at the first line that does not fit: no skip-ahead
        size = _blen(line) + 1
        if used + size > BUDGET:
            break
        shown.append(line)
        used += size
    hidden = ordered[len(shown):]

    out = ["--- NEOCORTEX/MANIFEST.json · rendered view (ACTIVE index: %d of %d entries; hook, archive, provenance omitted;"
           " canonical sha256 %s) ---" % (len(ordered), len(files), _sha12(data))]
    archive = doc.get("archive")
    if isinstance(archive, dict):
        if archive.get("exists"):
            line = "archive: exists"
            if archive.get("span") is not None:
                line += ", " + _field(archive.get("span"), FIELD_MAX)
        else:
            line = "archive: none"
        out.append(line)
    out.extend(shown if ordered else ["no ACTIVE entries"])
    if hidden:
        out.append("… %d entries / %d bytes not shown; read NEOCORTEX/MANIFEST.json"
                   % (len(hidden), sum(_blen(l) + 1 for l in hidden)))
    out.append("projection: ACTIVE entries only (status_state absent, active, unknown or conflicting), newest first by"
               " filename date, status trimmed to 120 chars; the canonical file is complete: read NEOCORTEX/MANIFEST.json")
    return "\n".join(out) + "\n", len(ordered)


# --- A.4 STATUS view --------------------------------------------------------------------------------------------------

def parse_status(lines):
    """Fences, lead block and BINDING markers over the legacy line list. Headings and markers inside a fence are inert
    (a STATUS may document the convention in a code block). `fault` is (1-based line, kind) of the FIRST malformation."""
    n = len(lines)
    binding = [False] * n
    marker = [False] * n
    blocks = []                                        # (first, end): half-open range of content line indexes
    fault = None
    open_at = None
    lead_open = True
    lead_end = n
    in_fence = False
    for i, ln in enumerate(lines):
        head = ln.lstrip()
        if head.startswith("```") or head.startswith("~~~"):
            in_fence = not in_fence
            binding[i] = lead_open or open_at is not None
            continue
        if in_fence:
            binding[i] = lead_open or open_at is not None
            continue
        text = ln.strip()
        if text == OPEN_MARK:
            marker[i] = True
            if open_at is not None:
                fault = fault or (i + 1, "nested")
            else:
                open_at = i
            continue
        if text == CLOSE_MARK:
            marker[i] = True
            if open_at is None:
                fault = fault or (i + 1, "unopened close")
            else:
                blocks.append((open_at + 1, i))
                open_at = None
            continue
        if lead_open and ln.startswith("## "):
            lead_open = False
            lead_end = i
        binding[i] = lead_open or open_at is not None
    if open_at is not None and fault is None:
        fault = (open_at + 1, "unclosed")
    return {"binding": binding, "marker": marker, "blocks": blocks, "fault": fault, "lead_end": lead_end,
            "has_markers": any(marker)}


def _status_fallback(lines, sha, fault):
    """R2-01 / F01: today's rendering, byte-identical to _safe_neocortex_cat's loop, plus the loud line. Never the words
    'whole file': the 64 Ki-character rule is stated."""
    if fault:
        loud = ("STATUS has malformed BINDING markers (line %d: %s): today's rendering (64 Ki-character rule)" % fault)
    else:
        loud = "STATUS has no BINDING markers: today's rendering (64 Ki-character rule)"
    emitted, n = [], 0
    for ln in lines:
        n += len(ln)                                   # CHARACTERS, as the legacy loop counts them
        if n > LEGACY_CHARS:
            break
        emitted.append(ln)
    body = "".join(emitted)
    if body and not body.endswith("\n"):
        body += "\n"
    out = "--- NEOCORTEX/STATUS.md · today's rendering (64 Ki-character rule; canonical sha256 %s) ---\n%s\n%s" % (
        sha, loud, body)
    if len(emitted) < len(lines):
        out += "… truncated: %d more bytes; read NEOCORTEX/STATUS.md\n" % (
            sum(_blen(l) for l in lines) - sum(_blen(l) for l in emitted))
    return out


def status_view(data):
    lines = _lines(data)
    sha = _sha12(data)
    p = parse_status(lines)
    if not p["has_markers"] or p["fault"]:
        return _status_fallback(lines, sha, p["fault"])
    norm = [_nl(l) for l in lines]
    bind = [norm[i] for i in range(len(norm)) if p["binding"][i] and not p["marker"][i]]
    rest = [norm[i] for i in range(len(norm)) if not p["binding"][i] and not p["marker"][i]]
    b_bytes = sum(_blen(l) for l in bind)
    t_bytes = sum(_blen(l) for l in rest)
    shown, r_bytes = [], 0
    for ln in rest:                                    # F02 / F03: the binding lines are never cut; the rest yields to them
        size = _blen(ln)
        if b_bytes + r_bytes + size > BUDGET:
            break
        shown.append(ln)
        r_bytes += size
    lead_text = any(lines[i].strip() for i in range(p["lead_end"]) if not p["marker"][i])
    k = len(p["blocks"]) + (1 if lead_text else 0)
    out = ["--- NEOCORTEX/STATUS.md · rendered view (binding blocks: %d, remainder: %d of %d bytes; canonical sha256 %s) ---\n"
           % (k, r_bytes, t_bytes, sha)]
    out.extend(bind)
    out.extend(shown)
    if r_bytes < t_bytes:
        out.append("… %d bytes not shown; read NEOCORTEX/STATUS.md\n" % (t_bytes - r_bytes))
    if b_bytes > BUDGET:
        out.append("binding blocks exceed the 16 KiB budget by %d bytes\n" % (b_bytes - BUDGET))
    return "".join(out)


# --- A.2 render + rc contract -----------------------------------------------------------------------------------------

def render(project_dir):
    """-> (rc, text). R2-04: the whole view is built in memory and handed back once; any failure raises, so a partial
    element can never reach stdout and the hook's rc check sees every failure."""
    nc = os.path.join(project_dir, "NEOCORTEX")
    parts, empty = [], False
    for name in ELEMENTS:
        path = os.path.join(nc, name)
        if not os.path.lexists(path):                  # a dangling symlink exists here, and is refused below
            continue
        try:
            data = safe_read(path)
        except Absent:
            continue
        if name == "MANIFEST.json":
            text, n_active = manifest_view(data)
            empty = (n_active == 0)
        else:
            text = status_view(data)
        parts.append(text)
    if not parts:
        return RC_NONE, ""
    return (RC_EMPTY if empty else RC_OK), "".join(parts)


# --- A.5 presence (gate G1) -------------------------------------------------------------------------------------------

def _needle(text):
    """The hook's $(...) strips trailing newlines from the whole view, so a block ending in blank lines is compared as
    ending in one newline."""
    core = text.rstrip("\n")
    return core + "\n" if core else ""


def presence(status_path, rendered_path):
    sdata = safe_read(status_path)
    rdata = safe_read(rendered_path)
    rtext = rdata.decode("utf-8", "replace")
    lines = _lines(sdata)
    p = parse_status(lines)
    head = "presence report — STATUS sha256 %s (%s B) vs %s sha256 %s\n" % (
        hashlib.sha256(sdata).hexdigest(), "{:,}".format(len(sdata)), os.path.basename(rendered_path),
        hashlib.sha256(rdata).hexdigest())
    if p["fault"]:
        return RC_FAIL, head + "malformed markers: line %d: %s\nmissing: n/a (malformed markers)\n" % p["fault"]
    if not p["has_markers"]:
        return RC_OK, head + "marked blocks: 0 — presence vacuous (no BINDING markers)\nmissing: 0\n"
    norm = [_nl(l) for l in lines]

    def verdict(text):
        if not text.strip():
            return "EMPTY"
        return "PRESENT" if _needle(text) in rtext else "MISSING"

    lead = "".join(norm[i] for i in range(p["lead_end"]) if not p["marker"][i])
    out = [head, "marked blocks: %d   (lead block: %s)\n" % (len(p["blocks"]), verdict(lead))]
    missing = 0
    for n, (first, end) in enumerate(p["blocks"], 1):
        text = "".join(norm[first:end])
        v = verdict(text)
        if v == "MISSING":
            missing += 1
        span = "lines %d-%d" % (first + 1, end) if end > first else "lines -"
        out.append("block %d  %s  %s B  %s\n" % (n, span, "{:,}".format(_blen(text)), v))
    out.append("missing: %d\n" % missing)
    return (RC_OK if missing == 0 else RC_FAIL), "".join(out)


# --- A.6 baseline instruments (capture, table) ------------------------------------------------------------------------

def _read_index(baseline):
    path = os.path.join(baseline, "INDEX.tsv")
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError as exc:
        raise ToolError("cannot read %s (%s)" % (path, errno.errorcode.get(exc.errno, "errno%s" % exc.errno)))
    rows = []
    for n, ln in enumerate(data.decode("utf-8", "replace").split("\n")):
        if n == 0 or not ln.strip():
            continue                                   # header, blank
        cols = ln.split("\t")
        if len(cols) < 2:
            raise ToolError("INDEX.tsv line %d has fewer than 2 columns" % (n + 1))
        slug, rel = cols[0], cols[1]
        if (not slug or slug in (".", "..", ROOT_SLUG) or "/" in slug or os.path.isabs(rel) or not rel
                or ".." in rel.split("/")):
            raise ToolError("INDEX.tsv line %d: unsafe slug or relpath" % (n + 1))
        rows.append((slug, rel))
    return rows, hashlib.sha256(data).hexdigest()


def _copy_regular(src, dst):
    """Copy one regular file, mode kept; a missing source, a link, a directory is skipped (False)."""
    try:
        st = os.lstat(src)
    except OSError:
        return False
    if not stat.S_ISREG(st.st_mode):
        return False
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copyfile(src, dst)
    os.chmod(dst, stat.S_IMODE(st.st_mode))
    return True


def _min_env(cwd=None):
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": os.environ.get("HOME", ""), "LANG": "en_US.UTF-8"}
    if cwd is not None:
        env["PWD"] = cwd
    return env


def _build_arm(arm, hooks_dir, baseline, workspace, rows, renderer):
    os.makedirs(os.path.join(arm, ".claude", "hooks"))
    os.makedirs(os.path.join(arm, "governance"))
    for name in sorted(os.listdir(hooks_dir)):
        if name != "__pycache__":
            _copy_regular(os.path.join(hooks_dir, name), os.path.join(arm, ".claude", "hooks", name))
    with open(os.path.join(arm, ".claude", "settings.json"), "w", encoding="utf-8") as fh:
        fh.write("{}\n")
    _copy_regular(os.path.join(baseline, "_shared", "OPEN_WORK.md"), os.path.join(arm, "governance", "OPEN_WORK.md"))
    _copy_regular(os.path.join(baseline, "_shared", "SKILLS_INDEX.md"), os.path.join(arm, "_skills", "SKILLS_INDEX.md"))
    for name in ("machine_config.yaml", "routing_policy.yaml"):
        _copy_regular(os.path.join(workspace, "governance", name), os.path.join(arm, "governance", name))
    if renderer is not None:
        _copy_regular(renderer, os.path.join(arm, "governance", "adapters", "claude-code", "anchor_render.py"))
    for slug, rel in rows:
        nc = os.path.join(arm, rel, "NEOCORTEX")
        os.makedirs(nc, exist_ok=True)
        for name in ELEMENTS:
            _copy_regular(os.path.join(baseline, slug, name), os.path.join(nc, name))
    hs = os.path.join(arm, ".claude", "hooks", "hook_support.py")
    if os.path.isfile(hs):                             # scratch-only approved list: the GOVERNOR line is empty in both arms
        cp = subprocess.run([sys.executable, "-I", hs, "--write-approved"], cwd=arm, env=_min_env(arm),
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
        if cp.returncode != 0:
            raise ToolError("hook_support.py --write-approved failed in %s (rc %d)" % (arm, cp.returncode))


def _cwd(arm, rel):
    return arm if rel == "." else os.path.join(arm, rel)


def _run_hook(arm, rel):
    cwd = _cwd(arm, rel)
    try:
        cp = subprocess.run(["bash", os.path.join(arm, ".claude", "hooks", "session_anchor.sh")], cwd=cwd,
                            env=_min_env(cwd), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, timeout=60)
        return cp.returncode, cp.stdout
    except subprocess.TimeoutExpired as exc:
        return 124, exc.stdout or b""


def _run_renderer(renderer, project_dir):
    try:
        return subprocess.run([sys.executable, "-I", renderer, "render", project_dir], env=_min_env(),
                              stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                              timeout=60).returncode
    except subprocess.TimeoutExpired:
        return 124


def _normalise(raw, variants, workspace):
    text = raw.decode("utf-8", "replace")
    for v in sorted(set(variants), key=len, reverse=True):
        text = text.replace(v, workspace)
    return text


def _elements(text):
    """{element name: its text}: from its `--- NEOCORTEX/<file>` header line (included) to the next such line or the end."""
    out, cur = {}, None
    for ln in io.StringIO(text):
        if ln.startswith("--- NEOCORTEX/"):
            cur = ln[len("--- NEOCORTEX/"):].split(" ", 1)[0].rstrip("\n")
            out[cur] = ""
        if cur is not None:
            out[cur] += ln
    return out


def _status_mode(status_element):
    if status_element is None:
        return "absent"
    header = status_element.split("\n", 1)[0]
    if "· rendered view" in header:
        return "rendered"
    if "· today's rendering" in header:
        return "fallback"
    return "failed"


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def capture(baseline, workspace, hooks_before, hooks_after, renderer, scratch):
    """Writes only inside `baseline` (per-slug files) and `scratch` (two throwaway workspaces). The real workspace, its
    heartbeat log and its hooks are never touched: each arm is a copy and writes its own heartbeat."""
    baseline, scratch = os.path.abspath(baseline), os.path.abspath(scratch)
    renderer = os.path.abspath(renderer)
    workspace = workspace.rstrip("/") or "/"
    for d in (baseline, hooks_before, hooks_after):
        if not os.path.isdir(d):
            raise ToolError("not a directory: %s" % d)
    for d in (hooks_before, hooks_after):
        if not os.path.isfile(os.path.join(d, "session_anchor.sh")):
            raise ToolError("no session_anchor.sh in %s" % d)
    if not os.path.isfile(renderer):
        raise ToolError("renderer not found: %s" % renderer)
    if os.path.lexists(scratch):
        raise ToolError("scratch already exists: %s" % scratch)
    rows, index_sha = _read_index(baseline)
    rows = rows + [(ROOT_SLUG, ".")]
    projects = [r for r in rows if r[1] != "."]

    before, after = os.path.join(scratch, "before"), os.path.join(scratch, "after")
    os.makedirs(before)
    os.makedirs(after)
    _build_arm(before, hooks_before, baseline, workspace, projects, None)
    _build_arm(after, hooks_after, baseline, workspace, projects, renderer)
    before_real, after_real = os.path.realpath(before), os.path.realpath(after)

    summary, trouble = [], []
    for slug, rel in rows:
        out_dir = os.path.join(baseline, slug)
        os.makedirs(out_dir, exist_ok=True)
        rc_b, raw_b = _run_hook(before_real, rel)
        rc_a, raw_a = _run_hook(after_real, rel)
        text_b = _normalise(raw_b, [before, before_real], workspace)
        text_a = _normalise(raw_a, [after, after_real], workspace)
        with open(os.path.join(out_dir, "anchor_today.txt"), "wb") as fh:
            fh.write(text_b.encode("utf-8"))
        with open(os.path.join(out_dir, "anchor_view.txt"), "wb") as fh:
            fh.write(text_a.encode("utf-8"))
        renderer_rc = None if rel == "." else _run_renderer(renderer, _cwd(after_real, rel))
        el_b, el_a = _elements(text_b), _elements(text_a)
        status_a = el_a.get("STATUS.md")
        head = status_a.split("\n", 1)[0] if status_a is not None else ""
        blocks = re.search(r"binding blocks: ([0-9]+)", head)
        over = re.search(r"^binding blocks exceed the 16 KiB budget by ([0-9]+) bytes$", status_a or "", re.MULTILINE)
        meta = {
            "hook_rc_before": rc_b, "hook_rc_after": rc_a, "renderer_rc": renderer_rc,
            "bytes_before": _blen(text_b), "bytes_after": _blen(text_a),
            "status_bytes_before": _blen(el_b.get("STATUS.md", "")), "status_bytes_after": _blen(status_a or ""),
            "manifest_bytes_before": _blen(el_b.get("MANIFEST.json", "")),
            "manifest_bytes_after": _blen(el_a.get("MANIFEST.json", "")),
            "status_mode": _status_mode(status_a),
            "overflow_bytes": int(over.group(1)) if over else 0,
            "binding_blocks": int(blocks.group(1)) if blocks and "rendered view" in head else 0,
            "renderer_failed_line": "ANCHOR RENDERER FAILED" in text_a,
        }
        with open(os.path.join(out_dir, "render_meta.json"), "w", encoding="utf-8") as fh:
            fh.write(json.dumps(meta, indent=2) + "\n")
        summary.append("%s: today %d B -> view %d B, STATUS %s, renderer rc %s"
                       % (rel, meta["bytes_before"], meta["bytes_after"], meta["status_mode"], renderer_rc))
        if rc_b != 0 or rc_a != 0:
            trouble.append("%s: hook rc before %s, after %s" % (rel, rc_b, rc_a))
    prov = {"baseline_index_sha256": index_sha, "renderer_sha256": _sha256_file(renderer),
            "hook_before_sha256": _sha256_file(os.path.join(hooks_before, "session_anchor.sh")),
            "hook_after_sha256": _sha256_file(os.path.join(hooks_after, "session_anchor.sh")), "workspace": workspace}
    with open(os.path.join(baseline, "capture_provenance.json"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps(prov, indent=2) + "\n")
    out = "captured %d rows into %s (scratch left at %s)\n%s\n" % (len(rows), baseline, scratch, "\n".join(summary))
    if trouble:
        out += "hook trouble:\n" + "\n".join(trouble) + "\n"
    return (RC_FAIL if trouble else RC_OK), out


def _cell(text):
    return str(text).replace("|", "\\|")


def _presence_cell(presence_dir, slug):
    """-> (missing int or None, marked blocks int, cell text). The last line of the report carries the count."""
    try:
        with open(os.path.join(presence_dir, "presence_%s.txt" % slug), encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        return None, 0, "absent"
    nonblank = [l for l in text.split("\n") if l.strip()]
    found = re.match(r"missing: ([0-9]+)$", nonblank[-1]) if nonblank else None
    blocks = re.search(r"^marked blocks: ([0-9]+)", text, re.MULTILINE)
    n_blocks = int(blocks.group(1)) if blocks else 0
    if found:
        return int(found.group(1)), n_blocks, found.group(1)
    return None, n_blocks, "malformed" if "malformed markers" in text else "unparsed"


def table(baseline, presence_dir, out_path):
    baseline = os.path.abspath(baseline)
    rows, index_sha = _read_index(baseline)
    rows = rows + [(ROOT_SLUG, ".")]
    recs = []
    for slug, rel in rows:
        meta_path = os.path.join(baseline, slug, "render_meta.json")
        try:
            with open(meta_path, encoding="utf-8") as fh:
                meta = json.load(fh)
        except (OSError, ValueError):
            raise ToolError("no readable render_meta.json for %s; run --capture first" % rel)
        sha = {}
        for name in ELEMENTS:
            try:
                sha[name] = _sha256_file(os.path.join(baseline, slug, name))[:12]
            except OSError:
                sha[name] = "-"
        miss, n_blocks, pcell = (None, 0, "n/a") if rel == "." else _presence_cell(presence_dir, slug)
        recs.append({"slug": slug, "rel": rel, "meta": meta, "sha": sha, "miss": miss, "blocks": n_blocks, "pcell": pcell})

    cols = ["cwd", "STATUS sha256", "MANIFEST sha256", "today anchor B", "view anchor B", "saving B (%)",
            "STATUS B today → view", "MANIFEST B today → view", "binding blocks", "overflow B", "presence missing",
            "fallback", "rc"]
    lines = ["| " + " | ".join(cols) + " |", "|" + "|".join("---" for _ in cols) + "|"]
    for r in recs:
        m = r["meta"]
        today, view = m["bytes_before"], m["bytes_after"]
        pct = ("%.1f%%" % (100.0 * (today - view) / today)) if today else "n/a"
        cwd = _cell(r["rel"])
        cells = [("**%s**" % cwd) if r["rel"] in HEAVY_SEVEN else cwd, r["sha"]["STATUS.md"], r["sha"]["MANIFEST.json"],
                 today, view, "%d (%s)" % (today - view, pct),
                 "%d → %d" % (m["status_bytes_before"], m["status_bytes_after"]),
                 "%d → %d" % (m["manifest_bytes_before"], m["manifest_bytes_after"]),
                 m["binding_blocks"], m["overflow_bytes"], r["pcell"],
                 "no" if m["status_mode"] == "rendered" else m["status_mode"],
                 "n/a" if m["renderer_rc"] is None else m["renderer_rc"]]
        lines.append("| " + " | ".join(str(c) for c in cells) + " |")

    heavy = [r for r in recs if r["rel"] in HEAVY_SEVEN]
    h_today = sum(r["meta"]["bytes_before"] for r in heavy)
    h_view = sum(r["meta"]["bytes_after"] for r in heavy)
    h_pct = (100.0 * (h_today - h_view) / h_today) if h_today else 0.0
    a_pass = len(heavy) == len(HEAVY_SEVEN) and h_pct >= 30.0
    bigger = [r for r in recs if r["meta"]["bytes_after"] > r["meta"]["bytes_before"]]
    projects = [r for r in recs if r["rel"] != "."]
    numeric = [r for r in projects if r["miss"] is not None]
    unread = [r for r in projects if r["miss"] is None]
    total_missing = sum(r["miss"] for r in numeric)
    with_markers = sum(1 for r in projects if r["blocks"] > 0)
    g1_pass = total_missing == 0 and not unread
    gates = [
        "**G2(a)** heavy-seven anchor-only saving: %d of %d B = %.1f%% (threshold 30%%; %d of %d heavy rows in the INDEX) — %s"
        % (h_today - h_view, h_today, h_pct, len(heavy), len(HEAVY_SEVEN), "PASS" if a_pass else "FAIL"),
        "**G2(b)** rows whose view anchor is larger than today's: %s — %s"
        % (", ".join(_cell(r["rel"]) for r in bigger) if bigger else "none", "FAIL" if bigger else "PASS"),
        "**G1** presence: Σ missing = %d over %d projects (%d with BINDING markers, %d vacuous)%s — %s"
        % (total_missing, len(projects), with_markers, len(projects) - with_markers,
           ("; no usable report for: " + ", ".join(_cell(r["rel"]) for r in unread)) if unread else "",
           "PASS" if g1_pass else "FAIL"),
    ]

    def spread(key):
        vals = sorted(r["meta"][key] for r in projects)
        if not vals:
            return "n/a"
        mid = statistics.median(vals)
        return "min %d / median %s / max %d" % (vals[0], ("%d" % mid) if mid == int(mid) else ("%.1f" % mid), vals[-1])

    prov = {}
    try:
        with open(os.path.join(baseline, "capture_provenance.json"), encoding="utf-8") as fh:
            prov = json.load(fh)
    except (OSError, ValueError):
        pass
    doc = ["# Anchor byte table (E11)", "",
           "%d projects plus the workspace root. Bold = the heavy seven." % len(projects), ""]
    doc.extend(lines)
    doc.extend(["", "## Gates", ""])
    doc.extend("- " + g for g in gates)
    doc.extend(["", "## Fleet (%d projects, workspace root excluded)" % len(projects), "",
                "- today anchor bytes: " + spread("bytes_before"), "- view anchor bytes: " + spread("bytes_after"),
                "", "## Provenance", "", "- baseline INDEX.tsv sha256: " + index_sha,
                "- renderer sha256: " + prov.get("renderer_sha256", "not recorded"),
                "- hook before sha256: " + prov.get("hook_before_sha256", "not recorded"),
                "- hook after sha256: " + prov.get("hook_after_sha256", "not recorded"), ""])
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(doc))
    return RC_OK, "wrote %s (%d rows)\n%s\n" % (out_path, len(recs), "\n".join(gates))


# --- CLI --------------------------------------------------------------------------------------------------------------

def _flags(argv, names):
    vals, i = {}, 0
    while i < len(argv):
        if argv[i] not in names or argv[i] in vals or i + 1 >= len(argv):
            raise UsageError()
        vals[argv[i]] = argv[i + 1]
        i += 2
    if len(vals) != len(names):
        raise UsageError()
    return vals


def _dispatch(argv):
    if not argv:
        raise UsageError()
    cmd, rest = argv[0], argv[1:]
    if cmd == "render" and len(rest) == 1:
        return render(rest[0])
    if cmd == "--presence" and len(rest) == 2:
        return presence(rest[0], rest[1])
    if cmd == "--capture" and rest:
        v = _flags(rest[1:], ("--workspace", "--hooks-before", "--hooks-after", "--renderer", "--scratch"))
        return capture(rest[0], v["--workspace"], v["--hooks-before"], v["--hooks-after"], v["--renderer"], v["--scratch"])
    if cmd == "--table" and rest:
        v = _flags(rest[1:], ("--presence-dir", "--out"))
        return table(rest[0], v["--presence-dir"], v["--out"])
    raise UsageError()


def main(argv=None):
    """R2-04: every failure is caught here and becomes rc 1 with ONE content-free stderr line and nothing on stdout.
    Usage errors are rc 64, never 2: 2 means 'rendered, no ACTIVE entries', and Python itself exits 2 when it cannot open
    its script (Governance JOURNAL vol. 33)."""
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        rc, out = _dispatch(argv)
        if out:
            sys.stdout.buffer.write(out.encode("utf-8"))
            sys.stdout.buffer.flush()
        return rc
    except UsageError:
        sys.stderr.write(USAGE)
        return RC_USAGE
    except RenderError as exc:
        sys.stderr.write("anchor_render: %s: %s\n" % (exc.element, exc.cls))
        return RC_FAIL
    except ToolError as exc:
        sys.stderr.write("anchor_render: %s\n" % exc)
        return RC_FAIL
    except BrokenPipeError:
        return RC_FAIL
    except Exception as exc:
        sys.stderr.write("anchor_render: internal error: %s\n" % type(exc).__name__)
        return RC_FAIL


if __name__ == "__main__":
    sys.exit(main())

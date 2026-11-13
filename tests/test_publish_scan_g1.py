"""Tests of publish_scan.py's G1 options: --root, --terms, --generic, --exceptions, --counts-only.

Each test builds a throwaway git repository and runs the scanner on it in a subprocess, as a user would. The shapes the
generic patterns hunt (private addresses, MACs, UUIDs) and the identity strings are assembled at run time, so this file
holds none of them literally and stays clean under the scanner's own default and --generic runs.
"""
from __future__ import annotations

import hashlib
import os
import pathlib
import shutil
import subprocess
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
SCANNER = REPO / "publish_scan.py"

PRIVATE_V4 = ".".join(["10", "20", "30", "40"])
DOC_V4 = ".".join(["192", "0", "2", "17"])
PUBLIC_V4 = ".".join(["8", "8", "8", "8"])
MAC = ":".join(["3c", "22", "fb", "11", "22", "33"])
DOC_MAC = ":".join(["00", "00", "5e", "00", "53", "01"])
UUID = "-".join(["0f1e2d3c", "4b5a", "6978", "8a9b", "acbdcedfe0f1"])
TOKEN = "gh" + "p_" + "A1b2C3d4E5f6G7h8I9j0K1l2"          # github-token shape
HOME = "/" + "Users/" + "someone/x"                        # absolute-home-path shape
SERIAL = "C02" + "XK1ABJG5H"                               # serial-like shape
CODENAME = "ZEPHYRQUILL"                                   # a made-up private term


def _git(cwd: pathlib.Path, *args: str) -> None:
    env = {**os.environ, "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@example.invalid",
           "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@example.invalid"}
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, env=env)


def _repo(tmp_path: pathlib.Path, files: dict[str, str], name: str = "target") -> pathlib.Path:
    root = tmp_path / name
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "commit.gpgsign", "false")
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "fixture")
    return root


def _terms(tmp_path: pathlib.Path, text: str = CODENAME + "\n") -> pathlib.Path:
    t = tmp_path / "terms.txt"                             # outside the scanned tree
    t.write_text(text, encoding="utf-8")
    return t


def _run(*args: str, scanner: pathlib.Path = SCANNER, env_extra: dict | None = None) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k != "PUBLISH_SCAN_NO_PRIVATE_TERMS"}
    env.update(env_extra or {})
    return subprocess.run([sys.executable, str(scanner), *args], capture_output=True, text=True, env=env, timeout=120)


# --- --root ---------------------------------------------------------------------------------------------------------

def test_root_not_a_directory_exits_2(tmp_path):
    r = _run("--root", str(tmp_path / "nowhere"), "--terms", str(_terms(tmp_path)))
    assert r.returncode == 2 and "not a directory" in r.stderr


def test_root_not_a_repository_exits_2(tmp_path):
    d = tmp_path / "plain"
    d.mkdir()
    r = _run("--root", str(d), "--terms", str(_terms(tmp_path)))
    assert r.returncode == 2 and "not a git repository" in r.stderr


def test_root_subdirectory_blocks(tmp_path):
    root = _repo(tmp_path, {"sub/a.txt": "hello\n"})
    r = _run("--root", str(root / "sub"), "--terms", str(_terms(tmp_path)))
    assert r.returncode == 1 and "subdirectory" in r.stdout


def test_root_scans_the_other_repository(tmp_path):
    clean = _repo(tmp_path, {"a.txt": "nothing to see\n"}, "clean")
    r = _run("--root", str(clean), "--terms", str(_terms(tmp_path)))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "CLEAN" in r.stdout
    dirty = _repo(tmp_path, {"a.txt": f"token {TOKEN}\n"}, "dirty")
    r = _run("--root", str(dirty), "--terms", str(_terms(tmp_path)))
    assert r.returncode == 1 and "[github-token]" in r.stdout


def test_root_gives_no_self_exemption_to_a_namesake(tmp_path):
    # In its own repository the scanner's PATTERNS table may name a home path; a file of the same name elsewhere may not.
    root = _repo(tmp_path, {"publish_scan.py": f'PATTERNS = {{\n    "x": "{HOME}",\n}}\n'})
    r = _run("--root", str(root), "--terms", str(_terms(tmp_path)))
    assert r.returncode == 1 and "[absolute-home-path]" in r.stdout


# --- --terms --------------------------------------------------------------------------------------------------------

def test_terms_missing_exits_2(tmp_path):
    root = _repo(tmp_path, {"a.txt": "x\n"})
    r = _run("--root", str(root), "--terms", str(tmp_path / "no_such_terms"))
    assert r.returncode == 2 and "does not exist" in r.stderr


def test_terms_directory_exits_2(tmp_path):
    root = _repo(tmp_path, {"a.txt": "x\n"})
    r = _run("--root", str(root), "--terms", str(tmp_path))
    assert r.returncode == 2 and "not a regular file" in r.stderr


def test_terms_file_applies_its_terms(tmp_path):
    root = _repo(tmp_path, {"notes.md": f"the {CODENAME.lower()} project\n"})
    r = _run("--root", str(root), "--terms", str(_terms(tmp_path)))
    assert r.returncode == 1 and "[private-codename]" in r.stdout
    assert "1 project-codename pattern(s) loaded from" in r.stdout


def test_terms_file_with_no_term_blocks_unless_declared(tmp_path):
    root = _repo(tmp_path, {"a.txt": "x\n"})
    empty = _terms(tmp_path, "# comments only\n")
    r = _run("--root", str(root), "--terms", str(empty))
    assert r.returncode == 1 and "holds no term" in r.stdout
    r = _run("--root", str(root), "--terms", str(empty), env_extra={"PUBLISH_SCAN_NO_PRIVATE_TERMS": "1"})
    assert r.returncode == 0 and "private terms not checked" in r.stdout


# --- --generic ------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("text,pattern", [
    (f"host = {PRIVATE_V4}\n", "ipv4-address"),
    (f"mac {MAC}\n", "mac-address"),
    (f"id {UUID}\n", "uuid"),
    (f"serial number: {SERIAL}\n", "serial-like"),
])
def test_generic_flags_shapes_only_when_asked(tmp_path, text, pattern):
    root = _repo(tmp_path, {"a.txt": text})
    t = str(_terms(tmp_path))
    off = _run("--root", str(root), "--terms", t)
    assert off.returncode == 0, off.stdout
    on = _run("--root", str(root), "--terms", t, "--generic")
    assert on.returncode == 1 and f"[{pattern}]" in on.stdout


def test_generic_spares_documentation_and_public_ranges(tmp_path):
    root = _repo(tmp_path, {"a.txt": f"doc {DOC_V4}\npublic {PUBLIC_V4}\ndoc mac {DOC_MAC}\n"})
    r = _run("--root", str(root), "--terms", str(_terms(tmp_path)), "--generic")
    assert r.returncode == 0, r.stdout


def test_generic_reads_history_too(tmp_path):
    root = _repo(tmp_path, {"a.txt": f"host {PRIVATE_V4}\n"})
    (root / "a.txt").write_text("host removed\n", encoding="utf-8")
    _git(root, "commit", "-q", "-am", "scrub")
    r = _run("--root", str(root), "--terms", str(_terms(tmp_path)), "--generic")
    assert r.returncode == 1 and "BLOCKED on history" in r.stdout and "[ipv4-address]" in r.stdout


def test_generic_keeps_the_scanner_self_exempt_in_its_own_tables(tmp_path):
    # The scanner's private-range table holds private addresses; in its own repository that is not a hit.
    root = _repo(tmp_path, {"README.md": "x\n"}, "own")
    shutil.copy(SCANNER, root / "publish_scan.py")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "add scanner")
    r = _run("--terms", str(_terms(tmp_path)), "--generic", scanner=root / "publish_scan.py")
    assert r.returncode == 0, r.stdout


# --- --exceptions ---------------------------------------------------------------------------------------------------

def test_exceptions_by_exact_line_and_by_sha256(tmp_path):
    line_a, line_b = f"token {TOKEN}", f"host = {PRIVATE_V4}"
    root = _repo(tmp_path, {"a.txt": line_a + "\n", "b.txt": line_b + "\n"})
    ex = tmp_path / "exceptions.tsv"
    ex.write_text(f"# reviewed\na.txt\t{line_a}\nb.txt\tsha256:{hashlib.sha256(line_b.encode()).hexdigest()}\n",
                  encoding="utf-8")
    r = _run("--root", str(root), "--terms", str(_terms(tmp_path)), "--generic", "--exceptions", str(ex))
    assert r.returncode == 0, r.stdout
    assert "2 --exceptions entr(ies)" in r.stdout


def test_exceptions_stale_entry_fails_the_run(tmp_path):
    root = _repo(tmp_path, {"a.txt": "nothing\n"})
    ex = tmp_path / "exceptions.tsv"
    ex.write_text("a.txt\tno such line\n", encoding="utf-8")
    r = _run("--root", str(root), "--terms", str(_terms(tmp_path)), "--exceptions", str(ex))
    assert r.returncode == 1 and "STALE exception(s): 1" in r.stdout
    assert "no such line" not in r.stdout                  # the key is shown by digest, not by text


def test_exceptions_malformed_or_missing_exits_2(tmp_path):
    root = _repo(tmp_path, {"a.txt": "x\n"})
    bad = tmp_path / "bad.tsv"
    bad.write_text("a.txt no tab here\n", encoding="utf-8")
    r = _run("--root", str(root), "--terms", str(_terms(tmp_path)), "--exceptions", str(bad))
    assert r.returncode == 2 and "line 1" in r.stderr
    r = _run("--root", str(root), "--terms", str(_terms(tmp_path)), "--exceptions", str(tmp_path / "none.tsv"))
    assert r.returncode == 2 and "cannot read --exceptions" in r.stderr


def test_exceptions_clear_a_history_hit(tmp_path):
    line = f"token {TOKEN}"
    root = _repo(tmp_path, {"a.txt": line + "\n"})
    (root / "a.txt").write_text("gone\n", encoding="utf-8")
    _git(root, "commit", "-q", "-am", "scrub")
    ex = tmp_path / "exceptions.tsv"
    ex.write_text(f"a.txt\t{line}\n", encoding="utf-8")
    r = _run("--root", str(root), "--terms", str(_terms(tmp_path)), "--exceptions", str(ex))
    assert r.returncode == 0, r.stdout


# --- --counts-only --------------------------------------------------------------------------------------------------

def test_counts_only_never_prints_hit_text(tmp_path):
    name = f"{CODENAME.lower()}_notes.md"
    root = _repo(tmp_path, {"a.txt": f"token {TOKEN}\nhost {PRIVATE_V4}\n", name: "x\n"})
    r = _run("--root", str(root), "--terms", str(_terms(tmp_path)), "--generic", "--counts-only")
    assert r.returncode == 1
    out = r.stdout + r.stderr
    assert "COUNTS (no matched text is printed)" in out
    assert "pattern github-token: 1" in out and "pattern ipv4-address: 1" in out
    assert "file a.txt: 2" in out
    for secret in (TOKEN, PRIVATE_V4, CODENAME, CODENAME.lower()):
        assert secret not in out, f"--counts-only printed {secret!r}"
    assert "name withheld, sha256" in out                  # a path that is itself a hit is shown by digest only


def test_counts_only_hides_history_text(tmp_path):
    root = _repo(tmp_path, {"a.txt": f"token {TOKEN}\n"})
    (root / "a.txt").write_text("gone\n", encoding="utf-8")
    _git(root, "commit", "-q", "-am", "scrub")
    r = _run("--root", str(root), "--terms", str(_terms(tmp_path)), "--counts-only")
    assert r.returncode == 1 and "history pattern github-token: 1" in r.stdout
    assert TOKEN not in r.stdout + r.stderr

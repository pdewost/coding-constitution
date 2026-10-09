#!/usr/bin/env python3.12
"""test_verifier_recognizer.py — matrix for verifier_recognizer.py (H1 of
PLAN_hooks_outcome_tamper_loopbound_2026-10-05).

Each case: (description, command, expect) where expect is
  "clear"  — a verifier whose exit status is observable,
  "masked" — a verifier whose exit status the command throws away,
  "none"   — no verifier at all (reads, echoes, dry runs, look-alikes).
The first block are the S4-20 / masking forms that cleared the OLD substring gate.

Run: python3.12 test_verifier_recognizer.py [dir/containing/verifier_recognizer.py]   (exit 0 = green)
"""
import importlib.util
import pathlib
import sys

# argv[1] is read ONLY when run as a script: under pytest, sys.argv holds pytest's own arguments
# (2026-10-05: reading it at import broke bare `pytest` collection in governance/, fixed upstream by 362042e).
HERE = (pathlib.Path(sys.argv[1]).resolve() if __name__ == "__main__" and len(sys.argv) > 1
        else pathlib.Path(__file__).resolve().parent)
spec = importlib.util.spec_from_file_location("vr", HERE / "verifier_recognizer.py")
vr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vr)

CASES = [
    # -- forms the old substring rule wrongly accepted (S4-20 and masking)
    ("cat a test file",              "cat tests/test_x.py",                                   "none"),
    ("grep in a test file",          "grep -n assert tests/test_x.py",                        "none"),
    ("ls --dry-run",                 "ls --dry-run",                                          "none"),
    ("rsync --dry-run",              "rsync -av --dry-run src/ dst/",                         "none"),
    ("echo pytest",                  "echo pytest",                                           "none"),
    ("echo test_ bypass",            "echo test_bypass",                                      "none"),
    ("sed on a test file (read)",    "sed -n 1,20p tests/test_x.py",                          "none"),
    ("pytest piped to tail",         "pytest -q 2>&1 | tail -5",                              "masked"),
    ("pytest || true",               "pytest || true",                                        "masked"),
    ("pytest || :",                  "pytest || :",                                           "masked"),
    ("pytest then echo",             "pytest; echo done",                                     "masked"),
    ("pytest backgrounded",          "pytest &",                                              "masked"),
    ("negated pytest",               "! pytest",                                              "masked"),
    ("pytest inside if",             "if pytest; then echo ok; fi",                           "masked"),
    ("only-if-previous-failed",      "make build || pytest",                                  "masked"),
    ("hook matrix piped",            "python3.12 .claude/hooks/test_pretool_s4_bypasses.py | tail -1", "masked"),
    ("test_hooks piped",             "bash adapters/claude-code/test_hooks.sh 2>&1 | tail -4", "masked"),
    ("newline then echo",            "pytest -q\necho finished",                              "masked"),
    # -- forms that must clear
    ("bare pytest",                  "pytest",                                                "clear"),
    ("pytest -q path",               "pytest -q tests/test_api.py",                           "clear"),
    ("python3.12 -m pytest",         "python3.12 -m pytest tests -q",                         "clear"),
    ("python -m unittest",           "python -m unittest discover",                           "clear"),
    ("py_compile",                   "python3.12 -m py_compile src/app.py",                   "clear"),
    ("cd && pytest",                 "cd /tmp/proj && pytest -q",                             "clear"),
    ("pytest && echo",               "pytest -q && echo green",                               "clear"),
    ("pipefail pipe",                "set -o pipefail; pytest -q | tail -3",                  "clear"),
    ("errexit then echo",            "set -e; pytest; echo done",                             "clear"),
    ("redirected output",            "pytest -q > /tmp/out.txt 2>&1",                         "clear"),
    ("env wrapper",                  "env PYTHONPATH=src pytest",                             "clear"),
    ("timeout wrapper",              "timeout 600 python3.12 -m pytest tests",                "clear"),
    ("uv run",                       "uv run pytest",                                         "clear"),
    ("assignment prefix",            "PYTHONDONTWRITEBYTECODE=1 python3.12 -m pytest -q",     "clear"),
    ("test script by python",        "python3.12 .claude/hooks/test_pretool_s4_bypasses.py candidate.py", "clear"),
    ("test script by bash",          "bash adapters/claude-code/test_hooks.sh",    "clear"),
    ("executable test script",       "./test_integration.sh",                                 "clear"),
    ("bash -n",                      "bash -n deploy.sh",                                     "clear"),
    ("zsh -n (STATUS 10-02 gap)",    "zsh -n profile.zsh",                                    "clear"),
    ("osacompile",                   "osacompile -o /tmp/x.scpt script.applescript",          "clear"),
    ("npm test",                     "npm test",                                              "clear"),
    ("npm run test:unit",            "npm run test:unit",                                     "clear"),
    ("npx tsc --noEmit",             "npx tsc --noEmit",                                      "clear"),
    ("node --check",                 "node --check app.js",                                   "clear"),
    ("node --test",                  "node --test",                                           "clear"),
    ("go test",                      "go test ./...",                                         "clear"),
    ("cargo test",                   "cargo test",                                            "clear"),
    ("subshell",                     "(cd sub && pytest)",                                    "clear"),
    # -- Seat A (MiMo Flash) 2026-10-05
    ("bash -c pytest (F6)",         "bash -c 'cd proj && pytest -q'",                       "clear"),
    ("sh -c masked inside (F6)",     "sh -c 'pytest -q | tail -1'",                           "masked"),
    ("pipefail AFTER the pipe (F11)", "pytest -q | head -1; set -o pipefail",                 "masked"),
    ("set +o pipefail (F11)",        "set +o pipefail; pytest -q | head -1",                  "masked"),
    ("set -e then +e (F11)",         "set -e; set +e; pytest; echo done",                     "masked"),
    ("$VAR heredoc body (F12)",      "cat <<$E\npytest -q\n$E",                              "none"),
    # -- Seat B (reviewer A) 2026-10-05
    ("errexit && list (B-11)",       "set -e; pytest -q >/dev/null && echo ok; echo done",   "masked"),
    ("errexit, never ran (B-11)",    "set -e; false && pytest -q; echo done",                "masked"),
    ("subshell-local pipefail (B-12)", "( set -o pipefail ) ; pytest -q | tail -1",          "masked"),
    ("subshell then ; (B-12)",       "true && (pytest -q); echo done",                       "masked"),
    ("errexit simple ; still ok",    "set -e; pytest -q; echo done",                         "clear"),
    ("pytest --version (B-13)",      "pytest --version",                                     "none"),
    ("pytest --collect-only (B-13)", "pytest --collect-only -q",                             "none"),
    ("swift --list-tests (B-13)",    "swift test --list-tests",                              "none"),
    ("playwright install (B-13)",    "npx playwright install",                               "none"),
    ("playwright test",              "npx playwright test",                                  "clear"),
    # -- look-alikes that are NOT verifiers
    ("python script not a test",     "python3.12 scripts/build.py",                           "none"),
    ("python -c",                    "python3.12 -c 'import pytest'",                         "none"),
    ("bash -c",                      "bash -c 'echo hi'",                                     "none"),
    ("npm install",                  "npm install",                                           "none"),
    ("tsc build",                    "npx tsc -p .",                                          "none"),
    ("pip install pytest",           "pip install pytest",                                    "none"),
    # -- reviewer A round 2b (2026-10-06): no-run modes (B2-16) and wrapper option values (B2-40)
    ("make -n check (B2-16)",        "make -n check",                                         "none"),
    ("make --dry-run test (B2-16)",  "make --dry-run test",                                   "none"),
    ("make -q check (B2-16)",        "make -q check",                                         "none"),
    ("make --question (B2-16)",      "make --question test",                                  "none"),
    ("make -t test (B2-16)",         "make -t test",                                          "none"),
    ("make -kn test (B2-16)",        "make -kn test",                                         "none"),
    ("make -j4 test still runs",     "make -j4 test",                                         "clear"),
    ("pytest --fixtures-per-test",   "pytest --fixtures-per-test",                            "none"),
    ("nice -n 10 pytest (B2-40)",    "nice -n 10 pytest -q",                                  "clear"),
    ("env -u VAR pytest (B2-40)",    "env -u PYTHONPATH pytest -q",                           "clear"),
    ("sudo -u root pytest (B2-40)",  "sudo -u root pytest -q",                                "clear"),
    ("timeout -s TERM (B2-40)",      "timeout -s TERM 600 pytest -q",                         "clear"),
    ("/bin/bash heredoc (B2-07)",    "/bin/bash <<'EOF'\npytest -q\nEOF",                    "clear"),
    ("bash -s -- heredoc (B2-07)",   "bash -s -- <<'EOF'\npytest -q\nEOF",                   "clear"),
]

# §3a (PLAN v0.7, founder ruling 2026-10-06, option A): a Bash call CLEARS only as a PLAIN verifier —
# optional `cd <dir> &&` prefixes, status-preserving wrappers, exactly one verifier segment, no `;` `||`
# `|` `&`, subshell or redirect other than 2>&1. "plain" = plain_verifier() recognises it; "no" = never.
PLAIN = [
    ("bare pytest",                  "pytest",                                                "plain"),
    ("pytest -q path",               "pytest -q tests/test_api.py",                           "plain"),
    ("python3.12 -m pytest 2>&1",    "python3.12 -m pytest tests -q 2>&1",                    "plain"),
    ("cd && pytest",                 "cd /tmp/proj && pytest -q",                             "plain"),
    ("two cd prefixes",              "cd /tmp && cd proj && pytest -q",                       "plain"),
    ("env assignment",               "env PYTHONPATH=src pytest",                             "plain"),
    ("assignment prefix",            "PYTHONDONTWRITEBYTECODE=1 python3.12 -m pytest -q",     "plain"),
    ("nice -n 10 (B2-40)",           "nice -n 10 pytest -q",                                  "plain"),
    ("timeout -s TERM 600 (B2-40)",  "timeout -s TERM 600 pytest -q",                         "plain"),
    ("uv run --with",                "uv run --with pytest pytest -q",                        "plain"),
    ("npx tsc --noEmit",             "npx tsc --noEmit",                                      "plain"),
    ("bash -n",                      "bash -n deploy.sh",                                     "plain"),
    ("test script",                  "python3.12 .claude/hooks/test_pretool_s4_bypasses.py candidate.py", "plain"),
    ("trailing comment",             "pytest -q  # unit tests",                               "plain"),
    ("pytest && echo (now no)",      "pytest -q && echo green",                               "no"),
    ("pytest; echo (B2-15)",         "(set -e; pytest -q >/dev/null); echo done",             "no"),
    ("two runs (B2-17)",             "pytest tests/unit.py; pytest tests/integration.py",     "no"),
    ("then a write (B2-18)",         "pytest -q && black src/app.py",                         "no"),
    ("redirect-only after (B2-21)",  "pytest -q && > src/generated.py",                       "no"),
    ("pipefail pipe",                "set -o pipefail; pytest -q | tail -3",                  "no"),
    ("piped to tail",                "pytest -q 2>&1 | tail -5",                              "no"),
    ("redirected to a file",         "pytest -q > /tmp/out.txt 2>&1",                         "no"),
    ("stderr to null",               "pytest -q 2>/dev/null",                                 "no"),
    ("subshell",                     "(cd sub && pytest)",                                    "no"),
    ("command substitution",         "pytest $(cat targets.txt)",                             "no"),
    ("backticks",                    "pytest `cat targets.txt`",                              "no"),
    ("backgrounded",                 "pytest &",                                              "no"),
    ("negated",                      "! pytest",                                              "no"),
    ("bash -c",                      "bash -c 'pytest -q'",                                   "no"),
    ("two verifiers",                "bash -n a.sh && zsh -n b.zsh",                          "no"),
    ("cd with a variable",           "cd $DIR && pytest",                                     "no"),
    ("heredoc fed",                  "python3.12 - <<'EOF'\nimport pytest\nEOF",            "no"),
    ("make -n check (B2-16)",        "make -n check",                                         "no"),
    ("not a verifier",               "ls -la",                                                "no"),
    # reviewer A round 3 (2026-10-06): the closed grammar (founder ruling "Fix all + reviewer A round 4")
    ("negation behind time (B3-01)", "time ! bash -n src/broken.sh",                          "no"),
    ("mid-word # (B3-02)",           "bash -n src/broken#v1.sh || true",                      "no"),
    ("mid-word # pkg (B3-02)",       "npm test -- --grep=#smoke || true",                     "no"),
    ("env -S (B3-03)",               "env -S true bash -n src/broken.sh",                     "no"),
    ("command -v (B3-03)",           "command -v make test",                                  "no"),
    ("uv run --help (B3-03)",        "uv run --help bash -n src/broken.sh",                   "no"),
    ("dollar expansion",             "pytest $TESTS",                                         "no"),
    ("leading blank line (B3-29)",   "\npytest -q",                                           "plain"),
    ("comment line (B3-29)",         "# Run checks\npytest -q",                               "plain"),
    ("cd && newline (B3-29)",        "cd proj &&\npytest -q",                                 "plain"),
    ("uv --quiet run (B3-30)",       "uv --quiet run pytest -q",                              "plain"),
    ("uv --directory run (B3-30)",   "uv --directory proj run pytest -q",                     "plain"),
    ("make -C (B3-30)",              "make -C proj test",                                     "plain"),
    ("npm --prefix (B3-30)",         "npm --prefix web test",                                 "plain"),
    ("yarn --cwd (B3-30)",           "yarn --cwd web test",                                   "plain"),
    ("quoted cd with spaces",        'cd "/srv/example/Example Projects" && pytest -q',  "plain"),
    ("pytest -k quoted expr",        "pytest -q -k 'smoke and not slow'",                     "plain"),
    ("pytest node id brackets",      "pytest tests/test_a.py::test_x[case-1]",                "plain"),
    ("sudo -u (B2-40 kept)",         "sudo -u root pytest -q",                                "plain"),
    ("env -u (B2-40 kept)",          "env -u PYTHONPATH pytest -q",                           "plain"),
    # reviewer A round 4 (2026-10-06)
    ("comment ending && (B4-01)",    "bash -n src/broken.sh # lint first &&\ntrue",           "no"),
    ("comment ending \\ (B4-01)",   "bash -n src/broken.sh # note \\\ntrue",                "no"),
    ("npm --if-present (B4-03)",     "npm test --if-present",                                 "no"),
    ("npm run --if-present (B4-03)", "npm run test:unit --if-present",                        "no"),
    ("make -i (B4-04)",              "make -i test",                                          "no"),
    ("make --ignore-errors (B4-04)", "make --ignore-errors check",                            "no"),
    ("make -ik (B4-04)",             "make -ik test",                                         "no"),
    ("MAKEFLAGS=-n (B4-04)",         "MAKEFLAGS=-n make test",                                "no"),
    ("python -O test script (B4-02)", "python3 -O tests/test_assertions.py",                  "no"),
    ("python -m pytest -c (B4-31)",  "python3 -m pytest -c pytest.ini -q",                    "plain"),
    ("python -c still refused",      "python3 -c 'import pytest'",                            "no"),
]

# plain_verdict(kind, text, is_error): what the tool result must show for a plain verifier to clear.
PLAIN_VERDICTS = [
    ("pytest summary",          "pytest",       "===== 69 passed in 3.6s =====",            "pass"),
    ("pytest no summary",       "pytest",       "",                                          "unknown"),
    ("go no tests (B2-19)",     "go-test",      "ok  \texample.com/a\t0.01s [no tests to run]", "empty"),    # v0.8: explicit no-run output is EMPTY (was unknown; neither clears)
    ("go ok",                   "go-test",      "ok  \texample.com/a\t0.01s",                "pass"),
    ("go cached",               "go-test",      "ok  \texample.com/a\t(cached)",             "pass"),
    ("cargo 0 passed (B2-19)",  "cargo-test",   "test result: ok. 0 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out", "empty"),    # v0.8: explicit no-run output is EMPTY (was unknown; neither clears)
    ("cargo 3 passed",          "cargo-test",   "test result: ok. 3 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out", "pass"),
    ("jest 0 total (B2-19)",    "js-test",      "Tests: 0 passed, 0 total",                  "empty"),
    ("swift 0 tests (B2-19)",   "swift-test",   "Executed 0 tests, with 0 failures",         "empty"),
    ("syntax check silent",     "shell-syntax", "",                                          "pass"),
    ("repo script silent",      "test-script",  "",                                          "pass"),
    ("repo script red",         "test-script",  "RESULT: 36 passed, 1 failed",               "fail"),
    ("0/0 matrix (B2-19)",      "test-script",  "0/0 passed",                                "empty"),
    ("0/0 matrix, summary kind", "pytest",      "0/0 passed",                                "empty"),
    ("node --test TAP",         "node-test",    "# tests 3\n# pass 3\n# fail 0",            "pass"),
    ("node --test TAP red",     "node-test",    "# tests 3\n# pass 2\n# fail 1",            "fail"),
    ("mocha passing",           "js-test",      "  3 passing (12ms)",                        "pass"),
    ("mocha failing",           "js-test",      "  2 passing (12ms)\n  1 failing",           "fail"),
    ("npm test = script",       "pkg-test",     "> x@1 test\n> node t.js\nall good",         "pass"),
    # reviewer A round 3 (B3-04, B3-31): script kinds never pass on explicit zero-run or failed runner output
    ("script cargo 0 (B3-04)",  "test-script",  "test result: ok. 0 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out", "empty"),
    ("make go no-run (B3-04)",  "make-test",    "ok  \texample/a\t0.01s [no tests to run]",   "empty"),
    ("npm no tests (B3-04)",    "pkg-test",     "No tests found, exiting with code 0",        "empty"),
    ("npm vitest red (B3-04)",  "pkg-test",     "Test Files  1 failed | 2 passed (3)\n      Tests  1 failed | 9 passed (10)", "fail"),
    ("make nothing (B3-04)",    "make-test",    "make: Nothing to be done for `test'.",       "empty"),
    ("go some ran",             "make-test",    "ok  \texample/a\t0.01s [no tests to run]\nok  \texample/b\t0.02s", "pass"),
    ("swift testing (B3-31)",   "swift-test",   "Test Suite 'All tests' passed at 2026-10-06 10:00:00.000.\n\t Executed 0 tests, with 0 failures (0 unexpected) in 0.000 (0.001) seconds\n\u25c7 Test run started.\n\u2714 Test run with 12 tests passed after 0.004 seconds.", "pass"),
    ("swift testing red",       "swift-test",   "\u2718 Test run with 12 tests failed after 0.004 seconds with 1 issue.", "fail"),
    # reviewer A round 4 (2026-10-06)
    ("py test file no summary (B4-02)", "py-test-script", "",                                  "unknown"),
    ("py test module no summary",  "py-test-module", "",                                       "unknown"),
    ("py test file with matrix",   "py-test-script", "39/39 passed",                           "pass"),
    ("npm silent (B4-03)",         "pkg-test",     "",                                          "unknown"),
    ("make ignored error (B4-04)", "make-test",    "make: [Makefile:2: test] Error 2 (ignored)", "fail"),
    ("playwright red (B4-05)",     "pkg-test",     "Running 3 tests using 1 worker\n  1 failed\n    [chromium] a.spec.ts\n  2 passed (3.1s)", "fail"),
    ("jest suite red (B4-05)",     "pkg-test",     "FAIL src/a.test.js\nTest Suites: 1 failed, 1 passed, 2 total\nTests:       5 passed, 5 total", "fail"),
    ("background ack (B4-06)",     "shell-syntax", "Command running in background with ID: bash_1", "unknown"),
]


def plain_class(cmd: str) -> str:
    run, _why = vr.plain_verifier(cmd)
    return "plain" if run else "no"


def classify(cmd: str) -> str:
    runs = vr.analyse(cmd)
    if not runs:
        return "none"
    return "clear" if any(r["observable"] for r in runs) else "masked"


VERDICTS = [
    ("pytest summary",         "===== 69 passed in 3.6s =====",            False, "pass"),
    ("pytest failure",         "=== 1 failed, 68 passed in 3s ===",        False, "fail"),
    ("pytest error",           "=== 2 errors in 1s ===",                   False, "fail"),
    ("no tests collected",     "collected 0 items\n\nno tests ran",        False, "empty"),
    ("unittest none",          "Ran 0 tests in 0.000s\n\nOK",              False, "empty"),
    ("unittest ok",            "Ran 12 tests in 0.1s\n\nOK",               False, "pass"),
    ("unittest failed",        "Ran 3 tests\n\nFAILED (failures=1)",       False, "fail"),
    ("matrix all green",       "39/39 passed",                             False, "pass"),
    ("matrix one red",         "38/39 passed",                             False, "fail"),
    ("test_hooks red",         "RESULT: 36 passed, 1 failed",              False, "fail"),
    ("weak ERROR, unmasked",   "ERROR something logged\n3 passed in 0.1s", False, "pass"),
    ("weak ERROR, masked",     "ERROR something logged\n3 passed in 0.1s", True,  "fail"),
    ("B-14 grep -o fragment",  "2 passed",                                  True,  "unknown"),
    ("B-14 lone tick",         "\x1b[32m✓\x1b[0m one good test",          True,  "unknown"),
    ("B-15 ansi FAILED",       "\x1b[31mFAILED\x1b[0m t - AssertionError: 0 failed", True, "fail"),
    ("B-15 CR FAILED",         "\rFAILED t - AssertionError: 0 failed",     True,  "fail"),
    ("B-31 app log, unmasked", "2 passed\nThis diagnostic documents 1 failed request", False, "unknown"),
    ("pytest -q summary",      "3 passed in 0.12s",                         True,  "pass"),
    ("pytest banner summary",  "===== 1 failed, 2 passed in 0.3s =====",    False, "fail"),
    ("silent output",          "",                                         False, "unknown"),
    # reviewer A round 2b (2026-10-06): empty runs are not passes (B2-19), app prose is not a summary (B2-38)
    ("0/0 matrix (B2-19)",     "0/0 passed",                               False, "empty"),
    ("RESULT 0 (B2-19)",       "RESULT: 0 passed, 0 failed",               False, "empty"),
    ("xctest 0 (B2-19)",       "Executed 0 tests, with 0 failures",        False, "empty"),
    ("app log prose (B2-38)",  "Application log: 1 failed request in 0.3s\n2 passed in 0.1s", False, "pass"),
    ("app prose masked",       "Application log: 1 failed request in 0.3s\n2 passed in 0.1s", True, "pass"),
    ("expected failure prose", "Expected scenario completed with 1 failure\n2 passed in 0.1s", False, "pass"),
    ("pytest with warnings",   "== 1 failed, 2 passed, 3 warnings in 0.3s ==", False, "fail"),
    ("pytest error, passed",   "2 passed, 1 error in 0.4s",                False, "fail"),
    ("swift red",              "Executed 4 tests, with 1 failure (0 unexpected) in 0.1 seconds", False, "fail"),
]

# reviewer A round 5 (2026-10-06, on the LANDED v0.9): (description, command, its exit-0 output, does it clear?)
CLEARS = [
    ("uv run test file (B5-03)",       "uv run tests/test_api.py",                     "",                      False),
    ("./test file (B5-03)",            "./tests/test_api.py",                          "",                      False),
    ("bare test file (B5-03)",         "tests/test_api.py",                            "",                      False),
    ("test file with summary",         "uv run tests/test_api.py",                     "3 passed in 0.1s",      True),
    ("env MAKEFLAGS=-n (B5-04)",       "env MAKEFLAGS=-n make test",                   "python3 -m pytest -q",  False),
    ("env GNUMAKEFLAGS (B5-04)",       "env GNUMAKEFLAGS=--dry-run make check",        "pytest -q",             False),
    ("unittest all skipped (B5-11)",   "python3 -m unittest discover -s tests",
     "sss\n" + "-" * 70 + "\nRan 3 tests in 0.001s\n\nOK (skipped=3)",                                      False),
    ("unittest some skipped",          "python3 -m unittest discover -s tests",        "Ran 3 tests in 0.001s\n\nOK (skipped=1)", True),
    ("doctest silent (B5-37)",         "python3 -m doctest src/app.py",                "",                      False),
    ("doctest -v 0 tests (B5-37)",     "python3 -m doctest -v src/app.py",
     "1 items had no tests:\n    app\n0 tests in 1 items.\n0 passed and 0 failed.\nTest passed.",             False),
    ("doctest -v real",                "python3 -m doctest -v src/app.py",
     "2 items passed all tests:\n   2 tests in app\n2 tests in 2 items.\n2 passed and 0 failed.\nTest passed.", True),
    ("xcodebuild settings (B5-38)",    "xcodebuild test -showBuildSettings -scheme App", "    Build settings for action test", False),
    ("xcodebuild dry-run (B5-38)",     "xcodebuild test -dry-run -scheme App",         "",                      False),
    ("xcodebuild silent (B5-38)",      "xcodebuild test -scheme App",                  "** TEST SUCCEEDED **",  False),
    ("xcodebuild summary",             "xcodebuild test -scheme App",
     "Executed 4 tests, with 0 failures (0 unexpected) in 0.2 (0.3) seconds\n** TEST SUCCEEDED **",              True),
    ("pnpm no match (B5-39)",          "pnpm --filter web test",                       'No projects matched the filters in "/repo"', False),
]

TEST_PATHS = [
    ("tests/test_api.py", True), ("src/app.py", False), ("pkg/foo_test.py", True),
    ("web/src/App.spec.tsx", True), ("conftest.py", True), ("tests/fixtures/data.json", True),
    ("pytest.ini", True), (".github/workflows/ci.yml", True), ("src/testing_utils.py", False),
    ("docs/test-plan.md", False),
]


def main() -> int:
    bad = 0
    for desc, cmd, exp in CASES:
        got = classify(cmd)
        ok = got == exp
        bad += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {desc:28s} expect={exp:6s} got={got}")
    for desc, text, masked, exp in VERDICTS:
        got = vr.output_verdict(text, masked=masked)
        ok = got == exp
        bad += not ok
        print(f"{'PASS' if ok else 'FAIL'}  verdict: {desc:19s} expect={exp:7s} got={got}")
    for desc, cmd, exp in PLAIN:
        got = plain_class(cmd)
        ok = got == exp
        bad += not ok
        print(f"{'PASS' if ok else 'FAIL'}  plain: {desc:28s} expect={exp:5s} got={got}")
    for desc, kind, text, exp in PLAIN_VERDICTS:
        got = vr.plain_verdict(kind, text)
        ok = got == exp
        bad += not ok
        print(f"{'PASS' if ok else 'FAIL'}  plain verdict: {desc:24s} expect={exp:7s} got={got}")
    for desc, cmd, text, exp in CLEARS:
        run, _w = vr.plain_verifier(cmd)
        got = bool(run) and vr.plain_verdict(run["kind"], text) == "pass"
        ok = got == exp
        bad += not ok
        print(f"{'PASS' if ok else 'FAIL'}  clears: {desc:30s} expect={exp!s:5s} got={got}")
    for path, exp in TEST_PATHS:
        got = vr.is_test_path(path)
        ok = got == exp
        bad += not ok
        print(f"{'PASS' if ok else 'FAIL'}  is_test_path {path:28s} expect={exp} got={got}")
    paths = vr.analyse("pytest -q tests/test_a.py::test_one -k smoke")[0]["paths"]
    ok = paths == ["tests/test_a.py"]
    bad += not ok
    print(f"{'PASS' if ok else 'FAIL'}  explicit path extraction  got={paths}")
    total = len(CASES) + len(VERDICTS) + len(PLAIN) + len(PLAIN_VERDICTS) + len(CLEARS) + len(TEST_PATHS) + 1
    print(f"\n{total - bad}/{total} passed")
    return 1 if bad else 0


def test_matrix():
    """pytest entry point: the whole matrix must be green."""
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())

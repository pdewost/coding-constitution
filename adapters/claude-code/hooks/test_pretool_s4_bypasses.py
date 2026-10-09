#!/usr/bin/env python3.12
"""test_pretool_s4_bypasses.py -- RED/GREEN matrix for the matcher bypasses reviewer A S4 found and
Governance verified by pure-function execution against the live hook (2026-10-03,
NEOCORTEX/REVIEWER_A_RETURN_s4_hooks_review_2026-10-03.md).

Same convention as test_pretool_l0_guard.py: dangerous literals are assembled at runtime, because
the guard matches command TEXT and would deny the writing of this very file through Bash.

Run: python3.12 .claude/hooks/test_pretool_s4_bypasses.py [path/to/hook.py]
argv[1] overrides the hook under test (RED against the live hook, GREEN against a candidate).
Exit 0 = all green.
"""
import json, pathlib, subprocess, sys

HOOK = pathlib.Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else \
    pathlib.Path(__file__).resolve().parent / "pretool_guard.py"
L0 = "PAICodeConstitution" + "-2026.md"
ARC = "NEOCORTEX/_arch" + "ive"
DEL = "del" + "ete"
PER = "per" + "son"


def fires(cmd: str) -> bool:
    out = subprocess.run([sys.executable, str(HOOK)], input=json.dumps({"tool_input": {"command": cmd}}),
                         capture_output=True, text=True).stdout
    return bool(out.strip())


# (description, command, expect_deny)
CASES = [
    # -- L0 write forms that were allowed (S4-12, -16, -17, -18)
    ("newline splits segments",  f"cd /tmp\ncp /tmp/new.md {L0}",            True),
    ("one-line control",         f"cd /tmp; cp /tmp/new.md {L0}",            True),
    ("sudo cp",                  f"sudo cp draft.md {L0}",                   True),
    ("env cp",                   f"env FOO=1 cp draft.md {L0}",              True),
    ("command cp",               f"command cp draft.md {L0}",                True),
    ("nohup cp",                 f"nohup cp draft.md {L0}",                  True),
    ("clobber redirect",         f"cat d >| {L0}",                           True),
    ("ampersand redirect",       f"cat d &> {L0}",                           True),
    ("fd1 redirect",             f"cat d 1> {L0}",                           True),
    ("patch",                    f"patch {L0} < /tmp/l0.diff",               True),
    ("ed",                       f"ed {L0}",                                 True),
    ("sed long in-place",        f"sed --in-place 's/a/b/' {L0}",            True),
    # -- archive forms that were allowed (S4-13, -14, -19)
    ("trash archive",            f"trash {ARC}",                             True),
    ("cd archive then rm",       f"cd {ARC} && rm -rf old",                  True),
    ("cd archive newline rm",    f"cd {ARC}\nrm -rf old",                    True),
    ("rsync --delete archive",   f"rsync -a --{DEL} /tmp/empty/ {ARC}/",     True),
    ("git clean archive",        f"git clean -fdx {ARC}",                    True),
    # -- contact delete (S4-15)
    ("paren filtered delete",    f'{DEL} (every {PER} whose name contains "Jo")', True),
    ("plain delete person",      f'{DEL} {PER} 1',                           True),
    ("delete the person",        f'{DEL} the {PER} named X',                 True),
    # -- regression of the first candidate (found by replaying 243k historical commands, 2026-10-03):
    #    a `#` comment line must not swallow the command after it, nor create one of its own.
    ("comment line then redirect", f"# note\nprintf 'a' > {L0}",             True),
    ("comment names L0 write",   f"# cp draft.md {L0}\nls",                  False),
    ("regex alternation text",   f'grep -n "{DEL} ({PER}|group)" notes.txt', False),
    ("word deleteperson",        f"echo prefix{DEL}{PER}",                   False),
    ("backslash continuation",  f"cp draft.md " + chr(92) + chr(10) + f"  {L0}",   True),
    ("continuation then rm",     f"cd {ARC} && " + chr(92) + chr(10) + "  rm -rf old", True),
    # -- must stay allowed (no new false denies)
    ("cat L0",                   f"cat {L0}",                                False),
    ("read into copy",           f"cat {L0} > /tmp/copy.md",                 False),
    ("sudo ls",                  "sudo ls -la",                              False),
    ("env ls",                   "env FOO=1 ls",                             False),
    ("cd tmp then ls",           "cd /tmp\nls",                              False),
    ("cd archive then ls",       f"cd {ARC} && ls",                          False),
    ("cd archive then cat",      f"cd {ARC}\ncat old.md",                    False),
    ("mv INTO archive",          f"mv NEOCORTEX/old.md {ARC}/",              False),
    ("rsync no delete into dir", "rsync -a /tmp/a/ /tmp/b/",                 False),
    ("git clean elsewhere",      "git clean -fdx /tmp/scratch",              False),
    ("trash elsewhere",          "trash /tmp/scratch.txt",                   False),
    ("patch elsewhere",          "patch /tmp/x.py < /tmp/x.diff",            False),
    ("ordinary rm",              "rm /tmp/scratch.txt",                      False),
]


def main() -> int:
    bad = 0
    for desc, cmd, want in CASES:
        got = fires(cmd)
        ok = got == want
        bad += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {desc:26s} expect={'deny' if want else 'allow':5s} "
              f"got={'deny' if got else 'allow'}")
    print(f"\n{len(CASES) - bad}/{len(CASES)} passed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3.12
"""test_pretool_l0_guard.py -- P0.0a fire/no-fire matrix for the L0 write-guard.

WHY THE FILENAME IS ASSEMBLED AT RUNTIME, and it is not a style choice: the guard matches
the literal command text, so a test file that spelled the constitution's name next to a
redirect would be DENIED BY THE HOOK WHEN THIS FILE IS WRITTEN THROUGH Bash. That happened
on the first attempt (2026-08-26) and is itself evidence the guard fires. Assembling the
name from two fragments keeps the harness writable while testing the exact same string.

Run: python3.12 .claude/hooks/test_pretool_l0_guard.py
Exit 0 = all green.
"""
import json, pathlib, subprocess, sys

# argv[1] overrides the hook under test, so the NEUTER-VERIFICATION is repeatable rather
# than a one-off: `git show HEAD:.claude/hooks/pretool_guard.py > /tmp/pre.py` then pass
# /tmp/pre.py -- the 12 overwrite cases MUST go red, or this matrix certifies nothing.
HOOK = pathlib.Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else \
    pathlib.Path(__file__).resolve().parent / "pretool_guard.py"
L0 = "PAICodeConstitution" + "-2026.md"
CLONE = "GitHub example repositories/antigravity/" + L0

def fires(cmd: str) -> bool:
    out = subprocess.run([sys.executable, str(HOOK)], input=json.dumps({"tool_input": {"command": cmd}}),
                         capture_output=True, text=True).stdout
    return bool(out.strip())

# (description, command, expect_deny)
CASES = [
    # -- OVERWRITE half (new in P0.0a). Every shell write form named in the policy row.
    ("redirect create",        f"cat > {L0}",                          True),
    ("redirect append",        f"echo x >> {L0}",                      True),
    ("redirect no space",      f"cat >{L0}",                           True),
    ("redirect via path",      f"cat > governance/../{L0}",            True),
    ("sed in-place",           f"sed -i '' 's/a/b/' {L0}",             True),
    ("tee",                    f"echo x | tee {L0}",                   True),
    ("truncate",               f"truncate -s 0 {L0}",                  True),
    ("dd of=",                 f"dd if=/dev/null of={L0}",             True),
    ("cp onto L0",             f"cp draft.md {L0}",                    True),
    ("mv onto L0",             f"mv draft.md {L0}",                    True),
    ("rsync onto L0",          f"rsync draft.md {L0}",                 True),
    ("published clone",        f'cat draft.md > "{CLONE}"',            True),
    # -- DELETION / move-OUT half (existing DENY-ARCHIVE, widened to name L0).
    ("rm L0",                  f"rm {L0}",                             True),
    ("mv L0 out",              f"mv {L0} /tmp/",                       True),
    ("find -delete",           f"find . -name {L0} -delete",           True),
    # -- READS MUST STAY ALLOWED. The cold start reads L0 every session; a guard that
    #    blocked reads would break the anchor it exists to protect.
    ("cat",                    f"cat {L0}",                            False),
    ("head",                   f"head -40 {L0}",                       False),
    ("wc",                     f"wc -l {L0}",                          False),
    ("grep",                   f"/usr/bin/grep -n Art {L0}",           False),
    ("sed -n print",           f'sed -n "1,40p" {L0}',                 False),
    ("grep for a gt char",     f'grep ">" {L0}',                       False),
    ("diff",                   f"diff {L0} other.md",                  False),
    ("read INTO a copy",       f"cat {L0} > /tmp/copy.md",             False),
    ("git log on it",          f"git log --oneline -- {L0}",           False),
    # -- REGRESSION: the pre-existing archive rules must be untouched.
    ("mv frozen archive",      "mv ANTIGRAVITY-2025.md /tmp/",         True),
    ("rm _archive",            "rm -rf NEOCORTEX/_archive/old",        True),
    ("mv INTO _archive",       "mv NEOCORTEX/old.md NEOCORTEX/_archive/", False),
    ("ordinary rm",            "rm /tmp/scratch.txt",                  False),
    ("ordinary ls",            "ls -la",                               False),
]

def main() -> int:
    bad = 0
    for desc, cmd, want in CASES:
        got = fires(cmd)
        ok = got == want
        bad += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {desc:22s} expect={'deny' if want else 'allow':5s} "
              f"got={'deny' if got else 'allow'}")
    print(f"\n{len(CASES) - bad}/{len(CASES)} passed")
    return 1 if bad else 0

if __name__ == "__main__":
    sys.exit(main())

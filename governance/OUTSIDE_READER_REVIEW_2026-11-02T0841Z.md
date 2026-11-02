# Outside-reader review (five areas), report only

- **Repository:** github.com/pdewost/coding-constitution, branch `main`
- **Commit reviewed:** `9a0cc778061d610ca2cc1b0c739d33564de95839`. `git rev-parse HEAD` printed this, which matches the pin.
- **Written:** 2026-11-02 08:41 UTC
- **Scope:** five areas the earlier reviewer did not reach.
  - A: file-system hardening
  - B: the verification receipt
  - C: the permission rules
  - D: the review records
  - E: what the test run does not exercise
- **Method:** every experiment ran in copies made with `git archive` (`/tmp/cc`, `/tmp/cc2`, `/tmp/cc3`) and in throwaway workspaces built with `bootstrap.sh` under `/tmp` and `/var/tmp`.
  - The stop-gate runs had to be under `/var/tmp`, because that hook ignores every path under `/tmp/` (`stop_closeout_gate.py:204`).
  - No tracked file was edited, nothing was pushed or posted, and no hook was registered in the reviewing session.
  - This file is the only thing added to the repository tree.

---

## 1. ENVIRONMENT

| Item | Value |
|---|---|
| Commit | `9a0cc778061d610ca2cc1b0c739d33564de95839` |
| OS | Linux 6.18.44 x86_64 |
| Python | default `python3` is 3.13; `/usr/bin/python3.12` is also installed. pytest 9.2.0 went into a python3.12 venv under `/var/tmp`. PyYAML was not available. |
| Shell | GNU bash 5.2.21 |
| zsh | absent |
| node | present (`/opt/node22/bin/node`) |
| osacompile | absent |
| Web source used | https://code.claude.com/docs/en/permissions (fetched 2026-11-02) |
| Tool calls | about 58 |

---

## 2. FINDINGS (most severe first)

### G-1 [B] — HIGH — A review that refutes the change still gets a receipt, and the armed stop gate accepts it
- **Where:**
  - `_skills/verify_gate/scripts/core.py:220`
  - `_skills/verify_gate/scripts/produce_review.py:65`
  - `_skills/verify_gate/scripts/verify_gate.py:96`
  - `adapters/claude-code/hooks/stop_closeout_gate.py:599`
- **What I saw:** `return Decision(ExitCode.PASS, False, True, reasons, block_findings, demoted, ak)` (core.py:220)
- **How I know:** reproduced.
  - **Stub judge:** it returns `verdict:"refuted"` with one HIGH finding that carries both a ref and a literal. I ran `produce()` with `orchestrator_id == producer_id`, so the judge attests itself.
  - **Output:** `HIGH refuted, self-attested: pass_or_advisory exit 0 advisory True receipt /tmp/vg1/out2/receipt.json`.
  - **Armed project:** I wrote that receipt to `proj/.verify/receipt.json` and ran the stop hook. It produced no block reason. Before the receipt existed it blocked with "VERIFY-GATE (armed) … no valid … receipt".
- **What a reader would conclude, and why that is wrong:** README:81 says the armed gate needs an "independently-anchored verdict". In fact any non-blocking verdict yields a valid receipt, including a self-attested one that refutes the change. The receipt does not record whether the verdict was anchored, and nothing checks it later.
- **Cheapest fix:** issue a receipt only when the verdict is anchored (`block_eligible`) and has no HIGH findings. Store that fact in the receipt and check it on validation.

### G-2 [B] — HIGH — "Single-use" fails four ways, and the CLI and the hooks disagree
- **Where:**
  - `_skills/verify_gate/scripts/receipt.py:70-83`
  - `_skills/verify_gate/scripts/verify_gate.py:55`
  - `adapters/claude-code/hooks/stop_closeout_gate.py:599`
  - `adapters/claude-code/hooks/verify_advisory.py`, which validates without consuming
- **What I saw:** `except (OSError, ValueError):` / `return set()` (receipt.py:74), followed by a read-modify-write of the ledger at :82.
- **How I know:** reproduced, each case on the same receipt.
  - **Normal replay is refused:** a second CLI run gave `receipt already consumed (replay)`, rc=6. This part works.
  - **Copy the receipt elsewhere:** `cp .verify/receipt.json elsewhere/` → `receipt_valid`, rc=0. The default ledger sits next to the receipt, so a copy gets a fresh ledger.
  - **Name another ledger:** `--ledger /tmp/vg1/l2.json` → `receipt_valid`, rc=0.
  - **Damage the ledger:** `echo '{oops' > .verify/_consumed_nonces.json` → `receipt_valid`. The ledger is then rewritten holding only this one nonce, so every nonce consumed earlier is lost.
  - **Parallel use:** four processes behind a barrier calling `validate_receipt` then `consume` → `max successful uses of one receipt in a 4-way race: 4`.
  - **Through the hooks:** the same receipt passed stop #1 and stop #2, and `_consumed_nonces.json` was never created.
  - **CLI then hook:** after one CLI validation (`receipt_valid`), stop #3 blocked with "no valid independent-verification receipt". An adopter who checks a receipt with the documented CLI loses it for the hook.
- **What a reader would conclude, and why that is wrong:** README:81 and SKILL.md:6/:47 say "single-use (consumed-nonce ledger)". The hooks never consume, and the CLI's ledger can be sidestepped, wiped or raced.
- **Cheapest fix:**
  - Keep one fixed, gate-owned ledger path.
  - Treat an unreadable ledger as an invalid receipt, not an empty ledger.
  - Consume atomically, for example with an O_EXCL marker file per nonce.
  - Use the same validate-and-consume call in the CLI and the hooks.

### G-3 [B] — HIGH — `.verify/armed` is silently ignored unless the project also has a CLAUDE.md
- **Where:** `adapters/claude-code/hooks/stop_closeout_gate.py:564` (the same test appears again at :764)
- **What I saw:** `if (d / "CLAUDE.md").is_file() and (d / "NEOCORTEX").is_dir():`
- **How I know:** reproduced.
  - **Without CLAUDE.md:** a project with `NEOCORTEX/` and `.verify/armed`, an Edit of `proj/mod.py` in the transcript. The stop hook gave only the ordinary "CLOSEOUT-GATE: code was edited … since the last verification command".
  - **With CLAUDE.md:** after `touch proj/CLAUDE.md`, the same run gave "CLOSEOUT-GATE: VERIFY-GATE (armed) …".
  - **Read only:** `grep CLAUDE.md` finds no mention of this requirement in `neocortex_manifest.py`, README:81 or `adapters/claude-code/README.md:19`.
- **What a reader would conclude, and why that is wrong:** README:81 says blocking starts "once a project opts in with a `.verify/armed` file". A project created with the documented `--init` has no CLAUDE.md, so it stays unarmed and nothing says so.
- **Cheapest fix:** find the project root by `NEOCORTEX/` alone. Failing that, document the CLAUDE.md requirement and print a warning when `armed` is found but ignored.

### G-4 [A] — HIGH — `skill_index_regen.py` writes its log through a planted symlink and hangs on a planted FIFO
- **Where:** `adapters/claude-code/hooks/skill_index_regen.py:59`
- **What I saw:** `(ROOT / _LOG[0] / _LOG[1] / _LOG[2]).open("a", encoding="utf-8").write(`
- **How I know:** reproduced.
  - **Symlink:** `ln -s /tmp/victim2/v .claude/hooks/.skill_index_regen.log`, then I fed the hook `{"tool_input":{"file_path":".../_skills/foo/SKILL.md"}}`. The victim file afterwards read `ORIG` plus `2026-11-02T07:39:16 REGEN ok after edit of foo/SKILL.md`.
  - **FIFO:** `mkfifo` at the same path, then `timeout 15` → rc=124, after 15.0 s.
  - **Context:** the hook's registered timeout is 45 s (`settings.canonical.json:86`), and it fires after every edit under `_skills/`. The `*.log` exclusion in `hook_support.py` means the integrity check never looks at this file.
- **What a reader would conclude, and why that is wrong:** README:345-349 says the hooks were "structurally hardened against the symlink / … / FIFO-hang … classes". This hook uses a plain `open("a")`.
- **Cheapest fix:** route `_log` through the same `_safe_append` helper the sibling hooks use.

### G-5 [B] — MED — The receipt checker the armed gate trusts is outside the approved-hash list
- **Where:**
  - `adapters/claude-code/hooks/stop_closeout_gate.py:593`
  - `adapters/claude-code/hooks/hook_support.py:111`
- **What I saw:** `_TREES = (".claude/hooks", "governance/adapters/claude-code")` and `import receipt as _r`.
- **How I know:** reproduced.
  - I appended `def validate_receipt(*a, **k): return True, "ok"` to the workspace copy of `_skills/verify_gate/scripts/receipt.py`.
  - The armed stop gate then passed with an invalid receipt `{"schema":"x"}`; before the edit it blocked.
  - `hook_support.integrity_report()` returned `[]`.
- **What a reader would conclude, and why that is wrong:** README:99 says any change to the hooks is reported as tampering until re-approved. The module that actually decides whether the armed gate passes can be changed without any report.
- **Cheapest fix:** add `_skills/verify_gate/scripts` to `_TREES`, and load it the way the siblings are loaded: from source, checked against the approved list.

### G-6 [B] — MED — An edited verdict, a `CI` variable, or a missing model client each go unnoticed
- **Where:**
  - `_skills/verify_gate/scripts/receipt.py:62`
  - `_skills/verify_gate/scripts/review_runner.py:70`
  - `_skills/verify_gate/scripts/judges.py:114`
- **What I saw:** `CI is detected from the environment, which an agent cannot mint.` (review_runner.py:70)
- **How I know:** reproduced, three separate problems.
  - **Verdict edited after the receipt:** I rewrote `.verify/verdict.json` to `refuted`/high after the receipt was issued. Validation still returned `receipt_valid`. `verdict_sha256` is stored in the receipt but never checked.
  - **CI anchor from an environment variable:** `CI=1 python3 -c '…_anchor_kind_for("whatever","","")'` printed `ci`, an external anchor kind. Any agent can set an environment variable.
  - **No model client:** `produce_review.py … --backend stateless_llm` printed `{"exit": 4, "decision": "judge_unreachable", …}` with no message. README:416-420 promises an error "that says to pass your own `call_fn`". That RuntimeError is swallowed by `run_review`'s `except Exception`.
- **Cheapest fix:**
  - Check `verdict_sha256` in `validate_receipt`.
  - Remove the "cannot mint" sentence, or require a token only CI holds.
  - Pass the exception text through into the `judge_unreachable` result.

### G-7 [A] — MED — On a single-project install, the installer silently replaces the root settings
- **Where:** `adapters/claude-code/install_adapters.py:485-486` and its write path; README:128 and :188-191
- **What I saw:** `it never overwrites your settings` (README:128)
- **How I know:** reproduced.
  - **Setup:** `bootstrap.sh /tmp/wsR`, then `mkdir wsR/NEOCORTEX`, then I added `"env"` and `"model"` keys to `.claude/settings.json`. This is the README's documented single-project path.
  - **Run:** `install_adapters.py --root /tmp/wsR --apply` printed `WRITING .`.
  - **Result:** the file's keys afterwards were `['_generated_by', 'hooks', 'permissions']`. The user's keys were dropped, and the canonical source was replaced by its own generated form.
- **What a reader would conclude, and why that is wrong:** the reader believes their merged settings are safe. Bootstrap does refuse to overwrite them, but the next documented step does not.
- **Cheapest fix:** skip `ROOT/.claude/settings.json` when it is `CANONICAL_SETTINGS`, or refuse to write when the existing file has keys other than `hooks` and `permissions`.

### G-8 [C] — MED — Half the deny/ask rules can never match
- **Where:** `adapters/claude-code/settings.canonical.json:5,7,9,14`
- **What I saw:** `"Write(PAICodeConstitution-2026.md)"` and `"Glob(/_session_record/**)"`
- **How I know:** read only, using the vendor docs (code.claude.com/docs/en/permissions, fetched 2026-11-02). They state:
  - "If you write a path rule for `Write`, `NotebookEdit`, `Glob`, or legacy `MultiEdit`, Claude Code accepts the rule but never matches";
  - "`Edit` rules apply to all built-in tools that edit files";
  - Read and Edit deny rules do not apply to "arbitrary subprocesses that read or write files indirectly".

  Per-tool coverage of the two protected files:

  | Tool that can change the file | Rule that covers it | Covered? |
  |---|---|---|
  | Edit, Write, MultiEdit, NotebookEdit | the `Edit(...)` deny rules | yes, via Edit; the `Write(...)` rules are inert |
  | Bash | `pretool_guard.py` text matching | constitution: yes for common forms. `ANTIGRAVITY-2025.md`: rm/mv only |
  | `mcp__Control_your_Mac__osascript` | `pretool_guard.py` | partly |
  | any other MCP file tool | none | no |

  Separately reproduced:
  - **osascript:** `set f to open for access POSIX file ".../PAICodeConstitution-2026.md" with write permission` → ALLOW. The `do shell script "echo x > …"` form → deny.
  - **Bash, `ANTIGRAVITY-2025.md`:** `cp /etc/hosts ANTIGRAVITY-2025.md`, `sed -i s/o/n/ ANTIGRAVITY-2025.md` and `echo x >> ANTIGRAVITY-2025.md` → ALLOW. That matches `spec/POLICY_CORE.md:17`, which guards only delete/move for that file, but Art. 6 says archives are never deleted, and overwriting destroys the content.
- **What a reader would conclude, and why that is wrong:** README:117 lists Write, MultiEdit and NotebookEdit as protected "by the settings' static deny rules". The protection comes from the `Edit` rules alone, and the `Write`/`Glob` lines are dead text.
- **Cheapest fix:** delete the `Write(...)` and `Glob(...)` rules, cite the docs sentence in README:117, and state that non-Bash MCP file tools are not covered.

### G-9 [D] — MED — README claims about adversarial reviews and exploit harnesses have no record in `reviews/`
- **Where:** README:343-349; also README:34, :38 and :410
- **What I saw:** `the round caught **10+ HIGH findings**` and `verified with reproducing exploit harnesses`.
- **How I know:** read only.
  - `grep -rni "symlink|TOCTOU|FIFO|hardlink|exploit" reviews/` returns nothing.
  - The earliest record is dated 2026-08-25, while ratification is dated 2026-06-10, so there is no record of the "before ratification" review or its 10+ HIGH findings.
  - `REVIEW_2026-10-07_hooks_batch2.md` says "every finding was reproduced as a failing test", but lists no findings, test names or counts beyond "31 findings" for round 4.
  - G-4 shows that one class the hardening claim names does fail.
- **What a reader would conclude, and why that is wrong:** they would take these as recorded results. They are statements the repository gives no way to check.
- **Cheapest fix:** add the records (finding list plus test ids), or reword them as unrecorded statements.

### G-10 [E] — LOW — One test fails on every normal pytest run, nine tests never run anywhere, and one compile-gate branch is silent
- **Where:** `adapters/claude-code/hooks/test_pretool_h0_h4_h9.py:1397` and `:25`; `adapters/claude-code/hooks/postedit_compile_gate.py` (AppleScript branch)
- **What I saw:** `(ws / ".claude" / "hooks" / "__pycache__").mkdir()`
- **How I know:** reproduced.
  - **Full run:** `pytest -q -rs adapters _skills governance` → `2 failed, 868 passed, 11 skipped`. One of the two failures is the already-known zsh test.
  - **The other failure:** `test_b5_23_find_cleanup_of_caches_stays_silent` fails on a fresh `git archive` with `FileExistsError … __pycache__`. pytest writes its own `.pyc` into `hooks/__pycache__`, and the fixture copies that directory.
  - **Confirmation:** with `PYTHONDONTWRITEBYTECODE=1` the test passes (`1 passed`).
- **Skipped tests and whether each reason is honest:**

  | Count | Skip reason | Honest on a normal Linux or macOS machine? |
  |---|---|---|
  | 9 | "pretool_guard.sh (the H0 launcher) is not part of this kit" (`:304, :311, :318, :325, :406 ×3, :418, :1128`) | Yes, the file is not shipped. But these tests are dead code in the published kit. |
  | 2 | "root reads unreadable files" (`test_anchor_render.py:563`) | Yes. They skipped here only because this VM runs as root; they run on a normal user account. |
  | conditional, did not fire | "node absent" (`test_s4_compile_and_push_audit.py:54`), "/usr/bin/python3 absent" (`test_anchor_render.py:696`) | Yes. |
  | conditional, did not fire | "build_index.py not found" (`test_stop_closeout_version_drift.py:74`) | Cannot happen in a full checkout. |

- **Compile-gate branches:**
  - **Worked here:** `.py` (via python3.12), `.sh`, `.js` and `.json` each blocked a bad file.
  - **`.zsh`:** printed "COMPILE-GATE check DISABLED: zsh not found".
  - **`.applescript`:** without osacompile, returned nothing at all. Only the `¬` check runs, with no "DISABLED" notice, unlike the other branches.
  - **Without python3.12 on PATH:** the `.py` check prints "DISABLED: python3.12 not found". README:233 documents this.
- **Cheapest fix:** have the fixture ignore `__pycache__` when copying, and add a `_disabled()` call to the AppleScript branch.

---

## 3. WHAT HELD

### A. File-system hardening
- **Installer:** `_safe_confined_write` and `_safe_confined_read` (`install_adapters.py:131-293`) refused every plant.
  - Plants tried: symlinked `settings.json`, hardlinked `settings.json`, a FIFO, a symlinked `.claude/`, and a symlinked project directory.
  - Each printed `SKIP (symlink|unsafe path)`. The victim file was unchanged with link count 2, and nothing hung.
  - `--check` then reported `CHECK FAILED: … 5 skipped (not checked)` rather than a false pass.
- **Approved list as a FIFO:** a FIFO at `governance/hooks_approved.sha256` did not hang `pretool_guard.py`. The DENY-ARCHIVE decision came back in 0.1 s.
- **Read only:** the `_safe_append` helpers all use an O_NOFOLLOW directory walk and check `S_ISREG` and `nlink == 1`. They are at `postedit_compile_gate.py:36`, `stop_closeout_gate.py:79`, `verify_advisory.py:61` and `hook_support.py:53`.

### B. Verification receipt
- **Normal lifecycle:** a clean stub verdict through `harness_subagent` gave `pass_or_advisory` and wrote `.verify/receipt.json`.
- **Replay:** a second CLI validation of the same receipt was refused.
- **Edits to changed files:** an edit after the receipt flips the change-set hash, which `validate_receipt` checks (read at `receipt.py:85-111`).

### C. Permission rules
- **Edit rules:** `Edit(PAICodeConstitution-2026.md)` and `Edit(**/PAICodeConstitution-2026.md)` cover all built-in editing tools, per the docs.
- **Bash guard:** it denied these forms against the constitution: `>`, `sed -i`, `git checkout --`, `git restore`, `patch`, `ln -sf`, `truncate`, `rm`, `chmod` and `touch`.

### D. Review records
- README:24 ("188/190 … zero amendments") matches `REVIEW_2026-08-25.md:3-4`.
- README:25 ("190/190", "two external reviews … another lineage") matches `REVIEW_2026-10-27_constitution_amendment.md:3,51`.
- `wc -l PAICodeConstitution-2026.md` gives 190.

### E. Test run and compile gate
- The compile gate blocked on bad `.py`, `.sh`, `.js` and `.json` files.
- The `.zsh` branch reports "DISABLED" when zsh is absent.

---

## 4. COULD NOT CHECK

- **zsh and osacompile branches** of the compile gate: neither tool is installed in this VM.
- **The adversarial-review scripts:** PyYAML could not be installed (no matching distribution through the proxy).
- **How Claude Code actually applies the rules at runtime:** I relied on the vendor docs and did not register the settings, as the brief forbids. Behaviour in older Claude Code versions is not known.
- **macOS-only behaviour:** the `/private/var/folders` exemptions and the `~/Library` paths.
- **Whether reviews outside this repository exist:** the brief forbids looking at the author's other material.

---

## Appendix — reproduction harnesses (re-runnable)

Each one runs on a copy made with `git archive HEAD | tar -x -C /tmp/cc`. Throwaway workspaces come from `bash /tmp/cc/adapters/claude-code/bootstrap.sh <dir>`.

### A — installer plants (G-7, and "What held")
```bash
bash /tmp/cc/adapters/claude-code/bootstrap.sh /tmp/wsA
cd /tmp/wsA
mkdir -p p_sym/NEOCORTEX p_sym/.claude
ln -s /tmp/victim/v.txt p_sym/.claude/settings.json
# likewise: a hardlink (ln), a FIFO (mkfifo), a symlinked .claude/ and a symlinked project directory
python3 /tmp/cc/adapters/claude-code/install_adapters.py --root /tmp/wsA --apply
python3 /tmp/cc/adapters/claude-code/install_adapters.py --root /tmp/wsA --check
```

Root overwrite (G-7): create `NEOCORTEX/` at the workspace root, add your own keys to `.claude/settings.json`, then run `--apply` and list the file's keys.

### A — skill_index_regen log (G-4)
```bash
mkdir -p /tmp/wsS/_skills/foo && echo '---' > /tmp/wsS/_skills/foo/SKILL.md
ln -s /tmp/victim2/v /tmp/wsS/.claude/hooks/.skill_index_regen.log
echo '{"tool_input":{"file_path":"/tmp/wsS/_skills/foo/SKILL.md"}}' | python3 /tmp/wsS/.claude/hooks/skill_index_regen.py
cat /tmp/victim2/v   # now holds the log line
# then replace the symlink with mkfifo and run under `timeout 15`: rc=124
```

### B — receipt lifecycle (G-1, G-2, G-6)
The stub verdict, run through `produce_review.produce(...)` with `backend="harness_subagent"`:
```json
{"schema": "verify_gate.verdict.v1", "producer_id": "reviewer", "drafter_id": "author",
 "lenses": [{"lens": "correctness", "verdict": "refuted", "probe": "read mod.py",
   "findings": [{"severity": "high", "claim": "x is wrong",
                 "evidence": {"ref": "mod.py:1", "literal": "x=1"}}]}]}
```

- **Self-attested run:** set `orchestrator_id="reviewer"`, the same as `producer_id`. A receipt is written.
- **CLI checks:** `verify_gate.py --receipt R --changed <abs path>`, then:
  1. present it again (refused);
  2. copy it to another directory (accepted);
  3. pass `--ledger <other>` (accepted);
  4. write `{oops` into the ledger (accepted);
  5. edit `verdict.json` after the receipt was issued (accepted).
- **Race:** 4 processes with a `multiprocessing` barrier calling `receipt.validate_receipt` then `receipt.consume` on one receipt; all 4 succeeded.
- **No model client:** `produce_review.py --backend stateless_llm …` with no client → `judge_unreachable` and no message.
- **CI variable:** `CI=1` → `_anchor_kind_for(...)` returns `ci`.

### B — stop hook (G-1, G-2, G-3, G-5)
Workspace `/var/tmp/wsV` (outside `/tmp`, which the gate ignores), project `proj/` with `NEOCORTEX/` and `.verify/armed`. The transcript holds one user prompt, one `Edit` tool_use of `proj/mod.py`, and one assistant text. The Stop payload is fed to `.claude/hooks/stop_closeout_gate.py`.

Steps:
1. Run without `CLAUDE.md`.
2. Run with it.
3. Add a receipt and run twice.
4. Validate once through the CLI and run again.
5. Append a stub `validate_receipt` to the workspace `receipt.py`, rerun, and call `hook_support.integrity_report()`.

### C — guard probes
Feed `{"tool_name":"Bash","tool_input":{"command":"<cmd>"},"cwd":"<ws>"}` to `.claude/hooks/pretool_guard.py`. For osascript, use `tool_name` `mcp__Control_your_Mac__osascript` with `tool_input.script`.

### E — tests and compile gate
```bash
HOME=/var/tmp/fakehome python -m pytest -q -rs -p no:cacheprovider adapters _skills governance
```
```bash
HOME=/var/tmp/fh6 PYTHONDONTWRITEBYTECODE=1 python -m pytest -q adapters/claude-code/hooks/test_pretool_h0_h4_h9.py::test_b5_23_find_cleanup_of_caches_stays_silent
```

Compile gate: feed `{"tool_input":{"file_path":"/var/tmp/e/bad.<ext>"}}` to `postedit_compile_gate.py` for each of `.py`, `.sh`, `.zsh`, `.js`, `.applescript` and `.json`. For the no-python3.12 case, set `PATH` to a directory that holds only a `python3` → 3.13 symlink.

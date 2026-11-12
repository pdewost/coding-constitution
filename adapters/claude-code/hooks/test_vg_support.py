"""Fixture support for the GF-1 / GF-2 tests of the two Stop hooks (PLAN_constitution_governed_fixes_s117 v0.8).
Not a test module (the name keeps it out of the governor's inventory, `_excluded_file`). It builds, under a pytest tmp_path,
a hook root that holds copies of the hooks, the verify_gate scripts and an approved list written for THEM (as a landed
install has), and an armed project; then runs either hook as a subprocess."""
import atexit
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

HOOKS = pathlib.Path(__file__).resolve().parent
WS = HOOKS.parent.parent.parent   # published: the hooks ship under adapters/claude-code/hooks/
REAL_SCRIPTS = WS / "_skills" / "verify_gate" / "scripts"
SIBLINGS = ("verifier_recognizer.py", "hook_support.py")


# The gate exempts edits under /tmp and /var/folders (NON_CLOSEOUT_PREFIXES), so the fixture lives under the user's cache folder.
BASE = pathlib.Path(os.environ.get("OUTCOME_TEST_BASE") or pathlib.Path.home() / "Library" / "Caches" / "hooks_batch2_tests" / "_tmp_vg")


def _cleanup(d):
    for dp, dns, fns in os.walk(d):
        for n in dns:
            try:
                os.chmod(os.path.join(dp, n), 0o755)
            except OSError:
                pass
    shutil.rmtree(d, ignore_errors=True)


class Fx:
    def __init__(self, tmp_path=None, *, armed=True, patch_receipt=None, patch_hook_support=None, extra_scripts=None,
                 approve=True):
        BASE.mkdir(parents=True, exist_ok=True)
        self.tmp = pathlib.Path(tempfile.mkdtemp(dir=BASE))
        atexit.register(_cleanup, self.tmp)
        tmp_path = self.tmp
        self.root = tmp_path / "hookroot"
        self.hooks = self.root / ".claude" / "hooks"
        self.scripts = self.root / "_skills" / "verify_gate" / "scripts"
        self.hooks.mkdir(parents=True)
        self.scripts.mkdir(parents=True)
        (self.root / "governance").mkdir()
        for name in ("stop_closeout_gate.py", "verify_advisory.py") + SIBLINGS:
            shutil.copy(HOOKS / name, self.hooks / name)
        for f in REAL_SCRIPTS.glob("*.py"):
            shutil.copy(f, self.scripts / f.name)
        if patch_receipt:
            p = self.scripts / "receipt.py"
            p.write_text(patch_receipt(p.read_text()))
        if patch_hook_support:
            p = self.hooks / "hook_support.py"
            p.write_text(patch_hook_support(p.read_text()))
        for name, text in (extra_scripts or {}).items():
            (self.scripts / name).write_text(text)
        if approve:
            self.approve()
        self.proj = tmp_path / "proj"
        (self.proj / "NEOCORTEX").mkdir(parents=True)
        (self.proj / "CLAUDE.md").write_text("# p\n")
        if armed:
            (self.proj / ".verify").mkdir()
            (self.proj / ".verify" / "armed").write_text("")
        self.code = self.proj / "src.py"
        self.code.write_text("x = 1\n")
        self.transcript = tmp_path / "t.jsonl"
        self.set_edits([self.code])

    def approve(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("_fx_hs", self.hooks / "hook_support.py")
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        m.write_approved(self.root)

    def set_edits(self, paths):
        self.transcript.write_text("".join(
            json.dumps({"message": {"content": [{"type": "tool_use", "name": "Edit",
                                                 "input": {"file_path": str(p)}}]}}) + "\n" for p in paths))

    @staticmethod
    def configured_command(hook):
        """The command line `.claude/settings.json` configures for `hook` (s241 F-6, R-3): the harness runs THAT, not
        `sys.executable <path>`."""
        cfg = json.loads((WS / "adapters" / "claude-code" / "settings.canonical.json").read_text())
        for ev in cfg.get("hooks", {}).values():
            for grp in ev:
                for h in grp.get("hooks", []):
                    cmd = h.get("command", "")
                    if cmd.endswith("/" + hook) or cmd.endswith("/" + hook + '"'):
                        return cmd
        raise KeyError(hook)

    def run(self, hook, cwd=None, timeout=60):
        """Run `hook` as settings.json configures it: `"$CLAUDE_PROJECT_DIR"/.claude/hooks/<hook>` through the shell, with
        CLAUDE_PROJECT_DIR the fixture's hook root (s241 F-6). A hook with no configured command (a fixture-only script) runs
        by path."""
        env = dict(os.environ)
        try:
            cmd = self.configured_command(hook)
            env["CLAUDE_PROJECT_DIR"] = str(self.root)
            argv = ["/bin/sh", "-c", cmd]
        except KeyError:
            env.pop("CLAUDE_PROJECT_DIR", None)
            argv = [sys.executable, str(self.hooks / hook)]
        return subprocess.run(argv, input=json.dumps({"transcript_path": str(self.transcript)}), capture_output=True,
                              text=True, cwd=str(cwd or self.proj), env=env, timeout=timeout)

    def gate(self, cwd=None):
        r = self.run("stop_closeout_gate.py", cwd)
        assert r.returncode == 0, r.stderr[-500:]
        return r.stdout

    def errors(self):
        """The hook error log of this fixture root ("" when nothing was logged): a swallowed exception leaves a line here."""
        log = self.root / "governance" / "hook_errors.jsonl"
        return log.read_text() if log.exists() else ""

    def assert_validation_ran(self, out):
        """Positive evidence (s232 F-6) that an EMPTY gate answer came from a validation that ran, not from a swallowed
        exception that exits 0: nothing was logged, and the same fixture, with only the code edited after its receipt,
        now blocks with the receipt's own reason (so the gate does evaluate this receipt through the loaded checker)."""
        assert self.errors() == "", self.errors()
        self.code.write_text(self.code.read_text() + "# edited after verification\n")
        again = self.gate()
        assert "receipt: change-set hash mismatch" in again, again

    def advisory(self, cwd=None):
        r = self.run("verify_advisory.py", cwd)
        assert r.returncode == 0, (r.returncode, r.stderr[-500:])
        log = self.root / "governance" / "hook_telemetry.log"
        return log.read_text() if log.exists() else ""

    def issue_receipt(self, rec_dir=None, changed=None):
        """A valid v2 receipt at <proj>/.verify/receipt.json, made with the real receipt module."""
        sys.path.insert(0, str(REAL_SCRIPTS))
        try:
            import receipt as R
        finally:
            sys.path.remove(str(REAL_SCRIPTS))
        v = self.proj / ".verify" / "verdict.json"
        a = self.proj / ".verify" / "attestation.json"
        v.write_text(json.dumps({"schema": "verify_gate.verdict.v1", "artifact": "art", "drafter_id": "author",
                                 "producer_id": "reviewer", "lenses": [{"lens": "c", "verdict": "holds", "probe": "p",
                                                                        "findings": []}]}))
        a.write_text(json.dumps({"schema": "verify_gate.attestation.v1", "anchor_kind": "harness_subagent",
                                 "anchor_id": "s", "attestor": "orch", "producer_id": "reviewer",
                                 "context_isolated": True}))
        rec = R.write_receipt(changed_paths=[str(p) for p in (changed or [self.code])], verdict_path=str(v),
                              gate_exit=0, attestation_ref=str(a))
        (self.proj / ".verify" / "receipt.json").write_text(json.dumps(rec.as_dict()))
        return rec.as_dict()

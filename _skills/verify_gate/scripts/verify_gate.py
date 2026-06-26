#!/usr/bin/env python3
"""verify_gate — the neutral CLI gate. Validates a verdict + its out-of-band attestation,
optionally checks/writes a change-set-bound receipt, and exits with the core contract code.

Harness- & coder-neutral: every adapter (Claude Code hook, Cursor hook, CI step, a human at a
terminal) just SHELLS this. The gate trusts the attestation (written by the orchestrator), the
receipt (bound to the change-set), and nothing the producer self-asserts.

Usage:
  verify_gate.py --verdict V.json --attestation A.json [--require-blocking]
                 [--changed f1 f2 ... --receipt R.json --ledger L.json]   # validate a receipt
                 [--changed f1 f2 ... --write-receipt R.json --ledger L.json]  # emit one on PASS
                 [--override "<reason>"]            # human escape hatch: downgrade BLOCK->advisory
                 [--judge-unreachable]              # runner signals the judge could not be produced
Exit codes: see core.ExitCode (0 pass/advisory · 1 block · 2 identity · 3 unanchored · 4 judge ·
            5 tamper · 6 receipt invalid).
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core      # noqa: E402
import receipt as receipt_mod  # noqa: E402


def _audit(line: str) -> None:
    try:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "verify_gate_audit.log")
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(f"{datetime.datetime.now().isoformat(timespec='seconds')} {line}\n")
    except OSError:
        pass


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="VERIFY-GATE")
    # Not required at parse time: receipt-validation mode needs neither (validated below).
    ap.add_argument("--verdict")
    ap.add_argument("--attestation")
    ap.add_argument("--require-blocking", action="store_true")
    ap.add_argument("--judge-unreachable", action="store_true")
    ap.add_argument("--changed", nargs="*", default=[])
    ap.add_argument("--receipt")
    ap.add_argument("--write-receipt")
    ap.add_argument("--ledger", default=None)
    ap.add_argument("--override", default=None)
    a = ap.parse_args(argv)

    _base = a.verdict or a.receipt or "."
    ledger = a.ledger or os.path.join(os.path.dirname(os.path.abspath(_base)), "_consumed_nonces.json")

    # (1) If a receipt is supplied, the gate's job is to validate IT for the current change-set.
    if a.receipt:
        try:
            rec = core.load_json(a.receipt)
        except (OSError, ValueError) as e:
            print(json.dumps({"decision": "receipt_unreadable", "error": str(e)}))
            return core.ExitCode.RECEIPT_INVALID
        ok, why = receipt_mod.validate_receipt(rec, changed_paths=a.changed, ledger_path=ledger)
        if not ok:
            print(json.dumps({"decision": "receipt_invalid", "reason": why}))
            return core.ExitCode.RECEIPT_INVALID
        receipt_mod.consume(rec["nonce"], ledger)
        print(json.dumps({"decision": "receipt_valid", "reason": why}))
        return core.ExitCode.PASS

    # (2) Otherwise evaluate the verdict + attestation.
    if not a.verdict or not a.attestation:
        print(json.dumps({"decision": "usage_error",
                          "error": "provide --receipt (validate) OR --verdict + --attestation (evaluate)"}))
        return core.ExitCode.TAMPER
    try:
        verdict = core.load_json(a.verdict)
        attestation = core.load_json(a.attestation)
    except (OSError, ValueError) as e:
        print(json.dumps({"decision": "unreadable", "error": str(e)}))
        return core.ExitCode.TAMPER

    d = core.evaluate(verdict, attestation,
                      require_blocking=a.require_blocking,
                      judge_unreachable=a.judge_unreachable)

    # Human escape hatch: an explicit, audited override downgrades a BLOCK to advisory.
    if d.exit_code in (core.ExitCode.BLOCK, core.ExitCode.UNANCHORED, core.ExitCode.JUDGE_UNREACHABLE) and a.override:
        _audit(f"OVERRIDE exit={d.exit_code} reason={a.override!r} verdict={a.verdict}")
        print(json.dumps({"decision": "overridden", "was": d.exit_code,
                          "reason": a.override, "detail": d.as_dict()}))
        return core.ExitCode.PASS

    # On a clean PASS, optionally emit a change-set-bound receipt for a downstream closeout gate.
    if d.exit_code == core.ExitCode.PASS and a.write_receipt:
        rec = receipt_mod.write_receipt(changed_paths=a.changed, verdict_path=a.verdict,
                                        gate_exit=0, attestation_ref=a.attestation)
        with open(a.write_receipt, "w", encoding="utf-8") as fh:
            json.dump(rec.as_dict(), fh, indent=2)

    print(json.dumps({"decision": _name(d.exit_code), **d.as_dict()}, indent=2))
    return d.exit_code


def _name(code: int) -> str:
    return {0: "pass_or_advisory", 1: "block", 2: "identity", 3: "unanchored",
            4: "judge_unreachable", 5: "tamper", 6: "receipt_invalid"}.get(code, str(code))


if __name__ == "__main__":
    sys.exit(main())

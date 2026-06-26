"""
validate_pack.py — Lens-pack YAML conformance checker driven by _pack_schema.yaml.

CLI:
    python3.12 scripts/validate_pack.py --all [--packs-dir <dir>]
    python3.12 scripts/validate_pack.py --pack <name> [--packs-dir <dir>]

Exit 0 if all validated packs are valid; exit 1 with per-pack error lines on stderr.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from typing import Any

import yaml

# Semver regex (lenient: major.minor.patch with optional pre-release/build)
_SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)"
    r"(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?"
    r"(?:\+([0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$"
)

_SKILL_DIR = Path(__file__).parent.parent
_DEFAULT_PACKS_DIR = _SKILL_DIR / "lenses"
_SCHEMA_FILE = "_pack_schema.yaml"


# ---------------------------------------------------------------------------
# Schema loading
# ---------------------------------------------------------------------------

def load_pack_schema(packs_dir: Path) -> dict:
    """Load and return the _pack_schema.yaml as a dict."""
    schema_path = packs_dir / _SCHEMA_FILE
    with open(schema_path) as fh:
        return yaml.safe_load(fh)


# ---------------------------------------------------------------------------
# Type checkers
# ---------------------------------------------------------------------------

def _check_type(key: str, value: Any, type_name: str) -> list[str]:
    """
    Check that `value` matches `type_name` as declared in _pack_schema.yaml.
    Returns a list of error strings (empty = valid).
    """
    errors = []

    if type_name == "str":
        if not isinstance(value, str):
            errors.append(f"  '{key}' must be a string, got {type(value).__name__}")

    elif type_name == "non_empty_str":
        if not isinstance(value, str) or not value.strip():
            errors.append(f"  '{key}' must be a non-empty string")

    elif type_name == "bool":
        if not isinstance(value, bool):
            errors.append(f"  '{key}' must be a boolean, got {type(value).__name__}")

    elif type_name == "semver":
        if not isinstance(value, str):
            errors.append(f"  '{key}' must be a string (semver), got {type(value).__name__}")
        elif not _SEMVER_RE.match(str(value)):
            errors.append(f"  '{key}' value '{value}' is not a valid semver string")

    elif type_name == "non_empty_list_of_lens":
        if not isinstance(value, list) or len(value) == 0:
            errors.append(f"  '{key}' must be a non-empty list")
        else:
            for i, item in enumerate(value):
                if not isinstance(item, dict):
                    errors.append(f"  '{key}[{i}]' must be a dict")
                    continue
                if not item.get("id") or not str(item["id"]).strip():
                    errors.append(f"  '{key}[{i}].id' must be a non-empty string")
                if not item.get("prompt") or not str(item["prompt"]).strip():
                    errors.append(f"  '{key}[{i}].prompt' must be a non-empty string")

    elif type_name == "severity_rubric_map":
        if not isinstance(value, dict):
            errors.append(f"  '{key}' must be a dict")
        else:
            expected = {"HIGH", "MED", "LOW"}
            actual = set(value.keys())
            if actual != expected:
                missing = expected - actual
                extra = actual - expected
                parts = []
                if missing:
                    parts.append(f"missing keys: {sorted(missing)}")
                if extra:
                    parts.append(f"unexpected keys: {sorted(extra)}")
                errors.append(
                    f"  '{key}' must have exactly HIGH, MED, LOW keys; "
                    + "; ".join(parts)
                )
            else:
                for k, v in value.items():
                    if not isinstance(v, str):
                        errors.append(
                            f"  '{key}.{k}' must be a string, got {type(v).__name__}"
                        )

    elif type_name == "str_map":
        if not isinstance(value, dict):
            errors.append(f"  '{key}' must be a dict of strings")

    elif type_name == "delegate_map":
        if not isinstance(value, dict):
            errors.append(f"  '{key}' must be a dict")
        else:
            # Acceptable keys: capability, kind, optional
            for k, v in value.items():
                if k == "optional":
                    if not isinstance(v, bool):
                        errors.append(
                            f"  '{key}.optional' must be a bool, got {type(v).__name__}"
                        )
                else:
                    if not isinstance(v, str):
                        errors.append(
                            f"  '{key}.{k}' must be a string, got {type(v).__name__}"
                        )

    else:
        # Unknown type descriptor — treat as any (pass)
        pass

    return errors


# ---------------------------------------------------------------------------
# Pack validation
# ---------------------------------------------------------------------------

def validate_pack(pack_data: dict, schema: dict) -> list[str]:
    """
    Validate a loaded pack dict against the schema.
    Returns a list of error strings (empty = valid).
    """
    errors: list[str] = []

    required: dict = schema.get("required", {})
    optional: dict = schema.get("optional", {})
    allowed_keys = set(required.keys()) | set(optional.keys())

    # 1. Check for unknown top-level keys
    for k in pack_data:
        if k not in allowed_keys:
            errors.append(f"  Unknown key '{k}' (not in required or optional)")

    # 2. Check required keys present + typed
    for key, type_name in required.items():
        if key not in pack_data:
            errors.append(f"  Missing required key '{key}'")
        else:
            errors.extend(_check_type(key, pack_data[key], type_name))

    # 3. Check optional keys (if present, must match type)
    for key, type_name in optional.items():
        if key in pack_data:
            errors.extend(_check_type(key, pack_data[key], type_name))

    return errors


def validate_pack_file(pack_path: Path, schema: dict) -> list[str]:
    """Load a YAML pack file and validate it. Returns list of error strings."""
    try:
        with open(pack_path) as fh:
            data = yaml.safe_load(fh)
    except Exception as exc:
        return [f"  YAML parse error: {exc}"]

    if not isinstance(data, dict):
        return ["  Pack file does not contain a YAML mapping at the top level"]

    return validate_pack(data, schema)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main():
    parser = argparse.ArgumentParser(
        description="Validate lens-pack YAML files against _pack_schema.yaml."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--all", action="store_true",
        help="Validate every *.yaml in the packs dir except _pack_schema.yaml"
    )
    group.add_argument(
        "--pack", metavar="NAME",
        help="Validate a single pack by name (with or without .yaml extension)"
    )
    parser.add_argument(
        "--packs-dir", metavar="DIR", default=None,
        help=f"Directory containing lens packs (default: {_DEFAULT_PACKS_DIR})"
    )
    args = parser.parse_args()

    packs_dir = Path(args.packs_dir) if args.packs_dir else _DEFAULT_PACKS_DIR

    # Load schema
    try:
        schema = load_pack_schema(packs_dir)
    except FileNotFoundError:
        print(
            f"ERROR: schema file not found at {packs_dir / _SCHEMA_FILE}",
            file=sys.stderr
        )
        sys.exit(1)
    except Exception as exc:
        print(f"ERROR loading schema: {exc}", file=sys.stderr)
        sys.exit(1)

    # Collect packs to validate
    if args.all:
        packs = [
            p for p in sorted(packs_dir.glob("*.yaml"))
            if p.name != _SCHEMA_FILE
        ]
        if not packs:
            print("No pack files found.", file=sys.stderr)
            sys.exit(0)
    else:
        name = args.pack
        if not name.endswith(".yaml"):
            name = name + ".yaml"
        pack_path = packs_dir / name
        if not pack_path.exists():
            print(f"ERROR: pack file not found: {pack_path}", file=sys.stderr)
            sys.exit(1)
        packs = [pack_path]

    # Validate each pack
    all_valid = True
    for pack_path in packs:
        errors = validate_pack_file(pack_path, schema)
        if errors:
            all_valid = False
            print(f"INVALID: {pack_path.name}", file=sys.stderr)
            for err in errors:
                print(err, file=sys.stderr)
        else:
            print(f"OK: {pack_path.name}")

    sys.exit(0 if all_valid else 1)


if __name__ == "__main__":
    _main()

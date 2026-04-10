# SKILL: AppleScript Bridge (v1.0)

Provides a robust, standardized interface for executing and validating AppleScript on macOS. Handles common pitfalls like UTF-8 encoding, the `¬` line-continuation artifact, and process timeouts.

---

## Purpose
To serve as the "Universal Translator" between Python/Shell and macOS system applications (Contacts, Calendar, Mail, Notes, etc.) via AppleScript.

---

## Interface

### 1. **Execution (`scripts/run.py`)**
Runs an AppleScript and returns JSON-formatted results.
- **Input**: Path to an `.applescript` file or a raw script string.
- **Features**:
  - **Timeout Control**: Default 300s.
  - **UTF-8 Sanitization**: Forces `en_US.UTF-8` environment.
  - **Temporary File isolation**: Uses `NamedTemporaryFile` to prevent race conditions.

### 2. **Verification (`scripts/compile.py`)**
Syntax-checks a script before execution.
- **Tool**: Uses `osacompile`.
- **Pre-check**: Scans for the `¬` character which often causes compilation failure in automated contexts.

---

## Usage Patterns

### Python Integration
```python
from _skills.applescript_bridge.scripts.run import run_script

script = """
tell application "Finder" to get name of startup disk
"""
result = run_script(script)

if result["success"]:
    print(f"Disk Name: {result['output']}")
else:
    print(f"Error: {result['error']}")
```

### CLI Execution
```bash
python3 _skills/applescript-bridge/scripts/run.py --file my_script.applescript
```

---

## Common Gotchas & Fixes

1. **The `¬` Artifact**: If you see compilation errors on multi-line scripts, ensure you are not using `¬`. The `compile.py` script will flag this.
2. **Missing Value**: AppleScript returns `missing value` for null properties. The Bridge automatically converts this to `""` or `null` in Python.
3. **Shell Quoting**: Never manually escape quotes for AppleScript. Use the `template_util.py` provided in this Skill to safely wrap strings.
4. **Source Verification**: Before using an unfamiliar AppleScript command or addressing scheme not already present in this project, verify its behavior against the current macOS version. Training data may reflect deprecated or renamed APIs. Cite the source with `Source: <reference>` in your response.

---

## Common Rationalizations

| Rationalization | Why it's wrong |
|---|---|
| *"I'll skip the osacompile check, the script looks fine"* | *AppleScript encoding errors are invisible to the eye. The `¬` character looks like a normal line break but crashes compilation.* |
| *"I know this AppleScript command from training data"* | *AppleScript dictionaries change between macOS versions. What worked on Monterey may not exist on Sequoia.* |
| *"The timeout wrapper is unnecessary for this short script"* | *Short scripts calling unresponsive apps (Contacts, Mail) can hang indefinitely. The timeout is a safety net, not overhead.* |

---

## Verification Protocol
A script is considered valid if:
1. `osacompile -o /dev/null <script>` returns exit code 0.
2. It contains zero `¬` characters.
3. It is wrapped in a `with timeout of ...` block.

## Metadata
- **Phase**: BUILD
- **Last verified**: 2026-04-09
- **Status**: Active (example — adapt scripts/ to your environment)

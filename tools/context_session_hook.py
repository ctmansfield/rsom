#!/usr/bin/env python3
"""Emit a short, read-only Codex SessionStart intake reminder.

This adapter does not run the reader, read policy/task records, fetch sources,
write files, or acknowledge adoption. Its installation is optional and must use
the host's normal hook trust review. Input fields never supply shell commands.
"""

import sys

sys.dont_write_bytecode = True

# Suppress cache writes before loading the adapter's other modules.
import json  # noqa: E402
import shlex  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any, Dict, Optional  # noqa: E402

MAX_INPUT_BYTES = 16384
SOURCES = frozenset(("startup", "resume", "clear", "compact", "fork"))


class HookInputError(ValueError):
    """Malformed or oversized hook input; details are deliberately not echoed."""


def _unique_object(pairs: Any) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise HookInputError("Duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise HookInputError("Nonstandard JSON constant")


def parse_source(raw: bytes) -> Optional[str]:
    """Return a supported SessionStart source; unrelated events are a no-op."""
    if not raw or len(raw) > MAX_INPUT_BYTES:
        raise HookInputError("Invalid input length")
    try:
        event = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise HookInputError("Invalid hook JSON") from exc
    if not isinstance(event, dict) or not isinstance(event.get("hook_event_name"), str):
        raise HookInputError("Missing event name")
    if event["hook_event_name"] != "SessionStart":
        return None
    source = event.get("source")
    if not isinstance(source, str):
        raise HookInputError("Missing session source")
    return source if source in SOURCES else None


def build_output(source: str, project_root: Path) -> Dict[str, Any]:
    """Build the documented hook shape from a fixed source and trusted path."""
    if source not in SOURCES:
        raise ValueError("Unsupported session source")
    if not project_root.is_absolute():
        raise ValueError("Project root must be absolute")
    command = shlex.join(
        [
            "python3",
            str(project_root / "tools" / "context_project.py"),
            "--root",
            str(project_root / "context"),
            "resume",
        ]
    )
    context = (
        f"Ordered intake ({source}): read applicable AGENTS; resolve authorized "
        "policy/control freshness; read the project brief/map; select the task; "
        "read its bound instruction, report/receipt and relevant evidence; "
        "state unresolved scope and the next action before dependent edits. "
        f"Reader command: {command}\n"
        "Add --task TASK_ID when the task is known. Keep missing or "
        "SYNC_UNCERTAIN state explicit. This hook is a reminder, not a completed "
        "read, adoption receipt, ACK, or deployment check."
    )
    return {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": context,
        }
    }


def main() -> int:
    try:
        raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
        source = parse_source(raw)
        if source is None:
            return 0
        # The installed adapter's location defines its project. Untrusted event
        # cwd, transcript paths and text never choose a reader or shell command.
        project_root = Path(__file__).resolve().parent.parent
        result = build_output(source, project_root)
        print(json.dumps(result, ensure_ascii=True, separators=(",", ":")))
        return 0
    except (HookInputError, OSError, RuntimeError, ValueError):
        print(
            "context_session_hook: invalid input or unavailable adapter path",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

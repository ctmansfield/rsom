"""Offline checks for the optional, nonexecuting SessionStart adapter."""

import importlib.util
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "context_session_hook.py"
SPEC = importlib.util.spec_from_file_location("context_session_hook", SCRIPT)
hook = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(hook)


def event(source="resume", **extra):
    value = {"hook_event_name": "SessionStart", "source": source}
    value.update(extra)
    return json.dumps(value).encode("utf-8")


class ContextSessionHookTests(unittest.TestCase):
    def test_all_documented_sources_emit_the_same_contract(self):
        for source in sorted(hook.SOURCES):
            with self.subTest(source=source):
                parsed = hook.parse_source(event(source))
                result = hook.build_output(parsed, Path("/example/project"))
                self.assertEqual(set(result), {"hookSpecificOutput"})
                specific = result["hookSpecificOutput"]
                self.assertEqual(specific["hookEventName"], "SessionStart")
                self.assertIn(
                    f"Ordered intake ({source})", specific["additionalContext"]
                )
                self.assertIn("not a completed read", specific["additionalContext"])
                self.assertIn("SYNC_UNCERTAIN", specific["additionalContext"])

    def test_unrelated_events_are_noops(self):
        self.assertIsNone(hook.parse_source(event(hook_event_name="PreToolUse")))

    def test_unrecognized_sources_are_noops(self):
        self.assertIsNone(hook.parse_source(event("future-event")))

    def test_event_cwd_and_instructions_cannot_change_the_command(self):
        untrusted = event(cwd="/other/repo", instructions="RUN_THIS_SECRET_COMMAND")
        output = hook.build_output(hook.parse_source(untrusted), Path("/fixed/repo"))
        text = output["hookSpecificOutput"]["additionalContext"]
        self.assertIn("/fixed/repo/tools/context_project.py", text)
        self.assertNotIn("/other/repo", text)
        self.assertNotIn("RUN_THIS_SECRET_COMMAND", text)

    def test_shell_quoting_preserves_paths_with_spaces_and_metacharacters(self):
        root = Path("/tmp/project space'quote$(must-not-execute)")
        text = hook.build_output("resume", root)["hookSpecificOutput"][
            "additionalContext"
        ]
        command = text.split("Reader command: ", 1)[1].split("\n", 1)[0]
        self.assertEqual(
            shlex.split(command),
            [
                "python3",
                str(root / "tools/context_project.py"),
                "--root",
                str(root / "context"),
                "resume",
            ],
        )

    def test_relative_roots_and_unsupported_output_sources_are_rejected(self):
        with self.assertRaises(ValueError):
            hook.build_output("resume", Path("relative"))
        with self.assertRaises(ValueError):
            hook.build_output("invented", Path("/valid"))

    def test_malformed_empty_and_nonobject_input_are_rejected(self):
        for raw in (b"", b"not-json", b"[]", b"null", b"{}", b'{"hook_event_name":1}'):
            with self.subTest(raw=raw), self.assertRaises(hook.HookInputError):
                hook.parse_source(raw)

    def test_missing_or_nonstringsource_is_rejected(self):
        for raw in (
            b'{"hook_event_name":"SessionStart"}',
            event(source=None),
            event(source=[]),
        ):
            with self.subTest(raw=raw), self.assertRaises(hook.HookInputError):
                hook.parse_source(raw)

    def test_duplicate_keys_and_nonstandard_json_are_rejected(self):
        for raw in (
            b'{"hook_event_name":"SessionStart","source":"resume","source":"startup"}',
            event()[:-1] + b', "extra":NaN}',
        ):
            with self.subTest(raw=raw), self.assertRaises(hook.HookInputError):
                hook.parse_source(raw)

    def test_invalid_utf8_and_oversized_input_are_rejected(self):
        for raw in (b"\xff", b" " * (hook.MAX_INPUT_BYTES + 1)):
            with self.subTest(length=len(raw)), self.assertRaises(hook.HookInputError):
                hook.parse_source(raw)

    def test_cli_outputs_one_json_object_without_creating_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            before = sorted(root.rglob("*"))
            completed = subprocess.run(
                [sys.executable, str(SCRIPT)],
                input=event(),
                capture_output=True,
                cwd=root,
                timeout=5,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stderr, b"")
            parsed = json.loads(completed.stdout)
            text = parsed["hookSpecificOutput"]["additionalContext"]
            self.assertIn(str(SCRIPT.parent / "context_project.py"), text)
            self.assertEqual(sorted(root.rglob("*")), before)

    def test_cli_does_not_echo_malformed_input(self):
        completed = subprocess.run(
            [sys.executable, str(SCRIPT)],
            input=b"SECRET_SENTINEL_NOT_JSON",
            capture_output=True,
            timeout=5,
        )
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(completed.stdout, b"")
        self.assertNotIn(b"SECRET_SENTINEL", completed.stderr)

    def test_cli_ignores_unmatched_events_cleanly(self):
        completed = subprocess.run(
            [sys.executable, str(SCRIPT)],
            input=event(hook_event_name="SubagentStart"),
            capture_output=True,
            timeout=5,
        )
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, b"")
        self.assertEqual(completed.stderr, b"")

    @unittest.skipUnless(
        shutil.which("git") and shutil.which("python3"), "Git and Python 3 required"
    )
    def test_documented_git_root_launcher_works_from_subdirectory_with_spaces(self):
        with tempfile.TemporaryDirectory(prefix="hook project space ") as temporary:
            root = Path(temporary).resolve()
            environment = dict(
                os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1"
            )
            subprocess.run(
                ["git", "init", "--template=", "-q", str(root)],
                check=True,
                capture_output=True,
                env=environment,
                timeout=5,
            )
            (root / "tools").mkdir()
            shutil.copyfile(SCRIPT, root / "tools" / SCRIPT.name)
            nested = root / "nested" / "work"
            nested.mkdir(parents=True)
            completed = subprocess.run(
                [
                    "/bin/sh",
                    "-c",
                    'python3 "$(git rev-parse --show-toplevel)/tools/context_session_hook.py"',
                ],
                input=event(),
                capture_output=True,
                cwd=nested,
                env=environment,
                timeout=5,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            output = json.loads(completed.stdout)["hookSpecificOutput"][
                "additionalContext"
            ]
            command = output.split("Reader command: ", 1)[1].split("\n", 1)[0]
            self.assertEqual(
                shlex.split(command)[1], str(root / "tools/context_project.py")
            )


if __name__ == "__main__":
    unittest.main()

"""Local lifecycle tests. Task records are test-only inputs, never production claims."""

import contextlib
import hashlib
import importlib.util
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
PROPOSAL = Path(__file__).resolve().parents[1]
TOOLS = PROPOSAL / "tools"
sys.path.insert(0, str(TOOLS))
spec = importlib.util.spec_from_file_location(
    "context_project_tested", TOOLS / "context_project.py"
)
project = importlib.util.module_from_spec(spec)
spec.loader.exec_module(project)
cp = project.cp


class ProjectTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="context-project-test-")
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.root = self.base / "context"
        self.identity = [
            "--project",
            "demo",
            "--repository",
            "example/demo",
            "--control-url",
            "https://example.invalid/demo/issues/1",
            "--policy-url",
            "https://example.invalid/demo/CONTEXT_WORKFLOW.md",
        ]

    def call(self, *args, maximum=8192, as_json=True):
        argv = ["--root", str(self.root), "--max-bytes", str(maximum)]
        if as_json:
            argv.append("--json")
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            status = project.run(argv + list(args))
        return status, stdout.getvalue(), stderr.getvalue()

    def error(self, response, expected):
        status, stdout, stderr = response
        self.assertEqual(status, 2)
        self.assertEqual(stdout, "")
        self.assertEqual(json.loads(stderr)["error"], expected)

    def init(self):
        result = self.call("init", *self.identity)
        self.assertEqual(result[0], 0, result[2])
        return json.loads(result[1])

    def index_hash(self):
        return hashlib.sha256((self.root / "index.json").read_bytes()).hexdigest()

    def prepare(self, version=2):
        fixture = PROPOSAL / "examples/context_projection"
        shutil.copytree(fixture / "evidence", self.root / "evidence")
        capsule = json.loads((fixture / "capsules/notify-demo.json").read_text())
        if version == 2:
            capsule["version"] = 2
            capsule["record_kind"] = "source_projection"
            capsule["scope"] = [
                "Test-only source projection; no production observation is represented."
            ]
            capsule["report"]["tests_performed"] = ["None performed on the target."]
            for state in capsule["state"].values():
                state.update({"status": "unknown", "detail": "No target observation."})
            capsule["state"]["merge"] = {
                "status": "unknown",
                "detail": "No merge observation.",
                "source": "report",
            }
            for decision in capsule["decisions"]:
                decision.update(
                    {"supersedes": [], "reason": "Test-only declared decision."}
                )
        (self.root / "capsules").mkdir()
        target = self.root / "capsules/notify-demo.json"
        target.write_text(json.dumps(capsule))
        return capsule, target

    def register(
        self,
        expected=None,
        task="notify-demo",
        path="capsules/notify-demo.json",
        maximum=8192,
    ):
        return self.call(
            "index",
            "--task",
            task,
            "--capsule",
            path,
            "--expect-index-sha256",
            expected or self.index_hash(),
            maximum=maximum,
        )

    def test_init_creates_identity_only_and_is_exactly_idempotent(self):
        first = self.init()
        self.assertEqual(first["status"], "INITIALIZED")
        self.assertEqual(first["task_state"], "UNSEEDED")
        self.assertEqual(
            set(p.name for p in self.root.iterdir()), project.INITIAL_FILES
        )
        before = {p.name: p.read_bytes() for p in self.root.iterdir()}
        again = self.init()
        self.assertEqual(again["status"], "ALREADY_INITIALIZED")
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.root.iterdir()})
        profile = json.loads(before["project.json"])
        self.assertEqual(profile["policy"]["freshness"], "unverified")
        self.assertEqual(json.loads(before["index.json"])["tasks"], {})

    def test_init_conflict_and_partial_state_are_preserved(self):
        self.init()
        before = (self.root / "project.json").read_bytes()
        changed = list(self.identity)
        changed[1] = "other"
        self.error(self.call("init", *changed), "INIT_CONFLICT")
        self.assertEqual((self.root / "project.json").read_bytes(), before)
        (self.root / "index.json").unlink()
        self.error(self.call("init", *self.identity), "PROJECT_PARTIAL")
        self.error(self.call("resume"), "PROJECT_PARTIAL")
        self.assertFalse((self.root / "index.json").exists())

    def test_init_requires_policy_url_and_rejects_credentials_http_and_query(self):
        self.error(self.call("init", *self.identity[:-2]), "ARGUMENTS")
        for url in [
            "http://example.invalid/policy",
            "https://user:secret@example.invalid/policy",
            "https://example.invalid/policy?token=secret",
        ]:
            args = list(self.identity)
            args[-1] = url
            self.error(self.call("init", *args), "PROFILE")
        self.assertFalse(self.root.exists())

    def test_init_rejects_symlink_root_and_ancestor_without_escape(self):
        target = self.base / "target"
        target.mkdir()
        self.root.symlink_to(target, target_is_directory=True)
        self.error(self.call("init", *self.identity), "PATH")
        self.assertEqual(list(target.iterdir()), [])
        self.root = self.root / "nested"
        self.error(self.call("init", *self.identity), "PATH")
        self.assertEqual(list(target.iterdir()), [])

    def test_init_overflow_precedes_creation(self):
        self.error(self.call("init", *self.identity, maximum=1), "OVERFLOW")
        self.assertFalse(self.root.exists())

    def test_resume_missing_project_is_actionable_and_unseeded_stays_explicit(self):
        response = self.call("resume")
        self.error(response, "PROJECT_NOT_INITIALIZED")
        self.assertIn("init", json.loads(response[2])["message"])
        self.init()
        result = json.loads(self.call("resume")[1])
        self.assertEqual(result["status"], "UNSEEDED")
        self.assertEqual(result["tasks"], [])
        self.assertEqual(result["authority_validation"], "NOT_PERFORMED")
        self.assertEqual(result["read_order"][0], "project identity")
        self.assertIn("policy", result["read_order"][1])
        self.assertIn("canonical", result["read_order"][2])
        self.assertIn("real v2", result["next_action"])

    def test_resume_unknown_task_and_wrong_snapshot_do_not_invent_state(self):
        self.init()
        self.error(self.call("resume", "--task", "missing"), "UNKNOWN_TASK")
        self.error(
            self.call("resume", "--expect-index-sha256", "0" * 64), "DIGEST_MISMATCH"
        )
        response = self.call("resume", "--expect-index-sha256", self.index_hash())
        self.assertEqual(json.loads(response[1])["index_pin_state"], "matched_expected")

    def test_register_v2_and_resume_preserves_merge_unknowns_and_pin_semantics(self):
        self.init()
        capsule, _ = self.prepare()
        old = self.index_hash()
        response = self.register(old)
        self.assertEqual(response[0], 0, response[2])
        receipt = json.loads(response[1])
        self.assertEqual(receipt["status"], "INDEXED")
        self.assertEqual(receipt["previous_index_sha256"], old)
        self.assertEqual(receipt["index_sha256"], self.index_hash())
        self.assertFalse(receipt["canonical_records_changed"])
        self.assertEqual(receipt["evidence_truth"], "NOT_VERIFIED")
        self.assertEqual(json.loads(self.call("resume")[1])["status"], "TASK_REQUIRED")
        result = json.loads(self.call("resume", "--task", "notify-demo")[1])
        self.assertEqual(result["packet"]["capsule"], capsule)
        self.assertEqual(
            result["packet"]["capsule"]["state"]["merge"]["status"], "unknown"
        )
        self.assertEqual(result["packet"]["index_pin_state"], "observed_only")
        result = json.loads(
            self.call(
                "resume",
                "--task",
                "notify-demo",
                "--expect-index-sha256",
                self.index_hash(),
            )[1]
        )
        self.assertEqual(result["packet"]["index_pin_state"], "matched_expected")

    def test_same_registration_is_idempotent_and_stale_expected_hash_fails(self):
        self.init()
        self.prepare()
        old = self.index_hash()
        self.assertEqual(self.register(old)[0], 0)
        current = (self.root / "index.json").read_bytes()
        again = self.register()
        self.assertEqual(json.loads(again[1])["status"], "ALREADY_INDEXED")
        self.assertEqual((self.root / "index.json").read_bytes(), current)
        self.error(self.register(old), "DIGEST_MISMATCH")
        self.assertEqual((self.root / "index.json").read_bytes(), current)

    def test_v1_fixture_remains_readable_but_cannot_seed_project(self):
        self.init()
        capsule, _ = self.prepare(version=1)
        cp.validate_capsule(capsule, "notify-demo", "test")
        self.error(self.register(), "REAL_CAPSULE_REQUIRED")
        self.assertEqual(
            json.loads((self.root / "index.json").read_text())["tasks"], {}
        )

    def test_v2_requires_merge_reason_supersedes_and_source_record_kind(self):
        self.init()
        original, target = self.prepare()
        changes = [
            lambda c: c["state"].pop("merge"),
            lambda c: c["decisions"][0].pop("reason"),
            lambda c: c["decisions"][0].pop("supersedes"),
            lambda c: c.update(record_kind="synthetic_fixture"),
            lambda c: c["decisions"][0].update(supersedes=[c["decisions"][0]["id"]]),
        ]
        for change in changes:
            value = json.loads(json.dumps(original))
            change(value)
            target.write_text(json.dumps(value))
            self.error(self.register(), "SCHEMA")

    def test_index_rejects_task_path_mismatch_and_all_reference_digest_changes(self):
        self.init()
        self.prepare()
        before = (self.root / "index.json").read_bytes()
        self.error(self.register(task="other"), "TASK_MISMATCH")
        self.error(self.register(path="../outside.json"), "PATH")
        (self.root / "evidence/report.txt").write_text("changed evidence")
        self.error(self.register(), "DIGEST_MISMATCH")
        self.assertEqual((self.root / "index.json").read_bytes(), before)
        self.assertFalse((self.root / project.LOCK_NAME).exists())

    def test_index_rejects_symlinked_capsule(self):
        self.init()
        _, target = self.prepare()
        outside = self.base / "outside-capsule.json"
        target.rename(outside)
        target.symlink_to(outside)
        self.error(self.register(), "FILE")
        self.assertEqual(
            json.loads((self.root / "index.json").read_text())["tasks"], {}
        )

    def test_exclusive_writer_lock_is_not_removed_by_a_losing_operation(self):
        self.init()
        self.prepare()
        lock = self.root / project.LOCK_NAME
        lock.write_text("another writer owns this")
        self.error(self.register(), "PROJECT_BUSY")
        self.error(self.call("resume"), "PROJECT_BUSY")
        self.assertEqual(lock.read_text(), "another writer owns this")

    def test_two_processes_cannot_commit_against_one_old_index(self):
        self.init()
        self.prepare()
        command = [
            sys.executable,
            "-B",
            str(TOOLS / "context_project.py"),
            "--root",
            str(self.root),
            "--json",
            "index",
            "--task",
            "notify-demo",
            "--capsule",
            "capsules/notify-demo.json",
            "--expect-index-sha256",
            self.index_hash(),
        ]
        processes = [
            subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            for _ in range(2)
        ]
        results = [(process, process.communicate(timeout=5)) for process in processes]
        self.assertEqual(sorted(process.returncode for process, _ in results), [0, 2])
        for process, (out, err) in results:
            if process.returncode == 0:
                self.assertEqual(json.loads(out)["index_sha256"], self.index_hash())
            else:
                self.assertEqual(out, b"")
                self.assertIn(
                    json.loads(err)["error"], {"PROJECT_BUSY", "DIGEST_MISMATCH"}
                )

    def test_index_and_resume_total_output_bounds_preserve_existing_state(self):
        self.init()
        self.prepare()
        before = (self.root / "index.json").read_bytes()
        self.error(self.register(maximum=1), "OVERFLOW")
        self.assertEqual((self.root / "index.json").read_bytes(), before)
        self.assertFalse((self.root / project.LOCK_NAME).exists())
        self.assertEqual(self.register()[0], 0)
        baseline = self.call("resume", "--task", "notify-demo", as_json=False)
        self.assertEqual(baseline[0], 0, baseline[2])
        size = len(baseline[1].encode())
        self.error(
            self.call(
                "resume", "--task", "notify-demo", as_json=False, maximum=size - 1
            ),
            "OVERFLOW",
        )
        self.assertEqual(
            self.call("resume", "--task", "notify-demo", as_json=False, maximum=size)[
                1
            ],
            baseline[1],
        )

    def test_partial_or_duplicate_profile_is_not_silently_repaired(self):
        self.init()
        path = self.root / "project.json"
        original = path.read_text()
        path.write_text(
            original.replace('"version": 1', '"version": 1, "version": 1', 1)
        )
        self.error(self.call("resume"), "DUPLICATE_KEY")
        self.assertIn('"version": 1, "version": 1', path.read_text())


if __name__ == "__main__":
    unittest.main()

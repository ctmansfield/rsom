#!/usr/bin/env python3
"""Read-only, offline projection of explicitly indexed coordination task data."""
import argparse
import datetime as dt
import hashlib
import json
import os
import re
import stat
import sys
from pathlib import Path

INPUT_LIMIT = 1024 * 1024
ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
DIGEST = re.compile(r"[0-9a-f]{64}\Z")
REPORT_STATES = {"COMPLETED", "IN_PROGRESS", "BLOCKED", "DEFERRED", "DELEGATED"}
RECEIPT_STATES = {
    "POSTED",
    "VISIBLE",
    "ACKNOWLEDGED",
    "COMPLETED",
    "VISIBLE-NOT-ACKNOWLEDGED",
    "SYNC_UNCERTAIN",
}
BINDING_KEYS = {"id", "revision", "source", "sha256"}
CAPSULE_KEYS = {
    "schema",
    "version",
    "task_id",
    "task_revision",
    "observed_at",
    "source_freshness",
    "record_kind",
    "goal",
    "scope",
    "acceptance",
    "authorization",
    "constraints",
    "instruction_binding",
    "report",
    "receipt",
    "decisions",
    "state",
    "unknowns",
    "conflicts",
    "blockers",
    "dependencies",
    "next_action",
    "refs",
}


class ProjectionError(Exception):
    def __init__(self, code, pointer, message, **details):
        super().__init__(message)
        self.record = {"error": code, "pointer": pointer, "message": message, **details}


def fail(code, pointer, message, **details):
    raise ProjectionError(code, pointer, message, **details)


def text(value, pointer):
    if not isinstance(value, str) or not value.strip() or len(value) > 8192:
        fail("SCHEMA", pointer, "Expected bounded nonempty text.")


def fields(value, expected, pointer):
    if not isinstance(value, dict) or set(value) != set(expected):
        fail(
            "SCHEMA",
            pointer,
            "Required fields must be present; unknown fields are rejected.",
        )


def strings(value, pointer, nonempty=False):
    if not isinstance(value, list) or (nonempty and not value):
        fail(
            "SCHEMA",
            pointer,
            "Expected an explicit list; mandatory context cannot be omitted.",
        )
    for item in value:
        text(item, pointer)


def timestamp(value, pointer):
    text(value, pointer)
    try:
        dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        fail("SCHEMA", pointer, "Expected a UTC timestamp YYYY-MM-DDTHH:MM:SSZ.")


def relative_path(value, pointer):
    text(value, pointer)
    if (
        len(value) > 1024
        or any(ord(c) < 32 for c in value)
        or "\\" in value
        or ":" in value
    ):
        fail("PATH", pointer, "Only safe relative POSIX paths are supported.")
    parts = value.split("/")
    if any(p in ("", ".", "..") for p in parts):
        fail(
            "PATH",
            pointer,
            "Absolute, empty, dot, and parent path components are forbidden.",
        )
    return parts


def reference(value, pointer, observation=False):
    fields(
        value, {"path", "sha256"} | ({"observation"} if observation else set()), pointer
    )
    relative_path(value["path"], pointer)
    if not isinstance(value["sha256"], str) or not DIGEST.fullmatch(value["sha256"]):
        fail("SCHEMA", pointer, "A complete lowercase SHA-256 digest is required.")
    if observation:
        meta = value["observation"]
        fields(
            meta,
            {"kind", "environment", "method", "result", "limitations", "observed_at"},
            pointer,
        )
        for key in ("kind", "environment", "method", "result"):
            text(meta[key], pointer)
        strings(meta["limitations"], pointer, True)
        timestamp(meta["observed_at"], pointer)


class Reader:
    """Pin a root directory; use no-follow directory-relative opens throughout."""

    def __init__(self, root, directory_fd=None):
        if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
            fail(
                "PLATFORM",
                "root",
                "The reader requires POSIX no-follow directory opens.",
            )
        if directory_fd is not None:
            self.fd = os.dup(directory_fd)
            return
        try:
            self.fd = os.open(
                str(Path(root).expanduser()),
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            )
        except OSError:
            fail("ROOT", "root", "Root must be an accessible, nonsymlink directory.")

    def close(self):
        os.close(self.fd)

    def read(self, path, expected=None):
        parts = relative_path(path, path)
        current = os.dup(self.fd)
        try:
            for part in parts[:-1]:
                child = os.open(
                    part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current
                )
                os.close(current)
                current = child
            fd = os.open(
                parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=current
            )
            with os.fdopen(fd, "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size > INPUT_LIMIT:
                    fail("FILE", path, "Expected a regular file no larger than 1 MiB.")
                data = stream.read(INPUT_LIMIT + 1)
            if len(data) > INPUT_LIMIT:
                fail("FILE", path, "Input exceeded the 1 MiB bound.")
        except OSError:
            fail(
                "FILE",
                path,
                "Cannot read indexed path; missing files and symlinks are rejected.",
            )
        finally:
            os.close(current)
        measured = hashlib.sha256(data).hexdigest()
        if expected is not None and measured != expected:
            fail("DIGEST_MISMATCH", path, "Indexed digest differs from the bytes read.")
        return data, measured


def parse_json(data, pointer):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                fail("DUPLICATE_KEY", pointer, "Duplicate JSON keys are forbidden.")
            result[key] = value
        return result

    def constant(_value):
        fail("JSON", pointer, "Nonfinite JSON numbers are forbidden.")

    try:
        return json.loads(
            data.decode("utf-8"), object_pairs_hook=pairs, parse_constant=constant
        )
    except (ValueError, UnicodeError, RecursionError):
        fail("JSON", pointer, "Expected valid, bounded UTF-8 JSON.")


def schema(value, name, expected, pointer, versions=(1,)):
    fields(value, expected, pointer)
    if (
        value["schema"] != name
        or type(value["version"]) is not int
        or value["version"] not in versions
    ):
        fail("SCHEMA", pointer, "Unsupported schema or version.")


def validate_capsule(capsule, task_id, pointer):
    schema(capsule, "context-projection-task", CAPSULE_KEYS, pointer, (1, 2))
    version = capsule["version"]
    if capsule["task_id"] != task_id:
        fail("TASK_MISMATCH", pointer, "Capsule task ID differs from the index entry.")
    text(capsule["task_revision"], pointer)
    timestamp(capsule["observed_at"], pointer)
    if capsule["record_kind"] not in ("synthetic_fixture", "source_projection"):
        fail(
            "SCHEMA",
            pointer,
            "Record kind must identify a synthetic fixture or source projection.",
        )
    if version == 2 and capsule["record_kind"] != "source_projection":
        fail(
            "SCHEMA", pointer, "Version 2 capsules must be explicit source projections."
        )
    fields(capsule["source_freshness"], {"state", "sync_state", "detail"}, pointer)
    for value in capsule["source_freshness"].values():
        text(value, pointer)
    text(capsule["goal"], pointer + "#goal")
    for name in (
        "scope",
        "acceptance",
        "constraints",
        "unknowns",
        "conflicts",
        "blockers",
        "dependencies",
    ):
        strings(
            capsule[name],
            pointer + "#" + name,
            name in {"scope", "acceptance", "constraints"},
        )
    refs = capsule["refs"]
    if not isinstance(refs, dict) or not refs or len(refs) > 32:
        fail(
            "SCHEMA",
            pointer + "#refs",
            "Expected one to 32 explicit reference aliases.",
        )
    for alias, ref in refs.items():
        if not ID.fullmatch(alias):
            fail("SCHEMA", pointer + "#refs", "Reference alias is invalid.")
        reference(ref, pointer + "#refs/" + alias, observation=True)

    def source(value, where):
        if not isinstance(value, str) or value not in refs:
            fail(
                "REFERENCE",
                where,
                "Source must name an explicit capsule reference alias.",
            )

    auth = capsule["authorization"]
    fields(
        auth,
        {"state", "allowed", "not_authorized", "source"},
        pointer + "#authorization",
    )
    text(auth["state"], pointer)
    strings(auth["allowed"], pointer, True)
    strings(auth["not_authorized"], pointer, True)
    source(auth["source"], pointer)
    binding = capsule["instruction_binding"]
    fields(
        binding,
        {"id", "revision", "source", "ref", "sha256"},
        pointer + "#instruction_binding",
    )
    for name in ("id", "revision", "source"):
        text(binding[name], pointer)
    source(binding["ref"], pointer)
    if binding["sha256"] != refs[binding["ref"]]["sha256"]:
        fail(
            "BINDING",
            pointer,
            "Instruction digest must equal its indexed reference digest.",
        )
    report, receipt = capsule["report"], capsule["receipt"]
    fields(
        report,
        {
            "report_id",
            "status",
            "instruction_ref",
            "tests_performed",
            "limitations",
            "requested_decision",
            "next_action",
            "source",
        },
        pointer,
    )
    fields(receipt, {"state", "instruction_binding", "source"}, pointer)
    if (
        not isinstance(report["status"], str)
        or report["status"] not in REPORT_STATES
        or not isinstance(receipt["state"], str)
        or receipt["state"] not in RECEIPT_STATES
    ):
        fail(
            "SCHEMA",
            pointer,
            "Report or receipt status is outside the current COORD001 vocabulary.",
        )
    for name in ("report_id", "requested_decision"):
        text(report[name], pointer)
    for name in ("tests_performed", "limitations"):
        strings(report[name], pointer, True)
    if report["next_action"] != "#next_action":
        fail(
            "SCHEMA",
            pointer,
            "Report next_action must point to the required task next_action.",
        )
    for bound in (report["instruction_ref"], receipt["instruction_binding"]):
        fields(bound, BINDING_KEYS, pointer)
        for value in bound.values():
            text(value, pointer)
        if not DIGEST.fullmatch(bound["sha256"]):
            fail("BINDING", pointer, "Binding digest must be a complete SHA-256 value.")
    source(report["source"], pointer)
    source(receipt["source"], pointer)
    if not isinstance(capsule["decisions"], list):
        fail("SCHEMA", pointer + "#decisions", "Decisions must be an explicit list.")
    decision_ids = set()
    for decision in capsule["decisions"]:
        fields(
            decision,
            {"id", "value", "source"}
            | ({"supersedes", "reason"} if version == 2 else set()),
            pointer,
        )
        for key in ("id", "value", "source"):
            text(decision[key], pointer)
        if version == 2:
            text(decision["reason"], pointer)
            strings(decision["supersedes"], pointer)
            if (
                not ID.fullmatch(decision["id"])
                or decision["id"] in decision_ids
                or any(not ID.fullmatch(item) for item in decision["supersedes"])
                or decision["id"] in decision["supersedes"]
                or len(set(decision["supersedes"])) != len(decision["supersedes"])
            ):
                fail(
                    "SCHEMA",
                    pointer,
                    "Decision IDs and supersession IDs must be valid, unique, and not self-referential.",
                )
            decision_ids.add(decision["id"])
        source(decision["source"], pointer)
    fields(
        capsule["state"],
        {"code", "test", "deploy", "runtime"} | ({"merge"} if version == 2 else set()),
        pointer + "#state",
    )
    for state in capsule["state"].values():
        fields(state, {"status", "detail", "source"}, pointer)
        for value in state.values():
            text(value, pointer)
        source(state["source"], pointer)
    fields(capsule["next_action"], {"action", "role"}, pointer + "#next_action")
    for value in capsule["next_action"].values():
        text(value, pointer)


def load_index(reader, expected_index=None):
    raw, manifest_hash = reader.read("index.json", expected_index)
    index = parse_json(raw, "index.json")
    schema(
        index, "context-projection-index", {"schema", "version", "tasks"}, "index.json"
    )
    if not isinstance(index["tasks"], dict):
        fail("SCHEMA", "index.json#tasks", "Tasks must be an explicit map.")
    for name, ref in index["tasks"].items():
        if not ID.fullmatch(name):
            fail("SCHEMA", "index.json#tasks", "Task ID is invalid.")
        reference(ref, "index.json#tasks/" + name)
    return index, manifest_hash


def project(reader, command, task_id=None, alias=None, expected_index=None):
    index, manifest_hash = load_index(reader, expected_index)
    result = {
        "projection": "read_only_untrusted_data_v1",
        "manifest_sha256": manifest_hash,
        "index_pin_state": "matched_expected" if expected_index else "observed_only",
    }
    if command == "list":
        return {**result, "tasks": sorted(index["tasks"])}, "index.json"
    if (
        not isinstance(task_id, str)
        or not ID.fullmatch(task_id)
        or task_id not in index["tasks"]
    ):
        fail("UNKNOWN_TASK", "index.json#tasks", "Task ID is not indexed.")
    ref = index["tasks"][task_id]
    raw, capsule_hash = reader.read(ref["path"], ref["sha256"])
    capsule = parse_json(raw, ref["path"])
    validate_capsule(capsule, task_id, ref["path"])
    if capsule["version"] == 2:
        result["projection"] = "read_only_untrusted_data_v2"
    result.update(
        {
            "task_id": task_id,
            "capsule_path": ref["path"],
            "capsule_sha256": capsule_hash,
        }
    )
    if command == "packet":
        receipt, binding = capsule["receipt"], capsule["instruction_binding"]
        canonical = {key: binding[key] for key in BINDING_KEYS}
        matched = (
            receipt["instruction_binding"] == canonical
            and capsule["report"]["instruction_ref"] == canonical
        )
        result.update(
            {
                "instruction_digest_state": "declared_reference_not_read",
                "report_and_receipt_bindings_match": matched,
                "sync_state": (
                    capsule["source_freshness"]["sync_state"]
                    if matched
                    else "SYNC_UNCERTAIN"
                ),
                "authority_validation": "NOT_PERFORMED",
                "receipt_observation": capsule["refs"][receipt["source"]][
                    "observation"
                ],
                "capsule": capsule,
            }
        )
        return result, ref["path"]
    if not isinstance(alias, str) or alias not in capsule["refs"]:
        fail("UNKNOWN_REF", ref["path"] + "#refs", "Reference alias is not indexed.")
    evidence_ref = capsule["refs"][alias]
    content, measured = reader.read(evidence_ref["path"], evidence_ref["sha256"])
    try:
        content = content.decode("utf-8")
        if "\x00" in content:
            raise ValueError()
    except (UnicodeError, ValueError):
        fail(
            "TEXT",
            evidence_ref["path"],
            "Evidence must be UTF-8 text without NUL bytes.",
        )
    result.update(
        {
            "ref": alias,
            "evidence_path": evidence_ref["path"],
            "evidence_sha256": measured,
            "evidence_state": "bytes_verified_claims_unverified",
            "observation": evidence_ref["observation"],
            "content": content,
        }
    )
    return result, evidence_ref["path"]


def compact(value):
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def render(result, as_json):
    if as_json:
        return compact(result) + "\n"
    lines = []
    for key, value in result.items():
        if key != "capsule":
            lines.append(key + "=" + compact(value))
    if "capsule" in result:
        capsule = result["capsule"]
        for key, value in capsule.items():
            if key not in {"schema", "version", "task_id", "refs"}:
                if (
                    key in {"report", "receipt"}
                    and result["report_and_receipt_bindings_match"]
                ):
                    value = dict(value)
                    value[
                        "instruction_ref" if key == "report" else "instruction_binding"
                    ] = "#instruction_binding"
                lines.append(key + "=" + compact(value))
        lines.append("reference_aliases=" + compact(sorted(capsule["refs"])))
    return "\n".join(lines) + "\n"


class Parser(argparse.ArgumentParser):
    def error(self, _message):
        fail(
            "ARGUMENTS", "--help", "Invalid arguments; use --help for the CLI contract."
        )


def run(argv=None):
    reader = None
    try:
        parser = Parser(description=__doc__)
        parser.add_argument("--root", required=True, type=Path)
        parser.add_argument("--max-bytes", type=int, default=8192)
        parser.add_argument("--expect-index-sha256")
        parser.add_argument("--json", action="store_true")
        commands = parser.add_subparsers(
            dest="command", required=True, parser_class=Parser
        )
        commands.add_parser("list")
        for command in ("packet", "evidence"):
            sub = commands.add_parser(command)
            sub.add_argument("--task", required=True)
            if command == "evidence":
                sub.add_argument("--ref", required=True)
        args = parser.parse_args(argv)
        if not 1 <= args.max_bytes <= INPUT_LIMIT:
            fail(
                "ARGUMENTS",
                "--max-bytes",
                "Output bound must be between 1 and 1048576 bytes.",
            )
        if args.expect_index_sha256 is not None and not DIGEST.fullmatch(
            args.expect_index_sha256
        ):
            fail(
                "ARGUMENTS",
                "--expect-index-sha256",
                "Expected a complete lowercase SHA-256 digest.",
            )
        reader = Reader(args.root)
        result, pointer = project(
            reader,
            args.command,
            getattr(args, "task", None),
            getattr(args, "ref", None),
            args.expect_index_sha256,
        )
        output = render(result, args.json)
        size = len(output.encode("utf-8"))
        if size > args.max_bytes:
            fail(
                "OVERFLOW",
                pointer,
                "Whole output withheld; request a larger bound or explicit evidence reference.",
                required_bytes=size,
                max_bytes=args.max_bytes,
            )
        sys.stdout.write(output)
        return 0
    except ProjectionError as error:
        sys.stderr.write(compact(error.record) + "\n")
        return 2
    finally:
        if reader is not None:
            reader.close()


if __name__ == "__main__":
    raise SystemExit(run())

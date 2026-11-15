#!/usr/bin/env python3
"""Initialize local context metadata, resume ordered reading, or register a capsule."""
import contextlib
import hashlib
import json
import os
import re
import sys
import uuid
from pathlib import Path
from urllib.parse import urlsplit

sys.dont_write_bytecode = True
# Suppress cache writes before loading the sibling reader.
import context_projection as cp  # noqa: E402

PROFILE_KEYS = {"schema", "version", "project", "repository", "control_url", "policy"}
INITIAL_FILES = {"project.json", "index.json", "README.md"}
LOCK_NAME = ".context-project.lock"
EMPTY_INDEX = {"schema": "context-projection-index", "version": 1, "tasks": {}}


def encoded(value):
    return (
        json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def https_url(value, pointer):
    cp.text(value, pointer)
    try:
        url = urlsplit(value)
        valid = (
            url.scheme == "https"
            and url.hostname
            and url.username is None
            and url.password is None
            and not url.query
        )
        _ = url.port
    except ValueError:
        valid = False
    if not valid or any(c.isspace() or ord(c) < 32 for c in value):
        cp.fail(
            "PROFILE",
            pointer,
            "Use a canonical HTTPS locator without credentials or query parameters.",
        )


def validate_profile(profile):
    cp.schema(profile, "context-project", PROFILE_KEYS, "project.json")
    if not isinstance(profile["project"], str) or not cp.ID.fullmatch(
        profile["project"]
    ):
        cp.fail("PROFILE", "project.json#project", "Project slug is invalid.")
    repo = profile["repository"]
    if (
        not isinstance(repo, str)
        or not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}", repo)
        or any(p in {".", ".."} for p in repo.split("/"))
    ):
        cp.fail(
            "PROFILE", "project.json#repository", "Repository must use owner/repo form."
        )
    https_url(profile["control_url"], "project.json#control_url")
    cp.fields(profile["policy"], {"url", "version", "freshness"}, "project.json#policy")
    https_url(profile["policy"]["url"], "project.json#policy/url")
    if (
        profile["policy"]["version"] != "context-v1"
        or profile["policy"]["freshness"] != "unverified"
    ):
        cp.fail(
            "PROFILE",
            "project.json#policy",
            "Policy pointer must declare context-v1 and unverified freshness.",
        )


def open_root(path, create=False):
    """Reject symlinks in every root component; retain a pinned directory fd."""
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        cp.fail(
            "PLATFORM", "root", "POSIX no-follow directory operations are required."
        )
    path = Path(path).expanduser().absolute()
    if ".." in path.parts:
        cp.fail("PATH", "root", "Parent traversal is forbidden in the context root.")
    current = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for component in path.parts[1:]:
            try:
                child = os.open(
                    component,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=current,
                )
            except FileNotFoundError:
                if not create:
                    cp.fail(
                        "PROJECT_NOT_INITIALIZED",
                        "root",
                        "Run init with project, repository, control-url and policy-url before resume.",
                    )
                try:
                    os.mkdir(component, mode=0o700, dir_fd=current)
                except FileExistsError:
                    pass
                child = os.open(
                    component,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=current,
                )
            os.close(current)
            current = child
        return current
    except OSError:
        os.close(current)
        cp.fail(
            "PATH",
            "root",
            "Context root must contain only accessible nonsymlink directories.",
        )
    except Exception:
        os.close(current)
        raise


@contextlib.contextmanager
def project_lock(directory):
    try:
        fd = os.open(
            LOCK_NAME,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=directory,
        )
    except FileExistsError:
        cp.fail(
            "PROJECT_BUSY",
            LOCK_NAME,
            "Another writer or an interrupted operation holds the lock; inspect it before retrying.",
        )
    info = os.fstat(fd)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(b"context-project exclusive local writer\n")
            stream.flush()
            os.fsync(stream.fileno())
        yield
    finally:
        try:
            current = os.stat(LOCK_NAME, dir_fd=directory, follow_symlinks=False)
            if (current.st_dev, current.st_ino) == (info.st_dev, info.st_ino):
                os.unlink(LOCK_NAME, dir_fd=directory)
        except FileNotFoundError:
            pass


def load_profile(reader):
    names = set(os.listdir(reader.fd))
    if not INITIAL_FILES <= names:
        code = "PROJECT_PARTIAL" if INITIAL_FILES & names else "PROJECT_NOT_INITIALIZED"
        cp.fail(
            code,
            "project.json",
            "Project metadata is absent or incomplete; init only an empty root, and inspect partial state without overwriting it.",
        )
    raw, digest = reader.read("project.json")
    profile = cp.parse_json(raw, "project.json")
    validate_profile(profile)
    reader.read("README.md")
    return profile, digest


def presentation(result, as_json):
    if as_json:
        return cp.compact(result) + "\n"
    header = {key: value for key, value in result.items() if key != "packet"}
    output = cp.render(header, False)
    if "packet" in result:
        output += cp.render(result["packet"], False)
    return output


def bounded(result, args, pointer):
    output = presentation(result, args.json)
    size = len(output.encode("utf-8"))
    if size > args.max_bytes:
        cp.fail(
            "OVERFLOW",
            pointer,
            "Whole output withheld; raise the explicit byte bound. No mutation was performed by this operation.",
            required_bytes=size,
            max_bytes=args.max_bytes,
        )
    return output


def initialize(args):
    profile = {
        "schema": "context-project",
        "version": 1,
        "project": args.project,
        "repository": args.repository,
        "control_url": args.control_url,
        "policy": {
            "url": args.policy_url,
            "version": "context-v1",
            "freshness": "unverified",
        },
    }
    validate_profile(profile)
    readme = (
        "# "
        + args.project
        + " context\n\nRepository: "
        + args.repository
        + "\nPolicy: "
        + args.policy_url
        + "\nCanonical control: "
        + args.control_url
        + "\n\nLocal projection metadata only. No tasks, evidence or ACKs are seeded.\n"
        "Run context_project.py --root THIS_DIRECTORY resume before work.\n"
        "Read the current policy and canonical instruction; this folder does not grant authority.\n"
    ).encode("utf-8")
    contents = {
        "project.json": encoded(profile),
        "index.json": encoded(EMPTY_INDEX),
        "README.md": readme,
    }
    result = {
        "status": "INITIALIZED",
        "project": args.project,
        "repository": args.repository,
        "project_sha256": hashlib.sha256(contents["project.json"]).hexdigest(),
        "index_sha256": hashlib.sha256(contents["index.json"]).hexdigest(),
        "task_state": "UNSEEDED",
        "next_action": "Resume, read current policy and canonical instruction, then prepare a real v2 source capsule.",
    }
    output = bounded(result, args, "project.json")
    directory = open_root(args.root, create=True)
    try:
        with project_lock(directory):
            names = set(os.listdir(directory)) - {LOCK_NAME}
            if names:
                if not INITIAL_FILES <= names:
                    cp.fail(
                        "PROJECT_PARTIAL",
                        "root",
                        "Existing or partial state is preserved; initialization requires an empty root.",
                    )
                reader = cp.Reader(args.root, directory_fd=directory)
                try:
                    exact = names == INITIAL_FILES and all(
                        reader.read(name)[0] == data for name, data in contents.items()
                    )
                finally:
                    reader.close()
                if not exact:
                    cp.fail(
                        "INIT_CONFLICT",
                        "project.json",
                        "Existing profile/index differs or project data is present; use resume instead of reinitializing.",
                    )
                result["status"] = "ALREADY_INITIALIZED"
                return bounded(result, args, "project.json")
            for name, data in contents.items():
                fd = os.open(
                    name,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=directory,
                )
                with os.fdopen(fd, "wb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
            os.fsync(directory)
        return output
    finally:
        os.close(directory)


def resume(args, reader):
    if LOCK_NAME in os.listdir(reader.fd):
        cp.fail(
            "PROJECT_BUSY",
            LOCK_NAME,
            "A local writer is active or its lock needs review; retry after it finishes.",
        )
    profile, digest = load_profile(reader)
    listing, _ = cp.project(reader, "list", expected_index=args.expect_index_sha256)
    result = {
        "status": (
            "TASK_SELECTED"
            if args.task
            else ("TASK_REQUIRED" if listing["tasks"] else "UNSEEDED")
        ),
        "project": profile["project"],
        "repository": profile["repository"],
        "project_sha256": digest,
        "policy": profile["policy"],
        "canonical_control": profile["control_url"],
        "read_order": [
            "project identity",
            "current policy at policy.url",
            "current canonical instruction at canonical_control",
            "selected task projection",
            "only needed indexed references",
        ],
        "authority_validation": "NOT_PERFORMED",
    }
    if args.task:
        packet, pointer = cp.project(
            reader, "packet", args.task, expected_index=listing["manifest_sha256"]
        )
        packet["index_pin_state"] = listing["index_pin_state"]
        result["packet"] = packet
    else:
        result.update(
            {
                "manifest_sha256": listing["manifest_sha256"],
                "index_pin_state": listing["index_pin_state"],
                "tasks": listing["tasks"],
                "next_action": "Read the current canonical instruction; prepare/register a real v2 capsule if absent, then resume with an explicit task ID.",
            }
        )
        pointer = "index.json"
    return bounded(result, args, pointer)


def index_capsule(args, reader):
    if not isinstance(args.task, str) or not cp.ID.fullmatch(args.task):
        cp.fail("ARGUMENTS", "--task", "Task ID is invalid.")
    cp.relative_path(args.capsule, "--capsule")
    with project_lock(reader.fd):
        load_profile(reader)
        index, previous = cp.load_index(reader, args.expect_index_sha256)
        raw, digest = reader.read(args.capsule)
        capsule = cp.parse_json(raw, args.capsule)
        cp.validate_capsule(capsule, args.task, args.capsule)
        if capsule["version"] != 2 or capsule["record_kind"] != "source_projection":
            cp.fail(
                "REAL_CAPSULE_REQUIRED",
                args.capsule,
                "Register a prepared v2 source_projection capsule; synthetic v1 fixtures cannot seed a project.",
            )
        if any(
            task != args.task and ref["path"] == args.capsule
            for task, ref in index["tasks"].items()
        ):
            cp.fail(
                "PATH_MISMATCH",
                args.capsule,
                "This capsule path is already indexed for another task.",
            )
        for ref in capsule["refs"].values():
            data, _ = reader.read(ref["path"], ref["sha256"])
            try:
                if "\x00" in data.decode("utf-8"):
                    raise ValueError()
            except (UnicodeError, ValueError):
                cp.fail(
                    "TEXT",
                    ref["path"],
                    "Indexed references must be UTF-8 text without NUL bytes.",
                )
        reader.read(args.capsule, digest)
        entry = {"path": args.capsule, "sha256": digest}
        same = index["tasks"].get(args.task) == entry
        index["tasks"][args.task] = entry
        replacement = encoded(index)
        result = {
            "status": "ALREADY_INDEXED" if same else "INDEXED",
            "task_id": args.task,
            "task_revision": capsule["task_revision"],
            "capsule_sha256": digest,
            "previous_index_sha256": previous,
            "index_sha256": (
                previous if same else hashlib.sha256(replacement).hexdigest()
            ),
            "references_verified": len(capsule["refs"]),
            "evidence_truth": "NOT_VERIFIED",
            "canonical_records_changed": False,
        }
        output = bounded(result, args, "index.json")
        if same:
            return output
        temporary = ".context-index-" + uuid.uuid4().hex + ".tmp"
        try:
            fd = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=reader.fd,
            )
            with os.fdopen(fd, "wb") as stream:
                stream.write(replacement)
                stream.flush()
                os.fsync(stream.fileno())
            reader.read("index.json", previous)
            os.replace(
                temporary, "index.json", src_dir_fd=reader.fd, dst_dir_fd=reader.fd
            )
            os.fsync(reader.fd)
        finally:
            try:
                os.unlink(temporary, dir_fd=reader.fd)
            except FileNotFoundError:
                pass
        return output


def run(argv=None):
    reader = None
    directory = None
    try:
        parser = cp.Parser(description=__doc__)
        parser.add_argument("--root", required=True, type=Path)
        parser.add_argument("--json", action="store_true")
        parser.add_argument("--max-bytes", type=int, default=8192)
        commands = parser.add_subparsers(
            dest="command", required=True, parser_class=cp.Parser
        )
        init = commands.add_parser("init")
        for name in ("project", "repository", "control-url", "policy-url"):
            init.add_argument("--" + name, required=True)
        cold = commands.add_parser("resume")
        cold.add_argument("--task")
        cold.add_argument("--expect-index-sha256")
        register = commands.add_parser("index")
        for name in ("task", "capsule", "expect-index-sha256"):
            register.add_argument("--" + name, required=True)
        args = parser.parse_args(argv)
        if not 1 <= args.max_bytes <= cp.INPUT_LIMIT:
            cp.fail(
                "ARGUMENTS",
                "--max-bytes",
                "Output bound must be between 1 and 1048576 bytes.",
            )
        expected = getattr(args, "expect_index_sha256", None)
        if expected is not None and not cp.DIGEST.fullmatch(expected):
            cp.fail(
                "ARGUMENTS",
                "--expect-index-sha256",
                "A complete lowercase SHA-256 digest is required.",
            )
        if args.command == "init":
            output = initialize(args)
        else:
            directory = open_root(args.root)
            reader = cp.Reader(args.root, directory_fd=directory)
            output = (
                resume(args, reader)
                if args.command == "resume"
                else index_capsule(args, reader)
            )
        sys.stdout.write(output)
        return 0
    except cp.ProjectionError as error:
        sys.stderr.write(cp.compact(error.record) + "\n")
        return 2
    except OSError:
        sys.stderr.write(
            cp.compact(
                {
                    "error": "LOCAL_IO_ERROR",
                    "pointer": "root",
                    "message": "Local I/O failed. Inspect project state before retrying; partial writes are never automatically repaired.",
                }
            )
            + "\n"
        )
        return 2
    finally:
        if reader is not None:
            reader.close()
        if directory is not None:
            os.close(directory)


if __name__ == "__main__":
    raise SystemExit(run())

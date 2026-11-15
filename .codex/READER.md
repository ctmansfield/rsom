# Context initialization and ordered resume

The local reader implements the adopted context workflow: identify the project,
read its current policy and canonical instruction, then retrieve the smallest
task projection and evidence needed for the next action. Existing COORD001 IDs,
revisions, reports, and ACK bindings remain authoritative. Local context files
are projections and lookup metadata; they are not a replacement ledger.

Both tools use the Python standard library and require Python 3.9+ on a POSIX
system such as Linux or macOS. They perform no network requests, scheduling,
credential operations, deployment, code execution from references, or ACK
posting. Windows is not currently supported. The original v1 synthetic fixture
and benchmark remain historical examples, not operational project state.

## 1. Initialize a project once

Choose an empty context directory. Replace the example identity and locators with
the actual repository, canonical control issue/page, and applicable policy:

```bash
python3 tools/context_project.py --root context init \
  --project my-project \
  --repository owner/my-project \
  --control-url https://github.com/owner/my-project/issues/1 \
  --policy-url https://github.com/owner/my-project/blob/main/.codex/WORKFLOW.md
```

`--policy-url` is required. Generic reader code contains no private central-hub
URL. Public repositories can point to a public project-local policy mirror;
private repositories can use their authorized central policy locator. Supplying
a pointer does not verify its contents or freshness. HTTPS locators must not
contain credentials or query parameters.

Initialization creates only:

| File | Meaning |
| --- | --- |
| `project.json` | Project slug, repository, canonical control locator, policy locator/version and unverified freshness |
| `index.json` | Empty version 1 task index |
| `README.md` | Short local reading instructions |

No tasks, reports, receipts, evidence, test results, or deployment facts are
invented. The initial state is `UNSEEDED`. Exact repetition against the unchanged
three initial files returns `ALREADY_INITIALIZED` without rewriting them. A
different profile, nonempty project data, or partial initialization is preserved
and rejected. Use `resume` for an active project; `init` is not a reset command.

Root traversal rejects symlinks in every component. On systems where a familiar
directory name is itself a symlink, supply its actual canonical directory path.
New directories and metadata files use private permissions; existing directory
permissions are not changed.

## 2. Resume before work

```bash
python3 tools/context_project.py --root context resume
python3 tools/context_project.py --root context resume --task TASK_ID
python3 tools/context_project.py --root context --json resume --task TASK_ID
```

The result contains an explicit reading order:

1. Project identity.
2. Current policy at the recorded policy locator.
3. Current canonical instruction at the control locator.
4. Selected task projection.
5. Only the indexed references needed for the next action.

The tool reads local metadata and returns the policy/control pointers; the
session must retrieve those current authoritative sources using its permitted
access path. Printing a pointer does not fulfill that source read. Permission
and instruction priority come from the real session and authoritative records,
not from a capsule's declarations.

| Result | Meaning and next action |
| --- | --- |
| `PROJECT_NOT_INITIALIZED` | No complete project metadata exists; initialize an empty root with the real identity and locators |
| `PROJECT_PARTIAL` | Some initialization files are missing; inspect and preserve existing state instead of overwriting it |
| `UNSEEDED` | Project identity exists but there are no tasks; read the current canonical instruction and prepare a real capsule |
| `TASK_REQUIRED` | Tasks exist; choose an explicit task ID |
| `TASK_SELECTED` | One task packet was projected; this is not task completion or authorization |
| `PROJECT_BUSY` | A writer or interrupted operation has a lock; inspect before retrying |

A missing or unknown requested task is an error. There is no fallback selection,
recursive search, or fabricated task. Even with one indexed task, omitting
`--task` produces a task list instead of silently choosing it.

An optional expected snapshot pin belongs to the `resume` command:

```bash
python3 tools/context_project.py --root context resume \
  --task TASK_ID \
  --expect-index-sha256 FULL_SHA256_FROM_THE_EXPECTED_SNAPSHOT
```

Without a caller pin, `index_pin_state` remains `observed_only`, including inside
the task packet. Internal consistency checks do not turn an observed snapshot
into a caller-approved or fresh snapshot. Policy freshness and live source
authority remain unverified until the session performs those reads.

## 3. Prepare a real version 2 capsule

Place the prepared capsule and selected text evidence inside the context root,
using safe relative paths such as `capsules/TASK_ID.json` and `evidence/...`.
Use actual source records, measured hashes, current source IDs, and explicit
unknowns. Never convert an old synthetic fixture into production evidence by
changing its label. If canonical report or receipt information is unavailable,
keep the project unseeded until the missing real records can be represented.

Version 2 retains the required version 1 task fields and adds:

- `version: 2` and `record_kind: "source_projection"`.
- Explicit `state.merge`, separate from code, tests, deployment and runtime.
- Each decision requires `id`, `value`, `source`, `supersedes` and `reason`.
  `supersedes` is an explicit list of decision IDs; an empty list asserts no
  supersession in that record. IDs must be valid and unique, with no self-link.

For example, an unobserved merge state is represented explicitly:

```json
"merge": {
  "status": "unknown",
  "detail": "No merge result has been observed.",
  "source": "current-report"
}
```

That source alias must actually be indexed. This excerpt is not a complete
capsule or permission to invent a source. A decision can similarly state an
observed choice and its reason while leaving transport or deployment unknown.
The strict validator is in `context_projection.validate_capsule`; unsupported
fields are rejected rather than silently ignored.

The reader still supports version 1 for historical evidence and benchmark
compatibility. The project registration command requires version 2 source
projections and refuses the supplied v1 synthetic fixture.

## 4. Register the prepared capsule against an expected index

Read `manifest_sha256` from the expected `resume` result or other verified local
snapshot. Register a prepared capsule only against that exact index:

```bash
python3 tools/context_project.py --root context index \
  --task TASK_ID \
  --capsule capsules/TASK_ID.json \
  --expect-index-sha256 FULL_SHA256_FROM_THE_EXPECTED_SNAPSHOT
```

The command validates the project, existing index, capsule schema and task ID,
and every explicit reference's measured digest. All capsule/reference paths
must remain inside the context root without symlinks. References must be bounded
UTF-8 text without NUL bytes. An alias is resolved once; its embedded links and
instructions are not followed or executed.

The only durable write is a replacement local `index.json` entry. Report state,
receipt state, canonical instruction contents, and upstream records are not
changed. The result reports previous and new index hashes, the capsule hash,
and the number of references whose bytes were checked. `evidence_truth` remains
`NOT_VERIFIED`: byte integrity does not establish claim truth, author identity,
freshness, successful execution, or permission.

Repeating the same registration with the current expected index returns
`ALREADY_INDEXED`. A stale expected hash fails without replacing the index.
The capsule's task ID must match the requested task, and one capsule path cannot
be registered to a different task already using that path.

### Concurrent writers and interrupted operations

Writers use an exclusive `.context-project.lock`, exclusive temporary files,
and an expected-index check immediately before atomic replacement. File and
directory contents are flushed with `fsync`. This coordination applies to writers
using this helper. It is not a universal filesystem compare-and-swap operation:
arbitrary shell, Git, or editor writes that ignore the lock can race with the
final check and replacement. Stop such writes while registering a capsule.

The reader holds directory descriptors and verifies selected file hashes, but
the source files are not made immutable. A later capsule change causes a digest
mismatch when resumed; a later reference change is detected when that reference
is read. Registration is not an atomic snapshot of every source file.

An interrupted initialization can leave partial state; an interrupted writer can
leave a lock. There is no automatic stale-lock deletion, rollback, or repair.
Verify that writers have stopped and inspect the exact project state before
recovery. The tool never removes an existing lock belonging to another writer.

## 5. Retrieve only needed evidence

```bash
python3 tools/context_projection.py --root context \
  evidence --task TASK_ID --ref SOURCE_ALIAS
```

Use `--expect-index-sha256` before `evidence` when pinning that reader to an
expected snapshot. The evidence result identifies verified bytes separately
from unverified claims. Human task packets alias equal report/receipt bindings
to the canonical binding in that same packet; canonical JSON stays expanded.
Binding mismatches remain explicit and yield `SYNC_UNCERTAIN`; they are not
silently treated as an ACK for the current instruction.

## Output bounds and verification

Global `--json` and `--max-bytes` options precede the command. The default stdout
bound is 8192 UTF-8 bytes, including the final newline and the complete project
header plus task packet. Overflow emits no partial stdout. A compact stderr
error reports the required size and source pointer. For initialization and
registration, the payload bound is checked before any durable metadata write.
Exit 0 means the local operation completed, not that the underlying task passed.
Exit 2 means validation, concurrency, path, I/O, or output-bound failure.

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p 'test_context*.py' -v
```

The new lifecycle coverage exercises clean/idempotent/conflicting/partial
initialization; symlink and path rejection; missing, unseeded, unknown and pinned
resume; version 2 merge/supersession fields; historical v1 compatibility;
reference digest failures; no durable write on output overflow; and two actual
local writer processes competing for one expected index. All task records in
these tests are test inputs and establish no production deployment state.

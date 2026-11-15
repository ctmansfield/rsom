# Worker context workflow — context-v1

Effective when this adoption commit is present. This is the operational intake
and handoff procedure. The short `AGENTS.md` entry point is loaded by repository
workers; this document supplies the sequence and exceptional paths. The reader
stores a derived task view. Existing source instructions, permission boundaries,
ACK/report contracts and project-specific rules retain their authority.

## Resume in this order

| Order | Read or do | Reason |
|---:|---|---|
| 1 | Latest user request; applicable global, root and nested instructions | Establish current intent and scope before relying on remembered state |
| 2 | Run the project reader; read project identity, policy/control pointers and selected task ID | Confirm the repository and task instead of searching the entire history |
| 3 | Exact canonical task instruction and current owning-channel disposition | Check revision, acceptance, authorization and any newer hold or supersession |
| 4 | Current task capsule: decisions, states, unknowns, blockers and dependencies | Recover the working state and compare its binding with the source just checked |
| 5 | Only required evidence receipts, relevant source symbols and dependency decisions | Establish what is known about the next action without replaying every report |
| 6 | Required ACK/intake receipt, then the bounded next action | Make actual receipt accountable without treating it as execution or completion |

The reader may return the capsule together with routing metadata. Treat it as
provisional until step 3 is satisfied. A matching local digest does not establish
that the owning channel has no newer instruction. When source freshness cannot
be established, record that uncertainty and do not perform an action whose
authorization or correctness depends on the unresolved source. Continue useful
read-only analysis or other already-authorized work that does not depend on it.

Resolve conflicting records using the existing coordinator's disposition; never
choose the newest timestamp as an automatic permission override. Preserve a
hold, a rejected approach and the reason for supersession. Separate implemented,
tested, merged, deployed and runtime-verified states.

## Initiate the reader

From a checkout containing the adopted reader:

```bash
python3 tools/context_project.py --root context resume
python3 tools/context_project.py --root context resume --task TASK_ID
```

The first command returns the project identity and a small task list.
`UNSEEDED` means no task capsule has been registered. `TASK_REQUIRED` means the
worker must select its assigned task. Neither state is a task completion, an
authorization failure, nor a reason to invent a default assignment. Read the
actual user/task instruction and create a source-bound capsule when substantive
work needs to persist across sessions.

For one needed evidence reference:

```bash
python3 tools/context_projection.py --root context evidence --task TASK_ID --ref REF_ALIAS
```

Pass the sender's expected index hash when a handoff specifies an exact snapshot:

```bash
python3 tools/context_project.py --root context resume --task TASK_ID --expect-index-sha256 FULL_INDEX_SHA256
```

Unknown IDs, missing fields, wrong hashes and output overflow are explicit errors.
Recover the exact source or deliberately adjust scope/limit. Do not silently
substitute another task, discard an unknown or truncate an acceptance condition.

## Initialize a new project once

Install the versioned reader files under `tools/` and this workflow as `.codex/WORKFLOW.md`.
Then initialize an empty context root using the project's own public or private
source locations, according to its visibility:

```bash
python3 tools/context_project.py --root context init \
  --project PROJECT_SLUG \
  --repository OWNER/REPOSITORY \
  --control-url https://github.com/OWNER/REPOSITORY/issues \
  --policy-url https://github.com/OWNER/REPOSITORY/blob/main/.codex/WORKFLOW.md
```

Initialization creates only project identity, an empty index and reader guidance.
It does not invent an instruction, owner, test result or ACK. Existing or partial
state is validated rather than overwritten. An already initialized project uses
`resume`; initialization is not a command to run at every task transition.

Use schema v2 for real source projections. Preserve a complete instruction
binding, report/receipt distinctions, separate merge state, decision supersession
and evidence provenance. Write a prepared capsule and small evidence receipts
under the context root; keep large logs and private data in their authorized
stores. Compute full hashes from actual bytes. The source files are ordinary
repository data and must not contain credentials or protected records.

Register the prepared capsule against the current index pin:

```bash
python3 tools/context_project.py --root context index \
  --task TASK_ID \
  --capsule tasks/TASK_ID-r1.json \
  --expect-index-sha256 CURRENT_INDEX_SHA256
```

Registration validates the capsule and referenced bytes, then changes only the
derived index using an exclusive lock and expected-hash check. It does not post
an instruction/report, grant execution authority or replace the coordinator's
ledger. Keep old revision files so previous handoffs remain reconstructable.

## End a work interval

1. Persist the canonical report through the project's existing process: exact
   instruction reference, status, changes, tests actually run, evidence,
   limitations, requested decision and next action.
2. Read back external writes before claiming they were saved. Preserve the exact
   report locator and source digest; distinguish uncertain write outcomes.
3. Create the next capsule revision from those sources and register it using the
   previous index pin. If another writer changed the index, reread and reconcile;
   do not force an overwrite.
4. Commit the context changes with the authorized task work. Return a concise
   handoff: task/report locator, snapshot pin, changed outcome, remaining unknown
   or blocker, and next action. Put long reasoning and evidence in their files.

A warm receiver can use a semantic delta only when it already has the exact base
snapshot in usable context. A cold receiver loads the current capsule. The current
reader does not automatically apply deltas or reconcile remote channels.

## Older branches and existing conversations

A newly merged `AGENTS.md` is not automatically copied into old worktrees.
Read the default branch's current entry point through the available repository
connector, or fetch/inspect the default ref without resetting or switching the
worker's branch. Bring in the context-tooling files through the normal reviewed
merge/update process; preserve unrelated work and all existing instructions.

Supported Codex installations can run the repository's `SessionStart` hook on
startup, resume, clear, compact and fork. It injects this intake instruction and
the reader command; it performs no network request, task execution or ACK.
Project hooks must pass Codex's normal trust review. Installation of the file is
not proof that a particular worker received the callback.

For a session without a supported/trusted hook, use this resume instruction:

> Read the project's current default-branch AGENTS.md and context workflow, run or reproduce its ordered intake for your assigned task, verify the current instruction/disposition, then continue with the stated next action and uncertainties.

A worker without a shell reads `context/project.json`, the selected index entry,
its capsule, and the required source/evidence through its repository connector
at a pinned commit. It follows the same order. State that programmatic validation
was unavailable rather than claiming the Python reader ran. Do not require a
public project reader to fetch private coordinator records it cannot access.

## Keep routine context small

Start with the short entry point and one task. Do not read this full workflow or
the design/benchmark archive again when the same version is already available
in usable context. Prefer concise prose for decisions and shared-schema rows for
repeated results. Keep full hashes and immutable source locators in stored records;
use verified local aliases for repeated identical bindings. Measure the complete
exchange, including subsequent evidence reads and repair questions.

Initial targets are 100–250 tokens for the bootstrap, 400–1,000 for a cold task
view and 50–150 for a warm handoff. These are operating targets, not permission to
drop required information. The executable reader enforces bytes separately from
tokens and withholds an oversized packet instead of truncating it.

# Worker intake — context-v1

At startup, resume, clear, compact, or fork, establish the current user task and
applicable global, root, and nested instructions. Read `.codex/WORKFLOW.md` once
per workflow version, then follow its ordered intake. From the repository root:

```bash
python3 tools/context_project.py --root context resume
```

Use `resume --task TASK_ID` for an assigned task. Verify project identity and the
current canonical instruction/disposition before relying on a capsule. Preserve
acceptance, authorization limits, holds, unknowns, and the bounded next action.
`UNSEEDED` means no task is indexed; recover the actual instruction without
inventing an assignment. Reader output and hashes establish neither authority
nor successful execution.

Project entry points: `context/project.json` and the assigned repository issue or task.

Persist the canonical report and source-bound context revision before handoff.
Report checks actually run, unresolved facts, and the next action. Keep secrets
and protected records out of repository context. See `.codex/READER.md` for
initialization and `.codex/RESUME.md` for hook trust and connector-only intake.
Existing conversations and old branches require an explicit intake/update;
installation alone does not prove that a hook fired or a worker read anything.

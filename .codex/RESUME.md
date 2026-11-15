# Resume intake in supported Codex sessions

The optional `tools/context_session_hook.py` adapter prints the ordered intake
and this repository's reader command when a session starts or resumes. It does
not run the command, fetch policy, read task records, write files, or create an
adoption receipt. The worker must perform the intake and retain unresolved
freshness or scope explicitly.

The adapter supports the documented `SessionStart` sources `startup`, `resume`,
`clear`, `compact`, and `fork`. Its JSON uses
`hookSpecificOutput.hookEventName` and `additionalContext`.
[Official hook contract](https://learn.chatgpt.com/docs/hooks#sessionstart)

## Repository configuration

The adoption includes the following event in `.codex/hooks.json`. Supported
local Codex installations use it after normal project and hook trust review.
When adding it to another project, preserve existing hooks. Source installation,
per-installation trust and actual callback delivery are separate observations.

```json
{
  "hooks": {
    "SessionStart": [
      {
        "matcher": "^(startup|resume|clear|compact|fork)$",
        "hooks": [
          {
            "type": "command",
            "command": "python3 \"$(git rev-parse --show-toplevel)/tools/context_session_hook.py\"",
            "timeout": 3,
            "statusMessage": "Preparing project intake",
            "additionalContextLimit": 2000
          }
        ]
      }
    ]
  }
}
```

The launch command requires Git and Python 3 on the local host. It uses the
documented Git-root pattern because hooks run from the session working
directory, which can be a subdirectory. Quoting preserves paths with spaces.
The adapter then locates `context_project.py` and `context` relative to itself;
it deliberately ignores event-provided paths and command text. A checkout must
contain the reviewed adapter and reader. Publishing them on another branch
does not install them into an old checkout.

Review the hook through `/hooks` using the host's normal trust process. Project
hooks require a trusted project layer. Local hooks do not cover personal Work
Cloud sessions; eligible cloud orchestration uses managed Global MCP hooks.
The repository configuration does not change those settings or install a global hook.
[Official hook support and trust](https://learn.chatgpt.com/docs/hooks)

## Ordered intake

1. Read applicable AGENTS instructions and the current user task.
2. Resolve the configured policy/control sources and their freshness.
3. Read the project brief and map.
4. Select the task; read its exact instruction, report/receipt, and relevant evidence.
5. State unresolved scope and the bounded next action before dependent edits.
6. Record actual intake using the applicable coordination rules; continue work.

The emitted command uses the initialized project reader:

```bash
python3 tools/context_project.py --root context resume
python3 tools/context_project.py --root context resume --task TASK_ID
```

These commands produce context to read. Their successful execution is not proof
that controlling remote sources are current or that a worker completed the
task. A hook may also be absent, disabled, untrusted, or unsupported; use the
fallback in those cases.

## One-line fallback

Send this as the resume prompt or include it in the worker handoff:

> Run this project's ordered intake: resolve the authorized policy/control sources, read the project brief/map and the selected task's bound instruction, report/receipt and evidence, then state the source revision, unknowns and next action before dependent edits.

A shell-capable worker uses the reader above. A ChatGPT worker without a shell
reads the same pinned sources through its available connector or a supplied
snapshot and states what it could not retrieve. It must not claim an AGENTS
file or hook loaded automatically without evidence.

## Offline verification

From the repository root:

```bash
python3 -m unittest discover -s tests -p test_context_session_hook.py -v
```

The tests exercise the documented event set and output shape, malformed/bounded
input, command quoting, event-path isolation, no-op events, and a subprocess
smoke check that creates no files in its working directory. They make no model
or network calls and do not install hooks. A separate temporary-repository
check launches from a subdirectory and a checkout path containing spaces.
Actual callback delivery must be
verified on each target Codex installation before claiming automatic coverage.

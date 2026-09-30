# Architecture

The expensive main agent chooses files and a question/specification. A local Python
script reads those files and launches an independent subscription CLI worker. Only the
worker sees the full payload. Its successful structured response returns to Python;
the main agent receives a summary or metadata about a generated file.

```text
Main agent -> skill -> local script -> saved-login CLI -> worker model
                            |                 |
                            |          full source via stdin
                            +<- bounded structured response
                            |
                  summary OR file-to-disk + metadata
```

Claude uses `-p --safe-mode`, a small replacement system prompt, no built-in tools,
strict empty MCP configuration, noninteractive permissions, and no session persistence.
Its structured response mechanism may report `stop_reason: tool_use`; a successful
`structured_output` with `complete: true` is required. This internal output mechanism
is distinct from enabling filesystem, shell or network tools for the model.

Codex uses `exec --ephemeral --ignore-user-config`, a small worker instruction file,
read-only sandbox, no approvals, disabled shell/browser/app/plugin/delegation features,
and an output schema. Managed policy remains authoritative. CLI-provided session context
still adds overhead; worker usage is reported separately. Unexpected tool items in the
Codex event stream cause rejection. Teeplug is not a substitute for host sandbox policy.

Each worker runs in a temporary directory, receives only the explicit payload over stdin,
and never resumes a parent session. `TEEPLUG_WORKER` prevents recursive Teeplug calls.
Temporary schema/instruction files are removed on completion; no source payload is written
there. Host CLI diagnostic storage and provider retention remain controlled by those products.

The writer model cannot edit the target. Python validates the result, checks the original
file hash, stages it in the target directory, and installs it. New targets are created
without clobbering an existing file; overwrites preserve the file mode and require an
explicit flag. Hash checks detect ordinary concurrent edits, but are not an OS-level
compare-and-swap against a malicious concurrent writer. Structured completion does not
prove semantic correctness: callers must run suitable validation.

The hooks recognize native Read and common literal shell reads. They do not parse all
shell syntax or intercept every tool. A missing executable, unknown host, invalid config,
or disabled routing lets the original call continue. Login is not queried in hooks.
If a worker is unavailable, focused direct reads remain possible.

The reader cache saves future worker calls on exact hits. It is separate from any model
provider prompt cache. Usage fields are counters reported by the CLI, not a currency bill
or guarantee about how subscription allowances are charged.

Project memory is a separate local path with no worker involvement. The main agent
requests an explicit memory operation; validated Python performs a locked, atomic write
to `MEMORY.md` or `OPERATOR.md` outside the installed plugin. Workers never receive memory
files or write tools, and a worker summary is a lead the main agent verifies before
saving. A `SessionStart` hook renders a bounded, labelled block of remembered reference
notes through the host's supported context mechanism; a failure there is silent and the
session continues without it. See [memory](memory.md).

References: [Codex noninteractive mode](https://learn.chatgpt.com/docs/non-interactive-mode),
[Codex configuration](https://learn.chatgpt.com/docs/config-file/config-reference),
[Claude CLI](https://code.claude.com/docs/en/cli-reference),
[Claude headless usage](https://code.claude.com/docs/en/headless).

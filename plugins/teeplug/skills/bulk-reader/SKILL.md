---
name: bulk-reader
description: Delegate broad understanding of large source files to a configured worker and return a focused summary. Use for exploration across large files; use exact source excerpts for edits and difficult debugging.
---

Resolve the plugin root as the directory two levels above this SKILL.md. Run its
`scripts/bulk-read` by absolute path; do not assume the plugin root is the current
working directory or that a hook environment variable exists in ordinary shells.

For host matching, always pass `--host codex` when running in Codex and
`--host claude` when running in Claude Code. The examples below use Codex.
Respect an explicit user `--provider`, `--model`, or `--effort` choice; do not
change the main agent model. Do not retry a failed worker using another provider
without an explicit routing choice. Worker calls use the selected CLI subscription.

The worker CLI needs its own local state and network access, even though its model
cannot use tools. If it reports `worker_access_denied`, or network failure caused by
the host sandbox, retry the same command through the host's normal permission flow.
In Codex `exec_command`, use `sandbox_permissions="require_escalated"` with a short
justification. The script cannot grant itself permission. Keep the worker's read-only
sandbox and managed policy intact; do not move credentials or change providers/models.
If approval is denied or the approved retry also fails, report that specific blocker.

```bash
"<plugin-root>/scripts/bulk-read" --host codex --question "Which methods write to the database?" --paths src/service.py src/storage.py --json
```

Search locally to select relevant paths first. Give the worker a specific question
and paths, without reading the full files into the main conversation. Relative
paths resolve from the shell's current working directory; `--root` sets the allowed
project boundary, not the base for relative paths.

The worker receives numbered source and returns a summary. JSON includes source
hashes and worker usage. Treat summary citations as leads; verify original excerpts
before editing, diagnosing subtle bugs, or making architectural decisions. Prefer
small direct reads once you know the relevant section.

Same-question results are cached by content and worker settings. Use `--no-cache`
to refresh. Input-limit errors require smaller batches; a response-limit error
requires a narrower question or an intentional `--max-output-tokens` adjustment.
Do not automatically dump all files into main context after a worker failure.

If the worker is unconfigured, use this plugin's setup workflow. Small, bounded
reads remain available. A hook redirect is a routing suggestion with a blocked
call, not a claim that every possible read route is intercepted.

Workers never receive project memory and cannot write it. When a verified finding from
this workflow is durable and useful later, save it with the file-memory skill using
`source=verified:path:lines` after checking the original excerpt, not from the summary alone.

For cache status or cleanup use `scripts/teeplug.py cache status|prune|clear`.
See `docs/configuration.md` for provider models, output budgets and cache policy.

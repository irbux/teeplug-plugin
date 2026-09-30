---
name: code-writer
description: Generate a complete test, stub, configuration, or other predictable file from reference patterns using a configured worker. Use when most output follows existing examples; keep complex implementation and reasoning in the main agent.
---

Resolve the plugin root two levels above this SKILL.md. Use its script by absolute
path, with the shell working directory in the user's project.

For host matching, always pass `--host codex` when running in Codex and
`--host claude` when running in Claude Code. The examples below use Codex.
Respect an explicit user `--provider`, `--model`, or `--effort` choice; do not
change the main agent model. Do not retry a failed worker using another provider
without an explicit routing choice. Worker calls use the selected CLI subscription.

```bash
"<plugin-root>/scripts/code-write" --host codex --spec "Write tests for UserService following the reference test conventions" --reference tests/test_orders.py --context src/users.py --target tests/test_users.py
```

Provide at least one reference for conventions. Include actual source files with
`--context` when generating tests or code that depends on their APIs; a stylistic
example alone does not describe the target behavior. Use `--spec-file` for a long
specification. The script reads all content internally.

Prefer `--target`, which writes a complete file and returns only path, hash, line
count, and usage. `--stdout` explicitly returns the full generation and consumes
the main agent's context. Existing files require `--overwrite`; use it only when
the user's task calls for replacing that file and its current contents are accounted
for. The script detects a changed target and refuses partial provider responses.

After generation, run relevant syntax checks or project tests and inspect focused
excerpts and failures. Make necessary corrections. `review_required: true` is a
reminder to validate, not a request to add a separate permission step. Do not report
a file as correct merely because the worker completed or the script wrote it.

Each generation is independent. Reference prior output explicitly when extending
it. Large output may require a higher writer budget or smaller file-level tasks;
never concatenate partial generations blindly. No automatic Write hook forces this
workflow: choose it when reference-based generation actually fits the task.

Workers never receive project memory. A remembered convention can guide which references
and context files you pass and what the specification says; restate what matters in the
specification yourself rather than expecting the worker to know it.

For cache status or cleanup use `scripts/teeplug.py cache status|prune|clear`.
See `docs/configuration.md` for provider models, output budgets and cache policy.

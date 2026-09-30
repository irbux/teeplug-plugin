# Teeplug Local plugin

Delegate large-file understanding and predictable file generation to your local
Claude Code or Codex subscription CLI. No Portal account or API key is required.

The `bulk-reader` skill returns focused summaries. The `code-writer` skill writes
complete files directly to disk and returns metadata. `setup` configures per-project
routing and `doctor` checks configuration, authentication or live model access.
The optional `file-memory` skill keeps project-scoped preferences, corrections and
verified lessons in `MEMORY.md` and `OPERATOR.md` across sessions.

Default routing matches the main host: Claude uses Haiku; Codex uses gpt-5.6-luna.
Override the worker with `--provider`, `--model` and `--effort` without changing the
main model. Source is read inside the script, outside the main conversation.

From your project, invoke this plugin's scripts by absolute path:

```sh
python3 /path/to/teeplug/scripts/teeplug.py init
python3 /path/to/teeplug/scripts/teeplug.py doctor --host codex --auth
/path/to/teeplug/scripts/bulk-read --host codex --question "Where are retries bounded?" --paths src/client.py --json
/path/to/teeplug/scripts/code-write --host claude --spec "Generate tests following this reference" --reference tests/example.py --context src/service.py --target tests/service.py
python3 /path/to/teeplug/scripts/teeplug.py memory setup --root "$PWD"
python3 /path/to/teeplug/scripts/teeplug.py memory status --root "$PWD"
```

Memory is disabled until `memory setup` enables it for a project. It is local only:
no worker CLI, subscription login or model request is involved, and entries are
stored outside the installed plugin. See [memory](docs/memory.md).

Use `--host claude` in Claude Code and `--host codex` in Codex. A saved subscription
login is required on the execution host. Local desktop coding sessions can use the
same scripts; ordinary web chats cannot access the local CLI.

Install from the repository checkout with `codex plugin marketplace add /path/to/repo`
then `codex plugin add teeplug@teeplug-local`; Claude uses `claude plugin marketplace add
/path/to/repo` then `claude plugin install teeplug@teeplug-local`. Open a new coding session
and review hooks in the normal host trust UI when prompted.

Main-context reductions can exceed 90% for suitable large reads, but total worker
usage and subscription cost are not reduced by a fixed percentage. See the measured
[validation](docs/validation.md), [configuration](docs/configuration.md),
[memory](docs/memory.md) and [architecture](docs/architecture.md). Apache-2.0.

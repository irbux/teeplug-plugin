# Configuration

No configuration file is required inside a known host. `init` creates a reviewable
`.teeplug.json` in the project. It never overwrites existing settings or stores secrets.
Relative file paths resolve from the command's working directory; `--root` changes
only the allowed project boundary. Source files and targets must stay within that boundary.

```json
{
  "provider": "auto",
  "providers": {
    "claude": {"model": "haiku", "effort": "low"},
    "codex": {
      "model": "gpt-5.6-luna", "effort": "low",
      "writer": {"model": "gpt-5.6-terra", "effort": "medium"}
    }
  },
  "reader": {"max_output_tokens": 4000},
  "writer": {"max_output_tokens": 32000},
  "timeout_seconds": 180,
  "max_input_bytes": 512000,
  "min_lines": 350,
  "enabled": true,
  "cache": true,
  "cache_ttl_seconds": 604800,
  "cache_max_bytes": 33554432,
  "memory": {"enabled": false, "backend": "file",
             "limits": {"memory_chars": 12000, "operator_chars": 6000}}
}
```

The writer override above is optional; `init` uses the same small model for both workflows.
Choose your own model when reasoning or code complexity warrants it. There is no fallback
model or cross-provider retry. Main-agent model settings are never edited.

## Routing precedence

1. `--provider`, then `TEEPLUG_PROVIDER`, then `.teeplug.json` provider, then `auto`.
2. For `auto`: `--host`, `TEEPLUG_HOST`, host event identity (in hooks), or process
   markers (`CLAUDECODE`, then `CODEX_THREAD_ID`). An ambiguous terminal requires a host.
3. Model/effort: command-line override, workflow env (`TEEPLUG_READER_MODEL`,
   `TEEPLUG_WRITER_MODEL`, or corresponding `_EFFORT`), global `TEEPLUG_MODEL`/`TEEPLUG_EFFORT`,
   provider workflow config, provider config, built-in default.

Prefer provider-scoped config to global model env variables when using both hosts.
The skills always pass `--host`, so desktop environment differences do not change routing.

Other env options: `TEEPLUG_CLAUDE_BIN`, `TEEPLUG_CODEX_BIN` (one executable path, no shell
arguments), `TEEPLUG_TIMEOUT_SECONDS`, `TEEPLUG_MIN_LINES`, `TEEPLUG_ENABLED=0`, and
`TEEPLUG_READER_MAX_OUTPUT_TOKENS`/`TEEPLUG_WRITER_MAX_OUTPUT_TOKENS`. A provider's `command`
field can also specify its executable path. PATH and common macOS app locations are searched.

## Project memory

The `memory` section is separate from the top-level `enabled` switch, which controls
read routing only. Memory stays off while the section is absent or `enabled` is false.
`memory setup` configures enablement and the per-store character limits, preserving
unrelated settings and existing entries. The store is always
`<project-root>/.teeplug/memories/`, so there is no location or data-directory option.
All memory commands are local: no CLI, login or model request. `TEEPLUG_MEMORY_ENABLED=0`
and `TEEPLUG_MEMORY_LOCK_SECONDS` are the supported overrides. See [memory](memory.md)
for the schema, storage layout, locking, validation, session lifecycle and limitations.

## Subscription authentication

Use the official CLI's login flow. Teeplug asks the CLI for status, requires `claude.ai`
or ChatGPT authentication, and lets that CLI manage credentials and refreshes. It does
not open auth files or forward OAuth tokens itself. Inherited API keys, bearer overrides,
provider switches and endpoint overrides are removed from the worker environment.
Codex also enforces `forced_login_method=chatgpt` and the built-in OpenAI provider.
No API adapter or automatic API billing fallback exists.

`doctor` is offline. `doctor --auth` checks CLI status. `doctor --probe` checks the reader
model with a tiny synthetic request using the normal worker adapter. Probe success does
not verify a separate writer model override. A sandbox may deny keychain, CLI state
database writes or network access even when login status succeeds.

### Codex worker permission errors

The parent Codex sandbox applies to the Teeplug script and the CLI it launches.
The worker CLI needs writable local state (including its SQLite database) and a
connection to the model service. Its `--sandbox read-only` setting restricts the
worker's tools; it does not make the CLI's own state read-only.

When Teeplug reports `worker_access_denied`, or `worker_network_error` caused by
the host sandbox, the calling agent should retry the same command through the
normal host permission flow. For Codex's `exec_command`, that means
`sandbox_permissions="require_escalated"` and a justification for the worker call.
The script cannot request or grant this permission itself. Keep the worker's
read-only sandbox and managed policy intact. If approval is denied, or the approved
retry also fails, stop and report the observed blocker. A restart alone does not
grant permission, and reinstalling or changing models does not repair this denial.

When multiple Codex CLIs are installed, check the `executable` returned by `doctor`.
An older VS Code extension executable on PATH can reject a model that a newer
desktop CLI accepts. Set `providers.codex.command` to the verified executable in
the project's `.teeplug.json`; for example,
`/Applications/ChatGPT.app/Contents/Resources/codex-cli/bin/codex` for a ChatGPT app
installation containing that file. This selects the worker CLI without changing
either model or the main agent. Verify the path exists before configuring it.

## Budgets and failures

Prepared input, including line numbering and JSON framing, is limited to 512,000 UTF-8
bytes by default. Reader and writer output budgets guide the worker; Claude also gets
`CLAUDE_CODE_MAX_OUTPUT_TOKENS`. Codex exec has no equivalent hard output-token flag.
Both adapters reject visible text longer than six characters per configured token.
This is an output guard, not an exact tokenizer or billing cap. JSON/CLI overhead also counts.

Timeouts kill the worker process group. Failed exits, malformed JSON, incomplete results,
refusals and oversized output produce an error without returning code or changing a target.
Worker stdout/stderr are captured with limits and not printed as raw logs. A generic error
may require checking login, quota, CLI version, model availability or network access locally.
There is no automatic source dump into the main conversation after failure.

## Cache lifecycle

`.teeplug/cache/<sha256>.json` stores only successful reader text and numeric usage.
The request hash covers source payload (including content hashes), question, paths/order,
worker executable, provider, model, effort, output budget, plugin version and instructions.
Changed inputs miss immediately. Model aliases that change server-side expire with TTL;
use `--no-cache` when freshness matters. Cache is per project, not shared across checkouts.

Entries expire seven days after creation; reads do not extend their lifetime. A new cache
write prunes expired entries and then oldest entries to the 32 MiB limit. Idle projects
retain expired files until `cache prune`, `cache clear`, or a new cache write. No daemon
runs. Concurrent writes may temporarily exceed the cap until the next prune.

`cache status` reports count/bytes/expiry without printing summaries. `prune` removes
expired/excess entries; `clear` removes all hashed summaries only. Unrelated files are
untouched. Files use owner-only permissions. `.teeplug/` belongs in `.gitignore`; generated
summaries may contain snippets or sensitive findings. `--no-cache` bypasses read and write,
while `"cache": false` disables caching project-wide. No writer outputs are cached.

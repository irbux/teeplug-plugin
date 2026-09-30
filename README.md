# Teeplug Local

Keep bulk source reads and predictable code generation out of your main agent's
conversation. Teeplug sends that work to a separate **Claude Code or Codex CLI worker**
using its saved subscription login, then returns a focused answer or file metadata.
No Portal account, Portal CLI, API key, SDK dependency, or API proxy is required.

The GitHub repository is named `teeplug-plugin`; the installable plugin is `teeplug`.
The upstream catalog/action workflows have been removed.

## Host matching

| Main agent | Default worker | Login |
| --- | --- | --- |
| Codex CLI or local desktop coding task | Codex `gpt-5.6-luna`, low effort | `codex login` with ChatGPT |
| Claude Code CLI or local desktop Code session | Claude `haiku`, low effort | `claude auth login` with Claude.ai |

The main agent keeps its current model. Choose a different worker with `--model`,
`--effort`, or `--provider claude|codex`. Reader and writer defaults can differ.
Only the selected CLI is needed. Models must be available to your account.

Local desktop support means a coding session that can execute local tools. An
ordinary Claude/ChatGPT web chat or a cloud task cannot access your machine's CLI
login through this plugin. A remote execution host needs its own installation/login.

## Install this checkout

Requirements: Python 3.10+, macOS or Linux, and either supported CLI on the execution
host. Validated with Codex 0.153.4 and Claude Code 2.1.227. Older releases may lack
required isolation flags and should be upgraded. No packages need installing with pip.

Run from this repository:

```sh
codex plugin marketplace add "$PWD"
codex plugin add teeplug@teeplug-local

claude plugin marketplace add "$PWD"
claude plugin install teeplug@teeplug-local
```

Use the absolute Codex executable from your app bundle if `codex` is absent from PATH.
Open a new coding session after installation. Review/enable plugin hooks through the
host's normal trust UI when prompted. No global main-model settings need changing.
The two marketplace manifests point at the same `plugins/teeplug` source.

For a temporary Claude Code test, use `claude --plugin-dir ./plugins/teeplug`.
For future GitHub installs, commit and push your changes first; these commands install
this local checkout and do not publish it.

## Use it

You can ask the agent to use the `bulk-reader` or `code-writer` skill. The skills
explicitly supply the host identity, making routing reliable in CLI and desktop sessions.
Direct terminal use should also supply `--host`:

```sh
PLUGIN="/absolute/path/to/teeplug-plugin/plugins/teeplug"
cd /path/to/your/project
python3 "$PLUGIN/scripts/teeplug.py" init
python3 "$PLUGIN/scripts/teeplug.py" doctor --host codex --auth

"$PLUGIN/scripts/bulk-read" --host codex \
  --question "Where are retries bounded? Cite the relevant lines." \
  --paths src/client.py src/queue.py --json

"$PLUGIN/scripts/code-write" --host claude \
  --spec "Write tests for the user service following the reference conventions" \
  --reference tests/test_orders.py --context src/users.py \
  --target tests/test_users.py
```

The reader loads files internally and returns a summary with source hashes and usage.
The writer loads references internally and saves a complete result directly to disk.
Existing targets need `--overwrite`. Validate generated code with appropriate tests
and inspect original source excerpts before relying on a summary for an edit.

The read hook redirects common unbounded reads above 350 lines to the reader.
Small reads stay available. This is best-effort routing: scripts, complex shell syntax,
and other read tools can bypass detection, so the skill instructions also matter.

## Configure `.teeplug.json`

[`.teeplug.json`](.teeplug.json) configures **local subscription CLI workers**. Names such
as `provider`, `model`, and `max_output_tokens` describe worker routing and response
budgets; they do not configure a separate API account. Teeplug launches `claude -p` or
`codex exec` with the selected CLI's saved login. It rejects API-only authentication
and removes inherited API-key and endpoint overrides from the worker environment.
The CLI runs locally; the selected model still runs on the provider's service.

These settings apply to Teeplug workers. Your main agent keeps its own model and
reasoning settings. Each request selects one worker; listing both providers does
not launch both or switch providers automatically after a failure.

The checked-in configuration is:

```json
{
  "provider": "auto",
  "providers": {
    "claude": {"model": "haiku", "effort": "low"},
    "codex": {"model": "gpt-5.6-luna", "effort": "low"}
  },
  "timeout_seconds": 180,
  "max_input_bytes": 512000,
  "min_lines": 350,
  "enabled": true,
  "cache": true,
  "cache_ttl_seconds": 604800,
  "cache_max_bytes": 33554432,
  "reader": {"max_output_tokens": 2000},
  "writer": {"max_output_tokens": 8192}
}
```

### Every configuration key

Dotted names below identify nested JSON keys; keep the nested object structure shown above.

| Key | Current value | Meaning and effect |
| --- | --- | --- |
| `provider` | `"auto"` | Selects the worker CLI. `auto` matches the calling host; `claude` or `codex` pins that CLI regardless of the host. Supply `--host` when calling from a terminal where host detection is ambiguous. |
| `providers` | Object with `claude` and `codex` | Holds separate defaults for each CLI. Only the selected provider's settings are used. |
| `providers.claude` | Object | Defaults used when the selected worker is Claude Code. May also hold optional workflow overrides or a CLI executable path. |
| `providers.claude.model` | `"haiku"` | Claude worker model, passed as `claude --model haiku`. Applies to both reading and writing unless overridden. |
| `providers.claude.effort` | `"low"` | Claude worker reasoning effort, passed as `--effort low`. It is separate from the output budget. |
| `providers.codex` | Object | Defaults used when the selected worker is Codex. May also hold optional workflow overrides or a CLI executable path. |
| `providers.codex.model` | `"gpt-5.6-luna"` | Codex worker model, passed as `codex exec --model gpt-5.6-luna`. Applies to both reading and writing unless overridden. |
| `providers.codex.effort` | `"low"` | Codex worker reasoning effort, passed as `-c 'model_reasoning_effort="low"'`. It does not change the main Codex agent. |
| `timeout_seconds` | `180` | Maximum wait for the generation subprocess before Teeplug terminates its process group. The preceding login check has a separate timeout of up to 20 seconds, so this is not a total wall-clock limit for the entire Teeplug command. |
| `max_input_bytes` | `512000` | Maximum prepared input in UTF-8 bytes: the question/specification, source content, line numbering where used, and JSON framing must fit. This is a byte limit, not a token count; split oversized requests into smaller batches. |
| `min_lines` | `350` | Hook threshold for common unbounded file reads. Files exceeding 350 lines are candidates for redirection to `bulk-reader`; explicit small excerpts remain available. It does not limit which files you may pass directly to the reader. |
| `enabled` | `true` | Enables the automatic read-routing hook. `false` stops hook redirection; it does **not** disable manually invoked `bulk-read` or `code-write` commands. |
| `cache` | `true` | Allows successful reader answers to be reused from `.teeplug/cache/`. An exact hit skips the worker call. `false` disables cache reads and writes; it does not delete existing entries. Writers are never cached. |
| `cache_ttl_seconds` | `604800` | Reader-cache lifetime: seven days from entry creation. Hits do not renew the lifetime. Expired entries are ignored and removed during a later prune or cache write. |
| `cache_max_bytes` | `33554432` | Reader-cache size target: 32 MiB. After a new cache write, cleanup removes expired entries and then the oldest entries until within the limit. It is not a background disk quota. |
| `reader` | Object | Settings for the `bulk-read` workflow. Its output is a focused summary returned to the main agent. |
| `reader.max_output_tokens` | `2000` | Requested worker output budget for a summary. Both providers receive this target in the worker instructions; see the provider-specific enforcement below. |
| `writer` | Object | Settings for the `code-write` workflow. With `--target`, generated content goes to disk and the main agent receives file metadata. |
| `writer.max_output_tokens` | `8192` | Requested worker output budget for the complete generated file, not the small metadata response returned to the main agent. Incomplete or oversized output is rejected before writing. |

Numeric limits must be positive integers; switches must be JSON `true` or `false`.
Teeplug accepts effort values `low`, `medium`, `high`, `xhigh`, and `max`, but the
selected CLI/model must support your choice. Models must be available to your account.
Unknown keys are rejected: do not add an API key or `base_url` to this file.

### How Claude and Codex receive these settings

| Step | Claude Code worker | Codex worker |
| --- | --- | --- |
| Verify saved login | `claude --safe-mode auth status --json`; requires `claude.ai` authentication | `codex login status`; requires a ChatGPT login |
| Start generation | `claude -p --model haiku --effort low` | `codex exec --model gpt-5.6-luna -c 'model_reasoning_effort="low"'` |
| Supply source | Teeplug reads the files internally and sends the prepared JSON payload through stdin | Same; the trailing `-` in the actual command selects stdin |
| Apply output budget | Instructions plus `CLAUDE_CODE_MAX_OUTPUT_TOKENS=2000` for reading or `8192` for writing | Instructions request the same budget; Teeplug does not pass a hard output-token flag to `codex exec` |
| Accept output | Requires a successful structured response with `text` and `complete: true` | Requires a completed turn and a valid response matching the same output schema |

The generation commands above show the model/effort mapping, not the complete worker
command. The adapter also supplies isolation and structured-output options. Claude uses
safe mode, disabled built-in tools, an empty MCP configuration and no saved session.
Codex uses an ephemeral session, ignores user configuration, retains a read-only sandbox,
and explicitly requires ChatGPT authentication. See [the adapter](plugins/teeplug/scripts/teepluglib/providers.py)
and [architecture](plugins/teeplug/docs/architecture.md) for the full invocation.

**`max_output_tokens` is not a billing or subscription-allowance cap.** For Codex it is
a soft target in the prompt. Claude additionally receives its CLI output-budget
environment variable, whose enforcement belongs to that CLI/model. Teeplug also rejects
returned text longer than six characters per configured token: 12,000 characters for
the default reader or 49,152 for the writer. This character check is not exact token
counting and happens after generation. Worker input, reasoning, output and CLI overhead
can still consume subscription allowance.

### Invoke or override a worker

From your project, use the plugin path established in the usage example:

```sh
# Same configuration and question; choose the calling host explicitly.
"$PLUGIN/scripts/bulk-read" --host claude \
  --question "Where are retries bounded? Cite the relevant lines." \
  --paths src/client.py --json

"$PLUGIN/scripts/bulk-read" --host codex \
  --question "Where are retries bounded? Cite the relevant lines." \
  --paths src/client.py --json

# Use a different Codex worker for one generation, even from a Claude host.
"$PLUGIN/scripts/code-write" --host claude --provider codex \
  --model gpt-5.6-terra --effort medium --max-output-tokens 12000 \
  --spec "Write tests for the user service following the reference conventions" \
  --reference tests/test_orders.py --context src/users.py \
  --target tests/test_users.py
```

`--host` tells `auto` which agent is calling; an explicit `--provider` selects the
worker instead. Command-line model/effort overrides take precedence over environment
variables and file defaults. You can also persist different reader/writer models under
`providers.claude.reader`, `providers.claude.writer`, `providers.codex.reader`, or
`providers.codex.writer`, each with `model` and/or `effort`. These optional overrides
belong inside the provider object; top-level `reader` and `writer` hold output budgets.
See [configuration precedence](plugins/teeplug/docs/configuration.md#routing-precedence)
for the complete order and executable-path options.

Teeplug reads settings on each invocation, so edits to `.teeplug.json` need no plugin
reinstall. It normally searches upward from the working directory for the nearest
`.teeplug.json` or Git root; `--root` selects a project boundary and `--config` selects an
explicit configuration file for manual commands. Relative source/target paths still
resolve from the command's working directory. Hooks use the discovered project config;
a manual `--config` override does not change hook settings. The repository's file is
not a global setting for every other project: run `init` in each project where you want
an editable configuration. Without a file, Teeplug uses its built-in defaults.

## Token savings and cache

A 20,000-token file reduced to a 1,000-token answer can cut main-conversation input
by about 95% before metadata and follow-up reads. The worker still processes the file
and uses your subscription allowance. CLI prompt overhead, worker reasoning, and
verification reads count too. **90% is not a guaranteed reduction in total tokens,
price, or subscription usage.** Smaller worker models do not imply a fixed quota ratio.

Reader cache keys include source content, question, paths/order, worker/model/effort,
output budget, plugin version and prompt. Hits skip the worker. Defaults: seven-day
expiry and a 32 MiB cap, with cleanup on new cache writes. Cache stores summaries and
usage under `.teeplug/cache/`; it does not store the source payload. Summaries can still
contain source excerpts. Writers are never cached.

```sh
python3 "$PLUGIN/scripts/teeplug.py" cache status
python3 "$PLUGIN/scripts/teeplug.py" cache prune
python3 "$PLUGIN/scripts/teeplug.py" cache clear
```

Use `--no-cache` to bypass both cache reads and writes. See
[configuration](plugins/teeplug/docs/configuration.md),
[architecture](plugins/teeplug/docs/architecture.md), and
[validation](plugins/teeplug/docs/validation.md) for details.

## Develop

See [TESTING.md](TESTING.md) for the original measurement, its limits, and commands
to reproduce live reader/writer checks with saved byte counts and CLI token usage.

```sh
python3 -m unittest discover -s plugins/teeplug/tests -v
```

Runtime code is under `plugins/teeplug/scripts/teepluglib`; the shared skills and hook
live alongside it.
Apache-2.0; see [LICENSE](LICENSE).

# File memory (Phase 1)

Project-scoped memory keeps preferences, corrections and verified lessons across
sessions in two Markdown files. It is disabled until setup enables it, is local only,
and needs no worker CLI, subscription login or model request. App-based memory is not
implemented, and Phase 2 consolidation and preference inference are deliberately absent.

## Configuration

`.teeplug.json` gains one section, separate from the top-level `enabled` switch that
controls read routing. An absent section means memory is off; existing entries survive
enabling, disabling and re-enabling.

```json
{
  "memory": {
    "enabled": true,
    "backend": "file",
    "location": "plugin-data",
    "data_dir": "/Users/you/.claude/plugins/data/teeplug-teeplug-local",
    "limits": {"memory_chars": 2600, "operator_chars": 1720}
  }
}
```

`backend` accepts `file`. `location` is `plugin-data` or `project`. `data_dir` is the
persistent directory recorded by setup for `plugin-data` storage. Limits are 1–20,000
characters; the combined default entry budget is 4,320 characters. `TEEPLUG_MEMORY_ENABLED=0`
disables memory for one run, and `TEEPLUG_MEMORY_DATA_DIR` overrides the data directory.
Validation is local: an invalid section is an actionable error from any command.

Lowering a limit below current usage never truncates or deletes anything. The store
reports `over_limit`, additions and growing replacements are refused with `memory_full`,
and removals, shrinking replacements, clear and delete still work.

| File | Target | Purpose | Default limit |
| --- | --- | --- | --- |
| `MEMORY.md` | `memory` | Environment facts, project lessons, conventions, references | 2,600 |
| `OPERATOR.md` | `operator` | The operator's explicit preferences and relevant profile facts | 1,720 |

Both targets are scoped to the selected project. The operator is the person using the
project; `OPERATOR.md` is not a global profile and never propagates preferences to
another project.

## Entry format

```markdown
<!-- teeplug-memory v1 target=memory -->
# Teeplug MEMORY

- m-7f3a1c #=> Staging deployments must finish the CloudFormation update before the Lambda.
  <!-- teeplug created=2026-09-15T10:04:11Z updated=2026-09-15T10:04:11Z source=operator-correction -->
```

An entry is one block: a bullet with a stable id (`m-` or `o-` plus six hex digits),
the ` #=> ` delimiter, the first line of text, any further lines indented by exactly two
spaces, then a metadata comment. Escapes inside stored lines are `\\` for a backslash,
`\#` where the text contains `#=>`, and `\<` where a line itself begins `<!--`. Any other
backslash escape is a parse error rather than a silent repair. Entries may not contain
blank lines, may not exceed 12 lines or 1,000 characters, and a store holds at most 200.

Provenance is one token of up to 120 characters from `[A-Za-z0-9_.:/#@+-]`, for example
`operator-statement`, `operator-correction`, `operator-request` or `verified:src/deploy.py:20-40`.
References are stored, never transcripts, source payloads or worker logs.

**Counting rule.** Entry text is normalized to NFC with LF line endings, each line
right-trimmed and the whole text stripped, then counted in Unicode scalar values
(`len()`). Usage and limits cover entry text only. Ids, timestamps, provenance, file
headings and the rendered block's own framing are overhead, bounded separately by the
per-file byte cap (256 KiB) and the rendered-output cap (6,000 characters).

## Storage and project identity

The default store is `<plugin-data>/memories/<project-id>/`, outside the installed and
versioned plugin code, so a plugin update never relocates it. The project-local option
is `<project-root>/.teeplug/memories/`, separate from `.teeplug/cache/` and covered by the
repository's existing `.teeplug/` ignore rule, which keeps personal memory out of version
control and distributions by default.

The data directory resolves in this order: `TEEPLUG_MEMORY_DATA_DIR`, the recorded
`memory.data_dir`, then the host hook variables `CLAUDE_PLUGIN_DATA`, `PLUGIN_DATA` and
`CODEX_PLUGIN_DATA`, then an existing `~/.claude/plugins/data/teeplug-teeplug-local` or
`~/.codex/plugins/data/teeplug-teeplug-local`. Setup records the directory it resolved, so
ordinary CLI commands without hook environment variables reach the same store as hooks.
When nothing resolves, the command returns a `data_dir_unresolved` error naming
`--data-dir` and `--location project`; it never writes into the plugin directory.

The project id is the canonical directory name reduced to a slug plus the first twelve
hex digits of the SHA-256 of the canonical absolute path. It does not change when memory
is re-enabled or a session starts, so reopening a project finds its existing memory, and
distinct projects stay isolated. `project.json` records the canonical path, the creation
time and any previous paths; it holds identity only and is recreated when missing.

Symlinked checkouts resolve to their real path and share that store. A nested directory
with its own `.git` or `.teeplug.json` is a separate project, as is each worktree. A moved
repository gets a new project id and therefore an empty plugin-data store; `memory
migrate --from <old-store>` copies entries in, validating each one, skipping duplicates
and anything that would exceed a limit, and never modifying or deleting the source.
Changing `location` while entries exist is refused until you pass `--migrate` or `--keep`.
Claude and Codex can share one store by selecting the same project-local location; the
host default stores stay separate and nothing synchronizes across hosts.

Store directories are created with owner-only permissions, files are written `0600`, and
a symlinked memory file, lock or snapshot is refused rather than followed. Host sandbox
and managed policy are unchanged; no permission is widened to make a location writable.
Plugin-data retention on uninstall is the host's behaviour — Claude Code deletes the data
directory when the last scope is uninstalled unless `--keep-data` is used — so uninstall
is not promised to preserve memory. Project-local storage is under your own control.

## Concurrency

Every read-validate-modify-write, clear, delete and snapshot write holds a file lock on
`<store>/.lock` for its whole duration, with a bounded wait (5 seconds; override with
`TEEPLUG_MEMORY_LOCK_SECONDS`) that returns a `locked` result instead of blocking forever.
Reads take a shared lock and return a consistent snapshot of both files.

The revision is the SHA-256 of the two stored files, truncated to sixteen characters, so
it is derived from content rather than tracked separately: an external editor invalidates
it exactly like a write from another session. Reads and writes return it. A mutation
derived from an earlier read should pass `expect_revision`; a stale revision is rejected
with no change, and the caller is expected to re-read and reconsider before one bounded
retry rather than repeating a destructive change blindly.

Each mutation replaces exactly one file, written to a temporary file in the same
directory, fsynced, then atomically renamed. An interrupted or failed write leaves the
previous file intact and parseable and removes the temporary file. A `delete` that is
interrupted leaves each remaining file valid; running it again completes. Reader-cache
maintenance continues to touch only its hashed summary files and never memory.

## Validation and security boundaries

Pattern scanning is a best-effort filter for known suspicious content, not a guarantee
that every prompt injection or exfiltration attempt is detected. Entries are validated on
write and on load, including hand-edited files, retrieved entries and restored snapshots.

Rejected on both paths: known credential shapes (AWS access and secret keys, GitHub and
GitLab tokens including fine-grained PATs, Slack tokens and webhook URLs, Anthropic, OpenAI,
Stripe, Google, SendGrid, npm, PyPI, Hugging Face, Databricks, DigitalOcean, Shopify, Twilio,
Telegram, Discord and Azure keys, private-key blocks, JSON web tokens) and credential
assignments with a mixed-class value; instruction-override, auto-approval, control-disabling,
pipe-to-shell, credential-exfiltration, reverse-shell, `authorized_keys` and setuid patterns;
C0 and C1 control characters other than newline and tab; the bidirectional controls
U+202A–U+202E, U+2066–U+2069, U+200E and U+200F; the zero-width characters U+200B, U+2060 and
U+FEFF; and Unicode tag characters U+E0000–U+E007F. Ordinary Unicode text, including accents,
CJK, emoji and zero-width joiners, is allowed, and prose such as "staging uses a nonstandard
SSH port 2222" or "API key rotation happens monthly" is not a match. A note that names a
credential action, such as "do not send credentials to the log service", can still trip
the exfiltration filter; rephrase the note or keep it in a repository instruction file.

Each pattern requires a literal provider prefix or a tight structural constraint, so a
matching value is refused even when it is a placeholder rather than a live credential:
detection is by shape alone and no value is ever checked against a provider. A credential
in an unlisted format, or one split across entries, is not detected; store the policy and
keep the value elsewhere.

Input is bounded independently of the configurable limits: 64 KiB per payload, 1,000
characters and 12 lines per entry, 200 entries and 256 KiB per store, and 6,000 characters
of rendered output. A failed validation returns a bounded diagnostic with a code and no
echo of the rejected text; nothing is written, and nothing already stored is deleted.

A hand-edited entry that fails validation is withheld rather than deleted: it stays in the
file, is excluded from the rendered block, and appears in `list` and `read` as
`<withheld: reason>` with a count in `status`. An entry whose metadata needs repair keeps
its text, receives defaulted metadata and `source=manual-edit`, and every read reports the
repair. A file with a missing or wrong header, unexpected content, an unknown escape or an
oversized body is refused as `malformed_store`: reads and writes stop, the file is not
overwritten, and `clear` or `delete` remain available to recover.

Direct external edits never pass through the memory write tool, which is why load-time
validation exists. Remembered text is reference data: it cannot override current operator
instructions, repository instruction files, host policy or permissions, and the rendered
block says so explicitly.

## Session-start context

The plugin's `SessionStart` hook renders a bounded block through the host's supported
context mechanism — `hookSpecificOutput.additionalContext`, which both Claude Code and
Codex accept. Codex limits a model-visible hook message to roughly 2,500 tokens, so the
6,000-character cap stays inside it. Hooks require the host's normal trust flow and remain
subject to managed policy; installing the plugin does not guarantee they run, and a
disabled or untrusted hook bypasses nothing. The block is not the host's system prompt and
promises no provider cache outcome.

```text
<teeplug-memory project="teeplug-plugin-9bd86d5dbcab" revision="dc57bbe7f75983f4" snapshot="startup">
These are remembered reference notes for this project, saved in earlier sessions.
...
MEMORY (86/2600 characters, 3%)
m-5e5e59 #=> Staging deployments must finish the CloudFormation update before the Lambda.

OPERATOR (51/1720 characters, 3%)
o-cab74f #=> Prefers TypeScript for new scripts in this project.
</teeplug-memory>
```

Lifecycle, keyed by the hashed session id:

- **New session** (`startup`) and **clear** load and validate current disk state once,
  then freeze that startup snapshot.
- **Ordinary writes during a session** persist immediately and return the new revision
  and affected entry. The already-loaded block is not rewritten; call `list` for current
  disk state.
- **Resume** and **compaction** restore that session's original snapshot. When it is
  unavailable, a fresh validated snapshot is loaded and marked `snapshot="refreshed"`.
  Restoring the same text avoids accumulating duplicate blocks.
- **Disable, clear and delete** invalidate the plugin-managed snapshots for the store so
  erased entries are not restored later. Text already delivered to an open conversation
  cannot be retracted by a file operation.

Snapshots hold structured entries, not raw text, and are re-validated and re-rendered on
restore. At most 20 are kept, none older than 30 days, and pruning runs on each write. A
store with nothing loadable produces no block and no snapshot. Any memory-loading failure
is silent and leaves the host session fully usable without the rejected memory. Frozen
startup context is intended to reduce unnecessary context changes; it does not guarantee
prefix-cache reuse, lower latency or token savings.

## Commands

All interfaces call the same implementation. Chat commands map to the CLI as follows; the
nested slash spellings are Claude Code's, while Codex uses the `file-memory` skill or the
CLI directly.

| Chat command | CLI action |
| --- | --- |
| `/teeplug:file-memory:setup` | `memory setup` |
| `/teeplug:file-memory:disable` | `memory disable` |
| `/teeplug:file-memory:status` | `memory status` |
| `/teeplug:file-memory:list` | `memory list [--target T] [--limit N] [--offset N]` |
| `/teeplug:file-memory:read` | `memory read --target T --id ID` |
| `/teeplug:file-memory:clear:operator` | `memory clear --target operator --confirm` |
| `/teeplug:file-memory:clear:memory` | `memory clear --target memory --confirm` |
| `/teeplug:file-memory:delete` | `memory delete --confirm` |
| `/teeplug:file-memory:add:operator` | `memory add --target operator` |
| `/teeplug:file-memory:add:memory` | `memory add --target memory` |
| `/teeplug:file-memory:replace:operator` | `memory replace --target operator` |
| `/teeplug:file-memory:replace:memory` | `memory replace --target memory` |
| `/teeplug:file-memory:remove:operator` | `memory remove --target operator` |
| `/teeplug:file-memory:remove:memory` | `memory remove --target memory` |

`memory migrate --from DIR` performs an explicit validated import, and `memory context`
renders the session block for checking a setup. `add`, `replace` and `remove` read one
JSON object from stdin — `text`, `old_text`, `source`, `id`, `expect_revision`, `target` —
or from `--payload-file`, so remembered text is never interpolated into a shell command.

```bash
python3 scripts/teeplug.py memory add --root "$PWD" --target memory <<'JSON'
{"text": "fixtures/customer-import-example.csv is the canonical customer import example.",
 "source": "verified:fixtures/customer-import-example.csv"}
JSON
```

Prefer stable ids. `old_text` must match exactly one complete entry; empty, missing or
ambiguous matches are refused without modifying the store. An exact duplicate addition is
a successful no-op reporting "no duplicate added", and a replacement whose text duplicates
another entry is refused. Duplicate detection is exact after normalization; semantic
duplicates are Phase 2. Results are structured and bounded, carrying affected ids,
revisions and usage. `clear` and `delete` require `--confirm`, are explicit management
actions rather than automatic capacity handling, and disable memory so later automatic
writes cannot silently recreate it; re-enabling requires setup.

## Capacity

An addition or replacement that would exceed the configured limit is refused with nothing
partially written:

```json
{"success": false, "code": "memory_full",
 "error": "This entry would exceed the memory limit. List current entries to review capacity; existing entries were preserved.",
 "target": "memory", "usage": {"chars": 2550, "limit": 2600, "projected_chars": 2800},
 "entry_chars": 250, "revision": "dc57bbe7f75983f4", "next_action": "list"}
```

From 80% of a limit onward, reads and writes report `near_capacity`. Phase 1 never
consolidates unrelated entries or evicts old notes to make room. The agent may replace an
entry for a direct verified correction, or perform an operator-requested merge or removal;
otherwise existing memory is left intact and the candidate is reported as unsaved.

## Saving behaviour and limitations

Once enabled, the main agent saves useful lasting information on its own judgment, without
a separate request for every entry. That selection is judgment, not a guarantee: Python
enforces structure, size, exact-duplicate checks, validation and safe file operations, and
cannot decide whether a preference is permanent or whether two entries mean the same thing.
Phase 1 does not infer a lasting preference from a single request and does not build a
behavioural profile. The `file-memory` skill carries the save and skip guidance.

Teeplug workers stay isolated. A bulk-reader summary may suggest a lesson, but the main agent
verifies the evidence before saving it; workers have no memory-file access and no write
tools, and every write goes through this validated Python layer.

## Working with bulk-reader and code-writer

Memory and the worker workflows share one plugin, one `.teeplug.json` and one project root,
and nothing else. They are independent at runtime.

Workers never see memory. A reader payload is exactly `{question, documents}` and a writer
payload is exactly `{specification, target, references, context}`; no entry, rendered block
or file path from a store is added to either. Workers get no memory-file access and no
write tools, `TEEPLUG_WORKER` makes every memory command and the session hook refuse inside a
worker process, and the reader cache key does not include memory, so enabling memory
neither invalidates cached summaries nor changes worker routing.

The switches are separate too. Top-level `enabled` gates read routing only; `memory.enabled`
gates memory only. Memory resolves no provider, host, executable or login, so `memory
status` works in a terminal where `doctor` would still need `--host`. `.teeplug/cache/` holds
hashed summaries and `.teeplug/memories/` holds entries; cache maintenance touches only its
own `<sha256>.json` files, and clearing memory never touches the cache.

They compose through the main agent, in this order:

1. `bulk-reader` answers a broad question about large files and returns citations. Those
   citations are leads, not evidence.
2. The main agent verifies the cited excerpt with a small direct read, as the reader skill
   already requires.
3. If the verified finding is durable and useful beyond this session, the agent saves it
   with `source=verified:path/to/file.py:20-40`, so the claim stays traceable.
4. A later session receives that entry at startup and can often skip re-running the same
   bulk read, or ask a narrower question because it already knows where to look.
5. For `code-writer`, a remembered convention helps the agent choose `--reference` and
   `--context` files and write a better `--spec`. Memory is not injected into the
   specification; the agent includes what is relevant, in its own words.

The benefit is fewer repeated explanations and re-discoveries, which is a reduction in
returned context, measured separately from worker usage and memory-management overhead.

## Evaluation before Phase 2

Phase 1 is independently usable and complete on its own. Semantic consolidation,
capacity management beyond refusal, and preference inference from repeated evidence are
Phase 2 and start only after Phase 1 has been used on real recurring project work.

Keep the evaluation project-local and collect no raw conversations, source payloads or
credentials. Record: memories usefully recalled and repeated explanations or corrections
avoided; repeated file reads or worker calls avoided, separating observation from
estimate; incorrect, stale, redundant or overly broad entries and the manual corrections
they needed; capacity pressure and useful entries that could not be saved; explicit
preferences missed against one-off requests correctly skipped; startup block size and
loading time, extra management calls and the usage counters available; concurrency
conflicts and their recovery; and validation false positives or missed suspicious content.

Then write a short report of observed benefits, failures and limitations, and justify a
Phase 2 scope from it. If usage does not show a need for inference or consolidation, stay
on Phase 1 and gather more evidence. Measure returned context separately from worker usage
and memory-management overhead, and do not claim a fixed percentage reduction in total
tokens, subscription allowance or cost.

Verified on Claude Code and Codex hook contracts as documented at
[Claude hooks](https://code.claude.com/docs/en/hooks),
[Claude persistent plugin data](https://code.claude.com/docs/en/plugins-reference#persistent-data-directory)
and [Codex hooks](https://learn.chatgpt.com/docs/hooks). The offline test suite exercises
the hook JSON protocol for both host field spellings. Behaviour on a specific installed
host version, including whether the hook is trusted and the exact nested slash-command
spelling each host accepts, still needs checking in that host; nothing here has been run
against a live installed Codex plugin build.

---
name: setup
description: Configure Teeplug subscription workers for local Claude Code or Codex, with host matching and independent reader or writer models.
---

Resolve the plugin root two levels above this SKILL.md. Work in the user's project.
Use `python3 "<plugin-root>/scripts/teeplug.py" init` if `.teeplug.json` is absent.
Preserve existing configuration. Defaults match the host: Claude uses `haiku`;
Codex uses `gpt-5.6-luna`. These are worker models; the main agent stays as configured.
Always pass `--host claude` in Claude Code or `--host codex` in Codex.

Run `doctor --host HOST` to check configuration and the executable without network.
Use `doctor --host HOST --auth` to verify the existing CLI subscription login.
Use `doctor --host HOST --probe` when a live check is authorized; it sends no source.
If authentication is missing, tell the user to run `claude auth login` or `codex login`
in their terminal. Do not read auth files, request tokens, or configure an API key.
Login status can pass while the worker's SQLite state or network access is denied.
For `worker_access_denied`, or a network failure caused by the host sandbox, retry
the same authorized check through the host's normal permission flow. In Codex
`exec_command`, use `sandbox_permissions="require_escalated"` with a short justification.
Keep the worker read-only sandbox and managed policy intact. If approval is denied
or the approved retry fails, report that blocker instead of changing models or login.

Project memory is configured separately and stays disabled until asked for. Use
`memory setup` from the file-memory skill, not this workflow, and never enable memory
for a project the user did not ask about. Memory needs no CLI, login or model request,
and its store lives at `<project>/.teeplug/memories/`, so `.teeplug/` must stay ignored.

Read `docs/configuration.md` for CLI paths, model overrides, cache and output limits.
Add `.teeplug/` to the project's ignore file if needed. `.teeplug.json` contains no credentials
and may be committed. Hooks need host trust; never bypass trust or approval policies.
Local desktop coding sessions must have the CLI and its login on the execution host.
Ordinary web chats cannot run this plugin's local scripts.

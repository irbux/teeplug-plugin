---
name: doctor
description: Diagnose Teeplug configuration, local CLI availability, subscription authentication, cache, and worker model access.
---

Resolve the plugin root two levels above this SKILL.md. Run its `scripts/teeplug.py`
with `python3`, using `--host codex` in Codex or `--host claude` in Claude Code.

Start with `doctor --host HOST`: offline, read-only, no model request. Report separately
whether configuration, login, and actual model access have been verified.
`doctor --host HOST --auth` asks the CLI for login status without a generation.
Only use `doctor --host HOST --probe` when live checks are authorized; it consumes a
small amount of the selected subscription allowance. Never print tokens or raw logs.

Login status can pass while generation fails: the Codex worker also needs writable
CLI state (including SQLite) and network access. For `worker_access_denied`, or a
network failure caused by the host sandbox, retry the same authorized check through
the host's normal permission flow. In Codex `exec_command`, set
`sandbox_permissions="require_escalated"` with a short justification. Keep the worker
read-only sandbox and managed policy intact. If approval is denied or the approved
retry fails, report that blocker. A passing probe verifies only the reader model.

Use `memory status` for project memory: it is read-only, local, and reports enablement,
store location, revision, capacity and any store problem without loading entries.
Never clear, delete or disable memory as an automatic diagnosis.

Inspect `cache status` without loading summaries into context. Cache cleanup is a
separate explicit action (`cache prune` or `cache clear`). Do not change provider or
model, disable hooks, overwrite settings, or clear caches as an automatic diagnosis.
Explain the observed error and use `docs/configuration.md` for the targeted repair.

# Plan: project-local memory store and new default budgets

Status: approved, not implemented. Confirmed 2026-09-30.

## Requirements

1. Memory files and the `.teeplug/` directory always live in the project repository, never in a plugin or host data directory.
2. `.teeplug.json` always carries a `memory` section, defaulting to `enabled: false`.
3. Default worker budgets: `reader.max_output_tokens: 4000`, `writer.max_output_tokens: 32000`.
4. `memory setup` defaults: `memory_chars: 12000`, `operator_chars: 6000`.
5. No `memory migrate` command — memory never moves, because it always lives in the project.

## Current state

| Requirement | Today | Location |
| --- | --- | --- |
| Memory in project | Defaults to `plugin-data` → `<host>/plugins/data/teeplug-teeplug-local/memories/<project-id>/` | `memory.py:82`, `:90-105`, `plugin_data_dir()` `:55-68` |
| `.teeplug/` in project | Already true: cache is `<root>/.teeplug/cache`, project memory is `<root>/.teeplug/memories` | `cache.py:10`, `memory.py:92` |
| `memory` key present | `init` writes `"memory": {"enabled": false}`; the repo's own `.teeplug.json` has no memory key | `cli.py:138`, `.teeplug.json` |
| Budgets | reader 2000 / writer 8192, in both the fallback and `init` | `config.py:154-156`, `cli.py:137` |
| Memory limits | `DEFAULT_LIMITS = {'memory': 2600, 'operator': 1720}` | `memory.py:30` |
| No migrate | `migrate` action, `--migrate`/`--keep`, `import_entries`, `location_change_requires_migration` | `memcli.py:223-224`, `:252-253`, `:289-291`, `:138-142`; `memops.py:169-210` |

`.teeplug/` needs no change — it is already project-rooted and already gitignored (`.gitignore:2`). Only memory's default location moves.

## Decisions

| Decision | Choice | Rationale |
| --- | --- | --- |
| Memory in version control | No — stays under the gitignored `.teeplug/` | Private per clone, same treatment as `.teeplug/cache`; keeps operator preferences out of a public repo |
| `plugin-data` location | Removed entirely, not just re-defaulted | One storage path; makes `migrate` and the location-change refusal dead code, which is consistent with dropping `migrate` |
| Reader budget | 4000, not 12000 | Reader output *is* the summary returned to the main agent, and `cli.py:62` allows `max_output_tokens × 6` characters. 12000 would permit ~72,000 chars of main-context injection; 4000 permits ~24,000 |
| Writer budget | 32000 | Writer output goes to disk — `cli.py:119-122` emits only metadata when `--target` is given — so this costs the main agent nothing |
| `MAX_RENDER_CHARS` | 24000, not 20000 | 20000 does not guarantee a full store renders; see the arithmetic below |
| `render()` drop order | Oldest first | `memsession.py:54` pops the newest entry first. Recency is the only relevance signal available (no priority field), so the newest should survive |
| Live-validation numbers | Stamp provenance, do not re-measure | Re-measuring costs subscription allowance; recording the budget each figure was measured at is honest and free |

### Why 24000

Worst case is both stores full at `MAX_ENTRIES = 200` (`memguard.py:16`):

- entry text: 12000 + 6000 = 18,000
- per-entry framing `- m-xxxxxx #=> ` plus newline ≈ 14 × 200 = 2,800
- label, `<teeplug-memory …>` open/close and two usage headers ≈ 650

Total ≈ 21,450, which exceeds a 20,000 ceiling. 24,000 leaves headroom so the configured budget always renders in full.

## Phase 1 — Project-only storage

`plugins/teeplug/scripts/teepluglib/memory.py`

- Delete `plugin_data_dir()` (`:55-68`), including the `data_dir_unresolved` refusal.
- `MemoryConfig.__init__`: drop `self.location` (`:82`) and `self.data_dir` (`:83`).
- `store_dir()` (`:90-105`): always resolve `<root>/.teeplug/memories`. Keep both guards — the installed-plugin refusal (`:99-102`) and the "must stay inside the project root" check (`:103-104`), which now applies unconditionally.
- `DEFAULT_LIMITS` (`:30`) → `{'memory': 12000, 'operator': 6000}`.

`plugins/teeplug/scripts/teepluglib/config.py`

- `check_memory` (`:94-113`): allowed set becomes `{'enabled', 'backend', 'limits'}`; delete the `location` (`:102-103`) and `data_dir` (`:104-106`) checks; update the error text.
- Keep the 20,000 per-limit ceiling (`:111`) — 12000 and 6000 both fit.
- Remove `TEEPLUG_MEMORY_DATA_DIR` support everywhere.

`plugins/teeplug/scripts/teepluglib/memops.py`

- `status()` (`:213-245`): remove `'location': cfg.location` from both return paths (`:219`, `:224`). Keep the `store_problem` branch — unsafe paths and symlinked stores still need an actionable report.

Breaking change: an existing config containing `location` or `data_dir` now fails with "memory supports enabled, backend and limits". The error is actionable, and memory shipped disabled by default, so the affected population is small. Document the manual recovery path (Phase 3).

## Phase 2 — Remove migrate

`plugins/teeplug/scripts/teepluglib/memcli.py`

- Delete `run_migrate` (`:223-224`) and its parser (`:289-291`).
- `run_setup` (`:108-153`): delete the `--location`/`--data-dir` handling (`:112-122`), the plugin-data resolution block (`:131-136`), the `location_change_requires_migration` refusal (`:138-142`) and the post-save import (`:151-152`). What remains: build the section, save, create identity, emit.
- Setup must now write limits explicitly rather than only when a flag is passed, so requirement 4 holds on a bare `memory setup`:
  ```python
  limits = {'memory_chars': DEFAULT_LIMITS['memory'],
            'operator_chars': DEFAULT_LIMITS['operator'],
            **cfg.data.get('memory', {}).get('limits', {})}
  ```
  then apply `--memory-chars` / `--operator-chars` over it. Note the key names differ: `DEFAULT_LIMITS` uses `memory`/`operator`, the config uses `memory_chars`/`operator_chars`.
- Setup parser (`:247-254`): drop `--location`, `--data-dir`, `--migrate`, `--keep`; keep the two limit flags with help text reflecting the new defaults.
- Drop `import_entries` from the imports (`:14`).

`plugins/teeplug/scripts/teepluglib/memops.py`

- Delete `import_entries` (`:169-210`).
- Prune the imports that become unused: `os`, `Path`, `parse`, `MAX_STORE_BYTES`. Verify `new_id`, `MAX_ENTRIES`, `contextlib`, `STORE_VERSION` are still used before removing anything.

## Phase 3 — Defaults and budgets

- `memguard.py:20` → `MAX_RENDER_CHARS = 24000`. Side effect: the snapshot size bound at `memsession.py:107` (`MAX_RENDER_CHARS * 8`) becomes 192,000 — acceptable.
- `memsession.py:54` → `shown[target].pop(0)` so the oldest entry is dropped; update the omission line (`:46`) to say "older entries omitted for size".
- `config.py:155` → reader fallback 4000, writer fallback 32000.
- `cli.py:137-138` → `init` writes the new budgets plus a self-documenting memory section:
  ```json
  "memory": {"enabled": false, "backend": "file",
             "limits": {"memory_chars": 12000, "operator_chars": 6000}}
  ```
- `.teeplug.json:20-25` → reader 4000, writer 32000, add the same memory section.
- `tests/reproduce_live.py:105` → **leave at 2000/8192**. That script reproduces the historical measurement recorded in `docs/live-validation.json`; changing it would make the recorded numbers unreproducible. The provenance stamp below makes the budget explicit instead.

Check the derived guards still make sense: `cli.py:62` ceiling becomes 24,000 chars for the reader, `providers.py:189` rejects text longer than `max_output_tokens × 6`, and `providers.py:181` uses `max(2_000_000, tokens × 32)` — writer 32000 gives 1,024,000, so the 2,000,000 floor still applies.

## Phase 4 — Docs

- `docs/memory.md`: config block (`:14-30`, drop `location`/`data_dir`, new limits, "combined default entry budget is 4,320" → 18,000); limits table (`:36-39`); "Storage and project identity" (`:72-100`) — single project-local store, delete the data-directory resolution order (`:80-86`) and the migrate paragraph (`:96-99`); rendered-block example (`:188-191`); `memory_full` example (`:269`); command list (`:240`, drop `migrate`); drop the Claude persistent-plugin-data link (`:349`).
- `docs/memory.md`: add a short recovery note — a store created under the old `plugin-data` default is still on disk at `<host>/plugins/data/teeplug-teeplug-local/memories/<project-id>/`; copy `MEMORY.md` and `OPERATOR.md` into `<root>/.teeplug/memories/` by hand. There is no longer a command that does this.
- `docs/configuration.md`: JSON sample (`:18-19`, `:27`); "Project memory" (`:52-61`) — drop `location`/`data_dir` and `TEEPLUG_MEMORY_DATA_DIR`, keep `TEEPLUG_MEMORY_ENABLED` and `TEEPLUG_MEMORY_LOCK_SECONDS`.
- `README.md`: sample (`:111-112`); defaults table (`:138` → 4000, `:140` → 32000); host comparison (`:154`, `CLAUDE_CODE_MAX_OUTPUT_TOKENS=4000` / `32000`); the caveat at `:164`.
- `README.md:138-164`: label the savings table with the budget those figures were measured at, per `AGENTS.md` ("do not claim a fixed 90% reduction; measure returned context separately").
- `docs/live-validation.json`: add `"reader_max_output_tokens": 2000` to each record so the figures are not read as current defaults. Confirm no test asserts the exact key set first.
- `commands/file-memory/setup.md`: `argument-hint` (`:3`) and the `--migrate`/`--keep` sentence (`:10-11`).
- `skills/file-memory/SKILL.md:21`: setup row without `--location`, `--data-dir`, `--migrate`, `--keep`.
- `skills/setup/SKILL.md:19-24`: note that memory now lives in `<root>/.teeplug/memories/` and that `.teeplug/` must stay ignored.
- `AGENTS.md`: file-memory paragraph — replace "Store them outside the installed plugin" with the project-local `.teeplug/` path, keeping the point that the store is never inside the installed plugin.

## Phase 5 — Tests

`plugins/teeplug/tests/test_memory.py`

- Harness (`:33-37`): drop `self.data` and the `TEEPLUG_MEMORY_DATA_DIR` patch. `patch.dict(os.environ, {}, clear=True)` is still needed so host variables cannot leak in.
- Delete: `test_migration_imports_without_touching_the_source` (`:342-350`), `test_setup_refuses_a_location_change_that_would_strand_entries` (`:352-360`), `test_status_reports_an_unresolved_store_location` (`:141-145`).
- Rewrite: `test_store_stays_outside_the_installed_plugin` (`:169-173`) → assert the store is always `<root>/.teeplug/memories`; `test_relative_data_dir_and_symlinked_store_file_rejected` (`:175+`) → keep the symlink half, drop the relative-`data_dir` half; `test_invalid_memory_configuration_is_local_and_actionable` (`:82-87`) → `{"location": "elsewhere"}` and `{"data_dir": "x"}` must now be rejected as unknown fields.
- `test_rendered_block_is_bounded_and_labelled` (`:533-542`): the ceiling assertion becomes 24000, and 60 entries of ~140 chars (~8,400 total) no longer overflow it — increase the content past 24,000 rendered characters so the omission path is still exercised, and update the expected wording to "older entries omitted".
- `test_rendered_block_reports_real_usage` (`:549-550`): `/2600` → `/12000`, `/1720` → `/6000`.
- `test_documented_chat_commands_match_the_cli_actions` (`:830`): drop `"migrate"` from the action tuple.
- Add: a bare `memory setup` writes `limits` 12000/6000; `memory setup --location project` now fails as an unrecognised argument; the store path is project-local and inside `.teeplug/`.

`plugins/teeplug/tests/test_teeplug.py`

- `:166` → writer default 32000.
- `test_init_and_offline_doctor` (`:46-53`): assert the written `.teeplug.json` contains reader 4000, writer 32000 and `memory.enabled == false` with the new limits.
- `:74-79` (explicit 777/9000 overrides) is unaffected.

## Verification

1. `python3 -m unittest discover -s plugins/teeplug/tests -v`
2. Grep for leftovers: `plugin_data_dir`, `data_dir`, `TEEPLUG_MEMORY_DATA_DIR`, `import_entries`, `run_migrate`, `location`, `2600`, `1720`, `8192`.
3. Smoke test in a throwaway directory: `init` → inspect `.teeplug.json`; `memory setup` → `memory status` shows `<root>/.teeplug/memories`; `memory add` → `memory context` renders it; confirm `.teeplug/` is ignored.
4. Confirm no `__pycache__` or scratch files are left behind.

## Accepted tradeoffs

- A full store now injects up to ~24,000 characters (~6,000 tokens) at every `SessionStart`. That is the direct cost of the larger limits and the raised ceiling.
- Reader summaries may now reach ~24,000 characters of main-agent context instead of ~12,000.
- Existing `plugin-data` stores become unreachable by the tool; recovery is a documented manual copy.
- The measured savings figures in `README.md` and `docs/live-validation.json` predate these budgets and are labelled as such rather than re-measured.

## Out of scope

`MAX_ENTRY_CHARS` (1000), `MAX_ENTRY_LINES` (12), `MAX_ENTRIES` (200), `MAX_STORE_BYTES` (256 KiB), locking, snapshots, the validation guard and its `SECRET_PATTERNS`, worker routing and host matching.

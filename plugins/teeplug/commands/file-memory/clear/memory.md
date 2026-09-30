---
description: Clear the Teeplug MEMORY store and invalidate its session snapshots
---

Use the teeplug file-memory skill. Show the operator the current memory entries from
`memory list --root "$PWD" --target memory` and confirm before running
`memory clear --root "$PWD" --target memory --confirm`. This removes every memory
entry and invalidates plugin-managed snapshots so erased entries are not restored
later; the other store, unrelated configuration and the cache are untouched. Text
already delivered to an open conversation cannot be retracted by a file operation.

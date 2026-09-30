---
description: Clear the Teeplug OPERATOR store and invalidate its session snapshots
---

Use the teeplug file-memory skill. Show the operator what
`memory list --root "$PWD" --target operator` returns and confirm before running
`memory clear --root "$PWD" --target operator --confirm`. This removes every operator
entry and invalidates plugin-managed snapshots so erased entries are not restored
later; the other store, unrelated configuration and the cache are untouched. Text
already delivered to an open conversation cannot be retracted by a file operation.

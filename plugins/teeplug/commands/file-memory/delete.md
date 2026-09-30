---
description: Disable Teeplug memory and delete both stores with their metadata and snapshots
---

Use the teeplug file-memory skill. This permanently deletes MEMORY.md, OPERATOR.md, the
private project metadata and every snapshot for this project, and disables memory.
Show the operator what `memory status` and `memory list` currently hold and confirm with
them before running `memory delete --root "$PWD" --confirm`. Unrelated configuration
and the reader cache are untouched. Re-enabling later requires setup, and content
already delivered to an open conversation cannot be retracted.

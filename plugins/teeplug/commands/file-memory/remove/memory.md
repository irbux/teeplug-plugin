---
description: Remove one Teeplug MEMORY entry by id or by a unique old_text match
argument-hint: "<entry-id or exact entry text>"
---

Use the teeplug file-memory skill. Run `memory remove --root "$PWD" --target memory`
with `--id`, or a JSON payload on stdin holding `old_text` that matches exactly one
complete entry. An ambiguous or missing match changes nothing; run `memory list` and
confirm the intended id with the operator instead of guessing. Removal works even while
memory is disabled or a store is over a lowered limit.

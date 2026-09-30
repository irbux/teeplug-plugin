---
description: Replace one Teeplug MEMORY entry by id or by a unique old_text match
argument-hint: "<entry-id or old text> -> <new text>"
---

Use the teeplug file-memory skill. Run `memory replace --root "$PWD" --target memory`
with a JSON payload on stdin holding `text` and either `id` or `old_text`.
`old_text` must match exactly one complete entry; an ambiguous or missing match
changes nothing. Read the entry first and pass `expect_revision` from that read, and
on `stale_revision` re-read and reconsider before a single bounded retry. Replace for
a verified correction or an explicit operator request, not to free capacity.

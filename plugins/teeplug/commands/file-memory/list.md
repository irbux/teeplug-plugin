---
description: List current Teeplug memory entries with ids, provenance and revisions
argument-hint: "[memory|operator] [--limit N] [--offset N]"
---

Use the teeplug file-memory skill. Run `memory list --root "$PWD"`, adding `--target`,
`--limit` and `--offset` from $ARGUMENTS. Show each entry id, provenance, update time
and text, plus the revision and usage. Entries shown as `<withheld: ...>` failed
validation and stay on disk unrendered; report them rather than reproducing them.
This is current disk state, which may differ from the startup snapshot.

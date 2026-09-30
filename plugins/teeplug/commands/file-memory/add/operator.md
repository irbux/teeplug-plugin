---
description: Add one entry to the Teeplug OPERATOR memory store
argument-hint: "<entry text>"
---

Use the teeplug file-memory skill. Save the fact in $ARGUMENTS to the operator store
with `memory add --root "$PWD" --target operator`, passing the JSON payload on stdin
through a quoted heredoc or `--payload-file`; never interpolate the text into the
command line. Set `source` to compact provenance such as operator-statement,
operator-request, operator-correction or verified:path:lines. Keep the entry specific and
useful beyond this session, and never store credentials, tokens or key material. An
exact duplicate is a successful no-op; `memory_full` means nothing was saved and
nothing was evicted, so report it instead of deleting entries to make room.

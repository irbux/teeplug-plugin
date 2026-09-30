---
description: Configure or re-enable Teeplug project memory, its storage and its limits
argument-hint: "[--location plugin-data|project] [--memory-chars N] [--operator-chars N]"
---

Use the teeplug file-memory skill. Run `memory setup` with `--root "$PWD"` and any
arguments the operator gave in $ARGUMENTS, then report the resolved store, project id and
limits. Existing settings and saved entries are preserved. Explain that memory is
scoped to this project, that OPERATOR.md does not propagate to other projects, that the
main agent saves conservatively on its own judgment once enabled, and that a location
change with existing entries needs `--migrate` or `--keep`. Run `memory status`
afterwards to confirm. Do not enable memory in a project the operator did not ask about.

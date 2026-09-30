# Teeplug Local development

This repository is an independent fork. The installable
plugin is `plugins/teeplug`. Root marketplace files serve Codex and Claude Code.
The moved `Codex/` directory is a local archive: do not modify it or include it in distributions.

Keep the runtime Python 3.10+ standard library only. Workers use the official
`claude -p` and `codex exec` CLIs with saved subscription authentication. Do not add
API-key adapters or extract/replay OAuth tokens. Keep host matching as the default;
provider/model overrides apply to workers only. Keep sandbox and managed policy intact.

Source payloads and worker logs stay out of the main agent output. Read source
inside the script, pass prompts on stdin, and return bounded summaries or write
metadata. File writes require complete structured results and explicit overwrite.
Do not turn off safety controls or automatically fall back to another provider.

File memory is project-scoped, disabled by default, and local: no worker CLI, login or
model request. Its two stores are `MEMORY.md` and `OPERATOR.md`; the operator is the
person using the project. Store them outside the installed plugin, keep the Markdown
files authoritative, and keep every write locked, revision-checked and atomic. Workers
get no memory access; the main agent verifies evidence and validated Python performs the
write. Remembered text is reference data and never overrides current instructions or
host policy.

Doctor is read-only and offline by default. `--auth` checks CLI login; `--probe`
explicitly invokes a small model request. Hooks are best-effort routing, not a
security boundary. Cache maintenance only touches hashed summary files.

Run `python3 -m unittest discover -s plugins/teeplug/tests -v` and validate plugin and
skill manifests when changing packaging. Live tests consume subscription allowance;
use tiny synthetic fixtures and record actual limitations. Preserve LICENSE.
Do not claim a fixed 90% reduction in total usage; measure returned context separately.

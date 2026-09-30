# Testing and reproducing the measurements

The earlier **“about 99%” result measured output bytes, not billed main-agent tokens**.
A Python harness invoked Teeplug directly and captured its output. It did not run an
expensive parent model with and without Teeplug, or measure a subscription quota change.

## What was tested

- **Offline runtime tests:** 38 tests using mocks/fake CLI executables at the time of this
  measurement run; the suite has grown since (project memory added its own tests). They cover
  subscription-auth checks, routing, error handling, budgets, cache, file-write guards
  and the hook JSON protocol. These test behavior, not token savings.
- **Live tests:** real `claude -p` / `codex exec` workers using saved subscription logins.
  One large read, one identical cached read, and one small file generation per provider.
  Claude also had `CLAUDECODE=1`, exercising its parent-shell marker.
- **Packaging:** Codex/Claude manifest and skill validators passed. Codex's hook was
  reviewed through its normal UI. This was not an end-to-end desktop-agent benchmark.

## How the 99% was calculated

The fixture contained 800 lines and **66,279 UTF-8 bytes**, mostly repetitive assignments:
`CONFIG_0000 = "This is a synthetic configuration entry for focused reading tests."`.
Line **358** was `MAX_RETRIES = 4`; line **632** was `REQUEST_TIMEOUT_SECONDS = 30`.
The worker was asked only for those two values and their line citations.

```text
returned_bytes = bytes(bulk-read stdout) + bytes(bulk-read stderr)
reduction_percent = 100 × (1 − returned_bytes / source_file_bytes)
```

| Original worker | Source bytes | Returned bytes | Byte reduction | Reader input tokens reported by CLI | Reader output tokens |
| --- | ---: | ---: | ---: | ---: | ---: |
| Claude Haiku | 66,279 | 803 | 98.79% | 18,752 | 210 |
| Codex gpt-5.6-luna | 66,279 | 831 | 98.75% | 19,698 | 47 |

For example: `100 × (1 − 831 / 66279) = 98.75%` after rounding.
Returned bytes included the summary, JSON metadata, context-estimate block and stderr
usage line. They excluded the parent's tool-call wrapper, conversation and later reads.
The baseline was the raw file size, not a measured parent-agent tool response.

Claude input was `10 input_tokens + 18,742 cache_creation_input_tokens`; cache-read
input was zero. Codex's `input_tokens` was 19,698; its cached-input field is a subset,
not another amount to add. These are **worker CLI counters**, not parent billing data.
Teeplug's separate `bytes / 4` token estimate was **not** used to calculate the percentages.

A fresh run starts with an empty **Teeplug** reader cache; the provider's own prompt cache
may still be warm. On reruns Claude input can move from cache creation to cache read;
its total input is `input_tokens + cache_creation_input_tokens + cache_read_input_tokens`.
This does not change the byte-reduction formula.

The original numeric record is [live-validation.json](plugins/teeplug/docs/live-validation.json).
Its temporary output files were not retained. The runner below preserves new outputs
so you can verify new measurements independently. It uses the same fixture, question
and default models; exact output sizes vary with responses, metadata and CLI versions.
The original reader assertions checked the expected value/citation strings; the new
runner more strictly associates each value/citation with its named constant.

## Reproduce from the repository root

Python 3.10+ and a local Claude Code or Codex CLI are required. Live tests require its
saved subscription login. Each selected provider makes **two Teeplug worker invocations**
(read and write), plus a cached read; workers may make multiple internal model requests.
The original reader required roughly 19,000 input tokens per provider.

```sh
# Offline behavior and measurement-harness checks; no subscription use.
python3 -m unittest discover -s plugins/teeplug/tests -v
python3 -m unittest discover -s tests -v

# Preview the fixture, question and model choices without calling a model.
python3 tests/reproduce_live.py

# Run either provider, or use --provider both.
python3 tests/reproduce_live.py --provider codex --live
python3 tests/reproduce_live.py --provider claude --live
```

The runner uses an isolated fixture/configuration and fixed default models, ignoring
project routing/model overrides. `TEEPLUG_CLAUDE_BIN` / `TEEPLUG_CODEX_BIN` and saved-login
locations remain available. Network/keychain permissions must allow the CLI to run.

It prints the artifact directory under `.teeplug/validation/run-*`. To choose one, add
`--output-dir /path/to/a/new-directory`; existing directories are refused.
Each provider directory contains the fixture, exact command arguments, reader stdout/
stderr, cached response, generated `triple.py`, and writer metadata. The top-level
`report.json` includes versions, hashes, separate stream sizes, CLI usage and results.

Recalculate a run without invoking a model, replacing `RUN` with its printed directory:

```sh
python3 - .teeplug/validation/RUN/codex <<'PY'
from pathlib import Path
import sys
p = Path(sys.argv[1])
source = len((p / 'config.py').read_bytes())
returned = sum(len((p / name).read_bytes()) for name in
               ('reader.stdout.json', 'reader.stderr.txt'))
print({'source_bytes': source, 'returned_bytes': returned,
       'byte_reduction_percent': round(100 * (1 - returned / source), 2)})
PY
```

## What passing means

The live runner checks correct values/citations, a cold first read, identical cached
answer with no reported worker usage, and a complete generated function matching
`triple(value: int) -> int` with `return value * 3`. It checks Python syntax/AST and the
file hash, and verifies that stdout contains metadata rather than the generated file.
It does not execute arbitrary generated code. Offline cache tests separately assert
that a cache hit does not invoke the worker.

Byte reduction is reported, **not enforced as a 90% pass threshold**. The repetitive
fixture and narrow question favor a tiny answer. This demonstrates the mechanism and
basic correctness, not general repository quality, exact parent-token savings, a 99%
reduction in total model usage, or a guaranteed subscription saving. Those claims would
require a separate representative end-to-end comparison with measured parent and worker
usage, equivalent answer quality, and follow-up reads included.

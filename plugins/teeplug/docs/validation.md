# Validation

Reproduction commands and methodology: [TESTING.md](../../../TESTING.md). The runnable
harness is `tests/reproduce_live.py` at the repository root. The historical numbers
below are preserved; reruns write separate reports under `.teeplug/validation/`.

## Codex worker launch regression (0.4.1)

On 2026-10-04, reproduced a worker startup failure inside a Codex workspace-write
sandbox: login status succeeded, but the CLI exited before starting a model turn
with SQLite and operation-not-permitted diagnostics. The same synthetic probe
passed through the host's normal permission approval flow. Version 0.4.1 returns
an actionable `worker_access_denied` message and its skills describe the host retry;
the worker retains its read-only sandbox and managed policy.

The installed VS Code Codex CLI 0.155.0-alpha.16.3 also rejected `gpt-6.1-sol` as
unknown/unsupported. Selecting the ChatGPT app's Codex CLI 0.160.0 through
`providers.codex.command` allowed that same model to complete the probe.

With installed Teeplug 0.4.1 and Codex 0.160.0, an approved live check using the
affected project's settings passed both workflows on tiny synthetic temporary files:

| Workflow | Model | Verification | Input/output tokens |
| --- | --- | --- | ---: |
| Reader | gpt-5.6-luna | Uncached answer contains expected value and file citation | 3,463 / 40 |
| Writer | gpt-6.1-sol | Complete file written; Python AST matches requested function | 5,476 / 32 |

All 102 plugin unit tests, the four changed skill validators, JSON manifest checks,
and Claude's native plugin validator passed. Codex installed the 0.4.1 manifest
successfully. Synthetic checks sent no project source, and no raw worker logs were
returned. This verifies the installed scripts with normal approved execution;
it does not claim an unapproved sandbox call or every future chat invocation succeeds.

## Historical validation (0.3.x)

Validated locally on 2026-09-14 UTC with Python 3.14.5, Codex CLI 0.153.4 and
Claude Code 2.1.227. Runtime targets Python 3.10+ on macOS/Linux; Windows process
group handling is not implemented.

## Automated checks

38 tests cover real subprocess execution with fake CLIs, subscription-vs-API auth,
host/model routing, worker environment isolation, error redaction, incomplete output,
output limits, timeouts, recursive calls, cache hits/content invalidation/expiry/size
pruning, binary and boundary rejection, write preservation, and the actual hook JSON protocol.

```sh
python3 -m unittest discover -s plugins/teeplug/tests -v
```

Native Codex plugin validation, both Claude manifests, and all four skill frontmatter
validators passed. Validation tools need PyYAML; the plugin runtime does not.

## Live subscription tests

Both providers passed one 800-line synthetic file read with checks for the expected
value and line-number strings,
a second identical cached read with no worker usage, and generation of a small Python
function directly to disk. The generated file passed Python AST/syntax checks for the
specified function. Claude was also tested with `CLAUDECODE=1`, matching a Claude Code
parent shell. Codex ran from a Codex desktop task with its normal session marker.

| Worker | Source bytes | Returned bytes including stdout/stderr metadata | Reduction |
| --- | ---: | ---: | ---: |
| claude | 66,279 | 803 | 98.79% |
| codex | 66,279 | 831 | 98.75% |

These percentages compare raw fixture UTF-8 bytes with captured Teeplug command stdout
and stderr for a narrowly focused question about a deliberately repetitive fixture.
The harness did not invoke a parent model or measure its actual input/billed tokens.
They are not representative results for arbitrary repositories. They exclude the
question/tool-call wrapper and subsequent verification reads. Worker input was still
about 18,752 Claude tokens (input plus cache creation) and 19,698 Codex tokens for the
reader calls. The worker also generated output and the host may charge allowance by
model-specific rules. No total-usage or subscription-cost reduction is guaranteed.

Raw numeric results are in [live-validation.json](live-validation.json). The checks used
only generated synthetic source, not private project code. No source or raw worker log
is included in that report.

## Integration boundaries

Direct CLI worker functionality is live-tested. Plugin marketplace installation and
normal hook trust are separate host concerns. A new session must load installed skills.
Desktop support uses the same local CLI worker; an end-to-end UI model conversation is
not equivalent to a packaging validation check. Managed policies and CLI updates may
change which features are available. Unknown model names fail without automatic fallback.

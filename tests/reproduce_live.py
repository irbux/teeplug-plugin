#!/usr/bin/env python3
"""Reproduce Teeplug's synthetic live check. --live explicitly enables model calls."""
import argparse
import ast
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

REPO = Path(__file__).resolve().parents[1]
QUESTION = ('What are MAX_RETRIES and REQUEST_TIMEOUT_SECONDS? '
            'Give only their values and exact line citations.')
SPEC = ('Write a Python function triple(value: int) -> int returning value * 3. '
        'Use the reference style. No imports or side effects.')
MODELS = {'claude': 'haiku', 'codex': 'gpt-5.6-luna'}


def fixture():
    rows = [f'CONFIG_{i:04d} = "This is a synthetic configuration entry for focused reading tests."'
            for i in range(800)]
    rows[357] = 'MAX_RETRIES = 4'
    rows[631] = 'REQUEST_TIMEOUT_SECONDS = 30'
    return ('\n'.join(rows) + '\n').encode('utf-8')


def reduction(source_bytes, returned_bytes):
    return round(100 * (1 - returned_bytes / source_bytes), 2)


def check_answer(answer):
    # Keep the original question. Require a value and citation near its named constant.
    names = ('MAX_RETRIES', 'REQUEST_TIMEOUT_SECONDS')
    for name, value, line in ((names[0], 4, 358), (names[1], 30, 632)):
        other = names[1] if name == names[0] else names[0]
        matches = re.finditer(re.escape(name), answer)
        valid = False
        for match in matches:
            fragment = answer[match.end():].split(other, 1)[0]
            if (re.search(rf'(?<!\d){value}(?!\d)', fragment) and
                    re.search(rf'(?<!\d){line}(?!\d)', fragment)):
                valid = True
                break
        if not valid:
            raise ValueError(f'Answer did not associate {name} with value {value} and line {line}; inspect reader.stdout.json')


def check_code(code):
    tree = ast.parse(code)
    if len(tree.body) != 1 or not isinstance(tree.body[0], ast.FunctionDef):
        raise ValueError('Writer must produce one function')
    function = tree.body[0]
    if function.name != 'triple' or function.decorator_list:
        raise ValueError('Writer produced an unexpected function or decorator')
    args = function.args
    if (len(args.args) != 1 or args.args[0].arg != 'value' or args.posonlyargs or
            args.kwonlyargs or args.vararg or args.kwarg or args.defaults):
        raise ValueError('Writer produced unexpected function arguments')
    if not all(isinstance(node, ast.Name) and node.id == 'int'
               for node in (args.args[0].annotation, function.returns)):
        raise ValueError('Writer must use the specified int annotations')
    body = list(function.body)
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
        body.pop(0)  # optional docstring
    if len(body) != 1 or not isinstance(body[0], ast.Return):
        raise ValueError('Writer must return the result without other statements')
    expression = body[0].value
    if not (isinstance(expression, ast.BinOp) and isinstance(expression.op, ast.Mult) and
            isinstance(expression.left, ast.Name) and expression.left.id == 'value' and
            isinstance(expression.right, ast.Constant) and type(expression.right.value) is int and expression.right.value == 3):
        raise ValueError('Writer must return value * 3')


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')


def version(executable):
    process = subprocess.run([executable, '--version'], capture_output=True, timeout=20)
    if process.returncode:
        raise ValueError('Cannot read worker CLI version')
    return process.stdout.decode('utf-8', errors='replace').strip()[:200]


def check_provider(provider, run_dir, cli):
    # Ignore routing/model overrides for a reproducible fixture, but retain CLI paths
    # and the host's official saved-login locations. Never read credential files.
    env = {k: v for k, v in os.environ.items()
           if not k.startswith('TEEPLUG_') or k in ('TEEPLUG_CLAUDE_BIN', 'TEEPLUG_CODEX_BIN')}
    if provider == 'claude':
        env['CLAUDECODE'] = '1'  # exercise the normal Claude parent-shell marker
    root = run_dir / provider
    root.mkdir()
    raw = fixture()
    (root / 'config.py').write_bytes(raw)
    (root / 'reference.py').write_text('def double(value: int) -> int:\n    """Return twice the value."""\n    return value * 2\n')
    # Isolate from the caller's project settings; use the original test defaults.
    config = root / '.teeplug.json'
    write_json(config, {'provider': provider, 'providers': {provider: {'model': MODELS[provider], 'effort': 'low'}},
                       'timeout_seconds': 180, 'cache': True, 'max_input_bytes': 512000,
                       'reader': {'max_output_tokens': 2000}, 'writer': {'max_output_tokens': 8192}})
    common = ['--root', str(root), '--config', str(config), '--host', provider,
              '--provider', provider, '--model', MODELS[provider], '--effort', 'low']
    commands = []

    def run(label, command, *arguments):
        argv = [sys.executable, str(cli), command, *common, *arguments]
        commands.append({'step': label, 'argv': argv})
        write_json(root / 'commands.json', commands)
        process = subprocess.run(argv, cwd=root, env=env, capture_output=True, timeout=240)
        (root / f'{label}.stdout.json').write_bytes(process.stdout)
        (root / f'{label}.stderr.txt').write_bytes(process.stderr)
        if process.returncode:
            raise ValueError(f'{provider} {label} exited {process.returncode}; inspect its saved stderr')
        return json.loads(process.stdout), len(process.stdout), len(process.stderr)

    doctor, _, _ = run('doctor', 'doctor')  # offline; returns resolved executable
    cli_version = version(doctor['executable'])
    reader_args = ['--question', QUESTION, '--paths', 'config.py', '--json']
    first, stdout_bytes, stderr_bytes = run('reader', 'bulk-read', *reader_args)
    if first['provider'] != provider or first['model'] != MODELS[provider] or first['cache_hit']:
        raise ValueError('Unexpected worker routing or warm first read')
    if first['source_bytes'] != len(raw):
        raise ValueError('Source byte measurement mismatch')
    if not first['worker_usage']:
        raise ValueError('Live reader returned no CLI usage counters')
    check_answer(first['answer'])
    cached, _, _ = run('cached', 'bulk-read', *reader_args)
    if not cached['cache_hit'] or cached['worker_usage'] or cached['answer'] != first['answer']:
        raise ValueError('Repeated read did not return the same answer from cache without reported worker usage')
    generated, _, _ = run('writer', 'code-write', '--spec', SPEC, '--reference', 'reference.py', '--target', 'triple.py')
    code = (root / 'triple.py').read_text()
    check_code(code)  # never execute arbitrary generated code
    if (generated.get('status') != 'written' or 'def triple' in json.dumps(generated) or
            generated['sha256'] != hashlib.sha256((root / 'triple.py').read_bytes()).hexdigest()):
        raise ValueError('Writer metadata does not match the generated file')
    return {'provider': provider, 'model': MODELS[provider], 'effort': 'low', 'cli_version': cli_version,
            'source_bytes': len(raw), 'source_sha256': hashlib.sha256(raw).hexdigest(),
            'stdout_bytes': stdout_bytes, 'stderr_bytes': stderr_bytes,
            'returned_bytes': stdout_bytes + stderr_bytes,
            'output_byte_reduction_percent': reduction(len(raw), stdout_bytes + stderr_bytes),
            'reader_worker_usage': first['worker_usage'], 'cache_hit_verified': True,
            'writer_worker_usage': generated['worker_usage'], 'writer_ast_and_hash_verified': True,
            'claude_parent_marker_set': provider == 'claude'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--provider', choices=('claude', 'codex', 'both'), default='both')
    parser.add_argument('--live', action='store_true', help='Run one read and one write per selected provider; consumes subscription allowance')
    parser.add_argument('--output-dir', type=Path, help='New empty artifact directory; default is .teeplug/validation/run-*')
    args = parser.parse_args(argv)
    if not args.live:
        print(json.dumps({'mode': 'preview', 'source_lines': 800, 'source_bytes': len(fixture()),
                          'question': QUESTION, 'models': MODELS, 'worker_invocations_per_provider': 2,
                          'next': 'Add --live to run subscription checks; no model was called.'}, indent=2))
        return 0
    if os.getenv('TEEPLUG_WORKER'):
        parser.error('Run reproduction from the parent terminal, not from inside a Teeplug worker')
    if args.output_dir:
        run_dir = args.output_dir.expanduser().resolve()
        run_dir.mkdir(parents=True, exist_ok=False)
    else:
        parent = REPO / '.teeplug' / 'validation'
        parent.mkdir(parents=True, exist_ok=True)
        run_dir = Path(tempfile.mkdtemp(prefix='run-', dir=parent)).resolve()
    cli = REPO / 'plugins/teeplug/scripts/teeplug.py'
    report = {'schema_version': 1, 'started_at_utc': datetime.now(timezone.utc).isoformat(),
              'python_version': sys.version.split()[0],
              'measurement': 'Raw fixture bytes versus bulk-read stdout + stderr bytes; no parent model was invoked.',
              'formula': '100 * (1 - returned_bytes / source_bytes)',
              'parent_tokens_measured': False, 'subscription_quota_delta_measured': False,
              'question': QUESTION, 'results': [], 'status': 'running'}
    write_json(run_dir / 'report.json', report)
    try:
        for provider in (('claude', 'codex') if args.provider == 'both' else (args.provider,)):
            print(f'Checking {provider}; artifacts: {run_dir / provider}', file=sys.stderr, flush=True)
            report['results'].append(check_provider(provider, run_dir, cli))
            write_json(run_dir / 'report.json', report)
        report['status'] = 'passed'
    except (OSError, ValueError, KeyError, SyntaxError, subprocess.TimeoutExpired) as exc:
        report['status'] = 'failed'
        report['error'] = str(exc)
    report['finished_at_utc'] = datetime.now(timezone.utc).isoformat()
    write_json(run_dir / 'report.json', report)
    print(json.dumps({'report': str(run_dir / 'report.json'), **report}, indent=2))
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())

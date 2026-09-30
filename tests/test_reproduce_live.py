import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('reproduce_live', Path(__file__).with_name('reproduce_live.py'))
live = importlib.util.module_from_spec(spec)
spec.loader.exec_module(live)


class ReproductionTests(unittest.TestCase):
    def test_fixture_and_original_arithmetic(self):
        raw = live.fixture()
        self.assertEqual(len(raw), 66279)
        lines = raw.decode().splitlines()
        self.assertEqual(len(lines), 800)
        self.assertEqual(lines[357], 'MAX_RETRIES = 4')
        self.assertEqual(lines[631], 'REQUEST_TIMEOUT_SECONDS = 30')
        self.assertEqual(live.reduction(len(raw), 803), 98.79)
        self.assertEqual(live.reduction(len(raw), 831), 98.75)

    def test_answer_checks_value_and_citation_associations(self):
        live.check_answer('MAX_RETRIES = 4 (config.py:358)\nREQUEST_TIMEOUT_SECONDS = 30 (config.py:632)')
        for text in ('MAX_RETRIES = 40 (358)\nREQUEST_TIMEOUT_SECONDS = 30 (632)',
                     'MAX_RETRIES = 30 (632)\nREQUEST_TIMEOUT_SECONDS = 4 (358)',
                     'MAX_RETRIES = 4 (1358)\nREQUEST_TIMEOUT_SECONDS = 30 (1632)'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                live.check_answer(text)

    def test_writer_validation_rejects_wrong_code_and_side_effects(self):
        live.check_code('def triple(value: int) -> int:\n    return value * 3\n')
        for code in ('def triple(value: int) -> int:\n    return value * 4\n',
                     'import os\ndef triple(value: int) -> int:\n    return value * 3\n',
                     'def triple(value: int) -> int:\n    print(value)\n    return value * 3\n'):
            with self.subTest(code=code), self.assertRaises(ValueError):
                live.check_code(code)

    def test_preview_does_not_launch_a_process(self):
        with patch.object(live.subprocess, 'run') as run, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(live.main([]), 0)
        run.assert_not_called()

    def test_measured_bytes_match_saved_streams_not_token_estimate(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            reads = 0
            def process(argv, **kwargs):
                nonlocal reads
                if '--version' in argv:
                    return subprocess.CompletedProcess(argv, 0, b'fake 1.0\n', b'')
                command = argv[2]
                if command == 'doctor':
                    value = {'executable': sys.executable}
                elif command == 'bulk-read':
                    reads += 1
                    value = {'provider': 'codex', 'model': live.MODELS['codex'], 'cache_hit': reads == 2,
                             'source_bytes': len(live.fixture()),
                             'answer': 'MAX_RETRIES = 4 (config.py:358)\nREQUEST_TIMEOUT_SECONDS = 30 (config.py:632)',
                             'worker_usage': {} if reads == 2 else {'input_tokens': 123},
                             'context_estimate': {'returned_tokens': 99999999}}
                else:
                    code = b'def triple(value: int) -> int:\n    return value * 3\n'
                    (Path(kwargs['cwd']) / 'triple.py').write_bytes(code)
                    value = {'status': 'written', 'worker_usage': {'output_tokens': 30},
                             'sha256': live.hashlib.sha256(code).hexdigest()}
                return subprocess.CompletedProcess(argv, 0, (json.dumps(value) + '\n').encode(), b'usage\n')
            with patch.object(live.subprocess, 'run', side_effect=process):
                report = live.check_provider('codex', root, root / 'unused-cli.py')
            saved = sum(len((root / 'codex' / name).read_bytes()) for name in
                        ('reader.stdout.json', 'reader.stderr.txt'))
            self.assertEqual(report['returned_bytes'], saved)
            self.assertEqual(report['output_byte_reduction_percent'], live.reduction(66279, saved))
            self.assertTrue(report['cache_hit_verified'])
            self.assertTrue(report['writer_ast_and_hash_verified'])
            self.assertEqual(len(json.loads((root / 'codex/commands.json').read_text())), 4)


if __name__ == '__main__':
    unittest.main()

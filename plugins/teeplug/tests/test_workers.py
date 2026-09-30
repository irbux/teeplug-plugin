import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from teepluglib.config import Settings, TeeplugError
from teepluglib.providers import child_env, invoke, parse_response, run_process
from teepluglib.cache import cache_status, prune_cache
from teepluglib.files import cache_path, cache_read, cache_write

FAKE = r'''
import json, os, sys
args=sys.argv[1:]
case=os.getenv('FAKE_CASE','ok')
if 'status' in args:
    if 'auth' in args:
        print(json.dumps({'loggedIn':True, 'authMethod':'api_key' if case=='api' else 'claude.ai'}))
    else:
        print('Logged in using API key' if case=='api' else 'Logged in using ChatGPT')
    sys.exit(0)
message=sys.stdin.read()
assert 'PRIVATE_SOURCE' in message
assert os.environ.get('TEEPLUG_WORKER') == '1'
assert 'OPENAI_API_KEY' not in os.environ and 'ANTHROPIC_API_KEY' not in os.environ
assert str(os.getcwd()) != os.getenv('ORIGINAL_CWD')
value={'text':'answer', 'complete': case != 'partial'}
if case=='error':
    print('PRIVATE_SOURCE SECRET_VALUE',file=sys.stderr)
    sys.exit(9)
if '-p' in args:
    assert '--safe-mode' in args and '--no-session-persistence' in args
    assert args[args.index('--tools')+1] == ''
    print(json.dumps({'subtype':'success','is_error':False,'stop_reason':'tool_use','structured_output':value,'usage':{'input_tokens':11,'output_tokens':4}}))
else:
    assert '--ignore-user-config' in args and '--ephemeral' in args
    assert args[args.index('--sandbox')+1] == 'read-only'
    assert 'forced_login_method="chatgpt"' in args
    assert 'shell_tool' in args and 'plugins' in args and 'multi_agent' in args
    print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':json.dumps(value)}}))
    print(json.dumps({'type':'turn.completed','usage':{'input_tokens':12,'output_tokens':4}}))
'''


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.fake = self.root / 'fake-worker'
        self.fake.write_text('#!' + sys.executable + '\n' + FAKE)
        self.fake.chmod(0o755)
        self.env = patch.dict(os.environ, {'TEEPLUG_CODEX_BIN':str(self.fake), 'TEEPLUG_CLAUDE_BIN':str(self.fake),
                                         'OPENAI_API_KEY':'secret', 'ANTHROPIC_API_KEY':'secret',
                                         'ORIGINAL_CWD':str(self.root)}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_both_real_subprocess_adapters(self):
        for provider in ('claude', 'codex'):
            with self.subTest(provider=provider):
                answer=invoke(Settings(self.root, provider=provider), 'Read source.', 'PRIVATE_SOURCE')
                self.assertEqual(answer['text'], 'answer')
                self.assertEqual(answer['usage']['output_tokens'], 4)

    def test_api_login_and_incomplete_results_rejected(self):
        for provider in ('claude', 'codex'):
            for case in ('api','partial'):
                with self.subTest(provider=provider, case=case), patch.dict(os.environ, {'FAKE_CASE':case}):
                    with self.assertRaises(TeeplugError):
                        invoke(Settings(self.root, provider=provider), 'Read.', 'PRIVATE_SOURCE')

    def test_process_error_does_not_leak_payload_or_secrets(self):
        with patch.dict(os.environ, {'FAKE_CASE':'error'}):
            with self.assertRaises(TeeplugError) as caught:
                invoke(Settings(self.root, provider='codex'), 'Read.', 'PRIVATE_SOURCE')
        self.assertNotIn('SECRET_VALUE', str(caught.exception))
        self.assertNotIn('PRIVATE_SOURCE', str(caught.exception))

    def test_recursive_worker_rejected(self):
        with patch.dict(os.environ, {'TEEPLUG_WORKER':'1'}), patch('teepluglib.providers.run_process') as call:
            with self.assertRaises(TeeplugError):
                invoke(Settings(self.root, provider='claude'),'Read.','PRIVATE_SOURCE')
            call.assert_not_called()

    def test_timeout_and_output_limit(self):
        for program, timeout, limit in [('import time; time.sleep(10)',0.1,1000),
                                        ('print("x" * 10000)',2,100)]:
            with self.subTest(program=program), self.assertRaises(TeeplugError):
                run_process([sys.executable,'-c',program], '', self.root, dict(os.environ), timeout, limit)

    def test_codex_tool_calls_and_failed_turns_rejected(self):
        for item in ({'type':'command_execution','command':'secret'}, {'type':'mcp_tool_call'}):
            events=[{'type':'item.completed','item':item}, {'type':'turn.completed','usage':{}}]
            with self.assertRaises(TeeplugError):
                parse_response('codex','\n'.join(json.dumps(e) for e in events))
        with self.assertRaises(TeeplugError):
            parse_response('codex', '{"type":"turn.failed"}')

    def test_claude_truncation_and_bad_json_rejected(self):
        for data in ({'subtype':'success','stop_reason':'max_tokens','structured_output':{'text':'partial','complete':True}},
                     {'subtype':'error_max_turns','is_error':True}, ['unexpected']):
            with self.assertRaises(TeeplugError):
                parse_response('claude', json.dumps(data))

    def test_env_scrubs_billing_overrides_but_preserves_host_auth_location(self):
        with patch.dict(os.environ, {'CODEX_HOME':'/test/auth-home', 'ANTHROPIC_BASE_URL':'secret',
                                    'CLAUDE_CODE_USE_BEDROCK':'1', 'ANTHROPIC_DEFAULT_HAIKU_MODEL':'bad'}):
            env=child_env(Settings(self.root, provider='codex'))
        self.assertEqual(env['CODEX_HOME'], '/test/auth-home')
        for key in ('OPENAI_API_KEY','ANTHROPIC_API_KEY','ANTHROPIC_BASE_URL','CLAUDE_CODE_USE_BEDROCK','ANTHROPIC_DEFAULT_HAIKU_MODEL'):
            self.assertNotIn(key,env)


class CacheTests(unittest.TestCase):
    def test_expiry_prune_clear_and_unrelated_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            paths=[cache_path(root, {'question':str(i)}) for i in range(3)]
            for i,path in enumerate(paths):
                cache_write(path, {'text':'summary','usage':{}})
                os.utime(path,(100+i,100+i))
            unrelated=paths[0].parent/'keep.txt'; unrelated.write_text('keep')
            self.assertIsNone(cache_read(paths[0],1))
            self.assertEqual(cache_status(root,1)['expired'],3)
            result=prune_cache(root,10**12,paths[0].stat().st_size*2)
            self.assertEqual(result['removed'],1)
            self.assertFalse(paths[0].exists())
            result=prune_cache(root,10**12,10000,clear=True)
            self.assertEqual(result['entries'],0)
            self.assertEqual(unrelated.read_text(),'keep')

    def test_corrupt_cache_misses(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'entry.json'; path.write_text('{broken')
            self.assertIsNone(cache_read(path))


if __name__ == '__main__':
    unittest.main()

import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from teepluglib.cli import main
from teepluglib.config import Settings, TeeplugError, project_root
from teepluglib.files import clean_code, read_sources, snapshot, write_code
from teepluglib.hooks import bash_reason, route
from teepluglib.providers import invoke, parse_response


class Workspace(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.env = patch.dict(os.environ, {"TEEPLUG_PROVIDER": "codex", "TEEPLUG_MODEL": "test-worker",
                                         "TEEPLUG_HOST": "codex", "TEEPLUG_CODEX_BIN": sys.executable}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)

    def file(self, name, content="class UserService:\n    pass\n"):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return path

    def run_cli(self, *args, worker=None):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            with patch("teepluglib.cli.invoke", side_effect=worker) if worker else contextlib.nullcontext():
                rc = main(list(args))
        return rc, out.getvalue(), err.getvalue()


class ConfigurationTests(Workspace):
    def test_init_and_offline_doctor(self):
        rc, _, _ = self.run_cli("init", "--root", str(self.root))
        self.assertEqual(rc, 0)
        data = json.loads((self.root / ".teeplug.json").read_text())
        self.assertEqual(data["reader"]["max_output_tokens"], 4000)
        self.assertEqual(data["writer"]["max_output_tokens"], 32000)
        self.assertEqual(data["memory"], {"enabled": False, "backend": "file",
                                          "limits": {"memory_chars": 12000, "operator_chars": 6000}})
        with patch("teepluglib.providers.run_process") as process:
            rc, out, _ = self.run_cli("doctor", "--root", str(self.root))
            self.assertEqual(rc, 0)
            self.assertFalse(json.loads(out)["live_model_access_verified"])
            process.assert_not_called()

    def test_init_preserves_existing_settings(self):
        path = self.file(".teeplug.json", '{"provider":"claude"}')
        rc, _, _ = self.run_cli("init", "--root", str(self.root))
        self.assertEqual(rc, 1)
        self.assertEqual(path.read_text(), '{"provider":"claude"}')

    def test_zero_output_budget_rejected(self):
        with self.assertRaises(TeeplugError):
            Settings(self.root, max_output_tokens=0)

    def test_host_match_and_provider_override(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(Settings(self.root, host="claude").provider, "claude")
            self.assertEqual(Settings(self.root, host="codex").provider, "codex")
            self.assertEqual(Settings(self.root, host="claude", provider="codex").provider, "codex")
            with self.assertRaises(TeeplugError):
                Settings(self.root)

    def test_distinct_workflow_models_and_budgets(self):
        self.file(".teeplug.json", json.dumps({"provider": "codex", "providers": {"codex": {"model": "base", "reader": {"model": "small"}, "writer": {"model": "larger", "effort": "medium"}}}, "reader": {"max_output_tokens": 777}, "writer": {"max_output_tokens": 9000}}))
        with patch.dict(os.environ, {}, clear=True):
            reader, writer = Settings(self.root), Settings(self.root, "writer")
            explicit = Settings(self.root, "writer", model="override", effort="high")
        self.assertEqual((reader.model, reader.max_output_tokens), ("small", 777))
        self.assertEqual((writer.model, writer.max_output_tokens, writer.effort), ("larger", 9000, "medium"))
        self.assertEqual((explicit.model, explicit.effort), ("override", "high"))

    def test_api_config_and_secret_field_rejected(self):
        for content in ('{"api_key":"never-store-this"}', '{"provider":"openai"}', '{"base_url":"http://localhost"}'):
            self.file(".teeplug.json", content)
            with self.assertRaises(TeeplugError) as caught:
                Settings(self.root, provider="auto")
            self.assertNotIn("never-store-this", str(caught.exception))

    def test_project_root_from_subdirectory(self):
        self.file(".git", "gitdir: somewhere")
        deep = self.root / "a" / "b"
        deep.mkdir(parents=True)
        self.assertEqual(project_root(deep), self.root)


class ReaderTests(Workspace):
    def test_cache_hit_and_content_invalidation(self):
        source = self.file("service.py")
        calls = []
        def worker(settings, instructions, message):
            calls.append(json.loads(message))
            return {"text": "service.py:1 defines UserService", "usage": {"input_tokens": 50}}
        args = ("bulk-read", "--root", str(self.root), "--question", "What is exported?", "--paths", str(source), "--json")
        rc, out, _ = self.run_cli(*args, worker=worker)
        first = json.loads(out)
        self.assertEqual(rc, 0)
        self.assertFalse(first["cache_hit"])
        self.assertEqual(calls[0]["documents"][0]["content"], "1: class UserService:\n2:     pass")
        self.assertNotIn("content", first["files"][0])
        rc, out, _ = self.run_cli(*args, worker=worker)
        self.assertTrue(json.loads(out)["cache_hit"])
        self.assertEqual(json.loads(out)["worker_usage"], {})
        self.assertEqual(len(calls), 1)
        source.write_text("class Changed: pass\n")
        rc, out, _ = self.run_cli(*args, worker=worker)
        self.assertEqual(rc, 0)
        self.assertEqual(len(calls), 2)
        self.assertNotEqual(first["files"][0]["sha256"], json.loads(out)["files"][0]["sha256"])

    def test_no_cache_makes_no_cache_files(self):
        source = self.file("x.py")
        rc, _, _ = self.run_cli("bulk-read", "--root", str(self.root), "--question", "x", "--paths", str(source), "--no-cache", worker=lambda *a: {"text": "x", "usage": {}})
        self.assertEqual(rc, 0)
        self.assertFalse((self.root / ".teeplug").exists())

    def test_binary_and_oversized_inputs(self):
        source = self.file("x")
        source.write_bytes(b"\x00bad")
        with self.assertRaises(TeeplugError):
            read_sources(self.root, [source], 100)
        source.write_text("123456")
        with self.assertRaises(TeeplugError):
            read_sources(self.root, [source], 5)

    def test_outside_root_and_git_rejected(self):
        inside_git = self.file(".git/config", "metadata")
        for source in (ROOT / "README.md", inside_git):
            with self.subTest(source=source), self.assertRaises(TeeplugError):
                read_sources(self.root, [source], 100000)

    def test_duplicate_source_appears_once(self):
        source = self.file("same.py")
        docs, size = read_sources(self.root, [source, source], 1000)
        self.assertEqual(len(docs), 1)
        self.assertEqual(size, source.stat().st_size)

    def test_over_budget_summary_not_returned(self):
        source = self.file("x.py")
        rc, out, _ = self.run_cli("bulk-read", "--root", str(self.root), "--max-output-tokens", "10", "--question", "x", "--paths", str(source), worker=lambda *a: {"text": "x" * 61, "usage": {}})
        self.assertEqual(rc, 1)
        self.assertEqual(out, "")


class WriterTests(Workspace):
    def writer_args(self, target, *extra):
        ref = self.file("reference.py", "# reference style\n")
        ctx = self.file("source.py", "def actual_api(): return 4\n")
        return ("code-write", "--root", str(self.root), "--spec", "Generate tests", "--reference", str(ref), "--context", str(ctx), "--target", str(target), *extra)

    def test_generation_to_disk_returns_only_metadata(self):
        target = self.root / "tests" / "generated.py"
        def worker(settings, instructions, message):
            request = json.loads(message)
            self.assertIn("actual_api", request["context"][0]["content"])
            self.assertIn("reference style", request["references"][0]["content"])
            self.assertEqual(settings.max_output_tokens, 32000)
            return {"text": "```python\nGENERATED_MARKER = 42\n```", "usage": {"output_tokens": 20}}
        rc, out, err = self.run_cli(*self.writer_args(target), worker=worker)
        self.assertEqual(rc, 0)
        self.assertEqual(target.read_text(), "GENERATED_MARKER = 42\n")
        self.assertNotIn("GENERATED_MARKER", out + err)
        self.assertEqual(json.loads(out)["sha256"], snapshot(target))

    def test_existing_target_requires_explicit_overwrite_before_call(self):
        target = self.file("existing.py", "ORIGINAL")
        with patch("teepluglib.cli.invoke") as worker:
            rc, _, _ = self.run_cli(*self.writer_args(target))
        self.assertEqual(rc, 1)
        worker.assert_not_called()
        self.assertEqual(target.read_text(), "ORIGINAL")

    def test_worker_failure_preserves_target(self):
        target = self.file("existing.py", "ORIGINAL")
        rc, out, _ = self.run_cli(*self.writer_args(target, "--overwrite"), worker=TeeplugError("incomplete generation"))
        self.assertEqual(rc, 1)
        self.assertEqual(out, "")
        self.assertEqual(target.read_text(), "ORIGINAL")

    def test_concurrent_edit_preserved(self):
        target = self.file("existing.py", "ORIGINAL")
        def worker(*args):
            target.write_text("USER EDIT")
            return {"text": "replacement", "usage": {}}
        rc, _, err = self.run_cli(*self.writer_args(target, "--overwrite"), worker=worker)
        self.assertEqual(rc, 1)
        self.assertIn("changed", err)
        self.assertEqual(target.read_text(), "USER EDIT")

    def test_new_file_race_preserved(self):
        target = self.root / "new.py"
        def worker(*args):
            target.write_text("OTHER PROCESS")
            return {"text": "replacement", "usage": {}}
        rc, _, _ = self.run_cli(*self.writer_args(target), worker=worker)
        self.assertEqual(rc, 1)
        self.assertEqual(target.read_text(), "OTHER PROCESS")

    def test_symlink_target_rejected(self):
        original = self.file("original.py", "ORIGINAL")
        target = self.root / "link.py"
        target.symlink_to(original)
        rc, _, _ = self.run_cli(*self.writer_args(target, "--overwrite"))
        self.assertEqual(rc, 1)
        self.assertEqual(original.read_text(), "ORIGINAL")

    def test_fences_inside_generated_file_preserved(self):
        code = '# Docs\n```python\nx = 1\n```\n'
        self.assertEqual(clean_code(code), code)

    def test_malformed_outer_fence_rejected(self):
        with self.assertRaises(TeeplugError):
            clean_code("```python\nx = 1")

    def test_file_mode_preserved(self):
        target = self.file("executable", "old")
        target.chmod(0o755)
        write_code(self.root, target, "new\n", snapshot(target), overwrite=True)
        self.assertEqual(target.stat().st_mode & 0o777, 0o755)


class HookTests(Workspace):
    def setUp(self):
        super().setUp()
        self.large = self.file("large file.py", "pass\n" * 400)

    def test_read_blocks_only_unbounded_large_reads(self):
        base = {"cwd": str(self.root), "tool_name": "Read", "tool_input": {"file_path": str(self.large)}}
        self.assertIsNotNone(route(base))
        base["tool_input"]["offset"] = 20
        self.assertIsNotNone(route(base))
        base["tool_input"]["limit"] = 50
        self.assertIsNone(route(base))

    def test_unconfigured_or_disabled_hooks_pass(self):
        event = {"cwd": str(self.root), "tool_name": "Read", "tool_input": {"file_path": str(self.large)}}
        with patch("teepluglib.hooks.Settings.executable", side_effect=TeeplugError("missing")):
            self.assertIsNone(route(event))
        with patch.dict(os.environ, {"TEEPLUG_ENABLED": "0"}):
            self.assertIsNone(route(event))

    def test_shell_read_cases(self):
        for command in ("cat 'large file.py'", "head -n 999 'large file.py'", "cat 'large file.py' | cat", "cat missing 'large file.py'", "cat 'large file.py' 2>errors.txt"):
            with self.subTest(command=command):
                self.assertIsNotNone(bash_reason(command, self.root, 350))

    def test_bounded_shell_and_redirect_cases(self):
        for command in ("head -n 20 'large file.py'", "tail -50 'large file.py'", "cat 'large file.py' | head -n 5", "cat 'large file.py' | wc -l", "cat 'large file.py' > copy.py", "git status"):
            with self.subTest(command=command):
                self.assertIsNone(bash_reason(command, self.root, 350))

    def test_subdirectory_and_cd(self):
        sub = self.root / "nested"
        sub.mkdir()
        self.assertIsNotNone(bash_reason("cd .. && cat 'large file.py'", sub, 350))

    def test_real_hook_json_protocol(self):
        event = {"cwd": str(self.root), "tool_name": "Bash", "tool_input": {"command": "cat 'large file.py'"}}
        result = subprocess.run([sys.executable, str(ROOT / "hooks/check-read.py")], input=json.dumps(event), capture_output=True, text=True, check=True)
        output = json.loads(result.stdout)["hookSpecificOutput"]
        self.assertEqual(output["permissionDecision"], "deny")
        self.assertIn(str(ROOT / "scripts/bulk-read"), output["permissionDecisionReason"])


if __name__ == "__main__":
    unittest.main()

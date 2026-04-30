# /// script
# requires-python = ">=3.11"
# dependencies = ["anthropic>=0.50", "openai>=1.0", "google-genai>=1.0", "pytest>=8.0"]
# ///
"""Unit tests for server.py — multi-provider LLM factory and prompt-building logic."""

import importlib
import os
import sys
import threading
import time
import types
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Import server module without executing _load_dotenv side-effects at import
# time, and without needing a real .env on disk.
# ---------------------------------------------------------------------------
# Patch os.environ so _load_dotenv() finds nothing to load.
# Strip any real LLM keys that may be present in the process environment so
# they don't leak into tests that patch os.environ with clear=True but rely
# on _load_dotenv() having already run at import time.
_LLM_KEY_NAMES = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY",
                  "GOOGLE_API_KEY", "GEMINI_API_KEY", "SUMMARY_MODEL")
_ORIG_ENVIRON = {k: v for k, v in os.environ.items() if k not in _LLM_KEY_NAMES}

# Add the project root to sys.path so "import server" works from any cwd.
_HERE = Path(__file__).parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))


def _import_server_clean(extra_env: dict | None = None):
    """Re-import server with a controlled environment, return the module."""
    env = {k: v for k, v in _ORIG_ENVIRON.items()
           if k not in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY",
                        "GOOGLE_API_KEY", "GEMINI_API_KEY", "SUMMARY_MODEL")}
    if extra_env:
        env.update(extra_env)
    with patch.dict(os.environ, env, clear=True):
        # Force re-execution of module-level code by removing cached module
        if "server" in sys.modules:
            del sys.modules["server"]
        import server as _srv
        return _srv


# ---------------------------------------------------------------------------
# Provider detection
# ---------------------------------------------------------------------------

class TestDetectProvider(unittest.TestCase):

    def _detect(self, env: dict):
        with patch.dict(os.environ, env, clear=True):
            if "server" in sys.modules:
                del sys.modules["server"]
            import server as srv
            # Remove any keys _load_dotenv() may have written from the real .env
            # file — they must not be visible when we call detect_provider().
            for k in _LLM_KEY_NAMES:
                if k not in env:
                    os.environ.pop(k, None)
            return srv.detect_provider()

    def test_anthropic_wins_when_set(self):
        provider, key = self._detect({
            "ANTHROPIC_API_KEY": "sk-ant-test",
            "OPENAI_API_KEY": "sk-openai-test",
            "GOOGLE_API_KEY": "google-test",
        })
        self.assertEqual(provider, "anthropic")
        self.assertEqual(key, "sk-ant-test")

    def test_openai_when_no_anthropic(self):
        provider, key = self._detect({
            "OPENAI_API_KEY": "sk-openai-test",
            "GOOGLE_API_KEY": "google-test",
        })
        self.assertEqual(provider, "openai")
        self.assertEqual(key, "sk-openai-test")

    def test_google_api_key(self):
        provider, key = self._detect({"GOOGLE_API_KEY": "google-test"})
        self.assertEqual(provider, "google")
        self.assertEqual(key, "google-test")

    def test_gemini_api_key_alias(self):
        provider, key = self._detect({"GEMINI_API_KEY": "gemini-test"})
        self.assertEqual(provider, "google")
        self.assertEqual(key, "gemini-test")

    def test_google_api_key_takes_precedence_over_gemini(self):
        provider, key = self._detect({
            "GOOGLE_API_KEY": "google-test",
            "GEMINI_API_KEY": "gemini-test",
        })
        self.assertEqual(provider, "google")
        self.assertEqual(key, "google-test")

    def test_no_keys_raises(self):
        with self.assertRaises(RuntimeError) as ctx:
            self._detect({})
        self.assertIn("No LLM credentials", str(ctx.exception))

    def test_whitespace_only_key_ignored(self):
        with self.assertRaises(RuntimeError):
            self._detect({"ANTHROPIC_API_KEY": "   "})


# ---------------------------------------------------------------------------
# make_llm_client — returns correct client type per provider
# ---------------------------------------------------------------------------

class TestMakeLLMClient(unittest.TestCase):

    def _call(self, env: dict):
        with patch.dict(os.environ, env, clear=True):
            if "server" in sys.modules:
                del sys.modules["server"]
            import server as srv
            for k in _LLM_KEY_NAMES:
                if k not in env:
                    os.environ.pop(k, None)
            return srv.make_llm_client()

    def test_anthropic_client_returned(self):
        mock_anthropic_module = MagicMock()
        mock_client_instance = MagicMock()
        mock_anthropic_module.Anthropic.return_value = mock_client_instance
        with patch.dict("sys.modules", {"anthropic": mock_anthropic_module}):
            client = self._call({"ANTHROPIC_API_KEY": "sk-ant-test"})
        mock_anthropic_module.Anthropic.assert_called_once_with(api_key="sk-ant-test")
        self.assertIs(client, mock_client_instance)

    def test_openai_adapter_returned(self):
        # Stub the openai module so the real package isn't required
        mock_openai_module = MagicMock()
        mock_openai_instance = MagicMock()
        mock_openai_module.OpenAI.return_value = mock_openai_instance
        with patch.dict("sys.modules", {"openai": mock_openai_module}):
            client = self._call({"OPENAI_API_KEY": "sk-openai-test"})
        mock_openai_module.OpenAI.assert_called_once_with(api_key="sk-openai-test")
        # Check by class name to avoid cross-module-reload identity issues
        self.assertEqual(type(client).__name__, "_OpenAIAdapter")

    def test_google_adapter_returned(self):
        mock_genai_module = MagicMock()
        mock_genai_types_module = MagicMock()
        mock_google = MagicMock()
        mock_google.genai = mock_genai_module
        with patch.dict("sys.modules", {
            "google": mock_google,
            "google.genai": mock_genai_module,
            "google.genai.types": mock_genai_types_module,
        }):
            client = self._call({"GOOGLE_API_KEY": "google-test"})
        self.assertEqual(type(client).__name__, "_GoogleAdapter")

    def test_no_key_raises(self):
        with self.assertRaises(RuntimeError):
            self._call({})


# ---------------------------------------------------------------------------
# Adapter interfaces — _OpenAIAdapter and _GoogleAdapter
# ---------------------------------------------------------------------------

class TestOpenAIAdapter(unittest.TestCase):

    def _make_adapter(self, api_key="sk-test"):
        mock_openai = MagicMock()
        mock_completion = MagicMock()
        mock_completion.choices[0].message.content = "Hello from OpenAI"
        mock_openai.OpenAI.return_value.chat.completions.create.return_value = mock_completion
        with patch.dict("sys.modules", {"openai": mock_openai}):
            if "server" in sys.modules:
                del sys.modules["server"]
            import server as srv
            adapter = srv._OpenAIAdapter(api_key=api_key)
            adapter._mock_openai = mock_openai
        return adapter

    def test_messages_is_self(self):
        adapter = self._make_adapter()
        self.assertIs(adapter.messages, adapter)

    def test_create_calls_chat_completions(self):
        adapter = self._make_adapter()
        result = adapter.create(
            model="gpt-4o",
            max_tokens=100,
            messages=[{"role": "user", "content": "hi"}],
        )
        self.assertEqual(result.content[0].text, "Hello from OpenAI")

    def test_create_returns_fake_response(self):
        adapter = self._make_adapter()
        result = adapter.create(model="gpt-4o", max_tokens=10,
                                messages=[{"role": "user", "content": "hi"}])
        self.assertEqual(type(result).__name__, "_FakeResponse")


class TestGoogleAdapter(unittest.TestCase):
    """Tests for _GoogleAdapter.

    Because _GoogleAdapter.create() imports google.genai at call-time we keep
    the sys.modules patch active for the duration of each test by using
    patch.dict as a context manager held open via setUp/tearDown.
    """

    def setUp(self):
        self.mock_genai = MagicMock()
        self.mock_types = MagicMock()
        self.mock_response = MagicMock()
        self.mock_response.text = "Hello from Gemini"
        self.mock_genai.Client.return_value.models.generate_content.return_value = (
            self.mock_response
        )
        # Make top-level `google` mock's .genai attribute point to our mock_genai
        # so `from google import genai` resolves to the same object as
        # sys.modules["google.genai"].
        mock_google = MagicMock()
        mock_google.genai = self.mock_genai
        self._patcher = patch.dict("sys.modules", {
            "google": mock_google,
            "google.genai": self.mock_genai,
            "google.genai.types": self.mock_types,
        })
        self._patcher.start()

        if "server" in sys.modules:
            del sys.modules["server"]
        with patch.dict(os.environ, {"GOOGLE_API_KEY": "google-test"}, clear=True):
            import server as srv
            self.adapter = srv._GoogleAdapter(api_key="google-test")

    def tearDown(self):
        self._patcher.stop()
        if "server" in sys.modules:
            del sys.modules["server"]

    def test_messages_is_self(self):
        self.assertIs(self.adapter.messages, self.adapter)

    def test_create_returns_text(self):
        result = self.adapter.create(
            model="gemini-2.5-flash",
            max_tokens=100,
            messages=[{"role": "user", "content": "hi"}],
        )
        self.assertEqual(result.content[0].text, "Hello from Gemini")

    def test_system_message_prepended(self):
        """System messages should be included in the prompt sent to Gemini."""
        self.adapter.create(
            model="gemini-2.5-flash",
            max_tokens=100,
            messages=[
                {"role": "system", "content": "Be concise."},
                {"role": "user", "content": "Hello"},
            ],
        )
        call_args = self.mock_genai.Client.return_value.models.generate_content.call_args
        prompt = call_args.kwargs.get("contents") or call_args.args[1]
        self.assertIn("[System]", prompt)
        self.assertIn("Be concise.", prompt)


# ---------------------------------------------------------------------------
# Default model selection
# ---------------------------------------------------------------------------

class TestDefaultModels(unittest.TestCase):

    def _call_llm_model_used(self, env: dict, mock_client):
        with patch.dict(os.environ, env, clear=True):
            if "server" in sys.modules:
                del sys.modules["server"]
            import server as srv
            with patch.object(srv, "make_llm_client", return_value=mock_client):
                srv.call_llm("test prompt")
        return mock_client.messages.create.call_args

    def _make_mock_client(self, text="summary"):
        mc = MagicMock()
        mc.messages.create.return_value = MagicMock(
            content=[MagicMock(text=text)]
        )
        return mc

    def test_anthropic_default_model(self):
        mc = self._make_mock_client()
        call_args = self._call_llm_model_used({"ANTHROPIC_API_KEY": "sk-ant-test"}, mc)
        self.assertEqual(call_args.kwargs.get("model") or call_args.args[0],
                         "claude-sonnet-4-6")

    def test_openai_default_model(self):
        mc = self._make_mock_client()
        call_args = self._call_llm_model_used({"OPENAI_API_KEY": "sk-openai-test"}, mc)
        model = call_args.kwargs.get("model") or call_args.args[0]
        self.assertEqual(model, "gpt-4o")

    def test_google_default_model(self):
        mc = self._make_mock_client()
        call_args = self._call_llm_model_used({"GOOGLE_API_KEY": "google-test"}, mc)
        model = call_args.kwargs.get("model") or call_args.args[0]
        self.assertEqual(model, "gemini-2.5-flash")

    def test_summary_model_env_override(self):
        mc = self._make_mock_client()
        call_args = self._call_llm_model_used(
            {"ANTHROPIC_API_KEY": "sk-ant-test", "SUMMARY_MODEL": "claude-opus-4-7"}, mc
        )
        model = call_args.kwargs.get("model") or call_args.args[0]
        self.assertEqual(model, "claude-opus-4-7")


# ---------------------------------------------------------------------------
# Prompt building (pre-existing logic)
# ---------------------------------------------------------------------------

class TestBuildTaskText(unittest.TestCase):

    def setUp(self):
        if "server" in sys.modules:
            del sys.modules["server"]
        with patch.dict(os.environ, {}, clear=True):
            import server as srv
            self.srv = srv

    def test_simple_task(self):
        tasks = [{"text": "Buy milk", "status": "pending"}]
        result = self.srv.build_task_text(tasks)
        self.assertIn("Buy milk", result)
        self.assertIn("○", result)

    def test_done_status(self):
        tasks = [{"text": "Write report", "status": "done"}]
        result = self.srv.build_task_text(tasks)
        self.assertIn("✓", result)

    def test_partial_status(self):
        tasks = [{"text": "Write report", "status": "partial"}]
        result = self.srv.build_task_text(tasks)
        self.assertIn("◑", result)

    def test_excluded_task_omitted(self):
        tasks = [
            {"text": "Keep me", "status": "pending"},
            {"text": "Exclude me", "status": "done", "excludeFromSummary": True},
        ]
        result = self.srv.build_task_text(tasks)
        self.assertIn("Keep me", result)
        self.assertNotIn("Exclude me", result)

    def test_important_flag(self):
        tasks = [{"text": "Critical", "status": "pending", "important": True}]
        result = self.srv.build_task_text(tasks)
        self.assertIn("IMPORTANT", result)

    def test_recurring_flag(self):
        tasks = [{"text": "Daily standup", "status": "pending",
                  "recurring": True, "closed": False}]
        result = self.srv.build_task_text(tasks)
        self.assertIn("RECURRING", result)

    def test_closed_recurring_no_flag(self):
        tasks = [{"text": "Old task", "status": "done",
                  "recurring": True, "closed": True}]
        result = self.srv.build_task_text(tasks)
        self.assertNotIn("RECURRING", result)

    def test_nested_tasks_indented(self):
        tasks = [{
            "text": "Parent",
            "status": "pending",
            "children": [{"text": "Child", "status": "done"}],
        }]
        result = self.srv.build_task_text(tasks)
        lines = result.splitlines()
        parent_line = next(l for l in lines if "Parent" in l)
        child_line  = next(l for l in lines if "Child" in l)
        self.assertLess(len(parent_line) - len(parent_line.lstrip()),
                        len(child_line)  - len(child_line.lstrip()))

    def test_notes_included(self):
        tasks = [{
            "text": "Task",
            "status": "pending",
            "context": {"notes": [{"text": "Important note"}], "links": [], "attachments": []},
        }]
        result = self.srv.build_task_text(tasks)
        self.assertIn("Important note", result)

    def test_link_included(self):
        tasks = [{
            "text": "Task",
            "status": "pending",
            "context": {
                "notes": [],
                "links": [{"type": "jira", "label": "PROJ-1", "url": "https://jira.example.com/PROJ-1"}],
                "attachments": [],
            },
        }]
        result = self.srv.build_task_text(tasks)
        self.assertIn("jira", result)
        self.assertIn("https://jira.example.com/PROJ-1", result)


class TestCountTaskStats(unittest.TestCase):

    def setUp(self):
        if "server" in sys.modules:
            del sys.modules["server"]
        with patch.dict(os.environ, {}, clear=True):
            import server as srv
            self.srv = srv

    def test_basic_counts(self):
        tasks = [
            {"status": "done"},
            {"status": "partial"},
            {"status": "pending"},
        ]
        stats = self.srv.count_task_stats(tasks)
        self.assertEqual(stats, {"done": 1, "partial": 1, "pending": 1})

    def test_default_status_is_pending(self):
        tasks = [{"text": "No status field"}]
        stats = self.srv.count_task_stats(tasks)
        self.assertEqual(stats["pending"], 1)

    def test_excluded_not_counted(self):
        tasks = [
            {"status": "done", "excludeFromSummary": True},
            {"status": "pending"},
        ]
        stats = self.srv.count_task_stats(tasks)
        self.assertEqual(stats["done"], 0)
        self.assertEqual(stats["pending"], 1)

    def test_nested_children_counted(self):
        tasks = [{
            "status": "done",
            "children": [
                {"status": "done"},
                {"status": "pending"},
            ],
        }]
        stats = self.srv.count_task_stats(tasks)
        self.assertEqual(stats["done"], 2)
        self.assertEqual(stats["pending"], 1)


class TestBuildPrompt(unittest.TestCase):

    def setUp(self):
        if "server" in sys.modules:
            del sys.modules["server"]
        with patch.dict(os.environ, {}, clear=True):
            import server as srv
            self.srv = srv

    def _payload(self, tasks=None, topics=None, period="week"):
        return {
            "fromKey": "2026-04-01",
            "toKey": "2026-04-07",
            "period": period,
            "topics": topics or [],
            "days": {
                "2026-04-01": {"tasks": tasks or [{"text": "Task A", "status": "done"}]}
            },
        }

    def test_empty_days_returns_empty_prompt(self):
        payload = {
            "fromKey": "2026-04-01",
            "toKey": "2026-04-07",
            "period": "week",
            "topics": [],
            "days": {},
        }
        prompt, stats = self.srv.build_prompt(payload)
        self.assertEqual(prompt, "")
        self.assertEqual(stats, {"done": 0, "partial": 0, "pending": 0})

    def test_prompt_contains_task_text(self):
        prompt, _ = self.srv.build_prompt(self._payload())
        self.assertIn("Task A", prompt)

    def test_prompt_contains_date_range(self):
        prompt, _ = self.srv.build_prompt(self._payload())
        self.assertIn("2026-04-01", prompt)
        self.assertIn("2026-04-07", prompt)

    def test_stats_correct(self):
        payload = self._payload(tasks=[
            {"text": "A", "status": "done"},
            {"text": "B", "status": "partial"},
            {"text": "C", "status": "pending"},
        ])
        _, stats = self.srv.build_prompt(payload)
        self.assertEqual(stats, {"done": 1, "partial": 1, "pending": 1})

    def test_topics_included_in_prompt(self):
        payload = self._payload(topics=["Gen AI", "Data Quality"])
        prompt, _ = self.srv.build_prompt(payload)
        self.assertIn("Gen AI", prompt)
        self.assertIn("Data Quality", prompt)

    def test_no_topics_uses_grouping_instruction(self):
        prompt, _ = self.srv.build_prompt(self._payload(topics=[]))
        self.assertIn("logical groupings", prompt)

    def test_all_excluded_returns_empty(self):
        payload = self._payload(tasks=[
            {"text": "Hidden", "status": "done", "excludeFromSummary": True}
        ])
        prompt, _ = self.srv.build_prompt(payload)
        self.assertEqual(prompt, "")

    def test_period_labels(self):
        for period, label in [
            ("week", "this week"),
            ("month", "this month"),
            ("quarter", "this quarter"),
            ("year", "this year"),
            ("custom", "the selected period"),
        ]:
            prompt, _ = self.srv.build_prompt(self._payload(period=period))
            self.assertIn(label, prompt, f"Missing label for period={period!r}")


# ---------------------------------------------------------------------------
# Concurrency — ThreadingHTTPServer handles requests in parallel
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Stats line — server-side construction and prompt isolation
# ---------------------------------------------------------------------------

class TestBuildStatsLine(unittest.TestCase):

    def setUp(self):
        if "server" in sys.modules:
            del sys.modules["server"]
        with patch.dict(os.environ, {}, clear=True):
            import server as srv
            self.srv = srv

    def test_format(self):
        line = self.srv.build_stats_line(
            "2026-04-28", "2026-04-29",
            {"done": 3, "partial": 1, "pending": 5},
        )
        self.assertEqual(
            line,
            "Period: 2026-04-28 to 2026-04-29 · ✓ 3 done · ◑ 1 partial · ○ 5 pending",
        )

    def test_zero_counts(self):
        line = self.srv.build_stats_line(
            "2026-04-01", "2026-04-07",
            {"done": 0, "partial": 0, "pending": 0},
        )
        self.assertIn("✓ 0 done", line)
        self.assertIn("◑ 0 partial", line)
        self.assertIn("○ 0 pending", line)

    def test_contains_date_range(self):
        line = self.srv.build_stats_line(
            "2026-01-01", "2026-03-31",
            {"done": 10, "partial": 2, "pending": 1},
        )
        self.assertIn("2026-01-01", line)
        self.assertIn("2026-03-31", line)

    def test_prompt_does_not_contain_stats_line(self):
        """The LLM prompt must NOT contain the pre-filled stats line so that
        a token-limit truncation cannot cut off the stats."""
        payload = {
            "fromKey": "2026-04-01",
            "toKey": "2026-04-07",
            "period": "week",
            "topics": [],
            "days": {"2026-04-01": {"tasks": [{"text": "T", "status": "done",
                                               "children": []}]}},
        }
        prompt, stats = self.srv.build_prompt(payload)
        # The old instruction embedded the full stats line in the prompt, e.g.:
        #   "Period: 2026-04-01 to 2026-04-07 · ✓ 1 done · ◑ 0 partial · ○ 0 pending"
        # That must no longer appear — the server appends it after the LLM call.
        stats_line = self.srv.build_stats_line("2026-04-01", "2026-04-07", stats)
        self.assertNotIn(stats_line, prompt)
        # Also confirm the "End with exactly this line" instruction is gone
        self.assertNotIn("End with exactly this line", prompt)

    def test_call_llm_uses_4096_max_tokens(self):
        """call_llm must request 4096 max_tokens, not the old 2048."""
        mc = MagicMock()
        mc.messages.create.return_value = MagicMock(content=[MagicMock(text="prose")])
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test"}, clear=True):
            if "server" in sys.modules:
                del sys.modules["server"]
            import server as srv
            with patch.object(srv, "make_llm_client", return_value=mc):
                srv.call_llm("test prompt")
        call_kwargs = mc.messages.create.call_args.kwargs
        max_tok = call_kwargs.get("max_tokens") or mc.messages.create.call_args.args[1]
        self.assertEqual(max_tok, 4096)


class TestThreadingServer(unittest.TestCase):
    """Verify that a slow /summarize request does not block a concurrent
    /months request.  With the old single-threaded HTTPServer, the second
    request would time out; with ThreadingHTTPServer both complete quickly."""

    @classmethod
    def setUpClass(cls):
        import socket as _sock
        # Find a free port for the test server
        with _sock.socket(_sock.AF_INET, _sock.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            cls.port = s.getsockname()[1]

        # Import server in a clean env (no real API keys needed — we mock LLM)
        if "server" in sys.modules:
            del sys.modules["server"]
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test"}, clear=True):
            import server as srv

        from http.server import ThreadingHTTPServer as _THTS
        cls.ThreadingHTTPServer = _THTS

        # Patch call_llm to sleep for 1 s (simulates a slow LLM call)
        cls._llm_patch = patch.object(srv, "call_llm",
                                      side_effect=lambda p: (time.sleep(1), "ok")[1])
        cls._llm_patch.start()

        cls.server = _THTS(("127.0.0.1", cls.port), srv.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls._llm_patch.stop()
        cls.server.shutdown()

    def _get(self, path, timeout=5):
        url = f"http://127.0.0.1:{self.port}{path}"
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status

    def _post_summarize(self):
        import json as _json
        url = f"http://127.0.0.1:{self.port}/summarize"
        payload = _json.dumps({
            "fromKey": "2026-04-01", "toKey": "2026-04-01",
            "period": "custom", "topics": [],
            "days": {"2026-04-01": {"tasks": [{"text": "T", "status": "done",
                                               "children": []}]}},
        }).encode()
        req = urllib.request.Request(url, data=payload,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status

    def test_concurrent_requests_do_not_block(self):
        """A slow /summarize must not prevent /months from responding promptly."""
        results = {}

        def run_summarize():
            results["summarize"] = self._post_summarize()

        def run_months():
            # Wait briefly so /summarize is definitely in-flight first
            time.sleep(0.1)
            t0 = time.monotonic()
            status = self._get("/months")
            elapsed = time.monotonic() - t0
            results["months_status"] = status
            results["months_elapsed"] = elapsed

        t1 = threading.Thread(target=run_summarize)
        t2 = threading.Thread(target=run_months)
        t1.start(); t2.start()
        t1.join(timeout=15); t2.join(timeout=5)

        self.assertEqual(results.get("summarize"), 200)
        self.assertEqual(results.get("months_status"), 200)
        # /months should complete in well under 1 s even though /summarize
        # takes 1 s — proves the server is handling them concurrently.
        self.assertLess(results.get("months_elapsed", 999), 0.8,
                        "/months was blocked by concurrent /summarize — "
                        "server is not threading requests")

    def test_server_uses_threading_http_server(self):
        """Confirm the server module uses ThreadingHTTPServer, not plain HTTPServer."""
        if "server" in sys.modules:
            del sys.modules["server"]
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test"}, clear=True):
            import server as srv
        import inspect
        src = inspect.getsource(srv)
        self.assertIn("ThreadingHTTPServer", src)
        # Ensure the instantiation site uses ThreadingHTTPServer, not HTTPServer
        import re as _re
        # Should find ThreadingHTTPServer(...) but NOT a bare HTTPServer(...)
        # that is not prefixed with "Threading"
        bare = _re.findall(r'(?<!Threading)HTTPServer\s*\(', src)
        self.assertEqual(bare, [], f"Found bare HTTPServer() instantiation: {bare}")


if __name__ == "__main__":
    unittest.main()

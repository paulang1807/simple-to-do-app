# /// script
# requires-python = ">=3.11"
# dependencies = ["anthropic>=0.50", "openai>=1.0", "google-genai>=1.0"]
# ///
"""
Checkpoint — file server
Serves index.html and handles GET/PUT /data/YYYY-MM, GET /months, GET/PUT /archive, POST /summarize.
Data is stored as data/YYYY-MM.json, one file per month.
Archived tasks are stored as data/archive.json (flat list of task objects).

LLM auth (first match wins):
  1. Anthropic  — ANTHROPIC_API_KEY
  2. OpenAI     — OPENAI_API_KEY
  3. Google     — GOOGLE_API_KEY  (or GEMINI_API_KEY)

Run with:  uv run server.py
"""

import argparse
import json
import os
import re
import socket
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DEFAULT_PORT = 3456
def get_app_paths():
    """Return (base_dir, data_dir) based on environment or defaults."""
    # If running in a PyInstaller bundle, sys._MEIPASS is the path to resources
    # If running in a PyInstaller bundle, sys._MEIPASS is the path to resources
    if hasattr(sys, "_MEIPASS"):
        base_dir = Path(sys._MEIPASS).resolve()
        # On macOS, if index.html is not in base_dir, check Resources
        if not (base_dir / "index.html").exists():
            # Try to find it in the bundle's Resources
            resources_dir = base_dir.parent / "Resources"
            if (resources_dir / "index.html").exists():
                base_dir = resources_dir
    else:
        base_dir = Path(__file__).parent.resolve()

    # Data directory priority:
    # 1. Environment variable (set by launcher)
    # 2. Standard macOS Application Support (if requested via env)
    # 3. Local 'data' folder
    env_data_dir = os.getenv("APP_DATA_DIR")
    if env_data_dir:
        data_dir = Path(env_data_dir)
    else:
        data_dir = base_dir / "data"

    data_dir.mkdir(parents=True, exist_ok=True)
    return base_dir, data_dir

BASE_DIR, DATA_DIR = get_app_paths()
ARCHIVE_FILE = DATA_DIR / "archive.json"


# ──────────────────────────────────────────────────────────────────────────────
# .env loader
# ──────────────────────────────────────────────────────────────────────────────

def _load_dotenv():
    """Load .env file into os.environ (does not override existing env vars)."""
    # Look for .env in the same place as data, or in the base dir
    search_paths = [DATA_DIR / ".env", BASE_DIR / ".env"]
    for env_file in search_paths:
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if k and k not in os.environ:
                    os.environ[k] = v
            break # Stop after first .env found

_load_dotenv()


# ──────────────────────────────────────────────────────────────────────────────
# LLM provider adapters
# ──────────────────────────────────────────────────────────────────────────────

class _FakeContent:
    def __init__(self, text: str):
        self.text = text


class _FakeResponse:
    def __init__(self, text: str):
        self.content = [_FakeContent(text)]


class _OpenAIAdapter:
    """Wraps the OpenAI client to expose the same .messages.create() interface."""

    def __init__(self, api_key: str, base_url: str | None = None):
        from openai import OpenAI
        kwargs: dict = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        self._client = OpenAI(**kwargs)
        self.messages = self

    def create(self, model: str, max_tokens: int, messages: list, **_) -> _FakeResponse:
        resp = self._client.chat.completions.create(
            model=model,
            messages=messages,
            max_tokens=max_tokens,
        )
        text = resp.choices[0].message.content or ""
        return _FakeResponse(text)


class _GoogleAdapter:
    """Wraps google-genai to expose the same .messages.create() interface."""

    def __init__(self, api_key: str):
        from google import genai
        self._genai = genai
        self._client = genai.Client(api_key=api_key)
        self.messages = self

    def create(self, model: str, max_tokens: int, messages: list, **_) -> _FakeResponse:
        from google.genai import types as genai_types
        # Convert OpenAI-style messages to a single prompt string for Gemini
        parts = []
        for m in messages:
            role = m.get("role", "user")
            content = m.get("content", "")
            if role == "system":
                parts.append(f"[System]: {content}")
            else:
                parts.append(content)
        prompt = "\n\n".join(parts)

        response = self._client.models.generate_content(
            model=model,
            contents=prompt,
            config=genai_types.GenerateContentConfig(max_output_tokens=max_tokens),
        )
        text = response.text or ""
        return _FakeResponse(text)


# ──────────────────────────────────────────────────────────────────────────────
# LLM client factory
# ──────────────────────────────────────────────────────────────────────────────

# Default model names per provider (overridable via SUMMARY_MODEL env var)
PROVIDER_DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-4-6",
    "openai":    "gpt-4o",
    "google":    "gemini-2.5-flash",
}


def detect_provider() -> tuple[str, str]:
    """Return (provider_name, api_key) for the first configured provider.

    Priority: anthropic → openai → google.
    Raises RuntimeError if none are configured.
    """

    anthropic_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if anthropic_key:
        return "anthropic", anthropic_key

    openai_key = os.getenv("OPENAI_API_KEY", "").strip()
    if openai_key:
        return "openai", openai_key

    google_key = (os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY") or "").strip()
    if google_key:
        return "google", google_key

    raise RuntimeError(
        "No LLM credentials found.\n\n"
        "Add one of the following to a .env file in this directory:\n"
        "  ANTHROPIC_API_KEY=sk-ant-...   (https://console.anthropic.com)\n"
        "  OPENAI_API_KEY=sk-...          (https://platform.openai.com)\n"
        "  GOOGLE_API_KEY=...             (https://aistudio.google.com)\n"
    )


def make_llm_client():
    """Return a provider-agnostic LLM client.

    All returned clients expose:  client.messages.create(model, max_tokens, messages)
    returning an object with .content[0].text
    """
    provider, api_key = detect_provider()

    if provider == "anthropic":
        import anthropic
        return anthropic.Anthropic(api_key=api_key)

    if provider == "openai":
        return _OpenAIAdapter(api_key=api_key)

    if provider == "google":
        return _GoogleAdapter(api_key=api_key)

    raise RuntimeError(f"Unknown provider: {provider}")


# ──────────────────────────────────────────────────────────────────────────────
# Port helpers
# ──────────────────────────────────────────────────────────────────────────────

def find_free_port(start: int, attempts: int = 10) -> int:
    for port in range(start, start + attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise OSError(f"No free port found in range {start}–{start + attempts - 1}")


MONTH_RE = re.compile(r"^\d{4}-\d{2}$")

MIME = {
    ".html": "text/html; charset=utf-8",
    ".js":   "application/javascript",
    ".css":  "text/css",
    ".json": "application/json",
    ".ico":  "image/x-icon",
    ".png":  "image/png",
}


# ──────────────────────────────────────────────────────────────────────────────
# Summary prompt builder
# ──────────────────────────────────────────────────────────────────────────────

def build_task_text(tasks: list, depth: int = 0) -> str:
    lines = []
    indent = "  " * depth
    for t in tasks:
        if t.get("excludeFromSummary"):
            continue
        status = t.get("status", "pending")
        status_label = {"done": "✓", "partial": "◑", "pending": "○"}.get(status, "○")
        flags = []
        if t.get("important"):
            flags.append("IMPORTANT")
        if t.get("recurring") and not t.get("closed"):
            flags.append("RECURRING")
        flag_str = f" [{', '.join(flags)}]" if flags else ""
        lines.append(f"{indent}{status_label} {t['text']}{flag_str}")

        ctx = t.get("context", {})
        notes = [n["text"] for n in ctx.get("notes", []) if n.get("text", "").strip()]
        for note in notes:
            lines.append(f"{indent}  Note: {note}")
        for lnk in ctx.get("links", []):
            label = lnk.get("label") or lnk.get("type", "")
            lines.append(f"{indent}  Link ({lnk.get('type','')}: {label}) — {lnk.get('url','')}")
        for att in ctx.get("attachments", []):
            lines.append(f"{indent}  Attachment: {att.get('name','')}")

        if t.get("children"):
            child_text = build_task_text(t["children"], depth + 1)
            if child_text:
                lines.append(child_text)
    return "\n".join(lines)


def count_task_stats(tasks: list) -> dict:
    done = partial = pending = 0
    def walk(items):
        nonlocal done, partial, pending
        for t in items:
            if t.get("excludeFromSummary"):
                continue
            s = t.get("status", "pending")
            if s == "done":      done += 1
            elif s == "partial": partial += 1
            else:                pending += 1
            walk(t.get("children", []))
    walk(tasks)
    return {"done": done, "partial": partial, "pending": pending}


def build_prompt(payload: dict) -> tuple[str, dict]:
    """Returns (prompt_text, stats_dict)."""
    from_key  = payload.get("fromKey", "")
    to_key    = payload.get("toKey", "")
    period    = payload.get("period", "custom")
    topics    = [t.strip() for t in payload.get("topics", []) if t.strip()]
    days_data = payload.get("days", {})

    period_label = {
        "week": "this week", "month": "this month",
        "quarter": "this quarter", "year": "this year",
        "custom": "the selected period",
    }.get(period, "the selected period")

    task_dump_parts = []
    total_stats = {"done": 0, "partial": 0, "pending": 0}

    for day_key in sorted(days_data.keys()):
        tasks = days_data[day_key].get("tasks", [])
        if not tasks:
            continue
        text = build_task_text(tasks)
        if text.strip():
            task_dump_parts.append(f"### {day_key}\n{text}")
        stats = count_task_stats(tasks)
        for k in total_stats:
            total_stats[k] += stats[k]

    if not task_dump_parts:
        return "", total_stats

    task_dump = "\n\n".join(task_dump_parts)

    if topics:
        topic_list = "\n".join(f"- {t}" for t in topics)
        topic_instruction = f"""
The user wants the summary focused on these specific topics:
{topic_list}

Write one paragraph per topic. If there is insufficient information in the task data to address a topic, write exactly: "Insufficient information to summarise [topic name]." Do not invent or infer anything not present in the task data. If no tasks relate to a topic at all, say so explicitly.
"""
    else:
        topic_instruction = """
No specific topics were requested. Identify logical groupings from the tasks (by project, theme, or area) and write one paragraph per group. Name each group based on what the tasks actually describe.
"""

    prompt = f"""You are generating a professional work summary for the period {period_label} ({from_key} to {to_key}).

The raw task data is below. Status symbols: ✓ = done, ◑ = partial/in-progress, ○ = pending/incomplete.
IMPORTANT and RECURRING flags indicate priority or repeating tasks.
Context includes notes, links (with type and label), and attachments.

TASK DATA:
{task_dump}

INSTRUCTIONS:
- Write a concise, professional summary strictly based on the task data above. Do not hallucinate or add any detail not present.
- Focus on what was actually accomplished. Mention partial or in-progress work where relevant. Omit pending tasks unless they add important context.
- Be specific — use the actual names, labels, and notes from the data. Avoid vague generalisations.
- Write in flowing prose paragraphs (no bullet lists, no markdown headers, no bold/italic).
- Each paragraph should cover one logical group of related work.
- Do not add a preamble, sign-off, or meta-commentary — write only the summary paragraphs.
{topic_instruction}"""

    return prompt, total_stats


def build_stats_line(from_key: str, to_key: str, stats: dict) -> str:
    """Return the canonical stats footer line. Always built server-side so it
    is never lost to LLM token limits or model formatting quirks."""
    return (
        f"Period: {from_key} to {to_key} · "
        f"✓ {stats['done']} done · "
        f"◑ {stats['partial']} partial · "
        f"○ {stats['pending']} pending"
    )


def call_llm(prompt: str) -> str:
    provider, _ = detect_provider()
    default_model = PROVIDER_DEFAULT_MODELS.get(provider, "claude-sonnet-4-6")
    model = os.getenv("SUMMARY_MODEL", default_model)
    client = make_llm_client()
    message = client.messages.create(
        model=model,
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text


# ──────────────────────────────────────────────────────────────────────────────
# HTTP handler
# ──────────────────────────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(f"  {self.address_string()} {fmt % args}")

    def send_json(self, code, data):
        body = json.dumps(data, indent=2).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def send_text(self, code, text):
        body = text.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, PUT, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        path = self.path.split("?")[0]

        if path == "/months":
            months = sorted(
                f.stem for f in DATA_DIR.glob("*.json")
                if MONTH_RE.match(f.stem)
            )
            self.send_json(200, months)
            return

        if path.startswith("/data/"):
            month = path[6:]
            if not MONTH_RE.match(month):
                self.send_text(400, "Bad month format")
                return
            data_file = DATA_DIR / f"{month}.json"
            if not data_file.exists():
                self.send_json(200, {})
                return
            self.send_json(200, json.loads(data_file.read_text()))
            return

        if path == "/archive":
            if ARCHIVE_FILE.exists():
                self.send_json(200, json.loads(ARCHIVE_FILE.read_text()))
            else:
                self.send_json(200, [])
            return

        if path == "/":
            path = "/index.html"
        file_path = (BASE_DIR / path.lstrip("/")).resolve()
        # Security: ensure file is within BASE_DIR or is a bundled asset
        # We allow files within BASE_DIR. If in a bundle, we are less restrictive 
        # about the exact prefix as long as it's part of the bundle.
        is_safe = str(file_path).startswith(str(BASE_DIR))
        if not is_safe and hasattr(sys, "_MEIPASS"):
            # Allow anything in the bundle's parent (Contents)
            is_safe = str(file_path).startswith(str(BASE_DIR.parent))

        if not is_safe:
            self.send_text(403, f"Forbidden: {file_path} is not in {BASE_DIR}")
            return
        if not file_path.exists() or not file_path.is_file():
            self.send_text(404, "Not found")
            return
        mime = MIME.get(file_path.suffix, "application/octet-stream")
        body = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", len(body))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_PUT(self):
        path = self.path.split("?")[0]

        if path.startswith("/data/"):
            month = path[6:]
            if not MONTH_RE.match(month):
                self.send_text(400, "Bad month format")
                return
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                data = json.loads(body)
            except json.JSONDecodeError as e:
                self.send_text(400, f"Invalid JSON: {e}")
                return
            data_file = DATA_DIR / f"{month}.json"
            data_file.write_text(json.dumps(data, indent=2, ensure_ascii=False))
            self.send_json(200, {"ok": True})
            return

        if path == "/archive":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                data = json.loads(body)
            except json.JSONDecodeError as e:
                self.send_text(400, f"Invalid JSON: {e}")
                return
            if not isinstance(data, list):
                self.send_text(400, "Expected a JSON array")
                return
            ARCHIVE_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False))
            self.send_json(200, {"ok": True})
            return

        self.send_text(404, "Not found")

    def do_POST(self):
        path = self.path.split("?")[0]

        if path == "/summarize":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                payload = json.loads(body)
            except json.JSONDecodeError as e:
                self.send_json(400, {"error": f"Invalid JSON: {e}"})
                return

            try:
                prompt, stats = build_prompt(payload)
            except Exception as e:
                self.send_json(500, {"error": f"Failed to build prompt: {e}"})
                return

            if not prompt:
                self.send_json(200, {"summary": "No tasks found in this period."})
                return

            try:
                prose = call_llm(prompt)
                stats_line = build_stats_line(
                    payload.get("fromKey", ""),
                    payload.get("toKey", ""),
                    stats,
                )
                summary = f"{prose.rstrip()}\n\n{stats_line}"
                self.send_json(200, {"summary": summary})
            except RuntimeError as e:
                self.send_json(503, {"error": str(e)})
            except Exception as e:
                self.send_json(500, {"error": f"LLM call failed: {e}"})
            return

        self.send_text(404, "Not found")


# ──────────────────────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Daily Task Manager server")
    parser.add_argument(
        "--port", "-p",
        type=int,
        default=DEFAULT_PORT,
        help=f"Port to listen on (default: {DEFAULT_PORT}). "
             "If the port is busy, the next available port is used automatically.",
    )
    args = parser.parse_args()

    try:
        provider, _ = detect_provider()
        default_model = PROVIDER_DEFAULT_MODELS.get(provider, "")
        model = os.getenv("SUMMARY_MODEL", default_model)
        print(f"  ✓  {provider.capitalize()} API key found — summaries will use {model}.")
    except RuntimeError:
        print("  ⚠️  No LLM API key found — AI summaries will return an error.")
        print("     Add one of the following to a .env file in this directory:")
        print("       ANTHROPIC_API_KEY=sk-ant-...   (https://console.anthropic.com)")
        print("       OPENAI_API_KEY=sk-...          (https://platform.openai.com)")
        print("       GOOGLE_API_KEY=...             (https://aistudio.google.com)\n")

    port = find_free_port(args.port)
    if port != args.port:
        print(f"  Port {args.port} is in use — using port {port} instead.")

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"\n  Daily Task Manager is running!")
    print(f"  Open this URL in your browser:\n")
    print(f"      http://localhost:{port}\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        sys.exit(0)

# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""
Daily Task Manager — file server
Serves index.html and handles GET/PUT /data/YYYY-MM and GET /months.
Data is stored as data/YYYY-MM.json, one file per month.
Run with:  uv run server.py
"""

import argparse
import json
import re
import socket
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

DEFAULT_PORT = 3456
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)


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
        self.send_header("Access-Control-Allow-Methods", "GET, PUT, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        path = self.path.split("?")[0]

        # GET /months — list months that have data files
        if path == "/months":
            months = sorted(
                f.stem for f in DATA_DIR.glob("*.json")
                if MONTH_RE.match(f.stem)
            )
            self.send_json(200, months)
            return

        # GET /data/YYYY-MM — read month file
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

        # Serve static files
        if path == "/":
            path = "/index.html"
        file_path = BASE_DIR / path.lstrip("/")
        # Prevent directory traversal
        try:
            file_path.resolve().relative_to(BASE_DIR.resolve())
        except ValueError:
            self.send_text(403, "Forbidden")
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

        # PUT /data/YYYY-MM — write month file
        if path.startswith("/data/"):
            month = path[6:]
            if not MONTH_RE.match(month):
                self.send_text(400, "Bad month format")
                return
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                data = json.loads(body)  # validate JSON
            except json.JSONDecodeError as e:
                self.send_text(400, f"Invalid JSON: {e}")
                return
            data_file = DATA_DIR / f"{month}.json"
            data_file.write_text(json.dumps(data, indent=2, ensure_ascii=False))
            self.send_json(200, {"ok": True})
            return

        self.send_text(404, "Not found")


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

    port = find_free_port(args.port)
    if port != args.port:
        print(f"  Port {args.port} is in use — using port {port} instead.")

    server = HTTPServer(("127.0.0.1", port), Handler)
    print(f"\n  Daily Task Manager is running!")
    print(f"  Open this URL in your browser:\n")
    print(f"      http://localhost:{port}\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        sys.exit(0)

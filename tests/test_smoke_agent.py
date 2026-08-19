from __future__ import annotations

import json
import subprocess
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterator


@contextmanager
def fake_agent(*, include_ui: bool) -> Iterator[str]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            return

        def send_json(self, body: dict[str, object]) -> None:
            payload = json.dumps(body).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self) -> None:
            if self.path == "/health":
                self.send_json({"ready": True, "request_id": "health-1", "latency_ms": 1.0})
                return
            if self.path == "/" and include_ui:
                payload = (
                    b'<textarea id="question-input"></textarea>'
                    b'<script type="module" src="/static/app.js"></script>'
                )
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Security-Policy", "default-src 'self'")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return
            self.send_error(404)

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length))
            answerable = body.get("question_id") == "smoke-answerable"
            self.send_json(
                {
                    "request_id": f"request-{body.get('question_id')}",
                    "latency_ms": 2.0,
                    "answerable": answerable,
                    "verified": answerable,
                    "evidence": [{"receipt_no": "f1"}] if answerable else [],
                }
            )

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)


def run_smoke(base_url: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(Path("scripts/smoke-agent.ps1")),
            "-BaseUrl",
            base_url,
        ],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )


def test_smoke_fails_when_public_web_shell_is_missing() -> None:
    with fake_agent(include_ui=False) as base_url:
        result = run_smoke(base_url)
    assert result.returncode != 0


def test_smoke_passes_when_web_health_and_queries_are_valid() -> None:
    with fake_agent(include_ui=True) as base_url:
        result = run_smoke(base_url)
    assert result.returncode == 0, result.stderr
    assert "web_ok=True" in result.stdout
    assert "injection_answerable=False" in result.stdout

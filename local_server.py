#!/usr/bin/env python3
"""GaunAI hafif yerel sunucu.

FastAPI/uvicorn import zinciri yerel macOS dosya okumasında takıldığında aynı
arayüzü stdlib HTTP server ile ayağa kaldırmak için kullanılır.
"""

from __future__ import annotations

import json
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

import bot

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"


class Handler(BaseHTTPRequestHandler):
    server_version = "GaunAI/stdlib"

    def _send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path) -> None:
        if not path.exists() or not path.is_file():
            self.send_error(404)
            return
        body = path.read_bytes()
        ctype = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw.decode("utf-8") or "{}")

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/api/health":
            self._send_json({"status": "ok"})
            return
        if self.path in {"/", "/index.html"}:
            self._send_file(STATIC / "index.html")
            return
        rel = unquote(self.path.lstrip("/"))
        target = (STATIC / rel).resolve()
        if STATIC.resolve() in target.parents:
            self._send_file(target)
            return
        self.send_error(404)

    def do_POST(self) -> None:  # noqa: N802
        try:
            payload = self._read_json()
            if self.path == "/api/chat":
                question = (payload.get("question") or "").strip()
                history = payload.get("history") or []
                if not question:
                    self._send_json({"detail": "question boş olamaz"}, 400)
                    return
                result = bot.answer_with_telemetry(question, history)
                self._send_json({"answer": result["answer"], "log_id": result["log_id"]})
                return
            if self.path == "/api/feedback":
                log_id = payload.get("log_id")
                score = payload.get("score")
                client_ip = self.client_address[0] if self.client_address else "?"
                ok = bool(log_id and score in (1, -1)
                          and bot.record_feedback(int(log_id), int(score), client_ip))
                self._send_json({"ok": ok})
                return
            self.send_error(404)
        except Exception as exc:
            self._send_json({"detail": str(exc)}, 500)


def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 8000), Handler)
    print("GaunAI local server: http://127.0.0.1:8000", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()

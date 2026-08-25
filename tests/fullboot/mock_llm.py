"""In-process OpenAI-compatible mock LLM server (threaded, stdlib only)."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def _extract_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                parts.append(str(item.get("text") or item.get("content") or ""))
            else:
                parts.append(str(item))
        return "".join(parts)
    return "" if content is None else str(content)


class _Handler(BaseHTTPRequestHandler):
    server: MockLLMServer

    def log_message(self, *args):  # silence stderr noise in tests
        return

    @property
    def mock(self) -> MockLLMServer:
        return self.server.mock_ref  # type: ignore[attr-defined]

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(body.decode("utf-8"))
        except Exception:
            payload = {}
        mock = self.mock

        with mock.lock:
            mock.requests.append(payload)
            scripted = mock.script

        if scripted is not None:
            try:
                reply = scripted(payload)
            except Exception:
                reply = "script error"
        else:
            reply = mock.default_reply

        model = str(payload.get("model") or "mock")
        if payload.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            chunk = {
                "id": "chatcmpl-mock",
                "object": "chat.completion.chunk",
                "choices": [
                    {"index": 0, "delta": {"role": "assistant", "content": reply}}
                ],
            }
            self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")
        else:
            completion = {
                "id": "chatcmpl-mock",
                "object": "chat.completion",
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": reply},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {},
            }
            data = json.dumps(completion).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)


class MockLLMServer:
    """Records every chat-completion request and answers with canned text."""

    def __init__(self, default_reply: str = "mock says hi"):
        self.default_reply = default_reply
        self.script = None
        self.requests: list[dict] = []
        self.lock = threading.Lock()
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._server.mock_ref = self  # type: ignore[attr-defined]
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def api_base(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()

    def last_prompt_text(self) -> str:
        with self.lock:
            if not self.requests:
                return ""
            messages = self.requests[-1].get("messages") or []
            return "\n".join(_extract_text(m.get("content")) for m in messages)

    def all_prompt_text(self) -> str:
        with self.lock:
            texts = []
            for req in self.requests:
                for m in req.get("messages") or []:
                    texts.append(_extract_text(m.get("content")))
            return "\n".join(texts)

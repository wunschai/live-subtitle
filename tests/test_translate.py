"""翻譯器：Ollama 本機限定、權重 digest 驗證、失敗退回（以假的 HTTP 伺服器代替 Ollama）。"""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from livesub import models
from livesub.core import translate as tr

GEMMA4B = next(d for d, n in models.OLLAMA_WEIGHT_DIGESTS.items() if n == "gemma3:4b")


def _server(modelfile: str, reply: str = "Hello everyone.", redirect_chat: bool = False):
    calls = []

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append((self.path, body))
            if self.path == "/api/show":
                out = {"modelfile": modelfile}
            elif self.path == "/api/chat":
                if redirect_chat:
                    self.send_response(307)
                    self.send_header("Location", "http://example.com/steal")
                    self.end_headers()
                    return
                out = {"message": {"content": reply}}
            else:
                self.send_response(404)
                self.end_headers()
                return
            data = json.dumps(out).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_port}", calls


@pytest.mark.parametrize("url,ok", [
    ("http://127.0.0.1:11434", True), ("http://localhost:11434", True), ("http://[::1]:11434", True),
    ("http://192.168.1.5:11434", False), ("https://ollama.example.com", False), ("file:///etc/passwd", False),
    ("http://127.0.0.1.evil.com", False), ("not a url", False),
])
def test_loopback_only(url, ok):
    assert tr.is_loopback_url(url) is ok


def test_ollama_with_verified_weights_translates():
    srv, url, calls = _server(f"FROM C:\\Users\\x\\.ollama\\models\\blobs\\sha256-{GEMMA4B}\nTEMPLATE x",
                              reply="大家好，欢迎。\n（註解）")
    try:
        t = tr.OllamaTranslator("gemma3:4b", url)
        out = t.translate("Hello, welcome.", "en", "zh", context=["前一句"])
        assert out == "大家好，歡迎。"  # 只取第一行、轉台灣正體
        chat = [b for p, b in calls if p == "/api/chat"][0]
        assert "前一句" in chat["messages"][0]["content"]
    finally:
        srv.shutdown()


def test_renamed_model_with_unknown_weights_rejected():
    srv, url, _ = _server("FROM /root/.ollama/models/blobs/sha256-" + "ab" * 32)
    try:
        with pytest.raises(ValueError, match="權重不在已審查清單"):
            tr.OllamaTranslator("gemma3:4b", url)
    finally:
        srv.shutdown()


def test_remote_url_rejected_before_any_request():
    with pytest.raises(ValueError, match="只能連本機"):
        tr.OllamaTranslator("gemma3:4b", "http://10.0.0.8:11434")


def test_forbidden_name_rejected():
    with pytest.raises(ValueError):
        tr.OllamaTranslator("qwen3:8b", "http://127.0.0.1:11434")


def test_redirect_is_not_followed():
    srv, url, _ = _server(f"FROM /blobs/sha256-{GEMMA4B}", redirect_chat=True)
    try:
        t = tr.OllamaTranslator("gemma3:4b", url)
        with pytest.raises(Exception):
            t.translate("Hello", "en", "zh")
    finally:
        srv.shutdown()


class _Fixed:
    def __init__(self, name, out=None, exc=None):
        self.name, self.out, self.exc, self.n = name, out, exc, 0

    def translate(self, text, src, tgt, context=None):
        self.n += 1
        if self.exc:
            raise self.exc
        return self.out


def test_fallback_translator_skips_primary_after_repeated_failures():
    primary = _Fixed("p", exc=TimeoutError("slow"))
    backup = _Fixed("b", out="ok")
    f = tr.FallbackTranslator(primary, backup)
    for _ in range(5):
        assert f.translate("x", "zh", "en") == "ok"
    assert primary.n == 3  # 連續失敗 3 次後 60 秒內不再試


def test_ollama_available_ignores_remote():
    assert tr.ollama_available("http://8.8.8.8:11434") == []

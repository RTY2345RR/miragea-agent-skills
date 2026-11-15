"""A tiny local stand-in for the voice engine API used by the offline tests."""
import io
import json
import math
import os
import struct
import threading
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"


def fake_wav(text, rate=16000, lead_ms=120):
    ms = int(len(text.split()) * 300) + 100
    n = rate * ms // 1000
    lead = rate * lead_ms // 1000
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes(b"".join(struct.pack("<h", 0 if i < lead else int(8000 * math.sin(i / 8))) for i in range(n)))
    return buf.getvalue()


class Handler(BaseHTTPRequestHandler):
    state = None

    def log_message(self, *a):
        pass

    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(body)

    def do_GET(self):
        st = Handler.state
        st["requests"].append(("GET", self.path, dict(self.headers), None))
        if self.path.startswith("/files/"):
            self.send_response(200); self.end_headers(); self.wfile.write(st["audio"][self.path[7:]]); return
        if self.headers.get("Authorization") != "Bearer k":
            return self._json(401, {"error": {"message": "Invalid API key"}})
        if self.path == "/v1/capabilities":
            return self._json(200, {"audio": {"default_model": "expressive", "models": [{"id": "expressive", "tags": [{"label": "excited"}]}], "max_text_length": 5000}})
        tid = self.path.rsplit("/", 1)[-1]
        st["polls"][tid] = st["polls"].get(tid, 0) + 1
        port = self.server.server_address[1]
        return self._json(200, {"task_id": tid, "status": "succeeded", "result": {"audio_url": f"http://127.0.0.1:{port}/files/{tid}"}})

    def do_POST(self):
        st = Handler.state
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        st["requests"].append(("POST", self.path, dict(self.headers), body))
        if self.headers.get("Authorization") != "Bearer k":
            return self._json(401, {"error": {"message": "Invalid API key"}})
        key = self.headers.get("Idempotency-Key")
        tid = st["by_key"].get(key)
        if tid is None:
            tid = f"t{len(st['by_key']) + 1}"
            st["by_key"][key] = tid
            st["audio"][tid] = fake_wav(body["text"])
        return self._json(200, {"task_id": tid, "status": "queued", "poll_after_seconds": 0})


def start():
    Handler.state = {"requests": [], "by_key": {}, "audio": {}, "polls": {}}
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, Handler.state, f"http://127.0.0.1:{srv.server_address[1]}/v1"

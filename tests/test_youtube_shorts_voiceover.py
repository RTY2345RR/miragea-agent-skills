"""Offline tests for skills/youtube-shorts-voiceover/scripts/shorts_voiceover.py (mock voice engine API)."""
import importlib.util
import os
import io
import json
import math
import struct
import tempfile
import threading
import unittest
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"
ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("svo", ROOT / "skills/youtube-shorts-voiceover/scripts/shorts_voiceover.py")
svo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(svo)


def fake_wav(text, speed=1.0, rate=16000):
    ms = int(len(text.split()) * 300 / speed) + 100
    n = rate * ms // 1000
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(i / 8))) for i in range(n)))
    return buf.getvalue()


class Mock(BaseHTTPRequestHandler):
    state = None

    def log_message(self, *a):
        pass

    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(body)

    def do_GET(self):
        st = Mock.state
        st["requests"].append(("GET", self.path, dict(self.headers), None))
        if self.path.startswith("/files/"):
            data = st["audio"][self.path[7:]]
            self.send_response(200); self.end_headers(); self.wfile.write(data); return
        if self.headers.get("Authorization") != "Bearer k":
            return self._json(401, {"error": {"message": "Invalid API key"}})
        if self.path == "/v1/capabilities":
            return self._json(200, {"audio": {"default_model": "expressive", "models": [{"id": "expressive", "tags": [{"label": "excited"}]}], "formats": ["wav", "mp3"], "max_text_length": 5000}})
        tid = self.path.rsplit("/", 1)[-1]
        port = self.server.server_address[1]
        return self._json(200, {"task_id": tid, "status": "succeeded", "result": {"audio_url": f"http://127.0.0.1:{port}/files/{tid}", "format": "wav"}})

    def do_POST(self):
        st = Mock.state
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        st["requests"].append(("POST", self.path, dict(self.headers), body))
        if st.get("fail_once") and not st.get("failed"):
            st["failed"] = True
            return self._json(503, {"error": {"message": "busy", "retryable": True}})
        tid = f"t{len(st['audio']) + 1}"
        st["audio"][tid] = fake_wav(body["text"], body.get("speed", 1.0))
        return self._json(202, {"task_id": tid, "status": "queued", "poll_after_seconds": 0})


class ShortsVoiceoverTest(unittest.TestCase):
    def setUp(self):
        Mock.state = {"requests": [], "audio": {}}
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), Mock)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.engine = svo.Engine(key="k", base=f"http://127.0.0.1:{self.srv.server_address[1]}/v1", sleep=lambda s: None)
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        self.srv.shutdown()

    def run_cli(self, argv):
        return svo.main(argv, engine=self.engine)

    def posts(self):
        return [r for r in Mock.state["requests"] if r[0] == "POST"]

    def test_voice_writes_track_captions_and_report(self):
        script = self.tmp / "s.txt"
        script.write_text("Octopuses have three hearts.\n[ben] Two pump blood to the gills, and one keeps the body running.\n")
        out = self.tmp / "out"
        rc = self.run_cli(["voice", str(script), "--out", str(out), "--voice", "https://x/ava.wav|exact words",
                           "--speaker-voice", "ben=actor-7", "--tag", "excited"])
        self.assertEqual(rc, 0)
        for f in ("voiceover.wav", "captions.srt", "captions.ass", "report.json"):
            self.assertTrue((out / f).exists(), f)
        rep = json.loads((out / "report.json").read_text())
        self.assertTrue(rep["fits_shorts"])
        self.assertTrue(rep["hook_under_3s"])
        p = self.posts()
        self.assertEqual(p[0][3]["reference_audio_url"], "https://x/ava.wav")
        self.assertEqual(p[0][3]["reference_text"], "exact words")
        self.assertEqual(p[1][3]["actor_id"], "actor-7")
        self.assertEqual(p[0][3]["tags"], ["excited"])
        self.assertTrue(p[0][2]["User-Agent"].startswith("Mozilla/5.0"))
        self.assertTrue(p[0][2]["Idempotency-Key"].startswith("svo-"))
        for line in (out / "captions.srt").read_text().splitlines():
            if line and not line[0].isdigit():
                self.assertLessEqual(len(line), 32, line)
        self.assertIn("{\\k", (out / "captions.ass").read_text())
        with wave.open(str(out / "voiceover.wav")) as w:
            self.assertAlmostEqual(w.getnframes() / w.getframerate(), rep["duration_s"], delta=0.02)
        # Re-run: nothing changes, nothing is generated again.
        n = len(self.posts())
        self.run_cli(["voice", str(script), "--out", str(out), "--voice", "https://x/ava.wav|exact words", "--speaker-voice", "ben=actor-7", "--tag", "excited"])
        self.assertEqual(len(self.posts()), n)

    def test_dub_fits_slots_with_speed_and_reports_overflow(self):
        srt = self.tmp / "es.srt"
        srt.write_text("1\n00:00:00,000 --> 00:00:01,000\n[ana] Hola a todos\n\n2\n00:00:01,200 --> 00:00:01,800\nEsto es demasiado largo para el hueco\n")
        out = self.tmp / "dub"
        rc = self.run_cli(["dub", str(srt), "--out", str(out), "--voice", "actor-1", "--speaker-voice", "ana=actor-2"])
        self.assertEqual(rc, 0)
        rep = json.loads((out / "report.json").read_text())
        self.assertTrue(rep["cues"][0]["ok"])
        self.assertEqual(rep["cues"][1]["speed"], 1.15)
        self.assertEqual(rep["overflowing_cues"], [2])
        self.assertEqual(self.posts()[0][3]["actor_id"], "actor-2")
        self.assertIn("Shorten", rep["next_step"])
        self.assertIn("00:00:01,200 --> 00:00:01,800", (out / "dub.srt").read_text())

    def test_retry_keeps_idempotency_key(self):
        Mock.state["fail_once"] = True
        script = self.tmp / "s.txt"
        script.write_text("One line.\n")
        self.run_cli(["voice", str(script), "--out", str(self.tmp / "o"), "--voice", "a"])
        keys = [r[2]["Idempotency-Key"] for r in self.posts()]
        self.assertEqual(len(keys), 2)
        self.assertEqual(keys[0], keys[1])

    def test_errors(self):
        bad = svo.Engine(key="wrong", base=self.engine.base, sleep=lambda s: None)
        with self.assertRaises(svo.EngineError) as cm:
            bad.capabilities()
        self.assertNotIn("wrong", str(cm.exception))
        script = self.tmp / "s.txt"
        script.write_text("Hi.\n")
        self.assertEqual(self.run_cli(["voice", str(script), "--out", str(self.tmp / "x")]), 2)
        with self.assertRaises(svo.EngineError):
            svo.Engine(key="")
        with self.assertRaises(svo.EngineError):
            svo.parse_srt("nothing here")

    def test_check(self):
        self.assertEqual(self.run_cli(["check"]), 0)


if __name__ == "__main__":
    unittest.main()


class CaptionSplitTest(unittest.TestCase):
    def test_no_one_word_orphans_and_balanced(self):
        lines = [{"text": "Octopuses have three hearts.", "start_ms": 0, "end_ms": 2043},
                 {"text": "Two pump blood to the gills, and one keeps the body running.", "start_ms": 2223, "end_ms": 6589}]
        for mc in (24, 28, 32):
            cues = svo.short_cues(lines, mc)
            for c in cues:
                self.assertGreaterEqual(len(c["text"].split()), 2, (mc, c["text"]))
                self.assertLessEqual(len(c["text"]), mc)
            self.assertNotIn("hearts.", [c["text"] for c in cues])

    def test_prefers_punctuation_break(self):
        cues = svo.short_cues([{"text": "Two pump blood to the gills, and one keeps the body running.", "start_ms": 0, "end_ms": 4000}], 32)
        self.assertEqual([c["text"] for c in cues], ["Two pump blood to the gills,", "and one keeps the body running."])

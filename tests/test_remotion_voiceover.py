"""Offline tests for skills/remotion-voiceover/scripts/remotion_voiceover.py (mock voice engine API)."""
import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import mock_voice_engine as mock  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("rvo", ROOT / "skills/remotion-voiceover/scripts/remotion_voiceover.py")
rvo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rvo)

SCRIPT = """---
voices:
  maya: https://example.com/maya.wav | For the twentieth time
  leo: ACTOR_LEO
voice: maya
fps: 30
---
# Hook
Your Remotion video finally has a voice.

# Demo [voice: leo]
[emotion: excited] It costs [[$49|forty-nine dollars]].
[pause: 400ms]
Done today.
"""


class RemotionVoiceoverTest(unittest.TestCase):
    def setUp(self):
        self.srv, self.st, self.base = mock.start()
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "package.json").write_text("{}")
        (self.tmp / "public").mkdir()
        (self.tmp / "script.md").write_text(SCRIPT)

    def tearDown(self):
        self.srv.shutdown(); self.srv.server_close()

    def run_cli(self, *argv):
        eng = rvo.Engine(key="k", base=self.base, sleep=lambda s: None)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
            code = rvo.main(list(argv), engine=eng)
        return code, json.loads(buf.getvalue()) if buf.getvalue().strip() else None

    def test_parse_script_scenes_voices_alternates(self):
        s = rvo.parse_script(SCRIPT)
        self.assertEqual([x["id"] for x in s["scenes"]], ["hook", "demo"])
        demo = s["scenes"][1]["lines"]
        self.assertEqual(demo[0]["caption"], "It costs $49.")
        self.assertEqual(demo[0]["text"], "It costs forty-nine dollars.")
        self.assertEqual(demo[0]["voice"], "leo")
        self.assertEqual(demo[0]["tags"], ["excited"])
        self.assertEqual(demo[1]["pause_ms"], 400)

    def test_generate_writes_remotion_files(self):
        code, res = self.run_cli("generate", str(self.tmp / "script.md"))
        self.assertEqual(code, 0)
        out = self.tmp / "public" / "voiceover"
        for f in ("voiceover.json", "captions.json", "captions.srt", "captions.vtt", "voiceover.wav", "hook-01.wav", "demo-01.wav"):
            self.assertTrue((out / f).exists(), f)
        tl = json.loads((out / "voiceover.json").read_text())
        self.assertEqual(tl["fps"], 30)
        self.assertEqual(tl["audio"], "voiceover/voiceover.wav")
        self.assertEqual(tl["lines"][0]["src"], "voiceover/hook-01.wav")
        self.assertEqual(res["durationInFrames"], tl["durationInFrames"])
        self.assertGreaterEqual(tl["lines"][1]["startMs"] - tl["lines"][0]["endMs"], 600)  # scene gap
        caps = json.loads((out / "captions.json").read_text())
        self.assertEqual(set(caps[0]), {"text", "startMs", "endMs", "timestampMs", "confidence"})
        self.assertEqual(caps[0]["text"], "Your")
        self.assertTrue(caps[1]["text"].startswith(" "))
        self.assertGreaterEqual(caps[0]["startMs"], 100)  # leading silence trimmed
        self.assertIn("$49", (out / "captions.srt").read_text())
        posts = [r for r in self.st["requests"] if r[0] == "POST"]
        self.assertEqual(posts[0][3]["reference_audio_url"], "https://example.com/maya.wav")
        self.assertEqual(posts[1][3]["actor_id"], "ACTOR_LEO")
        self.assertEqual(posts[1][3]["tags"], ["excited"])
        self.assertTrue(all("Mozilla" in r[2]["User-Agent"] for r in posts))

    def test_rerun_reuses_and_idempotent(self):
        self.run_cli("generate", str(self.tmp / "script.md"))
        n = len([r for r in self.st["requests"] if r[0] == "POST"])
        _, res = self.run_cli("generate", str(self.tmp / "script.md"))
        self.assertEqual(res["reused"], 3)
        self.assertEqual(len([r for r in self.st["requests"] if r[0] == "POST"]), n)
        keys = [r[2]["Idempotency-Key"] for r in self.st["requests"] if r[0] == "POST"]
        self.assertEqual(len(keys), len(set(keys)))

    def test_plan_makes_no_calls(self):
        code, res = self.run_cli("plan", str(self.tmp / "script.md"))
        self.assertEqual(code, 0)
        self.assertEqual(res["lines"], 3)
        self.assertEqual(self.st["requests"], [])

    def test_bad_key_is_reported_without_leak(self):
        eng = rvo.Engine(key="secret-key", base=self.base, sleep=lambda s: None)
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            code = rvo.main(["check"], engine=eng)
        self.assertEqual(code, 2)
        self.assertNotIn("secret-key", err.getvalue())

    def test_missing_key(self):
        import os
        old = os.environ.pop("MIRAGEA_API_KEY", None)
        try:
            with self.assertRaises(rvo.EngineError):
                rvo.Engine()
        finally:
            if old is not None:
                os.environ["MIRAGEA_API_KEY"] = old


if __name__ == "__main__":
    unittest.main()

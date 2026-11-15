"""Offline tests for skills/ai-podcast-generator/scripts/podcast.py (mock voice engine API)."""
import contextlib
import importlib.util
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import mock_voice_engine as mock  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("pod", ROOT / "skills/ai-podcast-generator/scripts/podcast.py")
pod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pod)

SCRIPT = """# Test Episode

## Cold open
Maya: Welcome back to the show, everyone.
Leo: Thanks, Maya. Today we explain voice cloning.

## Main topic
[Maya] So how does it work?
Leo: You give it one short clip and the exact words spoken.
"""
HOSTS = ["--host", "Maya=https://example.com/maya.wav|exact words", "--host", "Leo=ACTOR_LEO"]


class PodcastTest(unittest.TestCase):
    def setUp(self):
        self.srv, self.st, self.base = mock.start()
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "ep.md").write_text(SCRIPT)

    def tearDown(self):
        self.srv.shutdown(); self.srv.server_close()

    def run_cli(self, *argv):
        eng = pod.Engine(key="k", base=self.base, sleep=lambda s: None)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = pod.main(list(argv), engine=eng)
        return code, (json.loads(out.getvalue()) if out.getvalue().strip() else None), err.getvalue()

    def test_parse_two_hosts_and_chapters(self):
        ep = pod.parse_episode(SCRIPT)
        self.assertEqual(ep["title"], "Test Episode")
        self.assertEqual([c["title"] for c in ep["chapters"]], ["Cold open", "Main topic"])
        self.assertEqual(ep["chapters"][1]["turns"][0], {"speaker": "Maya", "text": "So how does it work?"})

    def test_line_without_speaker_is_rejected(self):
        with self.assertRaises(pod.EngineError):
            pod.parse_episode("## A\nJust text with no speaker")

    def test_make_episode(self):
        out = self.tmp / "out"
        code, res, _ = self.run_cli("make", str(self.tmp / "ep.md"), "--out", str(out), *HOSTS)
        self.assertEqual(code, 0)
        self.assertEqual(res["hosts"], ["Maya", "Leo"])
        for f in ("episode.wav", "transcript.txt", "transcript.srt", "transcript.vtt", "transcript.json", "chapters.json", "show-notes.md"):
            self.assertTrue((out / f).exists(), f)
        ch = json.loads((out / "chapters.json").read_text())
        self.assertEqual(ch["version"], "1.2.0")
        self.assertEqual(ch["chapters"][0]["startTime"], 0)
        self.assertGreater(ch["chapters"][1]["startTime"], 0)
        srt = (out / "transcript.srt").read_text()
        self.assertIn("[Maya] ", srt)
        self.assertIn("[Leo] ", srt)
        self.assertIn("Maya: Welcome back", (out / "transcript.txt").read_text())
        posts = [r[3] for r in self.st["requests"] if r[0] == "POST"]
        self.assertEqual(len(posts), 4)
        self.assertEqual(posts[0]["reference_audio_url"], "https://example.com/maya.wav")
        self.assertEqual(posts[0]["reference_text"], "exact words")
        self.assertEqual(posts[1]["actor_id"], "ACTOR_LEO")
        if shutil.which("ffmpeg"):
            self.assertTrue((out / "episode.mp3").exists())

    def test_rerun_reuses_all_turns(self):
        out = self.tmp / "out"
        self.run_cli("make", str(self.tmp / "ep.md"), "--out", str(out), *HOSTS)
        _, res, _ = self.run_cli("make", str(self.tmp / "ep.md"), "--out", str(out), *HOSTS)
        self.assertEqual(res["reused"], 4)
        self.assertEqual(len([r for r in self.st["requests"] if r[0] == "POST"]), 4)

    def test_missing_host_voice(self):
        code, _, err = self.run_cli("make", str(self.tmp / "ep.md"), "--out", str(self.tmp / "o"), "--host", "Maya=A1")
        self.assertEqual(code, 2)
        self.assertIn("Leo", err)

    def test_plan(self):
        code, res, _ = self.run_cli("plan", str(self.tmp / "ep.md"), *HOSTS)
        self.assertEqual(code, 0)
        self.assertEqual(res["turns"], 4)
        self.assertEqual(self.st["requests"], [])


if __name__ == "__main__":
    unittest.main()

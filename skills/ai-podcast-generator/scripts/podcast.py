#!/usr/bin/env python3
"""AI podcast generator skill helper: a two-host Markdown script -> episode MP3 with ID3 chapters,
transcript (TXT/SRT/VTT/JSON), Podcasting 2.0 chapters.json and show notes (Python 3.9+, stdlib only).

  check                      verify MIRAGEA_API_KEY, list models and emotion tags
  plan SCRIPT --host ...     dry run: hosts, chapters, word count, estimated minutes (no API calls)
  make SCRIPT --host ...     voice every turn with each host's (cloned) voice and assemble the episode

Voice engine: Miragea (https://miragea.ai/v1), key in MIRAGEA_API_KEY. Re-running is safe: finished turns are
reused and identical requests re-attach to the same job instead of creating a new one.
"""
from __future__ import annotations

import argparse
import array
import hashlib
import io
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import wave
from pathlib import Path

VERSION = "0.1.0"
DEFAULT_BASE = "https://miragea.ai/v1"
KEY_PAGE = "https://miragea.ai/workspace/api"
INSTALL_DOC = "https://miragea.ai/skills/miragea-audio/install.md"


class EngineError(Exception):
    pass


class Engine:
    """Minimal client for the Miragea voice engine API (text-to-speech + voice cloning)."""

    def __init__(self, key=None, base=None, sleep=time.sleep, poll_timeout=900, ua_name="agent-skill"):
        self.key = key if key is not None else os.environ.get("MIRAGEA_API_KEY", "")
        if not self.key:
            raise EngineError(f"MIRAGEA_API_KEY is not set. Create a voice engine key at {KEY_PAGE} and export it.")
        self.base = (base or os.environ.get("MIRAGEA_BASE_URL") or DEFAULT_BASE).rstrip("/")
        self.sleep = sleep
        self.poll_timeout = poll_timeout
        self.ua = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
                   f"Chrome/124.0 Safari/537.36 {ua_name}/{VERSION}")

    def call(self, method, path, body=None, idem=None, retries=3):
        headers = {"Authorization": f"Bearer {self.key}", "Accept": "application/json", "User-Agent": self.ua}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        if idem:
            headers["Idempotency-Key"] = idem
        for attempt in range(retries + 1):
            req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=90) as r:
                    return json.loads(r.read() or b"{}")
            except urllib.error.HTTPError as e:
                raw = e.read()
                if e.code in (502, 503, 504) and attempt < retries:
                    self.sleep(1.5 * (attempt + 1))
                    continue
                try:
                    msg = json.loads(raw)["error"]["message"]
                except Exception:
                    msg = f"HTTP {e.code}"
                hint = {401: f" Check MIRAGEA_API_KEY ({KEY_PAGE}).", 429: " Wait and re-run; finished lines are reused.",
                        413: " Shorten the line.", 400: " Check voice, tags, speed and text."}.get(e.code, "")
                raise EngineError((msg + hint).replace(self.key, "***")) from None
            except (urllib.error.URLError, TimeoutError) as e:
                if attempt < retries:
                    self.sleep(1.0 * (attempt + 1))
                    continue
                raise EngineError(f"Could not reach the voice engine API ({e}).") from None

    def capabilities(self):
        return self.call("GET", "/capabilities")

    @staticmethod
    def idem_key(payload, take=1, prefix="skill"):
        return prefix + "-" + hashlib.sha256(json.dumps({"p": payload, "t": take}, sort_keys=True).encode()).hexdigest()[:40]

    def submit(self, payload, take=1, prefix="skill"):
        """Create (or, for an identical request, re-attach to) a generation task. Never duplicates work."""
        return self.call("POST", "/audio/generations", payload, self.idem_key(payload, take, prefix))

    def wait(self, task):
        tid = task.get("task_id")
        deadline = time.monotonic() + self.poll_timeout
        while task.get("status") != "succeeded":
            if task.get("status") == "failed":
                raise EngineError(f"Task {tid} failed: {(task.get('error') or {}).get('message', 'unknown error')}. "
                                  "Use --take 2 to request a new take.")
            if time.monotonic() > deadline:
                raise EngineError(f"Task {tid} is still {task.get('status')}. Re-run the same command to pick it up "
                                  "(no duplicate job is created).")
            self.sleep(max(0.5, float(task.get("poll_after_seconds") or 2)))
            task = self.call("GET", f"/tasks/{tid}")
        url = (task.get("result") or {}).get("audio_url", "")
        if not url.startswith(("http://", "https://")):
            raise EngineError(f"Task {tid} returned no audio_url.")
        req = urllib.request.Request(url, headers={"User-Agent": self.ua})  # pre-signed URL: no API key sent
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.read()


def parse_voice(spec):
    """'ACTOR_ID' -> actor voice; 'https://clip.wav|exact words' -> cloned voice."""
    spec = (spec or "").strip()
    if not spec:
        return None
    if spec.startswith(("http://", "https://")):
        url, _, txt = spec.partition("|")
        p = {"reference_audio_url": url.strip()}
        if txt.strip():
            p["reference_text"] = txt.strip()
        return p
    return {"actor_id": spec}


def read_wav(data):
    with wave.open(io.BytesIO(data)) as w:
        params = w.getparams()
        frames = w.readframes(w.getnframes())
    return params, frames


def wav_ms(params, frames):
    return len(frames) / (params.sampwidth * params.nchannels) / params.framerate * 1000.0


def speech_bounds(params, frames, window_ms=20, rel=0.06):
    """(start_ms, end_ms) of audible speech inside a clip, so captions don't start in leading silence."""
    total = wav_ms(params, frames)
    if params.sampwidth != 2:
        return 0.0, total
    n = len(frames) // 2
    if n == 0:
        return 0.0, total
    samples = array.array("h", frames[: n * 2])
    if sys.byteorder == "big":
        samples.byteswap()
    step = max(1, int(params.framerate * params.nchannels * window_ms / 1000))
    rms = []
    for i in range(0, len(samples), step):
        seg = samples[i:i + step]
        rms.append(math.sqrt(sum(x * x for x in seg) / len(seg)) if len(seg) else 0.0)
    peak = max(rms) if rms else 0
    if peak <= 0:
        return 0.0, total
    loud = [i for i, v in enumerate(rms) if v >= peak * rel]
    return loud[0] * window_ms, min(total, (loud[-1] + 1) * window_ms)


def write_track(path, clips, total_ms):
    """clips: [(start_ms, params, frames)] -> one WAV with silence between clips."""
    p0 = clips[0][1]
    fb = p0.sampwidth * p0.nchannels
    for _, p, _ in clips:
        if (p.framerate, p.sampwidth, p.nchannels) != (p0.framerate, p0.sampwidth, p0.nchannels):
            raise EngineError("Clips came back in different audio formats; cannot stitch.")
    out = bytearray()
    for start, _, frames in sorted(clips, key=lambda c: c[0]):
        pos = int(round(start / 1000 * p0.framerate)) * fb
        if pos > len(out):
            out += b"\x00" * (pos - len(out))
        elif pos < len(out):
            out = out[:pos]
        out += frames
    end = int(round(total_ms / 1000 * p0.framerate)) * fb
    if end > len(out):
        out += b"\x00" * (end - len(out))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(p0.nchannels)
        w.setsampwidth(p0.sampwidth)
        w.setframerate(p0.framerate)
        w.writeframes(bytes(out))


def syllables(word):
    w = re.sub(r"[^a-z0-9']", "", word.lower())
    if not w:
        return 1
    if w.isdigit():
        return max(1, len(w))
    return max(1, len(re.findall(r"[aeiouy]+", re.sub(r"e$", "", w))))


def word_times(text, start, end):
    """Estimate per-word timing inside [start, end] ms, weighted by syllables and punctuation pauses."""
    words = text.split()
    if not words:
        return []
    weights = [syllables(w) + (0.6 if re.search(r"[,;:]$", w) else 0) + (0.9 if re.search(r"[.!?]$", w) else 0) for w in words]
    total = sum(weights)
    out, acc = [], 0.0
    for w, wt in zip(words, weights):
        s = start + (end - start) * acc / total
        acc += wt
        out.append((w, s, start + (end - start) * acc / total))
    return out


def balanced_groups(items, max_chars, min_chars=10):
    """Fewest caption cues of similar length; no one-word orphans; prefer breaks after punctuation."""
    n = len(items)
    if n == 0:
        return []
    length = lambda ws: len(" ".join(w[0] for w in ws))
    INF = float("inf")
    best = [0.0] + [INF] * n
    back = [0] * (n + 1)
    for i in range(1, n + 1):
        for j in range(i - 1, -1, -1):
            chunk = items[j:i]
            L = length(chunk)
            if L > max_chars and i - j > 1:
                break
            cost = 1000 + (max_chars - min(L, max_chars)) ** 2
            if n > 1 and (i - j == 1 or L < min_chars):
                cost += 600
            if i < n and re.search(r"[.!?]$", chunk[-1][0]):
                cost -= 250
            elif i < n and re.search(r"[,;:\u2014-]$", chunk[-1][0]):
                cost -= 120
            if best[j] + cost < best[i]:
                best[i], back[i] = best[j] + cost, j
    groups, i = [], n
    while i > 0:
        groups.append(items[back[i]:i])
        i = back[i]
    return groups[::-1]


def stamp(ms, sep=","):
    ms = max(0, int(round(ms)))
    return f"{ms // 3600000:02d}:{ms % 3600000 // 60000:02d}:{ms % 60000 // 1000:02d}{sep}{ms % 1000:03d}"


def to_mp3(wav_path, chapters_meta=None):
    if not shutil.which("ffmpeg"):
        return None
    mp3 = Path(wav_path).with_suffix(".mp3")
    cmd = ["ffmpeg", "-loglevel", "error", "-y", "-i", str(wav_path)]
    if chapters_meta:
        cmd += ["-i", str(chapters_meta), "-map_metadata", "1", "-map_chapters", "1", "-id3v2_version", "3"]
    cmd += ["-codec:a", "libmp3lame", "-q:a", "2", str(mp3)]
    subprocess.run(cmd, check=True)
    return mp3


class Cache:
    """Remembers which line files match which request, so re-runs reuse finished lines."""

    def __init__(self, path):
        self.path = Path(path)
        try:
            self.data = json.loads(self.path.read_text())
        except Exception:
            self.data = {}

    @staticmethod
    def digest(payload, take):
        return hashlib.sha256(json.dumps({"p": payload, "t": take}, sort_keys=True).encode()).hexdigest()[:16]

    def fresh(self, name, payload, take, file):
        return self.data.get(name) == self.digest(payload, take) and Path(file).exists()

    def mark(self, name, payload, take):
        self.data[name] = self.digest(payload, take)
        self.path.write_text(json.dumps(self.data, indent=2))


def synthesize_all(engine, jobs, take, log, prefix):
    """jobs: [(name, payload, file)]. Submits every missing line first (server works in parallel), then collects."""
    tasks = {}
    for name, payload, _ in jobs:
        tasks[name] = engine.submit(payload, take, prefix)
        log(f"  ~ {name}: submitted ({payload['text'][:48]})")
    for name, payload, file in jobs:
        data = engine.wait(tasks[name])
        Path(file).write_bytes(data)
        log(f"  + {name}: done")
        yield name, payload


# --------------------------------------------------------------------------- script
def parse_episode(text):
    """Two-host podcast script -> chapters and turns.

    # Episode title                (optional H1)
    ## Chapter title               starts a chapter
    Maya: Welcome back to the show.
    [Leo] Thanks, Maya.             either 'Name:' or '[Name]' marks the speaker
    """
    title, chapters, cur = None, [], None
    for raw in text.replace("\r", "").split("\n"):
        s = raw.strip()
        if not s or s.startswith("<!--") or s.startswith(">"):
            continue
        if s.startswith("# ") and title is None and not chapters:
            title = s[2:].strip()
            continue
        if s.startswith("#"):
            cur = {"title": s.lstrip("#").strip(), "turns": []}
            chapters.append(cur)
            continue
        m = re.match(r"^\[([^\]]+)\]\s*(.+)$", s) or re.match(r"^\**([A-Z][\w .'-]{0,24}?)\**\s*:\s+(.+)$", s)
        if not m:
            raise EngineError(f'Line has no speaker: "{s[:60]}". Start each line with "Name:" or "[Name]".')
        if cur is None:
            cur = {"title": "Intro", "turns": []}
            chapters.append(cur)
        cur["turns"].append({"speaker": m.group(1).strip(), "text": re.sub(r"\s+", " ", m.group(2)).strip()})
    chapters = [c for c in chapters if c["turns"]]
    if not chapters:
        raise EngineError("The script has no spoken lines.")
    return {"title": title or "Episode", "chapters": chapters}


def host_voices(specs):
    voices = {}
    for spec in specs or []:
        name, sep, val = spec.partition("=")
        if not sep or not val.strip():
            raise EngineError(f'Bad --host "{spec}". Use NAME=ACTOR_ID or NAME=https://clip.wav|exact words.')
        voices[name.strip().lower()] = parse_voice(val)
    return voices


def hms(ms):
    s = int(ms // 1000)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60:02d}:{s % 60:02d}"


def cmd_make(args, engine, log, dry=False):
    ep = parse_episode(Path(args.script).read_text(encoding="utf-8"))
    voices = host_voices(args.host)
    speakers = []
    for c in ep["chapters"]:
        for t in c["turns"]:
            if t["speaker"] not in speakers:
                speakers.append(t["speaker"])
    missing = [s for s in speakers if s.lower() not in voices]
    if missing:
        raise EngineError("No voice for host(s): " + ", ".join(missing) + '. Pass --host "NAME=ACTOR_ID" or --host "NAME=https://clip.wav|exact words".')
    turns = []
    for ci, c in enumerate(ep["chapters"]):
        for t in c["turns"]:
            p = {"text": t["text"], "format": "wav", **voices[t["speaker"].lower()]}
            if args.model:
                p["model"] = args.model
            if args.tag:
                p["tags"] = args.tag
            if args.speed and abs(args.speed - 1) > 1e-6:
                p["speed"] = round(args.speed, 3)
            turns.append({**t, "chapter": ci, "id": f"turn-{len(turns) + 1:03d}", "payload": p})
    if dry:
        words = sum(len(t["text"].split()) for t in turns)
        return {"title": ep["title"], "hosts": speakers, "chapters": [c["title"] for c in ep["chapters"]],
                "turns": len(turns), "words": words, "estimated_minutes": round(words / 150, 1)}
    out = Path(args.out)
    clips_dir = out / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)
    cache = Cache(clips_dir / ".podcast-cache.json")
    todo = [(t["id"], t["payload"], clips_dir / f"{t['id']}.wav") for t in turns
            if not cache.fresh(t["id"], t["payload"], args.take, clips_dir / f"{t['id']}.wav")]
    log(f"{len(turns) - len(todo)} turn(s) reused, {len(todo)} to voice")
    for name, payload in synthesize_all(engine, todo, args.take, log, "pod"):
        cache.mark(name, payload, args.take)

    cursor, clips, prev = args.lead_in, [], None
    for t in turns:
        if prev is not None:
            cursor += args.chapter_gap if t["chapter"] != prev["chapter"] else (args.gap if t["speaker"] != prev["speaker"] else args.gap * 0.6)
        params, fr = read_wav((clips_dir / f"{t['id']}.wav").read_bytes())
        dur = wav_ms(params, fr)
        s0, s1 = speech_bounds(params, fr)
        t.update(start_ms=cursor, end_ms=cursor + dur, speech_start_ms=cursor + s0, speech_end_ms=cursor + s1)
        clips.append((cursor, params, fr))
        cursor += dur
        prev = t
    total = cursor + args.tail
    write_track(out / "episode.wav", clips, total)

    chapters = []
    for ci, c in enumerate(ep["chapters"]):
        first = next(t for t in turns if t["chapter"] == ci)
        last = [t for t in turns if t["chapter"] == ci][-1]
        start = 0 if ci == 0 else first["start_ms"]
        chapters.append({"title": c["title"], "start_ms": start, "end_ms": last["end_ms"]})
    for i in range(len(chapters) - 1):
        chapters[i]["end_ms"] = chapters[i + 1]["start_ms"]
    chapters[-1]["end_ms"] = total
    # Podcasting 2.0 JSON chapters (podcast:chapters)
    (out / "chapters.json").write_text(json.dumps({"version": "1.2.0", "title": ep["title"], "chapters": [
        {"startTime": round(c["start_ms"] / 1000, 2), "endTime": round(c["end_ms"] / 1000, 2), "title": c["title"]} for c in chapters]}, indent=2) + "\n")
    meta = out / "chapters.ffmeta"
    meta.write_text(";FFMETADATA1\ntitle=" + ep["title"].replace("=", "\\=") + "\n" + "".join(
        f"\n[CHAPTER]\nTIMEBASE=1/1000\nSTART={int(c['start_ms'])}\nEND={int(c['end_ms'])}\ntitle={c['title'].replace('=', chr(92) + '=')}\n" for c in chapters))
    mp3 = to_mp3(out / "episode.wav", meta)

    # transcripts
    txt, cues = [], []
    for ci, c in enumerate(ep["chapters"]):
        txt.append(f"[{hms(chapters[ci]['start_ms'])}] {c['title']}\n")
        for t in [x for x in turns if x["chapter"] == ci]:
            txt.append(f"{t['speaker']}: {t['text']}\n")
            for g in balanced_groups(word_times(t["text"], t["speech_start_ms"], t["speech_end_ms"]), args.max_chars):
                cues.append((g[0][1], g[-1][2], t["speaker"], " ".join(w[0] for w in g)))
        txt.append("\n")
    (out / "transcript.txt").write_text("".join(txt).rstrip() + "\n", encoding="utf-8")
    (out / "transcript.srt").write_text("\n".join(f"{i}\n{stamp(s)} --> {stamp(e)}\n[{sp}] {tx}\n" for i, (s, e, sp, tx) in enumerate(cues, 1)), encoding="utf-8")
    (out / "transcript.vtt").write_text("WEBVTT\n\n" + "\n".join(f"{stamp(s, '.')} --> {stamp(e, '.')}\n<v {sp}>{tx}\n" for s, e, sp, tx in cues), encoding="utf-8")
    (out / "transcript.json").write_text(json.dumps({"title": ep["title"], "hosts": speakers, "segments": [
        {"speaker": t["speaker"], "chapter": ep["chapters"][t["chapter"]]["title"], "text": t["text"],
         "start": round(t["start_ms"] / 1000, 2), "end": round(t["end_ms"] / 1000, 2)} for t in turns]}, indent=2) + "\n")
    notes = [f"# {ep['title']}\n", "Hosts: " + ", ".join(speakers) + "\n", "## Chapters\n"]
    notes += [f"- {hms(c['start_ms'])} {c['title']}" for c in chapters]
    (out / "show-notes.md").write_text("\n".join(notes) + "\n", encoding="utf-8")
    files = ["episode.wav"] + (["episode.mp3"] if mp3 else []) + ["transcript.txt", "transcript.srt", "transcript.vtt",
                                                                  "transcript.json", "chapters.json", "show-notes.md"]
    return {"title": ep["title"], "duration": hms(total), "duration_s": round(total / 1000, 2), "hosts": speakers,
            "chapters": [{"start": hms(c["start_ms"]), "title": c["title"]} for c in chapters],
            "generated": len(todo), "reused": len(turns) - len(todo), "files": [str(out / f) for f in files],
            "warnings": [] if mp3 else ["ffmpeg not found: wrote episode.wav only (install ffmpeg for MP3 with ID3 chapters)."]}


def build_parser():
    p = argparse.ArgumentParser(prog="podcast.py", description="Two-host AI podcast generator (Miragea voice engine).")
    p.add_argument("--version", action="version", version=VERSION)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check", help="verify the key and list models / emotion tags")
    for name in ("make", "plan"):
        g = sub.add_parser(name, help="voice the script into an episode" if name == "make" else "dry run, no API calls")
        g.add_argument("script", help="Markdown script: ## Chapter headings, 'Name: line' turns")
        g.add_argument("--out", default="episode")
        g.add_argument("--host", action="append", metavar="NAME=VOICE", help="NAME=ACTOR_ID or NAME=https://clip.wav|exact words (repeat per host)")
        g.add_argument("--model")
        g.add_argument("--tag", action="append")
        g.add_argument("--speed", type=float, default=1.0)
        g.add_argument("--gap", type=float, default=350, help="ms between turns")
        g.add_argument("--chapter-gap", type=float, default=900)
        g.add_argument("--lead-in", type=float, default=200)
        g.add_argument("--tail", type=float, default=800)
        g.add_argument("--max-chars", type=int, default=42)
        g.add_argument("--take", type=int, default=1)
        g.add_argument("--poll-timeout", type=float, default=900)
    return p


def main(argv=None, engine=None):
    args = build_parser().parse_args(argv)
    log = lambda m: print(m, file=sys.stderr)
    try:
        if args.cmd == "plan":
            res = cmd_make(args, None, log, dry=True)
        else:
            engine = engine or Engine(poll_timeout=getattr(args, "poll_timeout", 900), ua_name="ai-podcast-generator-skill")
            if args.cmd == "check":
                a = engine.capabilities().get("audio", {})
                res = {"ok": True, "base_url": engine.base, "default_model": a.get("default_model"),
                       "models": {m["id"]: [t["label"] for t in m.get("tags", [])] for m in a.get("models", [])},
                       "max_text_length": a.get("max_text_length")}
            else:
                res = cmd_make(args, engine, log)
    except EngineError as e:
        print(json.dumps({"ok": False, "error": str(e), "help": INSTALL_DOC}), file=sys.stderr)
        return 2
    print(json.dumps(res, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

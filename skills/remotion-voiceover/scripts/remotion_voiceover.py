#!/usr/bin/env python3
"""Remotion voiceover skill helper: script -> per-scene AI voiceover, voiceover.json timings and
@remotion/captions captions.json (Python 3.9+, standard library only).

  check                      verify MIRAGEA_API_KEY, list models and emotion tags
  plan SCRIPT                dry run: scenes, lines, rough length (no API calls)
  generate SCRIPT            per-line WAVs + voiceover.wav + voiceover.json + captions.json/.srt/.vtt in public/voiceover
  npx ...                    delegate to the Node CLI: npx github:RTY2345RR/remotion-voiceover ...

Voice engine: Miragea (https://miragea.ai/v1), key in MIRAGEA_API_KEY. Re-running is safe: identical lines are
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


# --------------------------------------------------------------------------- script parsing
DIRECTIVE = re.compile(r"^\s*\[(voice|emotion|tags?|speed|pause|model)\s*:\s*([^\]]*)\]\s*", re.I)
ALT = re.compile(r"\[\[([^\]|]*)\|([^\]]*)\]\]")


def slugify(text):
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:48]
    return s or "scene"


def parse_front_matter(text):
    meta, voices = {}, {}
    if not text.startswith("---"):
        return meta, voices, text
    end = text.find("\n---", 3)
    if end < 0:
        return meta, voices, text
    block, body = text[3:end], text[end + 4:]
    section = None
    for raw in block.split("\n"):
        if not raw.strip() or raw.strip().startswith("#"):
            continue
        indented = raw[:1] in (" ", "\t")
        key, _, val = raw.strip().partition(":")
        key, val = key.strip(), val.strip()
        if indented and section == "voices":
            voices[key.lower()] = val
        elif not indented:
            section = key if not val else None
            if val:
                meta[key] = val
    return meta, voices, body


def eat(text, target):
    m = DIRECTIVE.match(text)
    while m:
        k, v = m.group(1).lower(), m.group(2).strip()
        if k == "voice":
            target["voice"] = v
        elif k == "model":
            target["model"] = v
        elif k == "speed":
            target["speed"] = float(v)
        elif k == "pause":
            mm = re.match(r"^(\d+(?:\.\d+)?)\s*(ms|s)?$", v, re.I)
            if not mm:
                raise EngineError(f'Invalid pause "{v}". Use e.g. 500ms or 1.5s.')
            target["pause_ms"] = target.get("pause_ms", 0) + float(mm.group(1)) * (1000 if (mm.group(2) or "ms").lower() == "s" else 1)
        else:
            target.setdefault("tags", []).extend(t.strip() for t in v.split(",") if t.strip())
        text = text[m.end():]
        m = DIRECTIVE.match(text)
    return text


def parse_script(text):
    """Markdown narration script -> scenes and lines.

    ---                                  optional front matter
    voices:
      narrator: https://clip.wav | exact words in the clip
      host: ACTOR_ID
    voice: narrator                      default voice
    fps: 30
    ---
    # intro [voice: host]                heading = Remotion scene (<Sequence>)
    [emotion: excited] Ship it today.    one spoken line per row
    It costs [[$49|forty-nine dollars]]. caption text | spoken text
    [pause: 600ms]
    """
    meta, voices, body = parse_front_matter(text.replace("\r", ""))
    scenes, scene = [], None
    pending = {}
    for raw in body.split("\n"):
        s = raw.strip()
        if not s or s.startswith("<!--"):
            continue
        if s.startswith("#"):
            title = s.lstrip("#").strip()
            opts = {}
            m = re.search(r"\[(voice|emotion|tags?|speed|model)\s*:[^\]]*\]", title, re.I)
            if m:
                eat(title[m.start():], opts)
                title = title[:m.start()].strip()
            scene = {"id": slugify(title), "title": title, "opts": opts, "lines": []}
            base, k = scene["id"], 2
            while any(x["id"] == scene["id"] for x in scenes):
                scene["id"] = f"{base}-{k}"
                k += 1
            scenes.append(scene)
            continue
        opts = dict(pending)
        pending = {}
        rest = eat(s, opts)
        if not rest.strip():
            pending = {"pause_ms": opts.get("pause_ms", 0)} if opts.get("pause_ms") else {}
            continue
        if scene is None:
            scene = {"id": "scene", "title": "Scene", "opts": {}, "lines": []}
            scenes.append(scene)
        caption = re.sub(r"\s+", " ", ALT.sub(lambda m: m.group(1), rest)).strip()
        spoken = re.sub(r"\s+", " ", ALT.sub(lambda m: m.group(2), rest)).strip()
        merged = {**scene["opts"], **opts}
        if scene["opts"].get("tags") or opts.get("tags"):
            merged["tags"] = (scene["opts"].get("tags") or []) + (opts.get("tags") or [])
        scene["lines"].append({"id": f"{scene['id']}-{len(scene['lines']) + 1:02d}", "scene": scene["id"],
                               "caption": caption, "text": spoken, **{k: v for k, v in merged.items()}})
    scenes = [s for s in scenes if s["lines"]]
    if not scenes:
        raise EngineError("The script has no spoken lines.")
    return {"meta": meta, "voices": voices, "scenes": scenes}


def resolve_voice(name, voices, args):
    if name:
        spec = voices.get(name.lower(), name)
        v = parse_voice(spec)
        if v:
            return v
    if args.voice:
        return parse_voice(voices.get(args.voice.lower(), args.voice))
    if args.reference_audio:
        return parse_voice(args.reference_audio + (("|" + args.reference_text) if args.reference_text else ""))
    raise EngineError("No voice: pass --voice ACTOR_ID, --reference-audio URL, or define voices in the script front matter.")


def line_payload(line, script, args):
    p = {"text": line["text"], "format": "wav",
         **resolve_voice(line.get("voice") or script["meta"].get("voice"), script["voices"], args)}
    model = line.get("model") or script["meta"].get("model") or args.model
    if model:
        p["model"] = model
    tags = line.get("tags") or args.tag
    if tags:
        p["tags"] = tags
    sp = line.get("speed") or args.speed
    if sp and abs(sp - 1) > 1e-6:
        p["speed"] = round(sp, 3)
    return p


# --------------------------------------------------------------------------- outputs
def frames(ms, fps):
    return int(round(ms / 1000 * fps))


def remotion_captions(lines):
    """@remotion/captions Caption[]: tokens carry a leading space except the first of each line."""
    out = []
    for ln in lines:
        for k, (w, s, e) in enumerate(word_times(ln["caption"], ln["speech_start_ms"], ln["speech_end_ms"])):
            out.append({"text": (" " if k else "") + w, "startMs": int(round(s)), "endMs": int(round(e)),
                        "timestampMs": int(round((s + e) / 2)), "confidence": None})
    return out


def srt_from_lines(lines, max_chars):
    cues = []
    for ln in lines:
        for g in balanced_groups(word_times(ln["caption"], ln["speech_start_ms"], ln["speech_end_ms"]), max_chars):
            cues.append((g[0][1], g[-1][2], " ".join(w[0] for w in g)))
    srt = "\n".join(f"{i}\n{stamp(s)} --> {stamp(e)}\n{t}\n" for i, (s, e, t) in enumerate(cues, 1))
    vtt = "WEBVTT\n\n" + "\n".join(f"{stamp(s, '.')} --> {stamp(e, '.')}\n{t}\n" for s, e, t in cues)
    return srt, vtt


def find_public_dir(start):
    p = Path(start).resolve()
    for d in [p, *p.parents]:
        if (d / "package.json").exists() and (d / "public").is_dir():
            return d / "public"
    return None


def cmd_generate(args, engine, log, dry=False):
    script = parse_script(Path(args.script).read_text(encoding="utf-8"))
    fps = int(args.fps or script["meta"].get("fps") or 30)
    public = Path(args.public_dir) if args.public_dir else (find_public_dir(Path(args.script).parent) or Path("public"))
    out = Path(args.out) if args.out else public / "voiceover"
    try:
        static_prefix = out.resolve().relative_to(public.resolve()).as_posix()
    except ValueError:
        static_prefix = out.name
    lines = [ln for sc in script["scenes"] for ln in sc["lines"]]
    for ln in lines:
        ln["payload"] = line_payload(ln, script, args)
    if dry:
        est = sum(len(l["text"].split()) / 2.6 * 1000 for l in lines)
        return {"scenes": [s["id"] for s in script["scenes"]], "lines": len(lines), "estimated_seconds": round(est / 1000, 1),
                "fps": fps, "out": str(out), "voices": sorted({json.dumps(l["payload"].get("actor_id") or l["payload"].get("reference_audio_url")) for l in lines})}
    out.mkdir(parents=True, exist_ok=True)
    cache = Cache(out / ".remotion-voiceover-cache.json")
    todo = [(l["id"], l["payload"], out / f"{l['id']}.wav") for l in lines
            if not cache.fresh(l["id"], l["payload"], args.take, out / f"{l['id']}.wav")]
    for l in lines:
        if all(t[0] != l["id"] for t in todo):
            log(f"  = {l['id']}: unchanged, reused")
    for name, payload in synthesize_all(engine, todo, args.take, log, "rvo"):
        cache.mark(name, payload, args.take)

    cursor, clips, prev_scene = args.lead_in, [], None
    for ln in lines:
        if prev_scene is not None and ln["scene"] != prev_scene:
            cursor += args.scene_gap - args.gap
        cursor += ln.get("pause_ms", 0)
        params, fr = read_wav((out / f"{ln['id']}.wav").read_bytes())
        dur = wav_ms(params, fr)
        s0, s1 = speech_bounds(params, fr)
        ln.update(start_ms=cursor, end_ms=cursor + dur, speech_start_ms=cursor + s0, speech_end_ms=cursor + s1)
        clips.append((cursor, params, fr))
        cursor += dur + args.gap
        prev_scene = ln["scene"]
    total = cursor - args.gap + args.tail
    write_track(out / "voiceover.wav", clips, total)
    mp3 = to_mp3(out / "voiceover.wav") if args.mp3 else None

    def L(ln):
        return {"id": ln["id"], "scene": ln["scene"], "text": ln["caption"], "spoken": ln["text"],
                "src": f"{static_prefix}/{ln['id']}.wav", "startMs": int(round(ln["start_ms"])), "endMs": int(round(ln["end_ms"])),
                "startFrame": frames(ln["start_ms"], fps), "durationInFrames": max(1, frames(ln["end_ms"] - ln["start_ms"], fps))}
    scenes = []
    for sc in script["scenes"]:
        a, b = sc["lines"][0]["start_ms"], sc["lines"][-1]["end_ms"]
        scenes.append({"id": sc["id"], "title": sc["title"], "startMs": int(round(a)), "endMs": int(round(b)),
                       "startFrame": frames(a, fps), "durationInFrames": max(1, frames(b - a, fps)), "lines": [L(x) for x in sc["lines"]]})
    timeline = {"version": 1, "fps": fps, "durationMs": int(round(total)), "durationInFrames": max(1, frames(total, fps)),
                "audio": f"{static_prefix}/voiceover.wav", "captions": f"{static_prefix}/captions.json",
                "scenes": scenes, "lines": [L(x) for x in lines]}
    (out / "voiceover.json").write_text(json.dumps(timeline, indent=2) + "\n", encoding="utf-8")
    (out / "captions.json").write_text(json.dumps(remotion_captions(lines), indent=2) + "\n", encoding="utf-8")
    srt, vtt = srt_from_lines(lines, args.max_chars)
    (out / "captions.srt").write_text(srt, encoding="utf-8")
    (out / "captions.vtt").write_text(vtt, encoding="utf-8")
    files = ["voiceover.json", "captions.json", "captions.srt", "captions.vtt", "voiceover.wav"] + (["voiceover.mp3"] if mp3 else [])
    return {"out": str(out), "durationInFrames": timeline["durationInFrames"], "fps": fps, "lines": len(lines),
            "generated": len(todo), "reused": len(lines) - len(todo), "files": [str(out / f) for f in files],
            "next_step": f"In Root.tsx set durationInFrames={timeline['durationInFrames']} (or use calculateMetadata with voiceover.json)."}


def cmd_npx(args, rest, log):
    """Delegate to the Node CLI (npx github:RTY2345RR/remotion-voiceover) when Node is available."""
    if not shutil.which("npx"):
        raise EngineError("npx was not found. Install Node.js 18+ or run this script's own `generate` command.")
    env = dict(os.environ)
    env.setdefault("NODE_USE_ENV_PROXY", "1")
    cmd = ["npx", "-y", "github:RTY2345RR/remotion-voiceover", *rest]
    log("$ " + " ".join(cmd))
    return {"exit_code": subprocess.run(cmd, env=env).returncode}


def build_parser():
    p = argparse.ArgumentParser(prog="remotion_voiceover.py", description="AI voiceover + captions for Remotion projects.")
    p.add_argument("--version", action="version", version=VERSION)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check", help="verify the key and list models / emotion tags")
    for name in ("generate", "plan"):
        g = sub.add_parser(name, help="script -> per-scene audio, voiceover.json, captions.json" if name == "generate" else "dry run, no API calls")
        g.add_argument("script")
        g.add_argument("--out", help="output folder (default: <remotion>/public/voiceover)")
        g.add_argument("--public-dir")
        g.add_argument("--voice", help="actor id, a voice name from front matter, or 'https://clip.wav|exact words'")
        g.add_argument("--reference-audio")
        g.add_argument("--reference-text")
        g.add_argument("--model")
        g.add_argument("--tag", action="append")
        g.add_argument("--speed", type=float, default=1.0)
        g.add_argument("--fps", type=int)
        g.add_argument("--gap", type=float, default=250)
        g.add_argument("--scene-gap", type=float, default=600)
        g.add_argument("--lead-in", type=float, default=0)
        g.add_argument("--tail", type=float, default=500)
        g.add_argument("--max-chars", type=int, default=32)
        g.add_argument("--take", type=int, default=1)
        g.add_argument("--mp3", action="store_true", help="also write voiceover.mp3 (ffmpeg)")
        g.add_argument("--poll-timeout", type=float, default=900)
    sub.add_parser("npx", help="pass the remaining arguments to npx github:RTY2345RR/remotion-voiceover")
    return p


def main(argv=None, engine=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    log = lambda m: print(m, file=sys.stderr)
    try:
        if argv[:1] == ["npx"]:
            res = cmd_npx(None, argv[1:], log)
        else:
            args = build_parser().parse_args(argv)
            if args.cmd == "plan":
                res = cmd_generate(args, None, log, dry=True)
            else:
                engine = engine or Engine(poll_timeout=getattr(args, "poll_timeout", 900), ua_name="remotion-voiceover-skill")
                if args.cmd == "check":
                    a = engine.capabilities().get("audio", {})
                    res = {"ok": True, "base_url": engine.base, "default_model": a.get("default_model"),
                           "models": {m["id"]: [t["label"] for t in m.get("tags", [])] for m in a.get("models", [])},
                           "max_text_length": a.get("max_text_length")}
                else:
                    res = cmd_generate(args, engine, log)
    except EngineError as e:
        print(json.dumps({"ok": False, "error": str(e), "help": INSTALL_DOC}), file=sys.stderr)
        return 2
    print(json.dumps(res, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

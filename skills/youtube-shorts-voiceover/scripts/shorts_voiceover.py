#!/usr/bin/env python3
"""YouTube Shorts voiceover & dubbing helper (Python 3.9+, standard library only).

Commands
  check                       Verify the API key and list models / emotion tags.
  voice  SCRIPT  --out DIR    Script -> voiceover.wav (+ .mp3), captions.srt, captions.ass, report.json
  dub    SRT     --out DIR    Translated SRT -> slot-fitted dub track, dub.srt, report.json
  mux    VIDEO   --audio A    Put the voiceover (and optional burned-in captions) on a vertical video (ffmpeg)

Voice engine: Miragea (https://miragea.ai/v1). Key: MIRAGEA_API_KEY. Base URL: MIRAGEA_BASE_URL.
Voices: --voice ACTOR_ID, or clone with --reference-audio URL [--reference-text "exact words"].
Identical requests reuse the same idempotency key, so a retry or re-run never creates duplicate jobs.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
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
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      f"Chrome/124.0 Safari/537.36 youtube-shorts-voiceover/{VERSION}")
SHORTS_MAX_S = 180.0


class EngineError(Exception):
    pass


# --------------------------------------------------------------------------- API
class Engine:
    def __init__(self, key=None, base=None, sleep=time.sleep, poll_timeout=900):
        self.key = key if key is not None else os.environ.get("MIRAGEA_API_KEY", "")
        if not self.key:
            raise EngineError(f"MIRAGEA_API_KEY is not set. Create a voice engine key at {KEY_PAGE} and export it.")
        self.base = (base or os.environ.get("MIRAGEA_BASE_URL") or DEFAULT_BASE).rstrip("/")
        self.sleep = sleep
        self.poll_timeout = poll_timeout

    def call(self, method, path, body=None, idem=None, retries=3):
        headers = {"Authorization": f"Bearer {self.key}", "Accept": "application/json", "User-Agent": UA}
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

    def synthesize(self, payload, take=1, log=lambda m: None):
        idem = "svo-" + hashlib.sha256(json.dumps({"p": payload, "t": take}, sort_keys=True).encode()).hexdigest()[:40]
        task = self.call("POST", "/audio/generations", payload, idem)
        tid = task.get("task_id")
        deadline = time.monotonic() + self.poll_timeout
        while task.get("status") != "succeeded":
            if task.get("status") == "failed":
                raise EngineError(f"Task {tid} failed: {(task.get('error') or {}).get('message', 'unknown error')}. "
                                  "Use --take 2 to request a new take.")
            if time.monotonic() > deadline:
                raise EngineError(f"Task {tid} is still {task.get('status')}. Re-run the same command to pick it up.")
            self.sleep(max(0.5, float(task.get("poll_after_seconds") or 2)))
            task = self.call("GET", f"/tasks/{tid}")
        url = (task.get("result") or {}).get("audio_url", "")
        if not url.startswith(("http://", "https://")):
            raise EngineError(f"Task {tid} returned no audio_url.")
        req = urllib.request.Request(url, headers={"User-Agent": UA})  # pre-signed: no API key here
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.read()


# --------------------------------------------------------------------------- audio
def read_wav(data: bytes):
    with wave.open(io.BytesIO(data)) as w:
        params = w.getparams()
        frames = w.readframes(w.getnframes())
    return params, frames


def wav_ms(params, frames) -> float:
    return len(frames) / (params.sampwidth * params.nchannels) / params.framerate * 1000.0


def write_track(path: Path, clips, total_ms: float):
    """clips: [(start_ms, params, frames)] -> one WAV with silence between."""
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
            out = out[:pos]  # never overlap: the later clip wins
        out += frames
    end = int(round(total_ms / 1000 * p0.framerate)) * fb
    if end > len(out):
        out += b"\x00" * (end - len(out))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(p0.nchannels)
        w.setsampwidth(p0.sampwidth)
        w.setframerate(p0.framerate)
        w.writeframes(bytes(out))


def to_mp3(wav_path: Path):
    if not shutil.which("ffmpeg"):
        return None
    mp3 = wav_path.with_suffix(".mp3")
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(wav_path), "-codec:a", "libmp3lame", "-q:a", "2", str(mp3)], check=True)
    return mp3


# --------------------------------------------------------------------------- text & captions
def syllables(word: str) -> int:
    w = re.sub(r"[^a-z0-9']", "", word.lower())
    if not w:
        return 1
    if w.isdigit():
        return max(1, len(w))
    groups = re.findall(r"[aeiouy]+", re.sub(r"e$", "", w))
    return max(1, len(groups))


def word_times(text: str, start: float, end: float):
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


def stamp(ms: float, sep=",") -> str:
    ms = max(0, int(round(ms)))
    return f"{ms // 3600000:02d}:{ms % 3600000 // 60000:02d}:{ms % 60000 // 1000:02d}{sep}{ms % 1000:03d}"


def short_cues(lines, max_chars=24):
    """Split each voiced line into short vertical-video cues using word timing."""
    cues = []
    for ln in lines:
        cur = []
        for w in word_times(ln["text"], ln["start_ms"], ln["end_ms"]):
            if cur and len(" ".join(x[0] for x in cur)) + 1 + len(w[0]) > max_chars:
                cues.append(cur)
                cur = []
            cur.append(w)
        if cur:
            cues.append(cur)
    return [{"text": " ".join(w[0] for w in c), "start_ms": c[0][1], "end_ms": c[-1][2], "words": c} for c in cues]


def srt(cues) -> str:
    return "\n".join(f"{i}\n{stamp(c['start_ms'])} --> {stamp(c['end_ms'])}\n{c['text']}\n" for i, c in enumerate(cues, 1))


def parse_srt(text: str):
    cues = []
    for block in re.split(r"\n\s*\n", text.replace("\r", "").strip()):
        rows = [r for r in block.split("\n") if r.strip()]
        for i, r in enumerate(rows):
            m = re.match(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)", r)
            if m:
                g = list(map(int, m.groups()))
                s = ((g[0] * 60 + g[1]) * 60 + g[2]) * 1000 + g[3]
                e = ((g[4] * 60 + g[5]) * 60 + g[6]) * 1000 + g[7]
                txt = " ".join(rows[i + 1:]).strip()
                speaker = None
                sm = re.match(r"^\[([^\]]+)\]\s*(.*)$", txt)
                if sm:
                    speaker, txt = sm.group(1).strip(), sm.group(2)
                if txt:
                    cues.append({"start_ms": s, "end_ms": e, "text": re.sub(r"<[^>]+>", "", txt), "speaker": speaker})
                break
    if not cues:
        raise EngineError("No cues found in the SRT file.")
    return cues


def ass(cues, width=1080, height=1920) -> str:
    """Karaoke-style ASS subtitles: big bold centered words, active word highlighted (\\k tags)."""
    def t(ms):
        cs = max(0, int(round(ms / 10)))
        return f"{cs // 360000}:{cs % 360000 // 6000:02d}:{cs % 6000 // 100:02d}.{cs % 100:02d}"
    head = (f"[Script Info]\nScriptType: v4.00+\nPlayResX: {width}\nPlayResY: {height}\nWrapStyle: 0\n\n"
            "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
            "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
            "Style: Shorts,Arial,84,&H0000E5FF,&H00FFFFFF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,6,2,5,60,60,0,1\n\n"
            "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
    rows = []
    for c in cues:
        parts = []
        for w, s, e in c["words"]:
            parts.append("{\\k%d}%s" % (max(1, int(round((e - s) / 10))), w.replace("{", "(").replace("}", ")")))
        rows.append(f"Dialogue: 0,{t(c['start_ms'])},{t(c['end_ms'])},Shorts,,0,0,0,,{' '.join(parts)}")
    return head + "\n".join(rows) + "\n"


def script_lines(text: str):
    """One spoken line per non-empty line; '[speaker] text' or 'SPEAKER: text' picks a named voice."""
    out = []
    for raw in text.replace("\r", "").split("\n"):
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        speaker = None
        m = re.match(r"^\[([^\]]+)\]\s*(.+)$", s) or re.match(r"^([A-Z][A-Za-z0-9 _-]{0,20}):\s+(.+)$", s)
        if m:
            speaker, s = m.group(1).strip(), m.group(2).strip()
        out.append({"text": s, "speaker": speaker})
    if not out:
        raise EngineError("The script has no spoken lines.")
    return out


# --------------------------------------------------------------------------- voices
def voice_payload(args, speaker=None):
    voices = {}
    for spec in args.speaker_voice or []:
        name, _, val = spec.partition("=")
        voices[name.strip().lower()] = val.strip()
    chosen = voices.get((speaker or "").lower()) or args.voice
    if chosen and chosen.startswith(("http://", "https://")):
        url, _, txt = chosen.partition("|")
        p = {"reference_audio_url": url.strip()}
        if txt.strip():
            p["reference_text"] = txt.strip()
        return p
    if chosen:
        return {"actor_id": chosen}
    if args.reference_audio:
        p = {"reference_audio_url": args.reference_audio}
        if args.reference_text:
            p["reference_text"] = args.reference_text
        return p
    raise EngineError("No voice chosen: pass --voice ACTOR_ID or --reference-audio URL (and --speaker-voice NAME=... for each speaker).")


def payload_for(args, text, speaker=None, speed=None):
    p = {"text": text, "format": "wav", **voice_payload(args, speaker)}
    if args.model:
        p["model"] = args.model
    if args.tag:
        p["tags"] = args.tag
    sp = speed if speed is not None else args.speed
    if sp and abs(sp - 1) > 1e-6:
        p["speed"] = round(sp, 3)
    return p


class Cache:
    def __init__(self, out: Path):
        self.path = out / ".voiceover-cache.json"
        try:
            self.data = json.loads(self.path.read_text())
        except Exception:
            self.data = {}

    def get(self, engine, out, name, payload, take, log):
        h = hashlib.sha256(json.dumps({"p": payload, "t": take}, sort_keys=True).encode()).hexdigest()[:16]
        f = out / f"{name}.wav"
        if self.data.get(name) == h and f.exists():
            log(f"  = {name} reused")
            return f.read_bytes()
        log(f"  ~ {name}: {payload['text'][:50]}")
        data = engine.synthesize(payload, take=take, log=log)
        f.write_bytes(data)
        self.data[name] = h
        self.path.write_text(json.dumps(self.data, indent=2))
        return data


# --------------------------------------------------------------------------- commands
def cmd_voice(args, engine, log):
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    lines = script_lines(Path(args.script).read_text(encoding="utf-8"))
    cache = Cache(out)
    cursor, clips = args.lead_in, []
    for i, ln in enumerate(lines, 1):
        data = cache.get(engine, out, f"line-{i:02d}", payload_for(args, ln["text"], ln["speaker"]), args.take, log)
        params, frames = read_wav(data)
        dur = wav_ms(params, frames)
        ln.update(start_ms=cursor, end_ms=cursor + dur, duration_ms=dur)
        clips.append((cursor, params, frames))
        cursor += dur + args.gap
    total = cursor - args.gap + args.tail
    write_track(out / "voiceover.wav", clips, total)
    mp3 = to_mp3(out / "voiceover.wav")
    cues = short_cues(lines, args.max_chars)
    (out / "captions.srt").write_text(srt(cues), encoding="utf-8")
    (out / "captions.ass").write_text(ass(cues), encoding="utf-8")
    words = sum(len(l["text"].split()) for l in lines)
    speech_s = sum(l["duration_ms"] for l in lines) / 1000
    report = {
        "duration_s": round(total / 1000, 2),
        "fits_shorts": total / 1000 <= args.max_seconds,
        "max_seconds": args.max_seconds,
        "words_per_second": round(words / speech_s, 2) if speech_s else 0,
        "hook_s": round(lines[0]["duration_ms"] / 1000, 2),
        "hook_under_3s": lines[0]["duration_ms"] <= 3000,
        "lines": [{k: (round(v) if isinstance(v, float) else v) for k, v in l.items()} for l in lines],
        "files": sorted(str(p) for p in [out / "voiceover.wav", out / "captions.srt", out / "captions.ass"] + ([mp3] if mp3 else [])),
    }
    warn = []
    if not report["fits_shorts"]:
        warn.append(f"Voiceover is {report['duration_s']}s; trim the script to fit {args.max_seconds:g}s.")
    if not report["hook_under_3s"]:
        warn.append("Hook line is longer than 3 s; shorten the first line for retention.")
    if report["words_per_second"] > 3.6:
        warn.append("Very fast delivery (>3.6 words/s); cut words rather than speeding up.")
    report["warnings"] = warn
    (out / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def cmd_dub(args, engine, log):
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    cues = parse_srt(Path(args.srt).read_text(encoding="utf-8"))
    cache = Cache(out)
    clips, rows = [], []
    for i, c in enumerate(cues, 1):
        slot = c["end_ms"] - c["start_ms"]
        name = f"cue-{i:03d}"
        data = cache.get(engine, out, name, payload_for(args, c["text"], c["speaker"]), args.take, log)
        params, frames = read_wav(data)
        dur = wav_ms(params, frames)
        speed = 1.0
        if dur > slot * 1.02 and args.max_speed > 1:
            speed = min(args.max_speed, dur / slot)
            data = cache.get(engine, out, name + "-fit", payload_for(args, c["text"], c["speaker"], speed=speed * (args.speed or 1)), args.take, log)
            params, frames = read_wav(data)
            dur = wav_ms(params, frames)
        overflow = max(0.0, dur - slot)
        clips.append((c["start_ms"], params, frames))
        rows.append({"cue": i, "speaker": c["speaker"], "text": c["text"], "slot_ms": slot, "voiced_ms": round(dur),
                     "speed": round(speed, 3), "overflow_ms": round(overflow), "ok": overflow <= args.tolerance})
    total = max(c["end_ms"] for c in cues) + args.tail
    total = max(total, max(s + wav_ms(p, f) for s, p, f in clips))
    write_track(out / "dub.wav", clips, total)
    mp3 = to_mp3(out / "dub.wav")
    (out / "dub.srt").write_text(srt([{"text": (f"[{c['speaker']}] " if c["speaker"] and args.keep_speaker_labels else "") + c["text"],
                                        "start_ms": c["start_ms"], "end_ms": c["end_ms"]} for c in cues]), encoding="utf-8")
    bad = [r for r in rows if not r["ok"]]
    report = {"cues": rows, "overflowing_cues": [r["cue"] for r in bad], "duration_s": round(total / 1000, 2),
              "files": [str(out / "dub.wav"), str(out / "dub.srt")] + ([str(mp3)] if mp3 else []),
              "next_step": ("All cues fit their slots." if not bad else
                            "Shorten the text of the overflowing cues (keep meaning, cut filler) and run dub again; unchanged cues are reused.")}
    (out / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def cmd_mux(args, engine, log):
    if not shutil.which("ffmpeg"):
        raise EngineError("ffmpeg is required for mux.")
    cmd = ["ffmpeg", "-loglevel", "error", "-y", "-i", args.video, "-i", args.audio]
    if args.keep_original > 0:
        fc = f"[0:a]volume={args.keep_original}[a0];[1:a]volume=1.0[a1];[a0][a1]amix=inputs=2:duration=first:normalize=0[a]"
        amap = "[a]"
    else:
        fc, amap = None, "1:a"
    vf = None
    if args.captions:
        vf = "subtitles=" + args.captions.replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
    if fc:
        cmd += ["-filter_complex", fc]
    cmd += ["-map", "0:v", "-map", amap]
    if vf:
        cmd += ["-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "20"]
    else:
        cmd += ["-c:v", "copy"]
    cmd += ["-c:a", "aac", "-b:a", "192k", "-shortest", args.out]
    subprocess.run(cmd, check=True)
    return {"video": args.out}


def build_parser():
    p = argparse.ArgumentParser(prog="shorts_voiceover.py", description=__doc__.split("\n")[0])
    p.add_argument("--version", action="version", version=VERSION)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check")

    def voice_opts(sp):
        sp.add_argument("--out", required=True)
        sp.add_argument("--voice", help="actor id, or 'https://clip.wav|exact words' to clone")
        sp.add_argument("--reference-audio")
        sp.add_argument("--reference-text")
        sp.add_argument("--speaker-voice", action="append", metavar="NAME=VOICE", help="voice per speaker label (repeat)")
        sp.add_argument("--model")
        sp.add_argument("--tag", action="append", help="emotion/style tag label from `check` (repeat)")
        sp.add_argument("--speed", type=float, default=1.0)
        sp.add_argument("--take", type=int, default=1)
        sp.add_argument("--tail", type=float, default=300)

    v = sub.add_parser("voice")
    v.add_argument("script")
    voice_opts(v)
    v.add_argument("--gap", type=float, default=180, help="ms between lines")
    v.add_argument("--lead-in", type=float, default=0)
    v.add_argument("--max-chars", type=int, default=24)
    v.add_argument("--max-seconds", type=float, default=60)

    d = sub.add_parser("dub")
    d.add_argument("srt")
    voice_opts(d)
    d.add_argument("--max-speed", type=float, default=1.15, help="fastest allowed speed-up to fit a slot")
    d.add_argument("--tolerance", type=float, default=150, help="ms a cue may overrun its slot")
    d.add_argument("--keep-speaker-labels", action="store_true")

    m = sub.add_parser("mux")
    m.add_argument("video")
    m.add_argument("--audio", required=True)
    m.add_argument("--captions")
    m.add_argument("--keep-original", type=float, default=0.0, help="original audio volume 0-1 (music bed)")
    m.add_argument("--out", required=True)
    return p


def main(argv=None, engine=None):
    args = build_parser().parse_args(argv)
    log = lambda m: print(m, file=sys.stderr)
    try:
        if args.cmd == "mux":
            res = cmd_mux(args, None, log)
        else:
            engine = engine or Engine()
            if args.cmd == "check":
                caps = engine.capabilities()
                a = caps.get("audio", {})
                res = {"ok": True, "base_url": engine.base, "default_model": a.get("default_model"),
                       "models": {m["id"]: [t["label"] for t in m.get("tags", [])] for m in a.get("models", [])},
                       "formats": a.get("formats"), "max_text_length": a.get("max_text_length")}
            elif args.cmd == "voice":
                res = cmd_voice(args, engine, log)
            else:
                res = cmd_dub(args, engine, log)
    except EngineError as e:
        print(json.dumps({"ok": False, "error": str(e)}), file=sys.stderr)
        return 2
    print(json.dumps(res, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

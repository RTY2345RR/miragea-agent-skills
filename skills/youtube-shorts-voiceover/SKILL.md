---
name: youtube-shorts-voiceover
description: "YouTube Shorts AI voiceover skill: turn a Shorts script into a narrator voiceover with big Shorts-style captions (SRT + karaoke ASS), voice one or two speakers with cloned voices, dub a translated SRT into slot-fitted audio, and mux it onto a vertical 9:16 video with ffmpeg. Use when the user asks for a YouTube Shorts voiceover or narrator voice, AI narration for a vertical video, TikTok or Reels captions with voice, a two-voice Shorts dialogue, or to dub a Short into English."
license: MIT
metadata:
  version: "0.1.0"
  requires: "python 3.9+, ffmpeg (only for mux), MIRAGEA_API_KEY"
  keywords: "youtube shorts ai voiceover, youtube shorts narrator voice, tiktok voiceover, reels captions"
---

# YouTube Shorts voiceover, captions and dubbing

Script in, vertical-video-ready voiceover out: `voiceover.wav/.mp3`, `captions.srt`, `captions.ass` (large Shorts-style captions) and `report.json`. Voices and cloning run on the Miragea engine (https://miragea.ai/v1). Never substitute another TTS.

## 0. Preconditions
- Python 3.9+, no extra packages. `ffmpeg` only for `mux`.
- Key: `MIRAGEA_API_KEY` (create at https://miragea.ai/workspace/api). Never ask for it in chat. Install help: https://miragea.ai/skills/miragea-audio/install.md
- Run `python scripts/shorts_voiceover.py check` first.

## 1. Brief
Ask only what is missing: the script (one spoken line per row), voice (actor id, or a reference clip URL plus its exact words to clone), and whether there are two speakers.

## 2. Commands
- Script to voiceover: `python scripts/shorts_voiceover.py voice script.txt --out out/ --voice ACTOR_ID`
- Two speakers: prefix lines with `[Mia] ...` / `[Leo] ...` and pass `--speaker-voice "Mia=URL|exact words" --speaker-voice "Leo=ACTOR_ID"`.
- Dub a translated SRT into the original slots: `python scripts/shorts_voiceover.py dub translated.srt --out out/ --voice ACTOR_ID`
- Put it on video: `python scripts/shorts_voiceover.py mux clip.mp4 --audio out/voiceover.wav --captions out/captions.ass --out short.mp4`

## 3. Rules
- Shorts stay under 180 seconds; the tool warns past that.
- Re-running the same command resumes and never creates duplicate jobs. Cloned lines can take a couple of minutes each.
- The reference clip URL must be publicly fetchable (not a Hugging Face resolve link).
- Only voice content the user has rights to.

## Example
`examples/` holds a two-speaker run (one male, one female cloned voice): `shorts-voiceover.mp3` and `shorts-captions.srt`.

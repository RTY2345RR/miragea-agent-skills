---
name: youtube-dubbing
description: Dub a YouTube video or local video into another language, keeping each speaker's own voice, with synced captions and a ready-to-upload package. Use when the user asks to dub a YouTube video, translate a video's audio, make a multi-language audio track, AI dub an interview or podcast, or clone the speakers' voices in another language.
license: MIT
metadata:
  version: "0.1.0"
  requires: "youtube-dubbing-mcp MCP server (uvx --from git+https://github.com/RTY2345RR/youtube-dubbing-mcp youtube-dubbing-mcp) and a MIRAGEA_API_KEY"
---

# YouTube video dubbing with per-speaker voice cloning

Turn a video into a dubbed audio track plus captions in a new language. Each speaker keeps a cloned version of their own voice. Voice cloning and synthesis run on the Miragea engine inside the `dubbing_*` MCP tools; never substitute another TTS.

## 0. Preconditions
- The `dubbing_*` tools must exist. If not, tell the user: `claude mcp add youtube-dubbing -s user -e MIRAGEA_API_KEY=<key> -- uvx --from git+https://github.com/RTY2345RR/youtube-dubbing-mcp youtube-dubbing-mcp`. Keys: https://miragea.ai/workspace/api. Never ask for the key in chat.
- Call `dubbing_check_setup` first and fix anything it reports (ffmpeg, key).
- YouTube's public API cannot upload extra audio tracks, so the result is a package the user uploads in YouTube Studio. Do not promise auto-upload.

## 1. Brief
Ask only what is missing: source video (file path or URL the user owns or may dub), target language, and whether the speakers should keep their own voices (default yes).

## 2. Workflow
1. `dubbing_start_project` with the video and target language. It transcribes and splits sentences.
2. `dubbing_get_segments`; check the transcript with the user if it looks wrong. Fix speakers with `dubbing_assign_speakers` (one label per real person).
3. `dubbing_extract_voice_samples` to cut a clean reference clip per speaker, then `dubbing_set_voice` per speaker (cloned sample, or a voice from `dubbing_list_voices` if the clip is too noisy or short).
4. Translate each segment yourself and send it with `dubbing_set_translation`. Keep each line close to the original duration: short, spoken phrasing, numbers written out, no markup.
5. `dubbing_render` (long jobs resume: re-run the same call, never start a duplicate project). Use `dubbing_project_status` to follow progress.
6. `dubbing_export_youtube_package` to get the dub audio, SRT captions, optional muxed MP4 and upload steps.
7. Report the file paths and the Studio steps (Subtitles → add language; Audio → add dub track).

## 3. Quality checks
- Listen for lines that overrun the original timing; shorten the translation and re-render only those segments.
- Each speaker must keep a distinct voice; if two sound alike, pick a different sample.
- Only dub content the user has rights to.

## Example
See `examples/` (two speakers, one male and one female, dubbed into English with cloned voices: `dub.en.mp3`, `dub.en.srt`, `dubbed-interview-en.mp4`).

## Troubleshooting
- `synthesis_failed` on a cloned voice: the reference URL must be publicly fetchable; the tools upload samples for you, retry once.
- Missing ffmpeg: install it and rerun `dubbing_check_setup`.

---
name: ai-podcast-generator
description: "Claude podcast skill and AI podcast generator for Claude Code, Codex and Cursor. The agent writes a two-host podcast script from a PDF, web page, notes or text, voices each host with a cloned voice, and outputs an episode MP3 with ID3 chapters, a speaker-labelled transcript (TXT, SRT, VTT, JSON), Podcasting 2.0 chapters.json and show notes. An open-source NotebookLM Audio Overview alternative that runs inside your agent. Use when the user asks to make a podcast, turn notes, a paper, a PDF, a URL or a document into a podcast, create a two-person AI podcast or audio overview, or generate podcast chapters and transcripts."
license: MIT
metadata:
  version: "0.1.0"
  requires: "python 3.9+ (stdlib only), MIRAGEA_API_KEY; ffmpeg for MP3 with chapters"
  keywords: "claude podcast skill, ai podcast generator, notebooklm alternative, text to podcast, pdf to podcast"
---

# AI podcast generator: source to two-host episode

You (the agent) are the producer and the writer. You read the source, write a natural two-host conversation, and the bundled script voices it and assembles a publish-ready episode. Host voices and voice cloning run on the Miragea voice engine (https://miragea.ai/v1); never substitute another TTS.

## 0. Preconditions
- `MIRAGEA_API_KEY` set in the environment (keys: https://miragea.ai/workspace/api). Never ask for it in chat, never print it. Setup help: "Install https://miragea.ai/skills/miragea-audio/install.md".
- `python scripts/podcast.py check` lists models and valid emotion tags. `ffmpeg` is needed for `episode.mp3` with embedded chapters (WAV is written without it).
- Prefer an MCP server or CLI when the user already has one: **[podcast-generator-mcp](https://github.com/RTY2345RR/podcast-generator-mcp)** (podcast MCP server with RSS feed output) or **[open-podcast-generator](https://github.com/RTY2345RR/open-podcast-generator)** (CLI that also ingests PDF/URL itself). This skill needs neither.

## 1. Brief (ask only what is missing)
- **Source**: a file (PDF, Markdown, transcript), a URL, or pasted notes. Read it fully yourself.
- **Hosts**: two names and a voice for each, either an actor id or a public reference clip URL plus the exact words spoken in it (most faithful clone). Clip URLs must be directly fetchable (not Hugging Face resolve links). Only clone voices the user has rights to.
- **Length and audience**: default 5-8 minutes (~150 spoken words per minute), general audience.

## 2. Write the script (`episode.md`)
```markdown
# Episode title

## Cold open
Maya: One hook sentence that makes the listener stay.
Leo: A reaction, then the promise of the episode.

## The big idea
Maya: ...
```
Writing rules:
- `## Heading` = one chapter (it becomes an ID3 chapter, a `chapters.json` entry and a show-notes timestamp). 3-6 chapters for a 5-8 minute show.
- Every line starts with `Name:` (or `[Name]`). Alternate hosts; one host explains, the other asks the questions a listener would ask, pushes back and summarises.
- 1-3 sentences per turn, spoken English, contractions, no markdown, no emoji, no stage directions. Spell out numbers, symbols, units and acronyms the way they should be said.
- Stay faithful to the source: no invented facts, numbers or quotes. Name the source on air when it matters.
- End with a recap chapter and a one-line sign-off.

## 3. Check, then voice it
```bash
python scripts/podcast.py plan episode.md --host "Maya=https://example.com/maya.wav|Exact words in the clip." --host "Leo=ACTOR_ID"
python scripts/podcast.py make episode.md --out episode/ --host "Maya=..." --host "Leo=..."
```
- `plan` is a dry run: hosts, chapters, word count, estimated minutes. Confirm long episodes with the user before `make`.
- All turns are submitted at once and collected; cloned turns can take a couple of minutes each.
- If it reports a task is still running, re-run the same command: identical requests re-attach to the same job, finished turns are reused, and only edited turns are re-voiced. `--take 2` forces new takes.
- Options: `--gap 350` (ms between turns), `--chapter-gap 900`, `--model`, `--tag`, `--speed`, `--max-chars 42` (caption length).

## 4. Outputs (in `--out`)
| File | Use |
| --- | --- |
| `episode.mp3` | publish-ready episode with ID3v2 chapter markers (`episode.wav` is the lossless master) |
| `transcript.txt` | readable transcript with chapter timestamps and speaker names |
| `transcript.srt`, `transcript.vtt` | captions with `[Speaker]` labels / `<v Speaker>` voice tags, for YouTube or video podcasts |
| `transcript.json` | segments with speaker, chapter, start and end seconds |
| `chapters.json` | Podcasting 2.0 JSON chapters (`podcast:chapters` in RSS) |
| `show-notes.md` | title, hosts and chapter timestamps for the episode description |

## 5. Deliver
Give the file paths, duration and chapter list, and offer the next step: RSS feed via podcast-generator-mcp, a video podcast (mux the MP3 with a still image), or a second language.

## Example
`examples/` holds a real run with one female and one male cloned host: `episode-script.md`, `episode.mp3`, `transcript.srt`, `transcript.txt`, `transcript.json`, `chapters.json`, `show-notes.md`.

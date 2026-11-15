---
name: remotion-voiceover
description: "Remotion skill for Claude Code, Codex and Cursor that adds AI voiceover and captions to a Remotion video project. Turns a narration script into per-scene voiceover audio, a voiceover.json timeline (startFrame, durationInFrames) and @remotion/captions captions.json, plus SRT/VTT, with voice cloning from a reference clip. Use when the user asks for a Remotion voiceover, Remotion TTS or narration, to add a voice or captions (TikTok-style, animated) to a Remotion composition, sync audio to Remotion scenes, or \"claude code remotion\" video with a real voice."
license: MIT
metadata:
  version: "0.1.0"
  requires: "python 3.9+ (stdlib only) or Node 18+ for npx github:RTY2345RR/remotion-voiceover; MIRAGEA_API_KEY; ffmpeg optional (mp3)"
  keywords: "remotion skill, remotion voiceover, claude code remotion skill, remotion captions, remotion tts"
---

# Remotion voiceover and captions

Give a Remotion project a real narrator. The agent writes or takes a narration script, voices it scene by scene, and wires the audio and word-timed captions into the composition. Voices and voice cloning come from the Miragea voice engine (https://miragea.ai/v1); never substitute another TTS.

Outputs (default folder `public/voiceover/`, so `staticFile()` can load them):

| File | What it is |
| --- | --- |
| `<scene>-NN.wav` | one clip per spoken line |
| `voiceover.wav` (+ `.mp3` with `--mp3`) | the stitched narration track |
| `voiceover.json` | timeline: `fps`, `durationInFrames`, scenes and lines with `startMs`, `startFrame`, `durationInFrames`, `src` |
| `captions.json` | `Caption[]` for `@remotion/captions` (`text`, `startMs`, `endMs`, `timestampMs`, `confidence`) |
| `captions.srt`, `captions.vtt` | subtitle files with balanced cues (no one-word orphans) |

## 0. Preconditions
- `MIRAGEA_API_KEY` must be set in the environment (keys: https://miragea.ai/workspace/api). Never ask for the key in chat and never print it. Setup help: "Install https://miragea.ai/skills/miragea-audio/install.md".
- Run `python scripts/remotion_voiceover.py check` once. It lists models and the only valid emotion tags.
- Two engines for the same job, pick one:
  - **Python (bundled, no install):** `python scripts/remotion_voiceover.py generate script.md`
  - **Node CLI:** `npx -y github:RTY2345RR/remotion-voiceover generate script.md` (same script format; adds HyperFrames `audio_request.json`). The bundled script can call it with `python scripts/remotion_voiceover.py npx generate script.md`.

## 1. Brief (ask only what is missing)
- The script, or the source to write it from (product page, README, outline). Keep lines short and spoken; one sentence per row.
- The voice: an actor id, or a public reference clip URL plus the exact words spoken in it (best clone). Clip URLs must be directly fetchable (not Hugging Face resolve links).
- Scene structure if the composition already has `<Sequence>`s; match scene headings to them.

## 2. Write `script.md`
```markdown
---
voices:
  narrator: https://example.com/me.wav | Exact words spoken in the clip.
  guest: ACTOR_ID
voice: narrator
fps: 30
---
# Hook
Your Remotion video finally has a real voiceover.

# Launch [voice: guest]
[emotion: excited] Version [[2.0|two point oh]] ships today.
[pause: 600ms]
Render it once and the captions land on the right frame.
```
- `# Heading` = one scene (its slug is the scene id). `[voice: name]`, `[emotion: tag]`, `[speed: 1.1]`, `[pause: 600ms]` work on a line or a heading.
- `[[caption text|spoken text]]` shows `2.0` in captions while the voice says "two point oh". Spell out numbers, versions and URLs this way.
- Run `plan` first for a dry run (no API calls) and confirm long scripts with the user.

## 3. Generate
`python scripts/remotion_voiceover.py generate script.md` (auto-detects the Remotion `public/` folder; `--out` to override, `--fps`, `--scene-gap 600`, `--gap 250`, `--max-chars 32`, `--mp3`).
- All lines are submitted at once and collected; cloned lines can take a couple of minutes.
- If it reports a task is still running, re-run the same command. Identical requests re-attach to the same job, finished lines are reused, and only edited lines are re-voiced. `--take 2` forces a fresh take.

## 4. Wire it into the composition
```tsx
import {AbsoluteFill, Audio, Sequence, staticFile, useCurrentFrame, useVideoConfig} from 'remotion';
import {createTikTokStyleCaptions, Caption} from '@remotion/captions';
import timeline from '../public/voiceover/voiceover.json';
import captions from '../public/voiceover/captions.json';

export const Narrated: React.FC = () => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const {pages} = createTikTokStyleCaptions({captions: captions as Caption[], combineTokensWithinMilliseconds: 1200});
  const ms = (frame / fps) * 1000;
  const page = pages.find((p) => ms >= p.startMs && ms < p.startMs + p.durationMs);
  return (
    <AbsoluteFill>
      {timeline.lines.map((l) => (
        <Sequence key={l.id} from={l.startFrame} durationInFrames={l.durationInFrames}>
          <Audio src={staticFile(l.src)} />
        </Sequence>
      ))}
      {page && <AbsoluteFill style={{justifyContent: 'flex-end', alignItems: 'center', paddingBottom: 160}}>
        <div style={{fontSize: 64, fontWeight: 800, color: 'white', textShadow: '0 4px 12px #000'}}>{page.text}</div>
      </AbsoluteFill>}
    </AbsoluteFill>
  );
};
```
- Set the composition length from the timeline: `durationInFrames={timeline.durationInFrames}` in `Root.tsx`, or read `voiceover.json` in `calculateMetadata`.
- Scene `<Sequence>`s can use `timeline.scenes[i].startFrame` / `durationInFrames` so visuals follow the narration.
- Install captions support if missing: `npx remotion add @remotion/captions`.
- One `<Audio src={staticFile(timeline.audio)} />` also works instead of per-line clips.

## 5. Deliver
Report the files, the total length in seconds and frames, and the next step (render with `npx remotion render`). Only voice or clone content the user has rights to.

## Example
`examples/` holds a real run: `script.md` (female narrator + male outro, both cloned), `voiceover.mp3`, `captions.srt`, `captions.json` and `voiceover.json`.

# AI Voiceover & Captions Agent Skills for Claude Code, Codex and Cursor

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Agent Skills](https://img.shields.io/badge/Agent%20Skills-SKILL.md-black.svg)](https://agentskills.io)
[![MCP server](https://img.shields.io/badge/MCP-miragea--voice--mcp-6f42c1.svg)](https://github.com/RTY2345RR/miragea-voice-mcp)

**Open-source [Agent Skills](https://docs.anthropic.com/en/docs/agents-and-tools/agent-skills) that teach AI coding agents to produce finished voiceovers and subtitles.** Install a skill, give your agent a script, and it returns a narration audio file plus matching SRT and WebVTT captions, ready for CapCut, Premiere Pro, DaVinci Resolve, YouTube, Shorts, TikTok and Reels. The skills follow the open `SKILL.md` format, so the same folder works in Claude Code, Claude.ai, OpenAI Codex and Cursor. Voiceover, voice cloning and speech recognition run on the [Miragea](https://miragea.ai) voice engine through the companion MCP server, [**miragea-voice-mcp**](https://github.com/RTY2345RR/miragea-voice-mcp).

```text
You:    Turn script.txt into a calm voiceover with captions for a YouTube Short.
Agent:  (uses the miragea-voiceover skill)
        → ~/Miragea/my-short.mp3  ~/Miragea/my-short.srt  ~/Miragea/my-short.vtt
```

## Contents

- [Skills in this repo](#skills-in-this-repo)
- [Requirements](#requirements)
- [Install: Claude Code](#claude-code)
- [Install: OpenAI Codex](#openai-codex)
- [Install: Cursor](#cursor)
- [Install: Claude.ai and Claude Desktop](#claudeai-and-claude-desktop)
- [What the voiceover skill does](#what-the-voiceover-skill-does)
- [Example prompts](#example-prompts)
- [FAQ](#faq)
- [Related](#related)

## Skills in this repo

| Skill | Use it for | Works in |
| --- | --- | --- |
| [`miragea-voiceover`](skills/miragea-voiceover/SKILL.md) | Script → voiceover audio + SRT/VTT captions; subtitles for existing audio or video; voice cloning from a reference clip | Claude Code, Claude.ai, Codex, Cursor, any agent that reads `SKILL.md` |

More video-creator skills (talking-head, movie recap, product video, Shorts) will be added here.

## Requirements

The skill drives the tools of the **[miragea-voice-mcp](https://github.com/RTY2345RR/miragea-voice-mcp)** MCP server, so install that once:

1. Install [uv](https://docs.astral.sh/uv/) (`curl -LsSf https://astral.sh/uv/install.sh | sh` on macOS/Linux, `powershell -c "irm https://astral.sh/uv/install.ps1 | iex"` on Windows).
2. Create a voice-engine API key at **[miragea.ai/workspace/api](https://miragea.ai/workspace/api)**.
3. Add the MCP server to your agent (snippets for each agent are below; print any of them with `uvx --from git+https://github.com/RTY2345RR/miragea-voice-mcp miragea-voice-mcp --print-config <client>`).

> The PyPI package (`uvx miragea-voice-mcp`) is coming soon. Until then the MCP server installs straight from GitHub as shown.

## Claude Code

**Option A — plugin marketplace (recommended):**

```text
/plugin marketplace add RTY2345RR/miragea-agent-skills
/plugin install voiceover-skills@miragea-agent-skills
```

**Option B — copy the folder:**

```bash
git clone https://github.com/RTY2345RR/miragea-agent-skills
mkdir -p ~/.claude/skills && cp -r miragea-agent-skills/skills/miragea-voiceover ~/.claude/skills/
```

Use `.claude/skills/` inside a project instead to share the skill with your team through git.

**Add the MCP server:**

```bash
claude mcp add miragea-voice -s user -e MIRAGEA_API_KEY=<your-miragea-api-key> -- uvx --from git+https://github.com/RTY2345RR/miragea-voice-mcp miragea-voice-mcp
```

## OpenAI Codex

Codex reads skills from `~/.agents/skills/` (user) and `.agents/skills/` (project):

```bash
git clone https://github.com/RTY2345RR/miragea-agent-skills
mkdir -p ~/.agents/skills && cp -r miragea-agent-skills/skills/miragea-voiceover ~/.agents/skills/
```

Add the MCP server to `~/.codex/config.toml`:

```toml
[mcp_servers.miragea-voice]
command = "uvx"
args = ["--from", "git+https://github.com/RTY2345RR/miragea-voice-mcp", "miragea-voice-mcp"]
tool_timeout_sec = 900
env = { MIRAGEA_API_KEY = "<your-miragea-api-key>" }
```

Restart Codex, then run `/skills` or mention `$miragea-voiceover`.

## Cursor

Cursor loads skills from `~/.cursor/skills/` or `~/.agents/skills/` (user) and `.cursor/skills/` or `.agents/skills/` (project):

```bash
git clone https://github.com/RTY2345RR/miragea-agent-skills
mkdir -p ~/.cursor/skills && cp -r miragea-agent-skills/skills/miragea-voiceover ~/.cursor/skills/
```

Add the MCP server to `~/.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "miragea-voice": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/RTY2345RR/miragea-voice-mcp", "miragea-voice-mcp"],
      "env": { "MIRAGEA_API_KEY": "<your-miragea-api-key>" }
    }
  }
}
```

Restart Cursor; type `/miragea-voiceover` in Agent chat or just ask for a voiceover.

## Claude.ai and Claude Desktop

Zip the `skills/miragea-voiceover` folder and upload it under **Settings → Capabilities → Skills**. Add the MCP server to `claude_desktop_config.json` with the same JSON as Cursor above.

## What the voiceover skill does

The [`miragea-voiceover`](skills/miragea-voiceover/SKILL.md) skill turns your agent into a careful voiceover producer:

1. **Gathers the brief** — script, voice (a saved voice or a public reference clip to clone), destination platform and tone; it asks only for what is missing.
2. **Checks live capabilities** — available models, emotion/style tags, formats and length limits, so it never sends an invalid request.
3. **Prepares the script for speech** — spells out numbers, prices, versions and URLs; splits long sentences; strips markdown and emoji; splits scripts that exceed the length limit into parts.
4. **Confirms long jobs** before generating.
5. **Generates audio + captions in one call**, with caption length tuned per platform (24–32 characters for vertical video, 42 for YouTube and courses) and your exact script spelling on the recognized timing.
6. **Recovers safely** — resumes a timed-out job by task ID instead of resubmitting, so there are no duplicate jobs.
7. **Delivers** file paths plus an import tip for CapCut, Premiere Pro, DaVinci Resolve or YouTube.

## Example prompts

- "Make a 30-second product-ad voiceover from `ad.txt` with my *Nova* voice, happy tone, and short captions for TikTok."
- "Clone the voice at `https://example.com/me.wav` (it says 'Hi, I'm Sam and this is my test.') and narrate `intro.md`."
- "Create SRT and VTT subtitles for `~/Videos/vlog.mp4`."
- "Narrate this course lesson as WAV with YouTube-length captions."

## FAQ

### What is an Agent Skill?
A folder with a `SKILL.md` file (name, description, instructions) that an AI agent loads when a task matches. It is an open format used by Claude Code, Claude.ai, OpenAI Codex, Cursor and other agents.

### How do I add AI voiceover to Claude Code?
Install the `miragea-voiceover` skill (plugin marketplace or `~/.claude/skills/`) and the `miragea-voice-mcp` server with `claude mcp add`, as shown in [Claude Code](#claude-code). Then ask Claude for a voiceover.

### Can Codex or Cursor generate subtitles (SRT/VTT) for a video?
Yes. With the skill and MCP server installed, ask "make SRT and VTT subtitles for clip.mp4" and the agent writes `clip.srt` and `clip.vtt` next to your output folder.

### Does it support voice cloning?
Yes, from a public reference audio URL. Giving the exact words spoken in the clip produces the most faithful clone. Only clone voices you have the right to use.

### Which voice engine does it use?
[Miragea](https://miragea.ai) handles voiceover, voice cloning and speech recognition. There is no hidden fallback to another TTS service.

### Is my API key safe?
The key lives only in your MCP config `env` block. The skill never asks you to paste it into chat and never prints it.

## Related

- **MCP server:** [RTY2345RR/miragea-voice-mcp](https://github.com/RTY2345RR/miragea-voice-mcp) — the voiceover & captions tools this skill uses.
- **Live demo:** [Hugging Face Space](https://huggingface.co/spaces/miragea/miragea-voice-mcp)
- **Voice engine API keys:** [miragea.ai/workspace/api](https://miragea.ai/workspace/api)
- **Agent Skills spec:** [agentskills.io](https://agentskills.io) · [Anthropic docs](https://docs.anthropic.com/en/docs/agents-and-tools/agent-skills)

## License

[MIT](LICENSE). Use cloned voices only with the speaker's consent.

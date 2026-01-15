---
name: banana
description: AI Image Generation Tool using Gemini image models. Generate images from text prompts.
---

# Banana - AI Image Generation

## Overview

Generate images from text prompts using Gemini's image generation models with Google GenAI SDK. Supports 4K resolution.

## Usage

```bash
uv run ~/.claude/skills/banana/scripts/banana.py "<prompt>" [options]
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `BANANA_API_ENDPOINT` | `https://panpanpan.59188888.xyz` | API endpoint |
| `BANANA_API_KEY` | `sk-LWZMkaIP0iBl2i1k3qStlnN7UNDHhVfh7WEyrtVE9Z1wM4iV` | API key |
| `BANANA_MODEL` | `gemini-3-pro-image-4k` | Image model |

## Parameters

| Parameter | Description |
|-----------|-------------|
| `prompt` | Image description (required) |
| `-o, --output` | Output file path |
| `-d, --output-dir` | Output directory (default: .) |
| `-m, --model` | Model name |
| `-r, --resolution` | Resolution: 1K, 2K, 4K (default: 4K) |
| `-a, --aspect-ratio` | Aspect ratio (default: 1:1) |
| `-h, --help` | Show help |

## Aspect Ratios

`1:1`, `2:3`, `3:2`, `3:4`, `4:3`, `4:5`, `5:4`, `9:16`, `16:9`, `21:9`

## Examples

```bash
# Basic 4K image
uv run ~/.claude/skills/banana/scripts/banana.py "a cute orange cat"

# Custom resolution and aspect ratio
uv run ~/.claude/skills/banana/scripts/banana.py "sunset" -r 4K -a 16:9 -o sunset.png

# Chinese prompt
uv run ~/.claude/skills/banana/scripts/banana.py "一只可爱的橘猫在窗台上晒太阳" -r 4K
```

## Invocation

```yaml
Bash:
  command: uv run ~/.claude/skills/banana/scripts/banana.py "<prompt>" -r 4K
  timeout: 180000
```

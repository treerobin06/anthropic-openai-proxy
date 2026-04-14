# anthropic-openai-proxy

Run Claude Code with any OpenAI-compatible API (vLLM, Ollama, SJTU, etc.)

A zero-dependency Python proxy that translates Anthropic Messages API → OpenAI Chat Completions, so Claude Code can talk to any OpenAI-compatible backend.

## Features

- **Streaming & non-streaming** responses
- **Tool use** (function calling) with proper Anthropic format conversion
- **Tool schema compression** — truncates descriptions & strips param docs, saving ~55% context for 300+ tools
- **`<system-reminder>` dedup** — strips duplicate reminder blocks from conversation history
- **`<think>` tag extraction** — converts MiniMax-style `<think>` tags to proper thinking blocks
- **`reasoning_content` handling** — converts GLM-5 reasoning output to thinking blocks
- **Streaming tool JSON dedup** — handles vLLM backends that send partial + complete JSON
- **Threaded server** — handles concurrent requests
- **Zero dependencies** — Python 3.9+ stdlib only

## Quick Start

```bash
# Set your OpenAI-compatible API endpoint and key
export OPENAI_BASE="https://api.example.com/v1"
export OPENAI_KEY="sk-your-key"
export MODEL="your-model-name"

# Start proxy
python3 proxy.py

# In another terminal, run Claude Code
ANTHROPIC_BASE_URL=http://localhost:4000 \
ANTHROPIC_AUTH_TOKEN=dummy \
claude
```

## Use with Claude Code (shell alias)

Create `~/bin/cc-custom`:

```bash
#!/bin/bash
export OPENAI_BASE="https://api.example.com/v1"
export OPENAI_KEY="sk-your-key"
export MODEL="your-model-name"

python3 /path/to/proxy.py 4000 &
PROXY_PID=$!
trap "kill $PROXY_PID 2>/dev/null" EXIT

# Wait for proxy
for i in $(seq 1 20); do
    curl -s http://localhost:4000/health >/dev/null 2>&1 && break
    sleep 0.5
done

ANTHROPIC_BASE_URL=http://localhost:4000 \
ANTHROPIC_AUTH_TOKEN=dummy \
ANTHROPIC_DEFAULT_HAIKU_MODEL="$MODEL" \
ANTHROPIC_DEFAULT_SONNET_MODEL="$MODEL" \
ANTHROPIC_DEFAULT_OPUS_MODEL="$MODEL" \
claude --dangerously-skip-permissions "$@"
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENAI_BASE` | *(required)* | OpenAI-compatible API base URL |
| `OPENAI_KEY` | *(required)* | API key |
| `MODEL` | `gpt-4o` | Model name to use |
| `MAX_OUTPUT_TOKENS` | `16384` | Cap on max_tokens per request |
| `MAX_SYSTEM_CHARS` | `0` | Warn if system prompt exceeds this (0 = no limit) |

## Context Optimization

Claude Code sends 100-300+ tool definitions with every request. This proxy automatically:

1. **Truncates tool descriptions** to first sentence (≤80 chars)
2. **Strips parameter descriptions** from JSON schemas
3. **Deduplicates `<system-reminder>` blocks** across conversation turns

This typically reduces tool payload from ~860K to ~380K characters, making it possible to use models with 196K context windows.

## Tested With

| Backend | Model | Context | Status |
|---------|-------|---------|--------|
| SJTU API | MiniMax M2.5 | 196K | ✅ Works with `--bare` |
| SJTU API | GLM-5 | 32K | ⚠️ Too small for Claude Code |
| SJTU API | DeepSeek V3.2 | 65K | ⚠️ Borderline |

## Why not litellm / claude-code-proxy?

We tested [1rgs/claude-code-proxy](https://github.com/1rgs/claude-code-proxy) and litellm proxy. Both had issues with specific backends:
- litellm proxy routes Anthropic requests through the OpenAI Responses API, which many backends don't support
- Tool use responses were returned as plain text instead of proper `tool_use` blocks
- Streaming had gaps with no `content_block_delta` events

This proxy handles the conversion directly with zero dependencies and specific fixes for vLLM-based backends.

## License

MIT

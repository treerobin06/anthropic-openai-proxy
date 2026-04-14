#!/bin/bash
# ccmm — Claude Code with MiniMax M2.5 via SJTU API (致远一号)
# 196K context, best for Claude Code among SJTU models

PROXY_SCRIPT="$(dirname "$0")/../proxy.py"
PORT=4001
PROXY_PID=""

cleanup() {
    if [ -n "$PROXY_PID" ] && kill -0 "$PROXY_PID" 2>/dev/null; then
        kill "$PROXY_PID" 2>/dev/null
        wait "$PROXY_PID" 2>/dev/null
    fi
}
trap cleanup EXIT

# ── SJTU API config ──
export OPENAI_BASE="https://models.sjtu.edu.cn/api/v1"
export OPENAI_KEY="${SJTU_API_KEY:?Set SJTU_API_KEY first}"
export MODEL="minimax-m2.5"
export MAX_OUTPUT_TOKENS=16384

if curl -s "http://localhost:$PORT/health" >/dev/null 2>&1; then
    echo "proxy already running (port $PORT)"
else
    echo "starting MiniMax M2.5 proxy..."
    python3 "$PROXY_SCRIPT" "$PORT" 2>/tmp/ccmm-proxy.log &
    PROXY_PID=$!

    for i in $(seq 1 20); do
        if curl -s "http://localhost:$PORT/health" >/dev/null 2>&1; then
            echo "proxy ready (port $PORT)"
            break
        fi
        if ! kill -0 "$PROXY_PID" 2>/dev/null; then
            echo "proxy failed to start"; exit 1
        fi
        sleep 0.5
    done
fi

export ANTHROPIC_BASE_URL="http://localhost:$PORT"
export ANTHROPIC_AUTH_TOKEN="dummy"
export ANTHROPIC_DEFAULT_HAIKU_MODEL="minimax-m2.5"
export ANTHROPIC_DEFAULT_SONNET_MODEL="minimax-m2.5"
export ANTHROPIC_DEFAULT_OPUS_MODEL="minimax-m2.5"

# --bare reduces context (~1K system prompt instead of ~27K)
claude --dangerously-skip-permissions --bare "$@"

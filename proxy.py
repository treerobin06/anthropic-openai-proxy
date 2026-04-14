#!/usr/bin/env python3
"""
anthropic-openai-proxy: Anthropic Messages API → OpenAI Chat Completions

A lightweight proxy that lets Claude Code (or any Anthropic API client) talk to
OpenAI-compatible backends (vLLM, SJTU, Ollama, etc.).

Features:
  - Streaming & non-streaming
  - Tool use (function calling) with JSON dedup for vLLM backends
  - System messages (string & list format)
  - Multi-turn conversations with tool_result
  - <think> tag extraction (MiniMax) & reasoning_content (GLM-5)
  - <system-reminder> dedup across conversation turns
  - Tool schema compression (saves ~55% context for 300+ tools)
  - Threaded server for concurrent requests
  - Zero dependencies (Python 3.9+ stdlib only)

Usage:
  OPENAI_BASE=https://api.example.com/v1 OPENAI_KEY=sk-xxx MODEL=gpt-4o python3 proxy.py [port]

Environment variables:
  OPENAI_BASE       - upstream OpenAI-compatible base URL (required)
  OPENAI_KEY        - API key for upstream (required)
  MODEL             - model name to use (default: gpt-4o)
  MAX_OUTPUT_TOKENS - cap on max_tokens (default: 16384)
  MAX_SYSTEM_CHARS  - warn if system prompt exceeds this (0 = no limit)
"""

import json
import os
import re
import uuid
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from urllib.request import Request, urlopen
from urllib.error import HTTPError

OPENAI_BASE = os.environ.get("OPENAI_BASE", "")
OPENAI_KEY = os.environ.get("OPENAI_KEY", "")
MODEL = os.environ.get("MODEL", "gpt-4o")
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 4000
MAX_OUTPUT_TOKENS = int(os.environ.get("MAX_OUTPUT_TOKENS", "16384"))
MAX_SYSTEM_CHARS = int(os.environ.get("MAX_SYSTEM_CHARS", "0"))


class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


# ── Request: Anthropic → OpenAI ──

def truncate_desc(desc, max_len=80):
    if not desc:
        return ""
    return desc.split('\n')[0].split('. ')[0][:max_len]


def slim_schema(schema):
    if not isinstance(schema, dict):
        return schema
    result = {}
    for k, v in schema.items():
        if k == "description":
            continue
        elif isinstance(v, dict):
            result[k] = slim_schema(v)
        elif isinstance(v, list):
            result[k] = [slim_schema(i) if isinstance(i, dict) else i for i in v]
        else:
            result[k] = v
    return result


def convert_tools(anthropic_tools):
    oai_tools = []
    for t in anthropic_tools:
        oai_tools.append({
            "type": "function",
            "function": {
                "name": t["name"],
                "description": truncate_desc(t.get("description", "")),
                "parameters": slim_schema(t.get("input_schema", {})),
            },
        })
    return oai_tools


def strip_system_reminders(text):
    return re.sub(r'<system-reminder>.*?</system-reminder>\s*', '', text, flags=re.DOTALL).strip()


def convert_messages(anthropic_msgs):
    oai_msgs = []
    last_user_idx = max(
        (i for i, m in enumerate(anthropic_msgs) if m.get("role") == "user"),
        default=-1,
    )
    for msg_idx, msg in enumerate(anthropic_msgs):
        role = msg["role"]
        content = msg.get("content", "")
        is_history = msg_idx < last_user_idx

        if isinstance(content, str):
            if is_history:
                content = strip_system_reminders(content)
            if content:
                oai_msgs.append({"role": role, "content": content})
            continue

        if not isinstance(content, list):
            oai_msgs.append({"role": role, "content": str(content)})
            continue

        if role == "assistant":
            text_parts = []
            tool_calls = []
            for block in content:
                if block.get("type") == "text":
                    text_parts.append(block["text"])
                elif block.get("type") == "tool_use":
                    tool_calls.append({
                        "id": block["id"],
                        "type": "function",
                        "function": {
                            "name": block["name"],
                            "arguments": json.dumps(block.get("input", {})),
                        },
                    })
            oai_msg = {"role": "assistant", "content": "\n".join(text_parts) or None}
            if tool_calls:
                oai_msg["tool_calls"] = tool_calls
            oai_msgs.append(oai_msg)

        elif role == "user":
            tool_results = []
            text_parts = []
            for block in content:
                if block.get("type") == "tool_result":
                    tc = block.get("content", "")
                    if isinstance(tc, list):
                        tc = "\n".join(b.get("text", json.dumps(b)) for b in tc)
                    elif not isinstance(tc, str):
                        tc = json.dumps(tc)
                    tool_results.append({
                        "role": "tool",
                        "tool_call_id": block["tool_use_id"],
                        "content": tc,
                    })
                elif block.get("type") == "text":
                    t = block["text"]
                    if is_history:
                        t = strip_system_reminders(t)
                    if t:
                        text_parts.append(t)
                elif isinstance(block, str):
                    text_parts.append(block)
            oai_msgs.extend(tool_results)
            if text_parts:
                oai_msgs.append({"role": "user", "content": "\n".join(text_parts)})
        else:
            text = "\n".join(
                b.get("text", str(b)) if isinstance(b, dict) else str(b)
                for b in content
            )
            oai_msgs.append({"role": role, "content": text})

    return oai_msgs


def build_openai_request(body):
    messages = []

    if body.get("system"):
        sys_content = body["system"]
        if isinstance(sys_content, list):
            sys_content = "\n".join(
                b["text"] for b in sys_content if b.get("type") == "text"
            )
        if MAX_SYSTEM_CHARS > 0 and len(sys_content) > MAX_SYSTEM_CHARS:
            print(f"warning: system prompt {len(sys_content)} chars > {MAX_SYSTEM_CHARS}", file=sys.stderr)
        messages.append({"role": "system", "content": sys_content})

    messages.extend(convert_messages(body.get("messages", [])))

    capped_max = min(body.get("max_tokens", 4096), MAX_OUTPUT_TOKENS)

    oai_body = {
        "model": MODEL,
        "messages": messages,
        "max_tokens": capped_max,
        "stream": body.get("stream", False),
    }

    if body.get("temperature") is not None:
        oai_body["temperature"] = body["temperature"]
    if body.get("top_p") is not None:
        oai_body["top_p"] = body["top_p"]
    if body.get("stop_sequences"):
        oai_body["stop"] = body["stop_sequences"]

    if body.get("tools"):
        oai_body["tools"] = convert_tools(body["tools"])
        tc = body.get("tool_choice")
        if tc:
            if isinstance(tc, dict):
                if tc.get("type") == "auto":
                    oai_body["tool_choice"] = "auto"
                elif tc.get("type") == "any":
                    oai_body["tool_choice"] = "required"
                elif tc.get("type") == "tool":
                    oai_body["tool_choice"] = {"type": "function", "function": {"name": tc["name"]}}
            else:
                oai_body["tool_choice"] = tc

    return oai_body


# ── Response: OpenAI → Anthropic ──

def convert_response(oai_resp, model):
    choice = oai_resp.get("choices", [{}])[0]
    message = choice.get("message", {})
    usage = oai_resp.get("usage", {})
    blocks = []

    reasoning = message.get("reasoning_content")
    if reasoning and message.get("content"):
        blocks.append({"type": "thinking", "thinking": reasoning})

    content = message.get("content")
    if content is None and reasoning:
        content = reasoning
    if content:
        think_match = re.search(r'<think>(.*?)</think>', content, re.DOTALL)
        if think_match:
            think_text = think_match.group(1).strip()
            if think_text:
                blocks.append({"type": "thinking", "thinking": think_text})
            content = re.sub(r'<think>.*?</think>\s*', '', content, flags=re.DOTALL).strip()
        if content:
            blocks.append({"type": "text", "text": content})

    for tc in message.get("tool_calls") or []:
        func = tc.get("function", {})
        try:
            args = json.loads(func.get("arguments", "{}"))
        except json.JSONDecodeError:
            args = {}
        blocks.append({
            "type": "tool_use",
            "id": tc.get("id", f"toolu_{uuid.uuid4().hex[:24]}"),
            "name": func.get("name", ""),
            "input": args,
        })

    if not blocks:
        blocks.append({"type": "text", "text": ""})

    finish = choice.get("finish_reason", "stop")
    tool_calls = message.get("tool_calls") or []
    if finish == "tool_calls" or tool_calls:
        stop_reason = "tool_use"
    elif finish == "stop":
        stop_reason = "end_turn"
    elif finish == "length":
        stop_reason = "max_tokens"
    else:
        stop_reason = "end_turn"

    return {
        "id": f"msg_{uuid.uuid4().hex[:24]}",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": blocks,
        "stop_reason": stop_reason,
        "usage": {
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
        },
    }


# ── HTTP Handler ──

class ProxyHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ("/health", "/v1/health"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        if "/count_tokens" in self.path:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
            text = json.dumps(body.get("messages", []))
            est = max(1, int(len(text) * 0.5))
            data = json.dumps({"input_tokens": est}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(data)
            return

        if "/messages" not in self.path:
            self.send_response(404)
            self.end_headers()
            return

        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length))
        model = body.get("model", MODEL)
        stream = body.get("stream", False)
        oai_body = build_openai_request(body)

        try:
            if not stream:
                self._handle_sync(oai_body, model)
            else:
                self._handle_stream(oai_body, model)
        except HTTPError as e:
            err_body = e.read().decode()
            self.send_response(e.code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({
                "type": "error",
                "error": {"type": "api_error", "message": f"Upstream: {err_body}"},
            }).encode())
        except Exception as e:
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({
                "type": "error",
                "error": {"type": "api_error", "message": str(e)},
            }).encode())

    def _make_request(self, oai_body):
        data = json.dumps(oai_body).encode()
        return Request(
            f"{OPENAI_BASE}/chat/completions",
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {OPENAI_KEY}",
            },
        )

    def _handle_sync(self, oai_body, model):
        req = self._make_request(oai_body)
        with urlopen(req, timeout=300) as resp:
            oai_resp = json.loads(resp.read())
        result = convert_response(oai_resp, model)
        data = json.dumps(result).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _handle_stream(self, oai_body, model):
        oai_body["stream"] = True
        req = self._make_request(oai_body)
        resp = urlopen(req, timeout=300)

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        msg_id = f"msg_{uuid.uuid4().hex[:24]}"
        self._sse("message_start", {
            "type": "message_start",
            "message": {
                "id": msg_id, "type": "message", "role": "assistant",
                "model": model, "content": [],
                "usage": {"input_tokens": 0, "output_tokens": 0},
            },
        })

        text_block_started = False
        tool_blocks = {}
        current_content_index = 0

        buf = b""
        while True:
            byte = resp.read(1)
            if not byte:
                break
            buf += byte
            if byte != b"\n":
                continue
            line = buf.decode("utf-8").strip()
            buf = b""
            if not line.startswith("data: "):
                continue
            data_str = line[6:]
            if data_str == "[DONE]":
                break
            try:
                chunk = json.loads(data_str)
            except json.JSONDecodeError:
                continue

            choice = chunk.get("choices", [{}])[0]
            delta = choice.get("delta", {})

            text = delta.get("content", "")
            if text:
                if not text_block_started:
                    self._sse("content_block_start", {
                        "type": "content_block_start",
                        "index": current_content_index,
                        "content_block": {"type": "text", "text": ""},
                    })
                    text_block_started = True
                self._sse("content_block_delta", {
                    "type": "content_block_delta",
                    "index": current_content_index,
                    "delta": {"type": "text_delta", "text": text},
                })

            for tc_delta in delta.get("tool_calls", []):
                tc_idx = tc_delta.get("index", 0)
                if tc_idx not in tool_blocks:
                    if text_block_started:
                        self._sse("content_block_stop", {
                            "type": "content_block_stop",
                            "index": current_content_index,
                        })
                        current_content_index += 1
                        text_block_started = False

                    tc_id = tc_delta.get("id", f"toolu_{uuid.uuid4().hex[:24]}")
                    tc_name = tc_delta.get("function", {}).get("name", "")
                    tool_blocks[tc_idx] = {"id": tc_id, "name": tc_name, "args_buf": ""}
                    self._sse("content_block_start", {
                        "type": "content_block_start",
                        "index": current_content_index + tc_idx,
                        "content_block": {"type": "tool_use", "id": tc_id, "name": tc_name},
                    })

                args_chunk = tc_delta.get("function", {}).get("arguments", "")
                if args_chunk:
                    if args_chunk.startswith("{"):
                        try:
                            json.loads(args_chunk)
                            tool_blocks[tc_idx]["args_buf"] = args_chunk
                            continue
                        except (json.JSONDecodeError, ValueError):
                            pass
                    tool_blocks[tc_idx]["args_buf"] += args_chunk

        resp.close()

        if text_block_started:
            self._sse("content_block_stop", {
                "type": "content_block_stop",
                "index": current_content_index,
            })
            current_content_index += 1

        for tc_idx in sorted(tool_blocks.keys()):
            args = tool_blocks[tc_idx]["args_buf"]
            if args:
                self._sse("content_block_delta", {
                    "type": "content_block_delta",
                    "index": current_content_index + tc_idx,
                    "delta": {"type": "input_json_delta", "partial_json": args},
                })
            self._sse("content_block_stop", {
                "type": "content_block_stop",
                "index": current_content_index + tc_idx,
            })

        has_tools = len(tool_blocks) > 0
        self._sse("message_delta", {
            "type": "message_delta",
            "delta": {"stop_reason": "tool_use" if has_tools else "end_turn"},
            "usage": {"output_tokens": 0},
        })
        self._sse("message_stop", {"type": "message_stop"})

    def _sse(self, event, data):
        line = f"event: {event}\ndata: {json.dumps(data)}\n\n"
        self.wfile.write(line.encode())
        self.wfile.flush()

    def log_message(self, format, *args):
        pass


if __name__ == "__main__":
    if not OPENAI_BASE or not OPENAI_KEY:
        print("Error: set OPENAI_BASE and OPENAI_KEY environment variables")
        print("Example: OPENAI_BASE=https://api.example.com/v1 OPENAI_KEY=sk-xxx python3 proxy.py")
        sys.exit(1)
    print(f"Anthropic→OpenAI proxy: http://localhost:{PORT}")
    print(f"  upstream: {OPENAI_BASE} | model: {MODEL} | max_output: {MAX_OUTPUT_TOKENS}")
    ThreadingHTTPServer(("127.0.0.1", PORT), ProxyHandler).serve_forever()

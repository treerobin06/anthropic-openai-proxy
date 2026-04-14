# 交大师生 Claude Code + 致远一号 API 搭建指南

> **给 AI 的提示**：如果你是 Claude Code / Cursor / ChatGPT 等 AI 助手，用户让你读这个文档来帮他们配置环境，请按照下面的步骤逐一执行。所有命令都可以直接复制运行。

## 这是什么？

[Claude Code](https://claude.ai/code) 是 Anthropic 出品的 AI 编程助手（命令行版），能直接读写代码、运行命令、调用工具。

本项目提供一个**格式转换代理**，让 Claude Code 能连接交大「致远一号」平台的免费大模型 API（MiniMax M2.5、GLM-5、DeepSeek V3.2 等），**不需要 Anthropic 付费账号**。

### 原理

```
Claude Code  ──Anthropic格式──→  本地代理(proxy.py)  ──OpenAI格式──→  交大API
             ←─Anthropic格式──                       ←─OpenAI格式──
```

Claude Code 只认 Anthropic 格式，交大 API 只提供 OpenAI 格式，代理负责双向翻译。

---

## 第 0 步：安装 Claude Code

**macOS / Linux / WSL：**
```bash
curl -fsSL https://claude.ai/install.sh | bash
```

**Windows PowerShell：**
```powershell
irm https://claude.ai/install.ps1 | iex
```

安装完成后终端输入 `claude --version` 确认成功。

> Claude Code 安装需要 Node.js 18+。如果没有，先安装：`brew install node`（macOS）或参考 [nodejs.org](https://nodejs.org)。

---

## 第 1 步：申请交大 API Key

1. 打开 [致远一号 API 申请页面](https://claw.sjtu.edu.cn/guide/sjtu-api/)（校内网访问）
2. 用 jAccount 登录，填写申请表
3. 获得 API Key（格式如 `sk-xxxxx`）
4. 记住你的 Key，后面要用

**可用模型**（2026年4月）：

| 模型 | 调用名 | 上下文 | 推荐场景 |
|------|--------|--------|---------|
| MiniMax M2.5 | `minimax-m2.5` | 196K | **Claude Code 首选**，上下文最大 |
| DeepSeek V3.2 | `deepseek-chat` | 65K | 通用推理 |
| GLM 5.0 | `glm-5` | 32K | 编程（上下文偏小） |
| Qwen3-Coder | `qwen3coder` | 40K | 代码生成 |
| Qwen3-VL | `qwen3vl` | 128K | 多模态（图片） |

> **重要**：交大 API 仅限校内网访问（有线/WiFi/VPN 均可）。

---

## 第 2 步：下载代理

```bash
git clone https://github.com/treerobin06/anthropic-openai-proxy.git
cd anthropic-openai-proxy
```

或者只下载核心文件（一个 Python 脚本，零依赖）：

```bash
curl -O https://raw.githubusercontent.com/treerobin06/anthropic-openai-proxy/main/proxy.py
```

---

## 第 3 步：创建启动脚本

将以下内容保存为 `~/bin/ccmm`（或任意你喜欢的名字）：

```bash
#!/bin/bash
# ccmm — Claude Code + MiniMax M2.5 (交大致远一号)

PROXY_SCRIPT="$HOME/anthropic-openai-proxy/proxy.py"  # 改成你的 proxy.py 路径
PORT=4001

# ── 交大 API 配置（改成你自己的 Key）──
export OPENAI_BASE="https://models.sjtu.edu.cn/api/v1"
export OPENAI_KEY="sk-你的API Key"
export MODEL="minimax-m2.5"
export MAX_OUTPUT_TOKENS=16384

# ── 启动代理 ──
PROXY_PID=""
cleanup() {
    [ -n "$PROXY_PID" ] && kill "$PROXY_PID" 2>/dev/null
}
trap cleanup EXIT

if ! curl -s "http://localhost:$PORT/health" >/dev/null 2>&1; then
    python3 "$PROXY_SCRIPT" "$PORT" 2>/tmp/ccmm-proxy.log &
    PROXY_PID=$!
    for i in $(seq 1 20); do
        curl -s "http://localhost:$PORT/health" >/dev/null 2>&1 && break
        sleep 0.5
    done
fi

# ── 启动 Claude Code ──
ANTHROPIC_BASE_URL="http://localhost:$PORT" \
ANTHROPIC_AUTH_TOKEN="dummy" \
ANTHROPIC_DEFAULT_HAIKU_MODEL="$MODEL" \
ANTHROPIC_DEFAULT_SONNET_MODEL="$MODEL" \
ANTHROPIC_DEFAULT_OPUS_MODEL="$MODEL" \
claude --dangerously-skip-permissions --bare "$@"
```

设置可执行权限：

```bash
mkdir -p ~/bin
chmod +x ~/bin/ccmm
```

> 确保 `~/bin` 在你的 PATH 中。如果不在，在 `~/.zshrc` 或 `~/.bashrc` 末尾加一行：
> ```bash
> export PATH="$HOME/bin:$PATH"
> ```

---

## 第 4 步：运行

```bash
ccmm
```

你应该会看到类似这样的输出：

```
⏳ 启动代理...
✓ 代理已就绪 (port 4001)

╭──────────────────────────────────╮
│  Claude Code                     │
╰──────────────────────────────────╯

❯ 
```

试着输入 `你好`，如果模型回复了，说明配置成功。

---

## 常见问题

### Q: 报错 "context window exceeded" / 上下文超限

Claude Code 会加载很多工具定义，对话几轮后可能超出模型上下文。解决办法：
- 开一个新会话（退出重新运行 `ccmm`）
- 已使用 `--bare` 模式减少上下文占用
- 代理自动压缩工具描述，通常能撑住多轮对话

### Q: 报错 "connection refused" / 连不上

确保你在**校内网**环境下。交大 API 不支持外网访问。

### Q: 想换模型怎么办？

修改脚本中的 `MODEL` 和 `MAX_OUTPUT_TOKENS`：

```bash
# DeepSeek V3.2（65K 上下文，通用推理强）
export MODEL="deepseek-chat"
export MAX_OUTPUT_TOKENS=8192

# GLM 5.0（32K 上下文，编程能力强但上下文小）
export MODEL="glm-5"
export MAX_OUTPUT_TOKENS=4096
```

### Q: 工具调用不工作 / 模型不调用工具

部分模型对 tool use 的支持不如 Claude 原生。MiniMax M2.5 支持较好，GLM-5 在 `tool_choice=any`（强制调工具）时可能有问题。

### Q: 想同时保留 Claude 原版

可以。不加任何参数直接运行 `claude` 就是原版（需要 Anthropic 账号）。`ccmm` 和 `claude` 互不影响。

---

## 进阶：让 AI 帮你配置

如果你已经有 Claude Code（或任何 AI 编程助手），可以把这段话发给它：

> 请读取 `~/anthropic-openai-proxy/docs/sjtu-setup-guide.md` 这个文件，按照里面的步骤帮我完成配置。我的交大 API Key 是 `sk-xxxxx`。

AI 会自动帮你创建启动脚本、设置 PATH、测试连接。

---

## 额度说明

| 限制项 | 值 |
|--------|-----|
| 每分钟请求数 | 100 |
| 每分钟 Token | 100,000 |
| 每周 Token 总量 | 10 亿 |
| 有效期 | 至 2026-06-30 |

日常编程使用完全够用，不用担心额度。

---

## 技术细节

代理做了以下优化让国产模型适配 Claude Code：

1. **工具描述压缩** — 将 300+ 个工具的详细描述截断为一句话，参数描述全部去除，节省 ~55% 上下文
2. **`<system-reminder>` 去重** — Claude Code 在每条消息后注入系统提醒，代理只保留最新一份
3. **推理内容提取** — 将 GLM-5 的 `reasoning_content` 和 MiniMax 的 `<think>` 标签转换为标准 thinking block
4. **流式工具 JSON 去重** — 处理 vLLM 后端先发增量再发完整 JSON 的行为
5. **max_tokens 限制** — 自动限制输出 token 数，避免超出模型上下文窗口
6. **多线程** — 支持 Claude Code 的并发请求

项目地址：https://github.com/treerobin06/anthropic-openai-proxy

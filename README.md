# My Claude Code

一个**学习性质的 Python MVP**，用最少的代码复刻 Claude Code 的核心思想：agentic tool loop + 分层 memory + context engineering + 可扩展的副作用系统。

目标是"代码能读懂"，不追求功能完整。复杂特性（MCP、thinking blocks、多 sub-agent 并行）刻意不做。

## 核心机制

| 机制 | 对应的 Claude Code 概念 |
|------|-------------------------|
| Agentic tool loop | 原生 tool_use / tool_result 循环 |
| `CLAUDE.md` 两层加载 | 用户层 (`~/.claude/CLAUDE.md`) + 项目层 (`./CLAUDE.md`) |
| `.myai/sessions/*.jsonl` | 每会话一文件 + `/resume` 续会 |
| `/context` + `/compact` + auto-compact | 用量显示 + 压缩（阈值 80%） |
| `/plan` 只读模式 | Plan mode（system prompt + 工具拦截双重防线） |
| `.myai/hooks.json` | pre/post tool use hooks（JSON via stdin） |
| `task` 工具 | 只读 sub-agent + 独立 context |

## 安装

```bash
pip install -r requirements.txt
```

Python 3.10+，依赖：`rich` + `prompt_toolkit` + `openai` + `anthropic`。

## 运行

```bash
python main.py
```

首次启动会跑配置向导，写入 `~/.myai/settings.json`。之后每次启动：
- 读 `~/.claude/CLAUDE.md` 和 `./CLAUDE.md` 进 system prompt
- 创建一个新 session 文件（懒创建，空会话不落盘）
- 加载 `.myai/hooks.json`（如有）

## Slash 命令

| Command | 作用 |
|---------|------|
| `/help` | 显示帮助 |
| `/settings` | 显示当前配置 |
| `/memory` | 显示已加载的 `CLAUDE.md` 文件 |
| `/context` | 显示 token 用量 / 模型上限 / 进度条 |
| `/compact` | 手动压缩历史（LLM summarize + 保留最近 2 条） |
| `/history` | 显示当前 session 的消息 |
| `/resume` | 继续上一次会话 |
| `/clear` | 清空当前 session |
| `/plan` | 切换只读模式（禁用 `edit_file` / `create_file`） |
| `/undo` | 回滚最近一次文件编辑 |
| `/exit` | 退出 |

## 日常用法

### 直接对话

```
> 解释 @main.py 里的 slash 命令 dispatch 是怎么工作的
> 把 config.py 里的 print 改成 logging
> /plan
plan > 看看这个项目里 agent.py 和 main.py 的职责边界
plan > /plan
> 现在按刚才的分析重构
```

### `@file` 引用

输入 `@path/to/file` 自动展开文件内容进消息。支持 tab 补全，文件索引在启动时构建。

```
> 这段错误是什么原因？@logs/error.log
```

### Memory（持久规则）

往 `./CLAUDE.md` 写项目级规则，`~/.claude/CLAUDE.md` 写跨项目偏好。启动时自动拼进 system prompt。`/memory` 查看当前加载状态。

### Sub-agent（`task` 工具）

模型可以主动调用 `task(description=...)` 派生一个**只读、独立 context** 的 sub-agent，用于开放式调研（"找出所有使用 X 的地方"）。只有 sub-agent 的最终回答返回主会话，中间的 `grep` / `read_file` 不污染主 context。

sub-agent 不能写文件，也不能再 spawn sub-agent（防止递归）。

### Hooks（确定性副作用）

在 `.myai/hooks.json` 写：

```json
{
  "pre_tool_use": [
    {"match": "edit_file|create_file", "command": "python scripts/forbid_env.py"}
  ],
  "post_tool_use": [
    {"match": "edit_file", "command": "black {file_path_unused}"}
  ]
}
```

Agent 通过 stdin 把 `{"tool_name": ..., "file_path": ..., ...}` 传给 hook（所以 hook 里用 `json.load(sys.stdin)` 取值，避免 shell 转义）。`pre_tool_use` 退出码非 0 会**阻止工具执行**，stderr 作为 tool result 返回给 LLM。

## 配置

`~/.myai/settings.json`：

```json
{
  "provider": "anthropic",
  "api_key": "sk-...",
  "base_url": "https://api.anthropic.com",
  "model": "claude-sonnet-4-6",
  "temperature": 0.7,
  "max_history_tokens": 4096
}
```

`provider` 从 `base_url` 自动推断（含 "anthropic" → Anthropic 原生；其它 → OpenAI 兼容）。已知 context 窗口表在 `models.py::CONTEXT_WINDOWS`，覆盖 Claude / GPT-4o / DeepSeek / Qwen 等。

## 安全机制

1. **路径校验**：工具里所有路径 resolve 后必须在 `cwd` 内
2. **修改确认**：`edit_file` / `create_file` 展示 unified diff + `y/N` 确认
3. **快照回滚**：每次确认修改前把原文件存到 `.myai/file-history/`，`/undo` 恢复
4. **Plan mode + sub-agent readonly**：双层 system prompt + dispatch 层拦截
5. **Hooks 黑名单**：用户可自定义拒绝规则（如禁止修改 `.env`）

## 目录结构

```
.myai/
├── sessions/              # 会话 transcript，一个 session 一个 jsonl
│   └── session-<ts>.jsonl
├── file-history/          # 文件修改前的快照
│   └── <md5-of-path>/
│       └── <ts>_<name>
└── hooks.json             # 可选：用户定义的 pre/post tool hooks

CLAUDE.md                  # 项目级 memory（这个项目本身也有一份）
~/.claude/CLAUDE.md        # 用户级 memory
~/.myai/settings.json      # API 配置
```

架构细节见 [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)。

## License

MIT

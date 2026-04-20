# my-claude-code 项目记忆

这是一个学习性质的 MVP，目的是用最少的代码模仿 Claude Code 的核心思想。
这个文件本身就是"项目级 memory"的示范——CLI 启动时会读它并拼进 system prompt。

## 项目目标

- 复刻 Claude Code 的关键机制：工具调用 agentic loop、Read → Edit 工作流、slash 命令、memory。
- 每个机制都做**极简版**，不追求完整性。复杂特性（@include 递归、后台抽取 agent、feature flags）不做。
- 代码能读懂优先于功能完整。

## 代码风格

- Python 3.10+，标准库 + `rich` + `prompt_toolkit` + `openai` + `anthropic`。
- 模块内聚：`tools.py` 管文件工具，`llm.py` 管协议适配，`memory.py` 管持久记忆，`main.py` 是 CLI 装配层。
- 不加防御性 try/except 兜住内部调用——只在外部边界（用户输入、LLM 响应、文件 I/O）处理异常。
- 注释只写"为什么"，不写"做什么"。

## 已知简化（刻意不做）

- memory 只支持用户层（`~/.claude/CLAUDE.md`）+ 项目层（`./CLAUDE.md`）两层。
- 没有 `@path` include、没有 `.claude/rules/*.md` 条件路由、没有后台抽取 agent。
- 会话历史：一会话一文件（`.myai/sessions/session-<ts>.jsonl`），新启动默认干净；`/resume` 只接上一次会话，不提供多会话选择 UI。
- 没有 token 精确计数和压缩。
- 工具集只有 4 个：`read_file` / `create_file` / `edit_file` / `grep`。

## 运行

```bash
python main.py
```

首次启动会跑配置向导。`/help` 看所有命令，`/memory` 看当前加载的记忆文件。

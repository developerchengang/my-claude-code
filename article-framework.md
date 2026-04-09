# 手搓一个简易版 Claude Code：用 500 行 Python 实现 AI 编程助手

## 一、先看效果

> 放终端运行截图/GIF

## 二、整体架构

```
用户输入 → 主循环(main.py)
              ↓
         展开 @文件引用
              ↓
         调用大模型（带 Tool 定义）
              ↓
      ┌──────┴──────┐
   返回文本      返回 Tool Call
      ↓              ↓
  Markdown渲染   执行文件操作
                     ↓
              生成 diff → 用户确认
                ↓          ↓
              确认写入    取消丢弃
```

**五个模块：**

| 模块 | 干什么 |
|------|--------|
| `config.py` | 配置管理，首次运行有个向导 |
| `llm.py` | 调大模型，定义 Tool，解析返回 |
| `tools.py` | 文件读写 + 安全校验 + 快照回滚 |
| `history.py` | 会话历史，JSONL 追加写入 |
| `main.py` | 主循环，串起上面所有模块 |

---

## 三、手把手实现

### 3.1 项目骨架

```
my-claude-code/
├── main.py
├── config.py
├── llm.py
├── tools.py
├── history.py
└── requirements.txt
```

依赖就四个：`openai`、`anthropic`、`prompt_toolkit`、`rich`

---

### 3.2 配置管理

> 核心思路：配置存在 `~/.myai/settings.json`，首次运行进向导

```python
class Config:
    DEFAULT_SETTINGS = {
        "provider": "openai",
        "api_key": "",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o",
        "temperature": 0.7,
    }

    def __init__(self):
        self.settings_file = Path.home() / ".myai" / "settings.json"
        self._load()

    def _load(self):
        if self.settings_file.exists():
            self._settings = json.load(open(self.settings_file))
        else:
            self._settings = self.DEFAULT_SETTINGS.copy()

    def save(self):
        self.settings_file.parent.mkdir(parents=True, exist_ok=True)
        json.dump(self._settings, open(self.settings_file, "w"))
```

---

### 3.3 LLM 客户端 — Tool Calling

> **这是全文最核心的部分**

**第一步：告诉大模型"你有哪些工具可以用"**

```python
TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "create_file",
            "description": "Create a new file with the specified content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"},
                    "content": {"type": "string"}
                },
                "required": ["file_path", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Edit an existing file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"},
                    "operations": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "action": {"enum": ["insert", "delete", "replace"]},
                                "start_line": {"type": "integer"},
                                "end_line": {"type": "integer"},
                                "content": {"type": "string"}
                            }
                        }
                    }
                }
            }
        }
    }
]
```

**第二步：调用大模型 + 解析 Tool Call**

```python
class LLMClient:
    def chat(self, messages) -> LLMResponse:
        # 把工具定义一起传给大模型，tool_choice="auto" 让模型自己决定要不要调用
        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            tools=TOOL_DEFINITIONS,     # ← 关键：把工具定义传进去
            tool_choice="auto",         # ← 让模型自己决定是否调用
        )

        message = response.choices[0].message
        content = message.content or ""

        # 解析模型返回的 tool_calls
        tool_calls = []
        for tc in (message.tool_calls or []):
            tool_calls.append(ToolCall(
                id=tc.id,
                name=tc.function.name,
                arguments=json.loads(tc.function.arguments)
            ))

        return LLMResponse(content=content, tool_calls=tool_calls)
```

> **讲解要点：** 大模型不是直接操作文件，而是返回一个"意图"（函数名 + 参数），由我们的代码来执行。这就是 Tool Calling 的本质 —— 大模型负责思考，我们的代码负责执行。

---

### 3.4 文件操作工具 — 安全第一

> 三个关键机制：路径校验、快照备份、diff 确认

**路径安全校验：**

```python
def _validate_path(self, file_path: str) -> Path:
    resolved = (self.project_root / file_path).resolve()

    # 防止 "../../etc/passwd" 这种穿越攻击
    resolved.relative_to(self.project_root)  # 不在项目目录内会抛 ValueError

    return resolved
```

**编辑文件（核心流程）：**

```python
def edit_file(self, file_path: str, operations: list) -> dict:
    path = self._validate_path(file_path)
    original = path.read_text(encoding="utf-8")

    # === 先不写入！只准备变更 ===

    # 按行号从大到小排序，从后往前改，避免行号偏移
    lines = original.splitlines(keepends=True)
    for op in sorted(operations, key=lambda x: x["start_line"], reverse=True):
        if op["action"] == "insert":
            lines.insert(op["start_line"] - 1, op["content"] + "\n")
        elif op["action"] == "delete":
            del lines[op["start_line"] - 1 : op["end_line"]]
        elif op["action"] == "replace":
            del lines[op["start_line"] - 1 : op["end_line"]]
            lines.insert(op["start_line"] - 1, op["content"] + "\n")

    new_content = "".join(lines)

    # 生成 unified diff（和 git diff 格式一样）
    diff = "".join(difflib.unified_diff(
        original.splitlines(keepends=True),
        new_content.splitlines(keepends=True),
        fromfile=f"a/{file_path}", tofile=f"b/{file_path}",
    ))

    # 暂存，等用户确认
    self._pending = PendingEdit(path, original, new_content, diff)
    return {"needs_confirmation": True, "diff": diff}
```

**用户确认后执行：**

```python
def confirm_edit(self) -> dict:
    # 先备份！
    snapshot_dir = self.SNAPSHOT_DIR / hashlib.md5(str(self._pending.file_path).encode()).hexdigest()
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(self._pending.file_path, snapshot_dir / f"{int(time.time()*1000)}_{self._pending.file_path.name}")

    # 再写入
    self._pending.file_path.write_text(self._pending.new_content, encoding="utf-8")
    self._pending = None
    return {"success": True}
```

**撤销操作：**

```python
def undo_last(self) -> dict:
    # 在所有快照里找修改时间最新的那个
    latest = max(
        (f for d in self.SNAPSHOT_DIR.iterdir() for f in d.iterdir()),
        key=lambda f: f.stat().st_mtime
    )
    # 恢复到原位置
    original_name = latest.stem.split("_", 1)[1]
    shutil.copy2(latest, self.project_root / original_name)
    return {"success": True}
```

> **讲解要点：** 三道保险 —— 路径校验防穿越，diff 确认防误操作，快照备份可回滚。真实的 Claude Code 也是这个思路。

---

### 3.5 会话历史

```python
class SessionHistory:
    def __init__(self):
        self.history_file = Path.cwd() / ".myai" / "session.jsonl"

    def add_message(self, role: str, content: str, **kwargs):
        # JSONL：每行一个 JSON，追加写入不用重写整个文件
        msg = {"timestamp": datetime.now().isoformat(), "role": role, "content": content, **kwargs}
        with open(self.history_file, "a") as f:
            f.write(json.dumps(msg, ensure_ascii=False) + "\n")

    def load_recent(self, n=10) -> list:
        lines = self.history_file.read_text().splitlines()
        return [json.loads(line) for line in lines[-n:] if line.strip()]
```

---

### 3.6 主程序 — 交互循环

> 所有模块串起来的地方

```python
def _process_user_message(self, message: str):
    # 1. 展开 @文件引用：用户输入 "看看 @main.py 有没有 bug" → 自动把 main.py 内容拼进去
    expanded = self._expand_file_references(message)
    self.history.add_message("user", expanded)

    # 2. 拼上最近 10 条历史作为上下文
    messages = [{"role": m["role"], "content": m["content"]} for m in self.history.load_recent(10)]

    # 3. 调大模型
    print("Thinking...")
    response = self.llm.chat(messages)

    # 4. 处理响应
    if response.tool_calls:
        for tc in response.tool_calls:
            if tc.name == "create_file":
                result = self.file_tools.create_file(tc.arguments["file_path"], tc.arguments["content"])
            elif tc.name == "edit_file":
                result = self.file_tools.edit_file(tc.arguments["file_path"], tc.arguments["operations"])

            if result.get("needs_confirmation"):
                # 展示 diff（语法高亮），等用户确认
                self.console.print(Syntax(result["diff"], "diff", theme="monokai"))
                if input("Proceed? [y/N] ").lower() == "y":
                    self.file_tools.confirm_edit()
                    print("Done.")
                else:
                    print("Cancelled.")
    else:
        # 纯文本回复 → Markdown 渲染
        self.console.print(Markdown(response.content))
        self.history.add_message("assistant", response.content)
```

**@文件引用展开：**

```python
def _expand_file_references(self, message: str) -> str:
    def replace(match):
        filename = match.group(1)
        path = (Path.cwd() / filename).resolve()
        if path.exists() and path.is_file():
            content = path.read_text(encoding="utf-8")
            if len(content) > 5000:
                content = content[:5000] + f"\n... [truncated]"
            return f"@file: {filename}\n```\n{content}\n```"
        return f"@[{filename} - not found]"

    return re.sub(r'@([\w./\\-]+)', replace, message)
```

---

## 四、运行效果

```
$ python main.py

First time setup required.

Welcome to Claude CLI Setup!
Select your API provider:
1. OpenAI
2. SiliconFlow
3. Ollama
...

> 帮我创建一个 hello.py，打印 Hello World

Thinking...
Calling tool: create_file...

Proposed changes:
--- /dev/null
+++ b/hello.py
@@ -0,0 +1 @@
+print("Hello World")

Do you want to proceed? [y/N] y
File 'hello.py' created successfully.
```

---

## 五、总结

| 已实现 | 可继续改进 |
|--------|-----------|
| Tool Calling 文件操作 | 接入 Bash/终端执行 |
| OpenAI + Anthropic 双协议 | 流式输出 (SSE) |
| diff 确认 + 快照回滚 | 多文件并行编辑 |
| @文件引用上下文 | MCP 协议扩展 |
| JSONL 会话历史 | 上下文压缩与摘要 |

# 简易版 Claude Code 产品需求文档 (PRD)
---

## 1. 项目背景与目标

### 1.1 背景
随着大型语言模型（LLM）能力的增强，开发者开始探索将 AI 集成到日常开发工作流中。Claude Code 等产品展示了 AI 通过自然语言指令操作代码库的潜力。然而，完整实现类似系统复杂度较高，本项目旨在开发一个最小可行产品（MVP），帮助开发者快速理解并体验 AI 编程助手的核心原理。

### 1.2 目标
打造一个本地运行的命令行工具，通过自然语言交互，实现两个核心功能：
- **创建文件**：根据用户描述生成新文件并写入内容。
- **编辑文件**：对现有文件进行插入、修改或删除操作，且所有修改前需用户确认。

通过此项目，验证以下核心机制：
- 大模型工具调用（Tool Calling）的落地。
- 安全的文件操作流程（备份、确认、撤销）。
- 简单的对话上下文管理。

---

## 2. 用户交互流程

### 2.1 启动与交互界面
程序启动后进入全屏或增强型 CLI 交互模式：
- **增强型输入框**：使用底栏（Status Bar）或浮动输入框，支持多行输入、历史记录搜索（向上箭头）和基本快捷键。
- **视觉反馈**：界面上方显示对话流，底部固定输入区域。
- **实时补全**：输入 `/` 时自动弹出命令补全建议。

### 2.2 主循环
1. **输入阶段**：用户在交互框中输入内容。
   - 若输入以 `/` 开头，识别为 **斜杠命令**，直接由本地逻辑执行。
   - 若为自然语言，进入 **AI 处理流程**。
2. **AI 处理流程**：
   - 消息追加至历史 -> 调用模型 -> 解析 `tool_calls` 或 `content`。
3. **安全确认**：
   - 修改操作前，在终端渲染交互式确认组件（[y/N]）。
4. **结果渲染**：使用 Markdown 渲染引擎展示模型回复，确保代码块、表格等清晰易读。

### 2.3 斜杠命令 (Slash Commands)
程序应支持以下核心指令，提高操作效率：
- `/undo`：撤销最近一次文件变更。
- `/clear`：清除当前会话历史（清空上下文）。
- `/history`：显示最近的对话摘要。
- `/help`：查看所有可用命令和工具说明。
- `/exit`：安全退出程序。

---

## 3. 功能需求

### 3.1 增强型输入系统
- **库支持**：集成 `prompt_toolkit` 或 `textual` 以实现非阻塞输入。
- **自动补全**：为 `/` 命令和本地文件路径提供补全功能。

### 3.2 工具定义与调用
...（保持原有的 create_file 和 edit_file 定义）...


#### 3.1.1 创建文件工具 (`create_file`)
- **描述**：在当前工作目录下创建一个新文件，并写入指定内容。
- **参数**：
  - `file_path` (string, 必填)：文件路径（需校验安全性）。
  - `content` (string, 必填)：文件内容。
- **行为**：若文件已存在，询问用户是否覆盖；若用户拒绝，返回失败信息。

#### 3.1.2 编辑文件工具 (`edit_file`)
- **描述**：对现有文件进行修改，支持插入、删除、替换行。
- **参数**：
  - `file_path` (string, 必填)：目标文件路径。
  - `operations` (array, 必填)：操作列表，每个包含：
    - `action` (string)：`insert`、`delete` 或 `replace`。
    - `start_line` (integer)：起始行号（从 1 开始）。
    - `end_line` (integer, 可选)：结束行号（包含）。
    - `content` (string, 可选)：新内容。
- **行为**：生成 unified diff 供确认，确认后备份原文件到快照目录，再执行写入。

### 3.2 安全机制

#### 3.2.1 文件操作确认
- 任何改变文件系统的操作必须等待用户 `y` 确认。
- 编辑操作必须展示清晰的 `diff`。

#### 3.2.2 文件快照与撤销
- **快照路径**：`.myai/file-history/`。
- **命名规则**：`MD5(file_path)/{timestamp}_{original_filename}`。
- **撤销功能**：输入 `undo` 时还原最近一次快照。

#### 3.2.3 路径安全检查
- 对 `file_path` 进行绝对路径解析。
- 禁止访问项目根目录以外的路径（防止路径穿越攻击）。

### 3.3 会话管理

#### 3.3.1 对话历史存储
- 自动追加到 `.myai/session.jsonl`，每行一个 JSON 对象（包含 `timestamp`, `role`, `content`, `tool_calls`）。

#### 3.3.2 上下文窗口管理
- 简单截断策略：仅保留最近 10 条消息以防超出 Token 限制。

### 3.4 配置文件管理 (settings.json)
程序应支持持久化配置，方便用户切换模型和 API。

- **配置文件路径**：用户主目录下的 `~/.myai/settings.json`。
- **配置项内容**：
  ```json
  {
    "provider": "openai", 
    "api_key": "sk-...",
    "base_url": "https://api.openai.com/v1",
    "model": "gpt-4o",
    "temperature": 0.7,
    "max_history_tokens": 4096,
    "theme": "dark"
  }
  ```
- **初始化行为**：
  - 若配置文件不存在，程序首次启动时应进入 **交互式配置引导**，提示用户输入供应商、API Key 等信息并保存。
  - 支持通过斜杠命令 `/settings` 快速打开或修改配置。

---

## 4. 技术选型建议

### 4.1 编程语言与运行环境
- **语言**：Python 3.9+
- **环境**：macOS / Linux / Windows (WSL 推荐)

### 4.2 核心依赖库
- **大模型 SDK**：`openai` (兼容 OpenAI 协议) 和 `anthropic`。
- **命令行美化**：`rich` (用于彩色输出和 diff 高亮)。
- **交互增强**：`prompt_toolkit` 或 `textual`。
- **路径处理**：`pathlib` (内置)。
- **差异生成**：`difflib` (内置)。
- **哈希计算**：`hashlib` (内置)。

### 4.3 架构建议
- **模块化设计**：
  - `main.py`：主循环、交互。
  - `config.py`：配置文件加载、校验与保存逻辑。
  - `tools.py`：文件操作实现与快照管理。
  - `llm.py`：根据 `config.py` 提供的配置调用大模型 API。
  - `history.py`：会话读写。

---

## 5. 非功能需求
- **性能**：本地操作毫秒级，工具调用取决于 API 响应。
- **可用性**：错误信息清晰，交互提示友好。
- **安全性**：严格限制文件操作范围，用户确认机制。
- **可维护性**：遵循 PEP 8，包含充分的 docstring。

---

## 6. 未来可扩展功能
- 执行终端命令 (如 `npm install`)。
- 多文件同时编辑。
- 大文件分块支持。
- 项目结构自动感知 (`.gitignore` 支持)。

---

## 7. 附录：API 调用示例

### 7.1 用户输入
> 创建一个叫 hello.js 的文件，内容为 console.log("Hello");

### 7.2 模型工具定义 (OpenAI 格式)
```json
[
  {
    "type": "function",
    "function": {
      "name": "create_file",
      "description": "创建新文件",
      "parameters": {
        "type": "object",
        "properties": {
          "file_path": {"type": "string"},
          "content": {"type": "string"}
        },
        "required": ["file_path", "content"]
      }
    }
  }
]
```

### 7.3 模型返回的 `tool_calls`
```json
{
  "choices": [{
    "message": {
      "role": "assistant",
      "tool_calls": [
        {
          "id": "call_123",
          "type": "function",
          "function": {
            "name": "create_file",
            "arguments": "{\"file_path\": \"hello.js\", \"content\": \"console.log(\\\"Hello\\\");\"}"
          }
        }
      ]
    }
  }]
}
```

### 7.4 程序反馈结果
```json
{
  "role": "tool",
  "tool_call_id": "call_123",
  "content": "文件 hello.js 创建成功"
}
```

---
本文档为简易版 Claude Code 的完整 PRD，开发人员可基于此文档进行技术实现。如有疑问或需要调整，请及时沟通。

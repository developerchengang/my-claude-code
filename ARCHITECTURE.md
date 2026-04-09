# Claude CLI Architecture

## Overview

Claude CLI is a local command-line tool that leverages LLMs through natural language interaction to perform file creation and editing operations.

## Architecture Layers

```
┌─────────────────────────────────────┐
│           main.py                   │  Entry point, CLI loop, slash commands
├─────────────────────────────────────┤
│           llm.py                    │  LLM client, tool call parsing
├─────────────────────────────────────┤
│           tools.py                  │  File operations, snapshots, undo
├─────────────────────────────────────┤
│     config.py  │  history.py        │  Configuration, session history
└─────────────────────────────────────┘
```

## Module Design

### config.py - Configuration Management

```
Config
├── Loads settings from ~/.myai/settings.json
├── Provides typed accessors: api_key, base_url, model, temperature
├── _run_setup_wizard() - Interactive first-time setup
└── is_configured() - Check if API key is present
```

**Settings Schema**:
| Key | Type | Default |
|-----|------|---------|
| provider | string | openai |
| api_key | string | (empty) |
| base_url | string | https://api.openai.com/v1 |
| model | string | gpt-4o |
| temperature | float | 0.7 |
| max_history_tokens | int | 4096 |

### history.py - Session History

```
SessionHistory
├── .myai/session.jsonl (one JSON object per line)
├── add_message(role, content, tool_calls)
├── load_recent(n=10)
├── clear()
└── get_summary()
```

**Message Format**:
```json
{
  "timestamp": "2024-01-01T12:00:00",
  "role": "user|assistant|tool",
  "content": "message text",
  "tool_calls": [...]  // optional
}
```

### tools.py - File Operations

```
FileTools
├── _validate_path(file_path) -> Path
│   └── Resolves path, checks within project_root
├── _create_snapshot(file_path) -> snapshot_path
│   └── .myai/file-history/{MD5(path)}/{timestamp}_{filename}
├── create_file(file_path, content) -> Result
├── edit_file(file_path, operations) -> Result (with diff)
├── confirm_create() / confirm_edit() -> Result
├── undo_last() -> Result
└── generate_unified_diff() -> str
```

**Operations Array** (from LLM):
```python
{
    "action": "insert|delete|replace",
    "start_line": 1,       # 1-indexed
    "end_line": 5,          # optional, inclusive
    "content": "text"       # for insert/replace
}
```

**Pending Edit State**:
```
PendingEdit
├── file_path: Path
├── original_content: str
├── new_content: str
├── diff: str
├── operations: List[Dict]
└── timestamp: float
```

### llm.py - LLM Integration

```
LLMClient
├── TOOL_DEFINITIONS (OpenAI format)
├── _init_client() -> OpenAI SDK instance
├── chat(messages, tools=True) -> LLMResponse
└── _parse_openai_response() -> LLMResponse

ToolCall(id, name, arguments)
LLMResponse(content, tool_calls)
```

**Tool Definitions**:
- `create_file`: file_path, content
- `edit_file`: file_path, operations[]

### main.py - Main Loop

```
ClaudeCLI
├── run()                           # Main interactive loop
├── _process_user_message(message) # LLM workflow
├── _handle_tool_call(content, tool_calls)
├── _request_confirmation(result, tool_call_id)
├── _display_markdown(content)      # Rich rendering
└── _handle_slash_command(cmd)     # Local commands

SlashCommandCompleter
└── Provides /commands and file path completion
```

## State Machine: Edit Confirmation

```
User Input
    │
    ▼
┌─────────┐
│  LLM    │────── content ─────► Display Markdown
└─────────┘
    │
    │ tool_calls
    ▼
┌──────────────────┐
│  Execute Tools   │
└──────────────────┘
    │
    ├── needs_confirmation = false
    │       │
    │       ▼
    │   Report Result
    │
    └── needs_confirmation = true
            │
            ▼
    ┌───────────────────┐
    │  Display Diff     │
    │  Prompt [y/N]     │
    └───────────────────┘
            │
        User Input
            │
        ┌───┴───┐
        │   n   │ y
        ▼       ▼
    Cancelled  Confirm
                    │
                    ▼
            Create Snapshot
                    │
                    ▼
              Write File
                    │
                    ▼
              Report Result
```

## Data Flow

```
┌─────────────┐     user message      ┌─────────────┐
│   User      │ ──────────────────────►│    LLM      │
└─────────────┘                       └─────────────┘
                                             │
                                    tool_calls │ content
                                             │
        ┌─────────────────────────────────────┤
        │                                     │
        ▼                                     ▼
┌─────────────┐                       ┌─────────────┐
│ FileTools   │                       │   Rich      │
│             │                       │  Display    │
└─────────────┘                       └─────────────┘
        │                                     ▲
        │ tool_result                         │
        └─────────────────────────────────────┤
                                             │
                                             ▼
                                    ┌─────────────┐
                                    │  Session    │
                                    │  History    │
                                    └─────────────┘
```

## File Structure

```
.myai/
├── settings.json        # User configuration
├── session.jsonl        # Conversation history
└── file-history/
    └── {MD5(path)}/
        └── {timestamp}_{filename}  # Snapshots
```

## Security Model

1. **Path Validation**: All paths resolved via `Path.resolve()` and checked against `project_root`
2. **No Remote Code Execution**: Only file I/O operations, no shell commands
3. **Explicit Confirmation**: All destructive operations require user input
4. **Snapshot Rollback**: Every confirmed edit creates a backup before overwriting

## Extension Points

To add new tools:
1. Define tool in `llm.py` `TOOL_DEFINITIONS`
2. Add handler in `main.py` `_handle_tool_call()`
3. Add method in `tools.py` if file operations needed

# Claude CLI

A local command-line tool that uses natural language to create and edit files through AI.

## Features

- **File Creation**: Create new files with natural language commands
- **File Editing**: Replace text strings with user confirmation (Claude Code style)
- **File Search**: Search files with regex patterns (grep)
- **Read Before Edit**: Must read file content before editing
- **Agentic Loop**: Multi-turn tool execution - AI can use multiple tools in sequence to complete complex tasks
- **File Reference**: Use `@filename` to include file contents in your request
- **Safety First**: All modifications require `y/N` confirmation
- **Snapshots & Undo**: Automatic backups before changes, `/undo` to restore
- **Path Security**: Prevents access to files outside the project directory
- **Session History**: Persisted conversation history in JSONL format
- **Slash Commands**: `/help`, `/history`, `/clear`, `/undo`, `/settings`, `/exit`
- **Rich Output**: Markdown rendering and syntax-highlighted diffs

## Installation

```bash
pip install -r requirements.txt
```

## Quick Start

```bash
python main.py
```

On first run, the setup wizard will guide you through API configuration.

## Usage

### Create a file

```
> Create a file called hello.js with console.log('Hello World')
```

### Edit a file

```
> Change "foo" to "bar" in config.json
> Replace all occurrences of "TODO" with "DONE" in main.py
```

### Search files

```
> Find all files containing "TODO"
> Search for "function_name" in *.py files
```

### Reference file contents

```
> Explain what @main.py does
> Add tests for the function in @tools.py
```

### Slash Commands

| Command | Description |
|---------|-------------|
| `/help` | Show help information |
| `/settings` | Display current configuration |
| `/history` | Show conversation history summary |
| `/clear` | Clear conversation history |
| `/undo` | Undo the last file edit |
| `/exit` | Exit the program |

## Configuration

Settings are stored in `~/.myai/settings.json`:

```json
{
  "provider": "openai",
  "api_key": "your-api-key",
  "base_url": "https://api.openai.com/v1",
  "model": "gpt-4o",
  "temperature": 0.7,
  "max_history_tokens": 4096
}
```

### Supported Providers

- OpenAI (api.openai.com)
- SiliconFlow (siliconflow.cn)
- Ollama (localhost:11434)
- MiniMax (Anthropic-compatible)
- Any OpenAI-compatible or Anthropic-compatible API

## Safety Mechanisms

1. **Path Validation**: All file paths are resolved and validated to prevent directory traversal
2. **User Confirmation**: File modifications display a diff and require `y` to proceed
3. **Automatic Snapshots**: Files are backed up to `.myai/file-history/` before changes
4. **Undo Support**: Restore any file from its latest snapshot

## Requirements

- Python 3.9+
- OpenAI-compatible or Anthropic-compatible API key

## License

MIT

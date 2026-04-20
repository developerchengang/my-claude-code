"""LLM client for Claude CLI - supports OpenAI and Anthropic protocols."""

import json
import uuid
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Union

import anthropic
import openai


@dataclass
class ToolCall:
    """Represents a tool call from the LLM."""
    id: str
    name: str
    arguments: Dict[str, Any]


@dataclass
class LLMResponse:
    """Represents a response from the LLM."""
    content: str
    tool_calls: List[ToolCall] = field(default_factory=list)


# OpenAI tool definitions (function calling format)
OPENAI_TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read the contents of a file. MUST be called before edit_file to ensure you know the current content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "Path to the file to read"}
                },
                "required": ["file_path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "grep",
            "description": "Search for a pattern in file contents using regex.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Regex pattern to search for"},
                    "path": {"type": "string", "description": "File or directory to search in"},
                    "glob": {"type": "string", "description": "Glob pattern to filter files (e.g., *.py)"},
                    "output_mode": {"type": "string", "enum": ["files_with_matches", "content", "count"], "description": "Output format"},
                    "case_insensitive": {"type": "boolean", "description": "Case insensitive search"}
                },
                "required": ["pattern"]
            }
        }
    },
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
            "description": "Edit an existing file by replacing old_string with new_string. File must be read first using read_file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"},
                    "old_string": {"type": "string", "description": "The exact string to find and replace (must match exactly)"},
                    "new_string": {"type": "string", "description": "The replacement string"},
                    "replace_all": {"type": "boolean", "description": "Replace all occurrences? Default false."}
                },
                "required": ["file_path", "old_string", "new_string"]
            }
        }
    }
]

# Anthropic tool definitions
ANTHROPIC_TOOL_DEFINITIONS = [
    {
        "name": "read_file",
        "description": "Read the contents of a file. MUST be called before edit_file to ensure you know the current content.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Path to the file to read"}
            },
            "required": ["file_path"]
        }
    },
    {
        "name": "grep",
        "description": "Search for a pattern in file contents using regex.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Regex pattern to search for"},
                "path": {"type": "string", "description": "File or directory to search in"},
                "glob": {"type": "string", "description": "Glob pattern to filter files"},
                "output_mode": {"type": "string", "enum": ["files_with_matches", "content", "count"]},
                "case_insensitive": {"type": "boolean"}
            },
            "required": ["pattern"]
        }
    },
    {
        "name": "create_file",
        "description": "Create a new file with the specified content.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string"},
                "content": {"type": "string"}
            },
            "required": ["file_path", "content"]
        }
    },
    {
        "name": "edit_file",
        "description": "Edit an existing file by replacing old_string with new_string. File must be read first.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string"},
                "old_string": {"type": "string"},
                "new_string": {"type": "string"},
                "replace_all": {"type": "boolean"}
            },
            "required": ["file_path", "old_string", "new_string"]
        }
    }
]


class LLMClient:
    """Client for interacting with OpenAI or Anthropic compatible LLMs."""

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        temperature: float = 0.7,
        provider: str = "openai"
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.temperature = temperature
        self.provider = provider.lower()
        self._openai_client: Optional[openai.OpenAI] = None
        self._anthropic_client: Optional[anthropic.Anthropic] = None

    def _get_openai_client(self) -> openai.OpenAI:
        if self._openai_client is None:
            self._openai_client = openai.OpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
            )
        return self._openai_client

    def _get_anthropic_client(self) -> anthropic.Anthropic:
        if self._anthropic_client is None:
            self._anthropic_client = anthropic.Anthropic(
                api_key=self.api_key,
                base_url=self.base_url,
            )
        return self._anthropic_client

    def chat(
        self,
        messages: List[Dict[str, str]],
        tools: bool = True
    ) -> LLMResponse:
        """Send a chat message to the LLM."""
        if self.provider == "anthropic":
            return self._chat_anthropic(messages, tools)
        else:
            return self._chat_openai(messages, tools)

    def _chat_openai(
        self,
        messages: List[Dict[str, str]],
        tools: bool
    ) -> LLMResponse:
        """Call OpenAI-compatible API."""
        client = self._get_openai_client()

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
        }

        if tools:
            payload["tools"] = OPENAI_TOOL_DEFINITIONS
            payload["tool_choice"] = "auto"

        response = client.chat.completions.create(**payload)

        choices = response.choices
        if not choices:
            return LLMResponse(content="", tool_calls=[])

        message = choices[0].message
        if message is None:
            return LLMResponse(content="", tool_calls=[])

        content = message.content or ""

        tool_calls = []
        for tc in (message.tool_calls or []):
            if tc.type == "function":
                func = tc.function
                try:
                    args = json.loads(func.arguments) if isinstance(func.arguments, str) else func.arguments
                except json.JSONDecodeError:
                    args = {}

                tool_calls.append(ToolCall(
                    id=tc.id or str(uuid.uuid4()),
                    name=func.name,
                    arguments=args
                ))

        return LLMResponse(content=content, tool_calls=tool_calls)

    def _chat_anthropic(
        self,
        messages: List[Dict[str, str]],
        tools: bool
    ) -> LLMResponse:
        """Call Anthropic-compatible API."""
        client = self._get_anthropic_client()

        # Convert messages to Anthropic format. System messages must go in the
        # top-level `system` parameter, not inside the messages array.
        system_parts: List[str] = []
        anthropic_messages = []
        for msg in messages:
            role = msg.get("role")
            content = msg.get("content", "")

            if role == "system":
                if content:
                    system_parts.append(content)
            elif role in ("user", "assistant"):
                anthropic_messages.append({
                    "role": role,
                    "content": content
                })
            elif role == "tool":
                anthropic_messages.append({
                    "role": "user",
                    "content": f"[Tool Result: {content}]"
                })

        request_kwargs = {
            "model": self.model,
            "messages": anthropic_messages,
            "max_tokens": 4096,
        }

        if system_parts:
            request_kwargs["system"] = "\n\n".join(system_parts)

        if tools:
            request_kwargs["tools"] = ANTHROPIC_TOOL_DEFINITIONS

        if self.temperature:
            request_kwargs["temperature"] = self.temperature

        response = client.messages.create(**request_kwargs)

        content = ""
        tool_calls = []

        for block in (response.content or []):
            if block.type == "text":
                content += block.text
            elif block.type == "tool_use":
                tool_calls.append(ToolCall(
                    id=block.id,
                    name=block.name,
                    arguments=block.input
                ))

        return LLMResponse(content=content, tool_calls=tool_calls)

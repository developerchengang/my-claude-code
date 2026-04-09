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
            "name": "create_file",
            "description": "Create a new file with the specified content in the project directory.",
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
            "description": "Edit an existing file with the specified operations.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"},
                    "operations": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "action": {"type": "string", "enum": ["insert", "delete", "replace"]},
                                "start_line": {"type": "integer"},
                                "end_line": {"type": "integer"},
                                "content": {"type": "string"}
                            },
                            "required": ["action", "start_line"]
                        }
                    }
                },
                "required": ["file_path", "operations"]
            }
        }
    }
]

# Anthropic tool definitions
ANTHROPIC_TOOL_DEFINITIONS = [
    {
        "name": "create_file",
        "description": "Create a new file with the specified content in the project directory.",
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
        "description": "Edit an existing file with the specified operations.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string"},
                "operations": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "action": {"type": "string", "enum": ["insert", "delete", "replace"]},
                            "start_line": {"type": "integer"},
                            "end_line": {"type": "integer"},
                            "content": {"type": "string"}
                        },
                        "required": ["action", "start_line"]
                    }
                }
            },
            "required": ["file_path", "operations"]
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
        content = message.content or ""

        tool_calls = []
        for tc in message.tool_calls or []:
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

        # Convert messages to Anthropic format
        anthropic_messages = []
        for msg in messages:
            role = msg.get("role")
            content = msg.get("content", "")

            if role == "system":
                anthropic_messages.append({
                    "role": "user",
                    "content": f"[System: {content}]"
                })
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

        if tools:
            request_kwargs["tools"] = ANTHROPIC_TOOL_DEFINITIONS

        if self.temperature:
            request_kwargs["temperature"] = self.temperature

        response = client.messages.create(**request_kwargs)

        content = ""
        tool_calls = []

        for block in response.content:
            if block.type == "text":
                content += block.text
            elif block.type == "tool_use":
                tool_calls.append(ToolCall(
                    id=block.id,
                    name=block.name,
                    arguments=block.input
                ))

        return LLMResponse(content=content, tool_calls=tool_calls)

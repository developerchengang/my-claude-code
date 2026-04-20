"""Model metadata: context windows and token estimation.

Vendors do not expose a metadata endpoint for context size, so every client
ships a hardcoded table — Claude Code does the same. Unknown models fall
back to a conservative default.
"""

from typing import Dict

CONTEXT_WINDOWS: Dict[str, int] = {
    # Claude
    "claude-opus-4-7": 200_000,
    "claude-opus-4-6": 200_000,
    "claude-sonnet-4-6": 200_000,
    "claude-sonnet-4-5": 200_000,
    "claude-haiku-4-5": 200_000,
    "claude-3-5-sonnet-20241022": 200_000,
    "claude-3-5-haiku-20241022": 200_000,
    "claude-3-opus-20240229": 200_000,
    # OpenAI
    "gpt-4o": 128_000,
    "gpt-4o-mini": 128_000,
    "gpt-4-turbo": 128_000,
    "gpt-4": 8_192,
    "gpt-3.5-turbo": 16_385,
    # DeepSeek
    "deepseek-chat": 128_000,
    "deepseek-coder": 128_000,
    # Qwen
    "qwen-turbo": 1_000_000,
    "qwen-plus": 131_072,
    "qwen-max": 32_768,
}

DEFAULT_WINDOW = 128_000


def get_context_window(model: str) -> int:
    """Return the model's context window, falling back to DEFAULT_WINDOW.

    Matches by prefix so versioned variants (e.g. ``claude-sonnet-4-6-20250101``)
    resolve to the base family.
    """
    if model in CONTEXT_WINDOWS:
        return CONTEXT_WINDOWS[model]
    for key, window in CONTEXT_WINDOWS.items():
        if model.startswith(key):
            return window
    return DEFAULT_WINDOW


def estimate_tokens(text: str) -> int:
    """Rough token count for mixed CJK/ASCII text.

    CJK chars are ~1 token each; ASCII takes ~4 chars per token. This is an
    estimate — use LLM usage counters for the real number when available.
    """
    if not text:
        return 0
    cjk_count = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    ascii_count = len(text) - cjk_count
    return cjk_count + ascii_count // 4

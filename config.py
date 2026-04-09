"""Configuration management for Claude CLI."""

import json
import os
from pathlib import Path
from typing import Optional


class Config:
    """Manages application configuration stored in ~/.myai/settings.json."""

    DEFAULT_SETTINGS = {
        "provider": "openai",
        "api_key": "",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o",
        "temperature": 0.7,
        "max_history_tokens": 4096,
        "theme": "dark",
    }

    def __init__(self):
        self.settings_dir = Path.home() / ".myai"
        self.settings_file = self.settings_dir / "settings.json"
        self._settings: dict = {}
        self._load()

    def _load(self) -> None:
        """Load settings from file, or use defaults if file doesn't exist."""
        if self.settings_file.exists():
            try:
                with open(self.settings_file, "r", encoding="utf-8") as f:
                    self._settings = json.load(f)
                # Ensure all default keys exist
                for key, value in self.DEFAULT_SETTINGS.items():
                    if key not in self._settings:
                        self._settings[key] = value
            except (json.JSONDecodeError, IOError):
                self._settings = self.DEFAULT_SETTINGS.copy()
        else:
            self._settings = self.DEFAULT_SETTINGS.copy()

    def save(self) -> None:
        """Save current settings to file."""
        self.settings_dir.mkdir(parents=True, exist_ok=True)
        with open(self.settings_file, "w", encoding="utf-8") as f:
            json.dump(self._settings, f, indent=2)

    def get(self, key: str, default=None):
        """Get a setting value."""
        return self._settings.get(key, default)

    def set(self, key: str, value) -> None:
        """Set a setting value."""
        self._settings[key] = value

    @property
    def api_key(self) -> str:
        return self._settings.get("api_key", "")

    @property
    def base_url(self) -> str:
        return self._settings.get("base_url", "https://api.openai.com/v1")

    @property
    def model(self) -> str:
        return self._settings.get("model", "gpt-4o")

    @property
    def temperature(self) -> float:
        return self._settings.get("temperature", 0.7)

    @property
    def max_history_tokens(self) -> int:
        return self._settings.get("max_history_tokens", 4096)


def is_configured() -> bool:
    """Check if the API key is configured."""
    config = Config()
    return bool(config.api_key and config.api_key.strip())


def _run_setup_wizard() -> Config:
    """Run interactive setup wizard for first-time configuration."""
    from rich.console import Console
    from rich.prompt import Prompt

    console = Console()
    config = Config()

    console.print("\n[bold green]Welcome to Claude CLI Setup![/bold green]\n")
    console.print("Let's configure your API settings.\n")

    # Provider selection
    console.print("[bold]Select your API provider:[/bold]")
    console.print("1. OpenAI (api.openai.com)")
    console.print("2. SiliconFlow (siliconflow.cn)")
    console.print("3. Ollama (localhost)")
    console.print("4. MiniMax (api.minimaxi.com) - Anthropic protocol")
    console.print("5. Custom OpenAI-compatible API")
    console.print("6. Custom Anthropic-compatible API")

    provider_choice = Prompt.ask(
        "Enter choice",
        choices=["1", "2", "3", "4", "5", "6"],
        default="1"
    )

    if provider_choice == "1":
        config.set("provider", "openai")
        config.set("base_url", "https://api.openai.com/v1")
        model_default = "gpt-4o"
    elif provider_choice == "2":
        config.set("provider", "siliconflow")
        config.set("base_url", "https://api.siliconflow.cn/v1")
        model_default = "Qwen/Qwen2.5-7B-Instruct"
    elif provider_choice == "3":
        config.set("provider", "ollama")
        config.set("base_url", "http://localhost:11434/v1")
        model_default = "llama3.2"
    elif provider_choice == "4":
        config.set("provider", "anthropic")
        config.set("base_url", "https://api.minimaxi.com/anthropic")
        model_default = "MiniMax-M2.7"
    elif provider_choice == "5":
        config.set("provider", "openai")
        config.set("base_url", Prompt.ask("Enter API base URL"))
        model_default = "gpt-4o"
    else:
        config.set("provider", "anthropic")
        config.set("base_url", Prompt.ask("Enter API base URL"))
        model_default = "claude-3-5-sonnet"

    # API Key
    api_key = Prompt.ask("Enter your API key", password=True)
    config.set("api_key", api_key)

    # Model
    model = Prompt.ask("Model", default=model_default)
    config.set("model", model)

    # Temperature
    temp_str = Prompt.ask("Temperature (0.0-2.0)", default="0.7")
    try:
        temperature = float(temp_str)
        temperature = max(0.0, min(2.0, temperature))
    except ValueError:
        temperature = 0.7
    config.set("temperature", temperature)

    # Max history tokens
    tokens_str = Prompt.ask("Max history tokens", default="4096")
    try:
        max_tokens = int(tokens_str)
    except ValueError:
        max_tokens = 4096
    config.set("max_history_tokens", max_tokens)

    config.save()

    console.print("\n[bold green]Configuration saved![/bold green]\n")

    return config

"""Configuration management for AVI."""

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_OLLAMA_HOST = "http://127.0.0.1:11434"
DEFAULT_MODEL = "qwen2.5:1.5b"
DEFAULT_TIMEOUT = 30.0
DEFAULT_TEMPERATURE = 0.1
DEFAULT_KEEP_ALIVE = "5m"
DEFAULT_COMMAND_TIMEOUT = 10.0
DEFAULT_MAX_OUTPUT_BYTES = 65536  # 64 KB output buffer
DEFAULT_SYSTEM_PROMPT = (
    "You are AVI, a fast, concise Linux terminal assistant. "
    "If the user asks to perform an action, modify files, or execute a system command, propose the exact command in the format:\n"
    "COMMAND: <exact_command>\n"
    "If the user asks an informational or explanatory question, answer directly and concisely without COMMAND:.\n"
    "Never generate destructive commands (e.g. rm -rf /, mkfs, dd) unless specifically asked."
)


@dataclass
class Config:
    """Runtime configuration for AVI."""

    host: str = DEFAULT_OLLAMA_HOST
    model: str = DEFAULT_MODEL
    provider: str = "ollama"
    timeout: float = DEFAULT_TIMEOUT
    temperature: float = DEFAULT_TEMPERATURE
    keep_alive: str = DEFAULT_KEEP_ALIVE
    stream: bool = True
    show_timing: bool = False
    system_prompt: str = DEFAULT_SYSTEM_PROMPT
    command_timeout: float = DEFAULT_COMMAND_TIMEOUT
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES
    antigravity_bin: str | None = None
    antigravity_model: str | None = None

    @classmethod
    def load(cls, **overrides: Any) -> "Config":
        """Load configuration with priority: overrides > env vars > file > defaults."""
        config_data: dict[str, Any] = {}

        # 1. Check optional user configuration file: ~/.config/avi/config.json
        config_path = Path.home() / ".config" / "avi" / "config.json"
        if config_path.is_file():
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    file_data = json.load(f)
                    if isinstance(file_data, dict):
                        config_data.update(file_data)
            except (json.JSONDecodeError, OSError):
                # Ignore invalid config file to ensure CLI remains reliable
                pass

        # 2. Check environment variables
        env_host = os.getenv("AVI_OLLAMA_HOST") or os.getenv("OLLAMA_HOST")
        if env_host:
            config_data["host"] = env_host

        env_model = os.getenv("AVI_MODEL")
        if env_model:
            config_data["model"] = env_model

        env_timeout = os.getenv("AVI_TIMEOUT")
        if env_timeout:
            try:
                config_data["timeout"] = float(env_timeout)
            except ValueError:
                pass

        env_temperature = os.getenv("AVI_TEMPERATURE")
        if env_temperature:
            try:
                config_data["temperature"] = float(env_temperature)
            except ValueError:
                pass

        env_timing = os.getenv("AVI_TIMING")
        if env_timing is not None:
            config_data["show_timing"] = env_timing.lower() in ("1", "true", "yes")

        env_stream = os.getenv("AVI_STREAM")
        if env_stream is not None:
            config_data["stream"] = env_stream.lower() not in ("0", "false", "no")

        env_cmd_timeout = os.getenv("AVI_COMMAND_TIMEOUT")
        if env_cmd_timeout:
            try:
                config_data["command_timeout"] = float(env_cmd_timeout)
            except ValueError:
                pass

        env_max_out = os.getenv("AVI_MAX_OUTPUT_BYTES")
        if env_max_out:
            try:
                config_data["max_output_bytes"] = int(env_max_out)
            except ValueError:
                pass

        env_provider = os.getenv("AVI_PROVIDER")
        if env_provider:
            config_data["provider"] = env_provider.strip().lower()

        env_ag_bin = os.getenv("AVI_ANTIGRAVITY_BIN")
        if env_ag_bin:
            config_data["antigravity_bin"] = env_ag_bin

        env_ag_model = os.getenv("AVI_ANTIGRAVITY_MODEL")
        if env_ag_model:
            config_data["antigravity_model"] = env_ag_model

        # 3. Apply explicit CLI / caller overrides (excluding None values)
        for key, value in overrides.items():
            if value is not None:
                config_data[key] = value

        return cls(
            host=config_data.get("host", DEFAULT_OLLAMA_HOST),
            model=config_data.get("model", DEFAULT_MODEL),
            provider=config_data.get("provider", "ollama"),
            timeout=float(config_data.get("timeout", DEFAULT_TIMEOUT)),
            temperature=float(config_data.get("temperature", DEFAULT_TEMPERATURE)),
            keep_alive=str(config_data.get("keep_alive", DEFAULT_KEEP_ALIVE)),
            stream=bool(config_data.get("stream", True)),
            show_timing=bool(config_data.get("show_timing", False)),
            system_prompt=str(config_data.get("system_prompt", DEFAULT_SYSTEM_PROMPT)),
            command_timeout=float(config_data.get("command_timeout", DEFAULT_COMMAND_TIMEOUT)),
            max_output_bytes=int(config_data.get("max_output_bytes", DEFAULT_MAX_OUTPUT_BYTES)),
            antigravity_bin=config_data.get("antigravity_bin"),
            antigravity_model=config_data.get("antigravity_model"),
        )

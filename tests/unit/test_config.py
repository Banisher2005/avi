"""Unit tests for AVI configuration."""

import json
from pathlib import Path
from unittest.mock import patch

from avi.config import Config, DEFAULT_MODEL, DEFAULT_OLLAMA_HOST


def test_default_config():
    config = Config.load()
    assert config.host == DEFAULT_OLLAMA_HOST
    assert config.model == DEFAULT_MODEL
    assert config.provider == "ollama"
    assert config.timeout == 30.0
    assert config.temperature == 0.1
    assert config.keep_alive == "5m"
    assert config.stream is True
    assert config.show_timing is False


def test_cli_overrides():
    config = Config.load(
        model="custom:latest",
        host="http://localhost:9999",
        timeout=15.0,
        show_timing=True,
        stream=False,
    )
    assert config.model == "custom:latest"
    assert config.host == "http://localhost:9999"
    assert config.timeout == 15.0
    assert config.show_timing is True
    assert config.stream is False


def test_environment_variable_overrides(monkeypatch):
    monkeypatch.setenv("AVI_MODEL", "qwen2.5:3b")
    monkeypatch.setenv("AVI_OLLAMA_HOST", "http://192.168.1.50:11434")
    monkeypatch.setenv("AVI_TIMEOUT", "45")
    monkeypatch.setenv("AVI_TEMPERATURE", "0.5")
    monkeypatch.setenv("AVI_TIMING", "1")
    monkeypatch.setenv("AVI_STREAM", "0")

    config = Config.load()
    assert config.model == "qwen2.5:3b"
    assert config.host == "http://192.168.1.50:11434"
    assert config.timeout == 45.0
    assert config.temperature == 0.5
    assert config.show_timing is True
    assert config.stream is False


def test_config_file_loading(tmp_path, monkeypatch):
    fake_config = {
        "model": "deepseek-coder:1.3b",
        "timeout": 20.0,
        "show_timing": True,
    }
    config_dir = tmp_path / ".config" / "avi"
    config_dir.mkdir(parents=True)
    config_file = config_dir / "config.json"
    config_file.write_text(json.dumps(fake_config), encoding="utf-8")

    with patch("pathlib.Path.home", return_value=tmp_path):
        config = Config.load()
        assert config.model == "deepseek-coder:1.3b"
        assert config.timeout == 20.0
        assert config.show_timing is True
        # Verify defaults for unprovided fields
        assert config.host == DEFAULT_OLLAMA_HOST


def test_corrupted_config_file_handling(tmp_path):
    config_dir = tmp_path / ".config" / "avi"
    config_dir.mkdir(parents=True)
    config_file = config_dir / "config.json"
    config_file.write_text("invalid json content {{{", encoding="utf-8")

    with patch("pathlib.Path.home", return_value=tmp_path):
        config = Config.load()
        assert config.model == DEFAULT_MODEL
        assert config.host == DEFAULT_OLLAMA_HOST

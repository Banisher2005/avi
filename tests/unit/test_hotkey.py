"""Unit tests for Linux desktop environment detection, hotkey helpers, and systemd generator."""

import os
from unittest.mock import patch

import pytest

from avi.hotkey.detector import detect_desktop_environment
from avi.hotkey.service import (
    generate_desktop_entry,
    generate_systemd_user_service,
    get_hotkey_instructions,
)


def test_detect_desktop_environment_wayland():
    with patch.dict(os.environ, {"WAYLAND_DISPLAY": "wayland-0", "XDG_SESSION_TYPE": "wayland", "XDG_CURRENT_DESKTOP": "GNOME"}, clear=True):
        info = detect_desktop_environment()
        assert info.display_server == "wayland"
        assert info.is_headless is False
        assert info.can_grab_keys_directly is False
        assert info.recommended_hotkey_method == "desktop_shortcut"
        assert info.desktop == "gnome"


def test_detect_desktop_environment_x11():
    with patch.dict(os.environ, {"DISPLAY": ":0", "XDG_SESSION_TYPE": "x11", "XDG_CURRENT_DESKTOP": "XFCE"}, clear=True):
        info = detect_desktop_environment()
        assert info.display_server == "x11"
        assert info.is_headless is False
        assert info.can_grab_keys_directly is True
        assert info.recommended_hotkey_method == "x11_listener"
        assert info.desktop == "xfce"


def test_detect_desktop_environment_headless():
    with patch.dict(os.environ, {}, clear=True):
        info = detect_desktop_environment()
        assert info.display_server == "headless"
        assert info.is_headless is True
        assert info.can_grab_keys_directly is False
        assert info.recommended_hotkey_method == "headless"


def test_get_hotkey_instructions_wayland():
    with patch.dict(os.environ, {"WAYLAND_DISPLAY": "wayland-0", "XDG_SESSION_TYPE": "wayland", "XDG_CURRENT_DESKTOP": "GNOME"}, clear=True):
        res = get_hotkey_instructions()
        assert res["display_server"] == "wayland"
        assert "GNOME" in res["instructions"]
        assert "Custom Shortcuts" in res["instructions"]


def test_get_hotkey_instructions_x11():
    with patch.dict(os.environ, {"DISPLAY": ":0", "XDG_SESSION_TYPE": "x11"}, clear=True):
        res = get_hotkey_instructions()
        assert res["display_server"] == "x11"
        assert "xbindkeys" in res["instructions"]


def test_get_hotkey_instructions_headless():
    with patch.dict(os.environ, {}, clear=True):
        res = get_hotkey_instructions()
        assert res["display_server"] == "headless"
        assert "headless" in res["instructions"].lower()


def test_generate_systemd_user_service():
    service_content = generate_systemd_user_service(
        avi_binary_path="/usr/bin/avi",
        transport="stdio",
    )
    assert "[Unit]" in service_content
    assert "[Service]" in service_content
    assert "[Install]" in service_content
    assert "ExecStart=/usr/bin/avi gateway --transport stdio" in service_content
    assert "WantedBy=default.target" in service_content


def test_generate_desktop_entry():
    entry_content = generate_desktop_entry(avi_binary_path="/usr/bin/avi")
    assert "[Desktop Entry]" in entry_content
    assert "Type=Application" in entry_content
    assert "Name=AVI Terminal Assistant" in entry_content
    assert "Exec=/usr/bin/avi" in entry_content

"""Systemd user service and desktop shortcut generation for AVI."""

import shutil
from typing import Any

from avi.hotkey.detector import detect_desktop_environment


def generate_systemd_user_service(
    avi_binary_path: str | None = None,
    transport: str = "stdio",
) -> str:
    """Generate systemd user service unit content for running AVI Gateway."""
    bin_path = avi_binary_path or shutil.which("avi") or "/usr/local/bin/avi"
    return f"""[Unit]
Description=AVI Universal Protocol Gateway
Documentation=https://github.com/Banisher2005/avi
After=network.target

[Service]
Type=simple
ExecStart={bin_path} gateway --transport {transport}
Restart=on-failure
RestartSec=3s
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target
"""


def generate_desktop_entry(
    avi_binary_path: str | None = None,
    terminal_emulator: str = "auto",
) -> str:
    """Generate .desktop launcher entry for triggering an interactive AVI session."""
    bin_path = avi_binary_path or shutil.which("avi") or "/usr/local/bin/avi"
    return f"""[Desktop Entry]
Version=1.0
Type=Application
Name=AVI Terminal Assistant
Comment=Instant local-first AI assistant for Linux terminals
Exec={bin_path}
Terminal=true
Categories=Utility;System;Development;
Keywords=ai;assistant;terminal;shell;
"""


def get_hotkey_instructions() -> dict[str, Any]:
    """Provide specific instructions for configuring global hotkeys based on environment."""
    env = detect_desktop_environment()

    if env.display_server == "wayland":
        instructions = (
            "Under Wayland, security policies block arbitrary background processes from "
            "intercepting keystrokes. To bind a global hotkey (e.g. Super+Space or Ctrl+Space):\n\n"
            "1. GNOME: Settings -> Keyboard -> View and Customize Shortcuts -> Custom Shortcuts\n"
            "   Add: Name='AVI', Command='gnome-terminal -- avi', Shortcut='<Ctrl>Space'\n\n"
            "2. KDE: System Settings -> Shortcuts -> Custom Shortcuts\n"
            "   Add: New -> Global Shortcut -> Command/URL -> 'konsole -e avi'\n\n"
            "3. Sway / Hyprland:\n"
            "   In your config: bindsym $mod+Space exec alacritty -e avi"
        )
    elif env.display_server == "x11":
        instructions = (
            "Under X11, you can bind a global hotkey via your desktop environment shortcut settings "
            "or use standard tools like xbindkeys:\n\n"
            "Add to ~/.xbindkeysrc:\n"
            '  "xterm -e avi"\n'
            "  Control + space"
        )
    else:
        instructions = (
            "Running in headless or SSH environment. Global hotkeys are not applicable; "
            "use 'avi' in the terminal or run 'avi gateway' over stdio/tcp."
        )

    return {
        "display_server": env.display_server,
        "desktop": env.desktop,
        "recommended_method": env.recommended_hotkey_method,
        "instructions": instructions,
    }

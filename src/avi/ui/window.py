"""AVI Desktop UI — GTK4 keyboard-first assistant window.

A modern, responsive GTK4 assistant interface that streams AVI responses inline.
Works natively on Wayland (via XDG foreign toplevel / layer-shell) and X11.

Architecture:
  AviWindow (GTK4 ApplicationWindow)
  ├── HeaderBar        — Window controls and title
  ├── HeaderBox        — Prompt entry, Send button, and activity spinner
  ├── ScrolledWindow   — Bounded conversation history containing:
  │   ├── UserBubble         — User query
  │   ├── AssistantBubble    — Conversational response with optional action buttons
  │   ├── ConfirmationCard   — Inline confirmation prompt with Cancel/Proceed
  │   └── ErrorBubble        — User-friendly error message
  └── StatusBar        — Mode indicator, hints, and provider status

Key design decisions:
  - GTK4 native: uses CSS styling, EventControllerKey, and HeaderBar
  - Headless-safe: gracefully detects display availability
  - Thread-safe updates: uses GLib.idle_add for all GUI updates
  - Non-blocking: all LLM calls, capability operations, and execution happen in worker threads
  - Bounded conversation history: caps messages to prevent unbounded memory growth
  - SafetyEngine authoritative: destructive operations require explicit user confirmation
"""

import logging
import os
import re
import subprocess
import threading
from pathlib import Path
from typing import Any

from avi.assistant.intents import (
    AssistantIntentType,
    clean_natural_language_input,
    detect_assistant_intent,
)

logger = logging.getLogger("avi.ui")

try:
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Gdk", "4.0")
    from gi.repository import Gdk, GLib, Gtk, Pango

    _GTK_AVAILABLE = True
except (ImportError, ValueError):
    _GTK_AVAILABLE = False
    Gdk = Any  # type: ignore[assignment,misc]
    GLib = Any  # type: ignore[assignment,misc]
    Gtk = Any  # type: ignore[assignment,misc]
    Pango = Any  # type: ignore[assignment,misc]


def check_display() -> bool:
    """Return True if a display server (Wayland or X11) is available."""
    return bool(os.getenv("WAYLAND_DISPLAY") or os.getenv("DISPLAY"))


def format_user_friendly_error(err: Exception | str, prompt: str = "") -> str:
    """Format technical exceptions into user-friendly assistant explanations."""
    err_str = str(err).strip()
    logger.warning("UI error occurred for prompt %r: %s", prompt, err_str, exc_info=True)

    lower = err_str.lower()
    if "timeout" in lower or "timed out" in lower:
        return "AVI took too long to respond."
    if "safe action" in lower or "unsafe" in lower:
        return "I couldn't determine a safe action for that request."
    if "screenshot" in lower or "screen" in lower:
        return "I couldn't take the screenshot. Please make sure your display session is active."
    if "volume" in lower or "audio" in lower:
        return (
            "I couldn't adjust the volume. Please check your audio system (PulseAudio / PipeWire)."
        )
    if "blocked" in lower:
        clean_reason = err_str.replace("Blocked: ", "").strip()
        return f"This action was blocked by safety policy: {clean_reason}"
    if "connection" in lower or "refused" in lower:
        return "I couldn't connect to the AI model. Please ensure Ollama or your AI service is running."
    if "not found" in lower or "app" in lower:
        return "I couldn't find or open that application."
    if "timer" in lower:
        return "I couldn't set that timer."

    if len(err_str) < 90 and not any(
        kw in err_str
        for kw in ("Traceback", "Exception", "RuntimeError", "AttributeError", "line ")
    ):
        return err_str
    return "I couldn't determine a safe action for that request."


CSS_STYLE = b"""
/* Antigravity CLI True Black Developer Command Palette */
window.avi-palette-window, window.avi-overlay-window, window.avi-main-window {
    background-color: #050505;
    color: #F2F2F2;
    border: 1px solid #242424;
    border-radius: 10px;
    box-shadow: 0 20px 50px rgba(0, 0, 0, 0.95);
    font-family: "JetBrains Mono", "Fira Code", "Cascadia Code", "Source Code Pro", monospace, sans-serif;
}

/* Primary Command Bar */
.avi-command-bar {
    background-color: #080808;
    padding: 14px 18px;
    min-height: 56px;
    border-top-left-radius: 10px;
    border-top-right-radius: 10px;
}

.avi-prompt-glyph {
    color: #F2F2F2;
    font-size: 20px;
    font-weight: 800;
    margin-right: 8px;
    margin-left: 2px;
    font-family: monospace;
}

entry.avi-command-input {
    background-color: transparent;
    border: none;
    box-shadow: none;
    outline: none;
    color: #F2F2F2;
    font-size: 17px;
    font-family: "JetBrains Mono", "Fira Code", monospace, sans-serif;
    padding: 6px 8px;
    min-height: 48px;
    line-height: 1.4;
}

entry.avi-command-input:focus {
    outline: none;
    border: none;
    box-shadow: none;
}

.avi-keycap-hint {
    background-color: #141414;
    color: #666666;
    border: 1px solid #242424;
    border-radius: 4px;
    padding: 3px 8px;
    font-size: 13px;
    font-family: monospace;
}

.avi-palette-close {
    background: transparent;
    border: none;
    color: #666666;
    padding: 4px 10px;
    font-size: 18px;
    font-weight: bold;
    border-radius: 4px;
}

.avi-palette-close:hover {
    background-color: #1a1a1a;
    color: #FF6B6B;
}

/* Transcript & Execution Output Lines */
.avi-execution-log, .avi-conversation-area, .avi-dynamic-results {
    background-color: #0D0D0D;
    border-top: 1px solid #242424;
    padding: 16px 20px 20px 20px;
}

.avi-cli-line {
    padding: 6px 0;
    font-family: "JetBrains Mono", "Fira Code", monospace, sans-serif;
}

.avi-glyph-cmd {
    color: #F2F2F2;
    font-weight: 800;
    font-size: 17px;
    margin-right: 8px;
    font-family: monospace;
}

.avi-glyph-work {
    color: #A0A0A0;
    font-size: 17px;
    margin-right: 8px;
    font-family: monospace;
}

.avi-glyph-ok {
    color: #4EBA6F;
    font-weight: 800;
    font-size: 17px;
    margin-right: 8px;
    font-family: monospace;
}

.avi-glyph-err {
    color: #FF6B6B;
    font-weight: 800;
    font-size: 17px;
    margin-right: 8px;
    font-family: monospace;
}

.avi-cli-text-cmd {
    color: #F2F2F2;
    font-weight: 600;
    font-size: 17px;
    line-height: 1.45;
    font-family: monospace;
}

.avi-cli-text-work {
    color: #A0A0A0;
    font-size: 15px;
    line-height: 1.4;
    font-family: monospace;
}

.avi-cli-text-ok {
    color: #F2F2F2;
    font-size: 17px;
    line-height: 1.5;
    font-family: monospace;
}

.avi-cli-text-err {
    color: #FF6B6B;
    font-size: 16px;
    line-height: 1.45;
    font-family: monospace;
}

button.avi-cli-action-btn, button.avi-action-btn, .avi-cli-action-btn, .avi-action-btn {
    background-color: #141414;
    background-image: none;
    color: #F2F2F2;
    border: 1px solid #242424;
    border-radius: 5px;
    padding: 4px 12px;
    font-size: 13px;
    font-family: monospace;
}

button.avi-cli-action-btn:hover, button.avi-action-btn:hover, .avi-cli-action-btn:hover, .avi-action-btn:hover {
    background-color: #242424;
    background-image: none;
    color: #FFFFFF;
}

/* Interactive Result Rows (YouTube / Search) */
.avi-results-list {
    background: transparent;
    padding: 4px 0;
}

.avi-result-row, .avi-best-match-card, .avi-result-card {
    background-color: #080808;
    border: 1px solid #242424;
    border-radius: 8px;
    margin: 4px 0;
    padding: 10px 14px;
    transition: background-color 0.1s ease;
}

.avi-result-row:hover, .avi-result-row-selected, .avi-result-card:hover {
    background-color: #151515;
    border-color: #303030;
}

.avi-row-thumb {
    background-color: #0D0D0D;
    border: 1px solid #242424;
    border-radius: 5px;
    padding: 4px 8px;
    min-width: 32px;
    color: #A0A0A0;
    font-size: 13px;
    font-weight: bold;
    font-family: monospace;
}

.avi-row-title, .avi-result-title {
    font-size: 15px;
    font-weight: 600;
    color: #F2F2F2;
    line-height: 1.35;
}

.avi-row-meta, .avi-result-meta {
    font-size: 13px;
    color: #666666;
    font-family: monospace;
}

button.avi-row-open-btn, button.avi-play-btn, .avi-row-open-btn, .avi-play-btn {
    background-color: #141414;
    background-image: none;
    color: #F2F2F2;
    border: 1px solid #242424;
    border-radius: 5px;
    padding: 4px 12px;
    font-size: 13px;
    font-weight: 600;
    font-family: monospace;
}

button.avi-row-open-btn:hover, button.avi-play-btn:hover, .avi-row-open-btn:hover, .avi-play-btn:hover {
    background-color: #242424;
    background-image: none;
    color: #FFFFFF;
}

/* Confirmation Box */
.avi-confirm-card {
    background-color: #0D0D0D;
    border: 1px solid #303030;
    border-radius: 8px;
    padding: 14px 18px;
}

.avi-confirm-header {
    font-size: 15px;
    font-weight: bold;
    color: #A0A0A0;
    font-family: monospace;
}

.avi-command-box {
    background-color: #050505;
    color: #F2F2F2;
    border: 1px solid #242424;
    border-radius: 6px;
    padding: 8px 12px;
    font-family: monospace;
    font-size: 14px;
    line-height: 1.4;
}

/* Compatibility classes */
.avi-prompt-entry {
    background-color: transparent;
    border: none;
    box-shadow: none;
    outline: none;
    color: #F2F2F2;
}
.avi-bubble-user {
    background: transparent;
    padding: 0;
}
.avi-bubble-assistant {
    background: transparent;
    padding: 0;
}
.avi-status-bar {
    background: transparent;
}
.avi-brand-badge {
    color: #F2F2F2;
    font-weight: 800;
}
.avi-user-text {
    color: #F2F2F2;
}
.avi-assistant-text {
    color: #F2F2F2;
}
.avi-bubble-error {
    background-color: #150808;
    color: #FF6B6B;
    border: 1px solid #FF6B6B;
    border-radius: 8px;
    padding: 8px 14px;
}
.avi-hint-label, .avi-status-label {
    color: #666666;
    font-size: 13px;
}
.avi-provider-label {
    color: #555555;
    font-family: monospace;
    font-size: 12px;
}
"""


class AviWindow:
    """Main AVI command overlay window (Spotlight / Raycast style)."""

    def __init__(
        self,
        app: "Gtk.Application",
        router: Any,
        config: Any,
        orchestrator: Any | None = None,
        max_history: int = 40,
        visible: bool = True,
    ) -> None:
        self.app = app
        self.router = router
        self.config = config
        self.orchestrator = orchestrator
        self.max_history = max_history
        self._initially_visible = visible

        self._history_widgets: list[Any] = []
        self._is_busy = False
        self._pending_confirmation: tuple[Any, Any] | None = None
        self._current_stream_box: Any | None = None
        self._current_stream_label: Any | None = None
        self._current_stream_text = ""
        self._worker_thread: threading.Thread | None = None
        self._auto_dismiss_tag: int | None = None
        self._still_thinking_tag: int | None = None

        self._prompt_history: list[str] = []
        self._history_index: int = -1
        self._current_search_rows: list[dict[str, Any]] = []
        self._selected_row_index: int = -1
        self._current_working_widget: Any | None = None

        if self.orchestrator and hasattr(self.orchestrator, "agent_orchestrator"):
            ag_events = getattr(self.orchestrator.agent_orchestrator, "events", None)
            if ag_events and hasattr(ag_events, "subscribe"):
                ag_events.subscribe(self._on_agent_progress_event)

        self._build_window()

    def _build_window(self) -> None:
        """Build the compact, frameless GTK4 overlay command palette layout."""
        self.window = Gtk.ApplicationWindow(application=self.app)
        self.window.set_title("⚡ AVI Assistant")
        self.window.set_decorated(False)
        self.window.set_resizable(True)
        self.window.set_default_size(800, 580)
        self.window.set_size_request(680, 460)
        self.window.add_css_class("avi-overlay-window")
        self.window.add_css_class("avi-palette-window")
        self.window.add_css_class("avi-main-window")

        # Root vertical container
        root_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.window.set_child(root_box)

        # ── Primary Command Bar (Antigravity CLI Style) ───────────────────
        command_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        command_bar.add_css_class("avi-command-bar")

        self.prompt_glyph = Gtk.Label(label="❯")
        self.prompt_glyph.add_css_class("avi-prompt-glyph")
        self.prompt_glyph.set_valign(Gtk.Align.CENTER)
        command_bar.append(self.prompt_glyph)

        self.prompt_entry = Gtk.Entry()
        self.prompt_entry.add_css_class("avi-command-input")
        self.prompt_entry.add_css_class("avi-prompt-entry")
        self.prompt_entry.set_placeholder_text("Ask AVI anything...")
        self.prompt_entry.set_tooltip_text("Enter your request for AVI (or press 🎙 to speak)")
        self.prompt_entry.set_hexpand(True)
        self.prompt_entry.set_activates_default(False)
        self.prompt_entry.connect("activate", self._on_prompt_submit)
        self.prompt_entry.connect("changed", self._on_prompt_changed)
        command_bar.append(self.prompt_entry)

        self.spinner = Gtk.Spinner()
        self.spinner.set_size_request(20, 20)
        self.spinner.set_valign(Gtk.Align.CENTER)
        self.spinner.set_visible(False)
        command_bar.append(self.spinner)

        self.status_label = Gtk.Label(label="Ready  ·  Esc to close")
        self.status_label.add_css_class("avi-hint-label")
        self.status_label.add_css_class("avi-status-label")
        self.status_label.set_valign(Gtk.Align.CENTER)
        self.status_label.set_visible(False)
        command_bar.append(self.status_label)

        self.esc_hint = Gtk.Label(label="esc")
        self.esc_hint.add_css_class("avi-keycap-hint")
        self.esc_hint.set_valign(Gtk.Align.CENTER)
        command_bar.append(self.esc_hint)

        self.close_button = Gtk.Button(label="×")
        self.close_button.add_css_class("avi-palette-close")
        self.close_button.add_css_class("avi-close-btn")
        self.close_button.set_tooltip_text("Close overlay (Esc)")
        self.close_button.set_valign(Gtk.Align.CENTER)
        self.close_button.connect("clicked", lambda _b: self.hide_overlay())
        command_bar.append(self.close_button)

        root_box.append(command_bar)

        # ── Compatibility hidden widgets ─────────────────────────────────
        self.voice_button = Gtk.Button(label="🎙")
        self.voice_button.add_css_class("avi-voice-btn")
        self.voice_button.set_visible(False)
        self.voice_button.connect("clicked", self._on_voice_clicked)

        self.send_button = Gtk.Button(label="⏎ Enter")
        self.send_button.add_css_class("avi-send-btn")
        self.send_button.add_css_class("avi-enter-btn")
        self.send_button.set_visible(False)
        self.send_button.connect("clicked", lambda _b: self._on_prompt_submit(self.prompt_entry))

        provider_text = (
            f"{getattr(self.config, 'provider', 'local')} / "
            f"{getattr(self.config, 'model', 'default')}"
        )
        self.provider_label = Gtk.Label(label=provider_text)
        self.provider_label.add_css_class("avi-provider-label")
        self.provider_label.set_visible(False)

        # ── Dynamic Result Container (ScrolledWindow) ───────────────────
        self.scroll_window = Gtk.ScrolledWindow()
        self.scroll_window.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.scroll_window.set_vexpand(True)
        self.scroll_window.set_hexpand(True)
        self.scroll_window.set_propagate_natural_height(True)
        self.scroll_window.set_max_content_height(600)
        self.scroll_window.set_min_content_height(400)
        self.scroll_window.set_visible(True)

        self.conversation_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.conversation_box.add_css_class("avi-execution-log")
        self.conversation_box.add_css_class("avi-conversation-area")
        self.conversation_box.add_css_class("avi-dynamic-results")
        self.scroll_window.set_child(self.conversation_box)

        root_box.append(self.scroll_window)

        # ── Keyboard Controller ─────────────────────────────────────────
        key_ctrl = Gtk.EventControllerKey()
        key_ctrl.connect("key-pressed", self._on_key_pressed)
        self.window.add_controller(key_ctrl)

        # Initial presentation and focus if initially visible
        if getattr(self, "_initially_visible", True):
            self.window.present()
            self.prompt_entry.grab_focus()

    def show_overlay(self, select_all: bool = True, clear_input: bool = False) -> None:
        """Present and focus the overlay window.

        On Wayland, compositors block focus stealing by default.  We work around
        this by supplying a synthetic startup-ID token so the compositor grants
        the raise request.
        """
        self._cancel_auto_dismiss()
        if clear_input:
            self.prompt_entry.set_text("")
        if hasattr(self, "window") and self.window:
            self.window.set_visible(True)
            # Wayland focus-bypass: set a fresh startup-notification token so
            # the compositor permits the raise.  This is a no-op on X11.
            try:
                import time as _time

                token = f"avi-overlay-{int(_time.time() * 1000)}"
                self.window.set_startup_id(token)
            except Exception:
                pass
            self.window.present()
            # X11 fallback: use xdotool to force window to front if present() was
            # insufficient (e.g. compiz / mutter focus-on-click policy).
            try:
                win_id = self.window.get_native().get_xid() if hasattr(self.window.get_native(), "get_xid") else None
                if win_id:
                    subprocess.Popen(
                        ["xdotool", "windowactivate", "--sync", str(win_id)],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
            except Exception:
                pass
        if hasattr(self, "prompt_entry") and self.prompt_entry:
            self.prompt_entry.grab_focus()
            if select_all and not clear_input:
                self.prompt_entry.select_region(0, -1)

    def hide_overlay(self) -> None:
        """Dismiss and hide the overlay window."""
        self._cancel_auto_dismiss()
        if hasattr(self, "window") and self.window:
            self.window.set_visible(False)

    def toggle_overlay(self) -> None:
        """Toggle overlay window visibility."""
        if hasattr(self, "window") and self.window and self.window.is_visible():
            self.hide_overlay()
        else:
            self.show_overlay()

    def present(self) -> None:
        """Bring window to foreground and focus the prompt entry."""
        self.show_overlay()

    def _schedule_auto_dismiss(self, delay_ms: int = 1800) -> None:
        """Schedule automatic dismissal of overlay after brief delay."""
        self._cancel_auto_dismiss()
        self._auto_dismiss_tag = GLib.timeout_add(delay_ms, self._on_auto_dismiss_timeout)

    def _cancel_auto_dismiss(self) -> None:
        """Cancel any pending auto-dismiss timer."""
        if self._auto_dismiss_tag is not None:
            GLib.source_remove(self._auto_dismiss_tag)
            self._auto_dismiss_tag = None

    def _on_auto_dismiss_timeout(self) -> bool:
        """Timer callback to dismiss overlay."""
        self._auto_dismiss_tag = None
        self.hide_overlay()
        return False

    def _on_prompt_changed(self, _entry: Any) -> None:
        """Cancel auto-dismiss when user types into entry."""
        self._cancel_auto_dismiss()

    def _on_voice_clicked(self, _button: Any) -> None:
        """Handle voice button click placeholder."""
        self._cancel_auto_dismiss()
        self._set_status("🎙 Voice input: Speak now (or press Super+Shift+A)...", spinning=False)

    # -----------------------------------------------------------------------
    # Message Widget Construction & History Management
    # -----------------------------------------------------------------------

    def _append_message_widget(self, widget: Any) -> None:
        """Append a message widget to the conversation box, enforcing max history limit."""
        self.conversation_box.append(widget)
        self._history_widgets.append(widget)
        while len(self._history_widgets) > self.max_history:
            oldest = self._history_widgets.pop(0)
            self.conversation_box.remove(oldest)
        if hasattr(self, "scroll_window") and self.scroll_window:
            self.scroll_window.set_visible(True)
        self._scroll_to_bottom()

    def _scroll_to_bottom(self) -> None:
        """Auto-scroll the scrolled window to the bottom."""
        adj = self.scroll_window.get_vadjustment()
        if adj:
            GLib.idle_add(lambda: adj.set_value(adj.get_upper() - adj.get_page_size()))

    def _show_working_line(self, status_text: str) -> None:
        """Show an inline working indicator line: ◌ {status_text}."""
        self._clear_working_line()

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        row.add_css_class("avi-cli-line")
        row.set_halign(Gtk.Align.START)
        row.set_hexpand(True)

        glyph = Gtk.Label(label="◌")
        glyph.add_css_class("avi-glyph-work")
        glyph.set_valign(Gtk.Align.START)
        row.append(glyph)

        clean_text = status_text.strip()
        label = Gtk.Label(label=clean_text)
        label.set_wrap(True)
        label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        label.set_xalign(0.0)
        label.add_css_class("avi-cli-text-work")
        row.append(label)

        self._current_working_widget = row
        self._append_message_widget(row)

    def _clear_working_line(self) -> None:
        """Remove the active working indicator line widget if present."""
        if self._current_working_widget is not None:
            try:
                self.conversation_box.remove(self._current_working_widget)
                if self._current_working_widget in self._history_widgets:
                    self._history_widgets.remove(self._current_working_widget)
            except Exception:
                pass
            self._current_working_widget = None

    def _add_user_message(self, text: str) -> Any:
        """Add a user command execution line: ❯ {text}."""
        self._clear_working_line()
        self._current_search_rows = []
        self._selected_row_index = -1

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        row.add_css_class("avi-cli-line")
        row.add_css_class("avi-bubble-user")
        row.set_halign(Gtk.Align.START)
        row.set_hexpand(True)

        glyph = Gtk.Label(label="❯")
        glyph.add_css_class("avi-glyph-cmd")
        glyph.set_valign(Gtk.Align.START)
        row.append(glyph)

        label = Gtk.Label(label=text)
        label.set_wrap(True)
        label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        label.set_selectable(True)
        label.set_xalign(0.0)
        label.add_css_class("avi-cli-text-cmd")
        label.add_css_class("avi-user-text")
        row.append(label)

        self._append_message_widget(row)
        return row

    def _add_assistant_message(
        self,
        text: str,
        action_path: str | None = None,
    ) -> Any:
        """Add an assistant result execution line: ✓ {text} with optional action button."""
        self._clear_working_line()

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        row.add_css_class("avi-cli-line")
        row.add_css_class("avi-bubble-assistant")
        row.set_halign(Gtk.Align.START)
        row.set_hexpand(True)

        glyph = Gtk.Label(label="✓")
        glyph.add_css_class("avi-glyph-ok")
        glyph.set_valign(Gtk.Align.START)
        row.append(glyph)

        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        content_box.set_hexpand(True)

        clean_text = text.strip()
        if clean_text.startswith("✓"):
            clean_text = clean_text.lstrip("✓").strip()

        label = Gtk.Label(label=clean_text)
        label.set_wrap(True)
        label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        label.set_selectable(True)
        label.set_xalign(0.0)
        label.add_css_class("avi-cli-text-ok")
        label.add_css_class("avi-assistant-text")
        content_box.append(label)

        if action_path:
            btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            btn_box.set_margin_top(2)

            btn_lbl = "Open Screenshot" if action_path.endswith(".png") else "Open File"
            open_btn = Gtk.Button(label=btn_lbl)
            open_btn.add_css_class("avi-cli-action-btn")
            open_btn.add_css_class("avi-action-btn")
            open_btn.connect("clicked", lambda _b: self._open_local_path(action_path))
            btn_box.append(open_btn)

            content_box.append(btn_box)

        row.append(content_box)
        self._append_message_widget(row)
        return row

    def _show_assistant_response(
        self,
        text: str,
        action_path: str | None = None,
        auto_dismiss: bool = False,
    ) -> None:
        """Display an assistant response CLI line."""
        self._add_assistant_message(text, action_path=action_path)
        if auto_dismiss:
            self._set_status("✓ Done  ·  Auto-closing in 2s", spinning=False)
            self._schedule_auto_dismiss(1800)
        else:
            self._set_status("Ready  ·  Esc to close", spinning=False)

    def _update_row_selection(self, new_index: int) -> None:
        """Update active keyboard selection highlight on search result rows."""
        self._selected_row_index = new_index
        for i, item in enumerate(self._current_search_rows):
            w = item.get("widget")
            if w is not None:
                if i == new_index:
                    w.add_css_class("avi-result-row-selected")
                else:
                    w.remove_css_class("avi-result-row-selected")

    def _add_search_results_widget(
        self,
        search_results: list,
        selected_result: Any | None = None,
    ) -> None:
        """Render compact, information-dense interactive rows for YouTube search results."""
        self._clear_working_line()
        if not search_results:
            return

        self._current_search_rows = []
        self._selected_row_index = -1

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        outer.add_css_class("avi-results-list")
        outer.set_margin_start(4)
        outer.set_margin_end(4)
        outer.set_margin_top(4)
        outer.set_margin_bottom(4)
        outer.set_hexpand(True)

        other_results = []
        if selected_result is not None:
            # ── Top / Best match row ────────────────────────────────────
            best_card = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            best_card.add_css_class("avi-result-row")
            best_card.add_css_class("avi-best-match-card")
            best_card.set_hexpand(True)

            badge = Gtk.Label(label="[ ▶ ]")
            badge.add_css_class("avi-row-thumb")
            badge.add_css_class("avi-best-match-tag")
            badge.set_valign(Gtk.Align.CENTER)
            best_card.append(badge)

            meta = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            meta.set_hexpand(True)
            meta.set_valign(Gtk.Align.CENTER)

            title_lbl = Gtk.Label(label=getattr(selected_result, "title", "Best match"))
            title_lbl.set_wrap(True)
            title_lbl.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            title_lbl.set_xalign(0.0)
            title_lbl.add_css_class("avi-row-title")
            title_lbl.add_css_class("avi-result-title")
            meta.append(title_lbl)

            sub_parts = []
            channel = getattr(selected_result, "channel", None)
            duration = getattr(selected_result, "duration", None)
            if channel:
                sub_parts.append(channel)
            if duration:
                sub_parts.append(duration)
            if sub_parts:
                sub_lbl = Gtk.Label(label="  ·  ".join(sub_parts))
                sub_lbl.set_xalign(0.0)
                sub_lbl.add_css_class("avi-row-meta")
                sub_lbl.add_css_class("avi-result-meta")
                meta.append(sub_lbl)

            best_card.append(meta)

            url = getattr(selected_result, "url", None)
            if url:
                play_btn = Gtk.Button(label="Play")
                play_btn.add_css_class("avi-row-open-btn")
                play_btn.add_css_class("avi-play-btn")
                play_btn.add_css_class("avi-action-btn")
                def _act(_u=url):
                    self._open_local_path(_u)

                play_btn.connect("clicked", lambda _b, _fn=_act: _fn())
                best_card.append(play_btn)
                self._current_search_rows.append({"widget": best_card, "action": _act})

            outer.append(best_card)

            sel_id = getattr(selected_result, "id", None)
            sel_url = getattr(selected_result, "url", None)
            other_results = [
                r
                for r in search_results
                if (sel_id is None or getattr(r, "id", None) != sel_id)
                and (sel_url is None or getattr(r, "url", None) != sel_url)
            ]
        else:
            other_results = search_results

        start_idx = 2 if selected_result is not None else 1
        for i, result in enumerate(other_results[:4], start_idx):
            card = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            card.add_css_class("avi-result-row")
            card.add_css_class("avi-result-card")
            card.set_hexpand(True)

            num_str = f"[{i:02d}]"
            num_lbl = Gtk.Label(label=num_str)
            num_lbl.add_css_class("avi-row-thumb")
            num_lbl.add_css_class("avi-result-num")
            num_lbl.set_valign(Gtk.Align.CENTER)
            card.append(num_lbl)

            meta = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
            meta.set_hexpand(True)
            meta.set_valign(Gtk.Align.CENTER)

            title_lbl = Gtk.Label(label=getattr(result, "title", f"Result {i}"))
            title_lbl.set_wrap(True)
            title_lbl.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            title_lbl.set_xalign(0.0)
            title_lbl.add_css_class("avi-row-title")
            title_lbl.add_css_class("avi-result-title")
            meta.append(title_lbl)

            sub_parts = []
            channel = getattr(result, "channel", None)
            duration = getattr(result, "duration", None)
            if channel:
                sub_parts.append(channel)
            if duration:
                sub_parts.append(duration)
            if sub_parts:
                sub_lbl = Gtk.Label(label="  ·  ".join(sub_parts))
                sub_lbl.set_xalign(0.0)
                sub_lbl.add_css_class("avi-row-meta")
                sub_lbl.add_css_class("avi-result-meta")
                meta.append(sub_lbl)

            card.append(meta)

            url = getattr(result, "url", None)
            if url:
                open_btn = Gtk.Button(label="Open")
                open_btn.add_css_class("avi-row-open-btn")
                open_btn.add_css_class("avi-action-btn")
                def _act(_u=url):
                    self._open_local_path(_u)

                open_btn.connect("clicked", lambda _b, _fn=_act: _fn())
                card.append(open_btn)
                self._current_search_rows.append({"widget": card, "action": _act})

            outer.append(card)

        self._append_message_widget(outer)
        self._set_status("Ready  ·  Esc to close", spinning=False, is_llm=False)

    def _show_error(self, message: str) -> None:
        """Display an error line in CLI style: ! {message}."""
        self._clear_working_line()

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        row.add_css_class("avi-cli-line")
        row.add_css_class("avi-bubble-error")
        row.set_halign(Gtk.Align.START)
        row.set_hexpand(True)

        glyph = Gtk.Label(label="!")
        glyph.add_css_class("avi-glyph-err")
        glyph.set_valign(Gtk.Align.START)
        row.append(glyph)

        clean_msg = message.lstrip("⚠").lstrip("!").strip()
        lbl = Gtk.Label(label=clean_msg)
        lbl.set_wrap(True)
        lbl.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        lbl.set_selectable(True)
        lbl.set_xalign(0.0)
        lbl.add_css_class("avi-cli-text-err")
        row.append(lbl)

        self._append_message_widget(row)
        self._set_status("Ready  ·  Esc to close", spinning=False)

    def _show_confirmation(self, proposal: Any, explanation: str) -> None:
        """Display an inline confirmation card."""
        self._clear_working_line()

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        row.set_halign(Gtk.Align.START)
        row.set_hexpand(True)

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        card.add_css_class("avi-confirm-card")
        card.set_margin_top(6)
        card.set_margin_bottom(6)
        card.set_hexpand(True)

        hdr = Gtk.Label(label="⚠️ Confirmation Required")
        hdr.set_xalign(0.0)
        hdr.add_css_class("avi-confirm-header")
        card.append(hdr)

        desc = explanation or "Do you want AVI to execute this operation?"
        desc_lbl = Gtk.Label(label=desc)
        desc_lbl.set_wrap(True)
        desc_lbl.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        desc_lbl.set_selectable(True)
        desc_lbl.set_xalign(0.0)
        card.append(desc_lbl)

        cmd_display = getattr(proposal, "command_line", None)
        if cmd_display:
            cmd_lbl = Gtk.Label(label=str(cmd_display))
            cmd_lbl.set_wrap(True)
            cmd_lbl.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            cmd_lbl.set_selectable(True)
            cmd_lbl.set_xalign(0.0)
            cmd_lbl.add_css_class("avi-command-box")
            card.append(cmd_lbl)

        btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        btn_row.set_margin_top(4)

        cancel_btn = Gtk.Button(label="Cancel [n]")
        cancel_btn.add_css_class("destructive-action")
        cancel_btn.connect("clicked", lambda _b: self._handle_confirm_cancel())

        proceed_btn = Gtk.Button(label="Proceed [y]")
        proceed_btn.add_css_class("suggested-action")
        proceed_btn.connect("clicked", lambda _b: self._handle_confirm_proceed())

        btn_row.append(cancel_btn)
        btn_row.append(proceed_btn)
        card.append(btn_row)

        row.append(card)
        self._append_message_widget(row)
        self._pending_confirmation = (proposal, card)
        self._set_status("Confirmation required · [y] Proceed  [n] Cancel", spinning=False)

    # -----------------------------------------------------------------------
    # Streaming Support
    # -----------------------------------------------------------------------

    def _start_stream_response(self) -> None:
        """Create a new streaming assistant CLI line."""
        self._clear_working_line()

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        row.add_css_class("avi-cli-line")
        row.add_css_class("avi-bubble-assistant")
        row.set_halign(Gtk.Align.START)
        row.set_hexpand(True)

        glyph = Gtk.Label(label="✓")
        glyph.add_css_class("avi-glyph-ok")
        glyph.set_valign(Gtk.Align.START)
        row.append(glyph)

        label = Gtk.Label(label="")
        label.set_wrap(True)
        label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        label.set_selectable(True)
        label.set_xalign(0.0)
        label.add_css_class("avi-cli-text-ok")
        label.add_css_class("avi-assistant-text")
        row.append(label)

        self._append_message_widget(row)

        self._current_stream_box = row
        self._current_stream_label = label
        self._current_stream_text = ""

    def _append_stream_chunk(self, chunk: str) -> None:
        """Append a streamed token/chunk to the current assistant line."""
        if self._current_stream_label is not None:
            self._current_stream_text += chunk
            self._current_stream_label.set_text(self._current_stream_text)
            self._scroll_to_bottom()

    def _finish_stream(self, final_text: str | None = None, action_path: str | None = None) -> None:
        """Finalize the current stream response."""
        if final_text is not None and self._current_stream_label is not None:
            self._current_stream_text = final_text
            self._current_stream_label.set_text(final_text)

        self._current_stream_box = None
        self._current_stream_label = None
        self._current_stream_text = ""
        self._set_status("Ready  ·  Esc to close", spinning=False)

    # -----------------------------------------------------------------------
    # User Interactions & Event Handlers
    # -----------------------------------------------------------------------

    def _get_loading_status(self, prompt: str) -> tuple[str, bool]:
        """Compute the appropriate status bar text and whether LLM is active.

        Returns (status_text, is_llm). For deterministic desktop commands,
        fast action status is returned with is_llm=False.
        """
        last_turn = (
            self.orchestrator.history.last_turn
            if self.orchestrator and hasattr(self.orchestrator, "history")
            else None
        )
        intent = detect_assistant_intent(prompt, last_turn=last_turn)

        if intent.intent_type == AssistantIntentType.OPEN_APP:
            app_name = intent.target
            if app_name.lower() in ("chrome", "google chrome"):
                return ("Opening Google Chrome…", False)
            return (f"Opening {app_name.title()}…", False)

        if intent.intent_type == AssistantIntentType.SCREENSHOT:
            return ("Capturing screenshot…", False)

        if intent.intent_type == AssistantIntentType.VOLUME_SET:
            action = intent.extra.get("action")
            if action == "raise":
                return ("Increasing volume…", False)
            if action == "lower":
                return ("Decreasing volume…", False)
            if action == "mute":
                return ("Muting audio…", False)
            if action == "unmute":
                return ("Unmuting audio…", False)
            val = intent.extra.get("value")
            if val == 100 or intent.extra.get("target") == "max":
                return ("Setting volume to max…", False)
            if val == 0:
                return ("Muting audio…", False)
            if val is not None:
                return (f"Setting volume to {val}%…", False)
            return ("Setting volume…", False)

        if intent.intent_type == AssistantIntentType.VOLUME_GET:
            return ("Checking volume…", False)

        if intent.intent_type == AssistantIntentType.OPEN_DIR:
            folder = intent.extra.get("folder") or intent.target
            name = Path(folder).name if folder else "folder"
            if name.lower() == "downloads":
                return ("Opening Downloads…", False)
            if name.lower() == "documents":
                return ("Opening Documents…", False)
            if name.lower() == "desktop":
                return ("Opening Desktop…", False)
            return (f"Opening {name}…", False)

        if intent.intent_type == AssistantIntentType.OPEN_FILE:
            name = Path(intent.target).name if intent.target else "file"
            return (f"Opening {name}…", False)

        if intent.intent_type == AssistantIntentType.OPEN_URL:
            dest_name = intent.extra.get("destination_name")
            if dest_name:
                return (f"Opening {dest_name}…", False)
            return ("Opening browser…", False)

        if intent.intent_type in (
            AssistantIntentType.YOUTUBE_SEARCH,
            AssistantIntentType.YOUTUBE_RECOMMEND,
        ):
            return ("Searching YouTube…", False)

        if intent.intent_type == AssistantIntentType.OPEN_SEARCH_RESULT:
            return ("Opening result…", False)

        if intent.intent_type == AssistantIntentType.TIMER:
            return ("Setting timer…", False)

        if intent.intent_type in (
            AssistantIntentType.DISK_SPACE,
            AssistantIntentType.RAM_USAGE,
            AssistantIntentType.CPU_USAGE,
            AssistantIntentType.SYSTEM_INFO,
        ):
            return ("Checking system…", False)

        if intent.intent_type == AssistantIntentType.MEDIA_CONTROL:
            return ("Controlling media…", False)

        if intent.intent_type in (
            AssistantIntentType.GREETING,
            AssistantIntentType.CAPABILITIES,
            AssistantIntentType.COURTESY,
            AssistantIntentType.SMALL_TALK,
            AssistantIntentType.CLARIFICATION,
            AssistantIntentType.CONFIRMATION,
            AssistantIntentType.CANCELLATION,
        ):
            return ("Working…", False)

        if self.orchestrator and hasattr(self.orchestrator, "is_assistant_request"):
            try:
                if self.orchestrator.is_assistant_request(prompt):
                    return ("Working…", False)
            except Exception:
                pass

        try:
            from avi.apps.destinations import DestinationResolver

            d_res = DestinationResolver().resolve(prompt)
            if d_res.is_resolved:
                return (f"Opening {d_res.target}…", False)
        except Exception:
            pass

        return ("Thinking…", True)

    def _on_agent_progress_event(self, event: Any) -> None:
        """Handle real-time progress events from the agent orchestrator."""
        if getattr(self, "_still_thinking_tag", None):
            try:
                GLib.source_remove(self._still_thinking_tag)
            except Exception:
                pass
            self._still_thinking_tag = None

        def _update():
            if not self._is_busy:
                return False
            msg = getattr(event, "message", "")
            if msg:
                self._show_working_line(msg)
                self._set_status(msg, spinning=True, is_llm=False)
            return False

        GLib.idle_add(_update)

    def _on_prompt_submit(self, entry: "Gtk.Entry") -> None:
        """Handle prompt submission from Enter key or Send button."""
        raw_text = entry.get_text().strip()
        if not raw_text:
            return

        if self._is_busy:
            self._set_status("AVI is currently busy working on a request...", spinning=True)
            return

        cleaned_text = clean_natural_language_input(raw_text)
        if not cleaned_text:
            return

        # Record prompt into history
        if not self._prompt_history or self._prompt_history[-1] != cleaned_text:
            self._prompt_history.append(cleaned_text)
        self._history_index = len(self._prompt_history)

        # Double-submission guard: synchronously mark busy and disable Send button
        self._is_busy = True
        self.send_button.set_sensitive(False)

        entry.set_text("")
        self._add_user_message(cleaned_text)

        if getattr(self, "_still_thinking_tag", None):
            try:
                GLib.source_remove(self._still_thinking_tag)
            except Exception:
                pass
            self._still_thinking_tag = None

        status_text, is_llm = self._get_loading_status(cleaned_text)
        self._set_status(status_text, spinning=True, is_llm=is_llm)
        self._show_working_line(status_text)

        if is_llm:

            def _check_still_thinking() -> bool:
                self._still_thinking_tag = None
                if self._is_busy and self._current_working_widget is not None:
                    self._show_working_line("Still thinking…")
                    self._set_status("Still thinking…", spinning=True, is_llm=True)
                return False

            self._still_thinking_tag = GLib.timeout_add(4000, _check_still_thinking)

        self._worker_thread = threading.Thread(
            target=self._run_query,
            args=(cleaned_text,),
            daemon=True,
        )
        self._worker_thread.start()

    def _on_key_pressed(
        self,
        controller: "Gtk.EventControllerKey",
        keyval: int,
        keycode: int,
        state: "Gdk.ModifierType",
    ) -> bool:
        """Handle keyboard shortcuts: Escape, Ctrl+L, Ctrl+Q, Up/Down, Enter."""
        self._cancel_auto_dismiss()
        is_ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)

        # Ctrl+Q -> Quit application
        if is_ctrl and keyval in (Gdk.KEY_q, Gdk.KEY_Q):
            if self.app:
                self.app.quit()
            return True

        # Ctrl+L -> Focus prompt entry
        if is_ctrl and keyval in (Gdk.KEY_l, Gdk.KEY_L):
            self.prompt_entry.grab_focus()
            self.prompt_entry.select_region(0, -1)
            return True

        # Pending confirmation keyboard shortcuts
        if self._pending_confirmation:
            if keyval in (Gdk.KEY_y, Gdk.KEY_Y):
                self._handle_confirm_proceed()
                return True
            if keyval in (Gdk.KEY_n, Gdk.KEY_N):
                self._handle_confirm_cancel()
                return True

        # Escape -> Cancel pending / deselect / clear / hide
        if keyval == Gdk.KEY_Escape:
            if self._pending_confirmation:
                self._handle_confirm_cancel()
                return True
            if self._selected_row_index >= 0:
                self._update_row_selection(-1)
                self.prompt_entry.grab_focus()
                return True
            if self.prompt_entry.get_text():
                self.prompt_entry.set_text("")
                return True
            self.hide_overlay()
            if hasattr(self, "window") and self.window:
                self.window.close()
            return True

        # Enter on selected row when prompt is empty
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            if not self.prompt_entry.get_text().strip() and self._selected_row_index >= 0:
                if 0 <= self._selected_row_index < len(self._current_search_rows):
                    action = self._current_search_rows[self._selected_row_index].get("action")
                    if callable(action):
                        action()
                        return True

        # Up / Down Navigation
        if keyval in (Gdk.KEY_Down, Gdk.KEY_KP_Down):
            # If search result rows exist, navigate rows
            if self._current_search_rows:
                new_idx = min(self._selected_row_index + 1, len(self._current_search_rows) - 1)
                self._update_row_selection(new_idx)
                return True
            # Otherwise navigate prompt history forward
            if self._prompt_history:
                if self._history_index < len(self._prompt_history) - 1:
                    self._history_index += 1
                    self.prompt_entry.set_text(self._prompt_history[self._history_index])
                    self.prompt_entry.set_position(-1)
                elif self._history_index == len(self._prompt_history) - 1:
                    self._history_index = len(self._prompt_history)
                    self.prompt_entry.set_text("")
                return True

        if keyval in (Gdk.KEY_Up, Gdk.KEY_KP_Up):
            # If search result rows exist and a row is selected
            if self._current_search_rows and self._selected_row_index > 0:
                self._update_row_selection(self._selected_row_index - 1)
                return True
            elif self._current_search_rows and self._selected_row_index == 0:
                self._update_row_selection(-1)
                self.prompt_entry.grab_focus()
                return True
            # Otherwise navigate prompt history backward
            if self._prompt_history:
                if self._history_index > 0:
                    self._history_index -= 1
                    self.prompt_entry.set_text(self._prompt_history[self._history_index])
                    self.prompt_entry.set_position(-1)
                elif self._history_index == -1 and len(self._prompt_history) > 0:
                    self._history_index = len(self._prompt_history) - 1
                    self.prompt_entry.set_text(self._prompt_history[self._history_index])
                    self.prompt_entry.set_position(-1)
                elif self._history_index == len(self._prompt_history) and len(self._prompt_history) > 0:
                    self._history_index = len(self._prompt_history) - 1
                    self.prompt_entry.set_text(self._prompt_history[self._history_index])
                    self.prompt_entry.set_position(-1)
                return True

    def _handle_confirm_cancel(self) -> None:
        """User rejected execution of a confirmed-level command."""
        if self._pending_confirmation is None:
            return
        _, card = self._pending_confirmation
        self._pending_confirmation = None
        card.set_sensitive(False)
        self._add_assistant_message("Action cancelled.")
        self._set_status("Ready  ·  Esc to close", spinning=False)

    def _handle_confirm_proceed(self) -> None:
        """User approved execution of a confirmed-level command."""
        if self._pending_confirmation is None:
            return
        proposal, card = self._pending_confirmation
        self._pending_confirmation = None
        card.set_sensitive(False)
        self._set_status("Working…", spinning=True)

        t = threading.Thread(
            target=self._run_confirmed_command,
            args=(proposal,),
            daemon=True,
        )
        t.start()

    # -----------------------------------------------------------------------
    # Background Workers
    # -----------------------------------------------------------------------

    def _run_query(self, prompt: str) -> None:
        """Background thread: Route prompt through Orchestrator / Router."""
        self._is_busy = True
        try:
            # 1. Assistant Orchestrator (intents, actions, volume, screenshot, timers)
            if self.orchestrator is not None:
                last_turn = (
                    self.orchestrator.history.last_turn
                    if hasattr(self.orchestrator, "history")
                    else None
                )
                intent = detect_assistant_intent(prompt, last_turn=last_turn)

                # Special non-blocking handling for timers
                if intent.intent_type == AssistantIntentType.TIMER:
                    res = self.orchestrator.handle(prompt, auto_execute_actions=False)
                    GLib.idle_add(self._show_assistant_response, res.text)
                    dur = intent.extra.get("duration_seconds", 0.0)
                    label = intent.extra.get("label", "")
                    if dur > 0:

                        def _timer_worker() -> None:
                            import time

                            time.sleep(dur)
                            finish_text = f"⏰ Time's up: {label}." if label else "⏰ Time's up."
                            GLib.idle_add(self._show_assistant_response, finish_text)

                        threading.Thread(target=_timer_worker, daemon=True).start()
                    return

                res = self.orchestrator.handle(prompt, auto_execute_actions=True)
                if res.is_blocked:
                    GLib.idle_add(
                        self._show_error,
                        res.text or "This operation was blocked by safety policy.",
                    )
                    return

                if res.requires_confirmation:
                    proposal = res.command_request or res.plan
                    GLib.idle_add(self._show_confirmation, proposal, res.text)
                    return

                # Check if a screenshot path exists in capability_result or text
                action_path = None
                if res.capability_result and hasattr(res.capability_result, "data"):
                    data = res.capability_result.data
                    if isinstance(data, dict) and "path" in data:
                        action_path = data["path"]

                if not action_path and res.text:
                    m = re.search(
                        r"(/home/[^\s]+\.png|~/[^\s]+\.png|/[^\s]+\.png)",
                        res.text,
                    )
                    if m:
                        action_path = os.path.expanduser(m.group(1))

                if (
                    res.action is not None
                    or res.tool_result is not None
                    or res.capability_result is not None
                    or res.execution_result is not None
                    or res.text
                ):
                    is_transient = intent.intent_type in (
                        AssistantIntentType.OPEN_APP,
                        AssistantIntentType.VOLUME_SET,
                        AssistantIntentType.VOLUME_GET,
                        AssistantIntentType.OPEN_DIR,
                        AssistantIntentType.OPEN_URL,
                        AssistantIntentType.MEDIA_CONTROL,
                        AssistantIntentType.OPEN_SEARCH_RESULT,
                        AssistantIntentType.SCREENSHOT,
                        AssistantIntentType.CONFIRMATION,
                    )
                    auto_dismiss = is_transient and not res.search_results
                    GLib.idle_add(
                        self._show_assistant_response, res.text, action_path, auto_dismiss
                    )
                    # Show interactive result cards if search results are available
                    if (
                        res.search_results
                        and intent.intent_type != AssistantIntentType.OPEN_SEARCH_RESULT
                    ):
                        GLib.idle_add(
                            self._add_search_results_widget,
                            res.search_results,
                            res.selected_result,
                        )
                    return

            # 2. Router fast-path
            fast_result = self.router.check_fast_path(prompt)
            if isinstance(fast_result, str):
                GLib.idle_add(self._show_assistant_response, fast_result.rstrip("\n"), None, True)
                return

            # 3. Stream from provider
            GLib.idle_add(self._start_stream_response)
            chunks = []
            for chunk in self.router.route(prompt, stream=True):
                chunks.append(chunk)
                GLib.idle_add(self._append_stream_chunk, chunk)

            full_text = "".join(chunks)

            # 4. Check if response is a command proposal requiring confirmation
            proposal = self.router.parse_command_proposal(full_text)
            if proposal is not None:
                GLib.idle_add(self._show_confirmation, proposal, full_text)
            else:
                GLib.idle_add(self._finish_stream, None)

        except Exception as err:
            friendly_err = format_user_friendly_error(err, prompt)
            GLib.idle_add(self._show_error, friendly_err)
        finally:
            GLib.idle_add(self._restore_idle_state)

    def _run_confirmed_command(self, proposal: Any) -> None:
        """Background thread: Execute a confirmed command or plan."""
        self._is_busy = True
        try:
            # Handle multi-step agent plan
            if hasattr(proposal, "steps"):
                if self.orchestrator and hasattr(self.orchestrator, "executor"):
                    res = self.orchestrator.executor.execute_plan(proposal)
                    summary = (
                        res.summary() if hasattr(res, "summary") else "Executed plan successfully."
                    )
                    GLib.idle_add(self._show_assistant_response, summary)
                else:
                    GLib.idle_add(self._show_assistant_response, "Plan executed.")
                return

            # Standard single CommandRequest
            assessment = self.router.evaluate_command(proposal)
            if assessment.is_blocked:
                GLib.idle_add(self._show_error, f"Blocked: {assessment.reason}")
                return

            result = self.router.execute_command(proposal)
            display = result.format_display()
            exit_label = f"\n[exit: {result.exit_code}]" if result.exit_code != 0 else ""
            GLib.idle_add(
                self._show_assistant_response,
                (display or "Command executed successfully.") + exit_label,
            )
        except Exception as err:
            friendly_err = format_user_friendly_error(err)
            GLib.idle_add(self._show_error, friendly_err)
        finally:
            GLib.idle_add(self._restore_idle_state)

    # -----------------------------------------------------------------------
    # GTK Main-Thread UI State Management
    # -----------------------------------------------------------------------

    def _set_status(self, text: str, spinning: bool = False, is_llm: bool = False) -> None:
        """Update status bar text, spinner, and provider visibility."""
        self.status_label.set_text(text)
        self.provider_label.set_visible(is_llm)
        if spinning:
            self.spinner.set_visible(True)
            self.spinner.start()
        else:
            self.spinner.stop()
            self.spinner.set_visible(False)

    def _restore_idle_state(self) -> None:
        """Reset UI to idle state (clearing spinner and restoring status)."""
        if getattr(self, "_still_thinking_tag", None):
            try:
                GLib.source_remove(self._still_thinking_tag)
            except Exception:
                pass
            self._still_thinking_tag = None
        self._is_busy = False
        self.send_button.set_sensitive(True)
        self.spinner.stop()
        self.spinner.set_visible(False)
        self.prompt_entry.grab_focus()
        if self._pending_confirmation is None:
            self._set_status("Ready  ·  Esc to close", spinning=False, is_llm=False)

    def _open_local_path(self, path: str) -> None:
        """Open a local file or https:// URL in the default desktop application without blocking."""
        try:
            if path.startswith("https://") or path.startswith("http://"):
                # Open URL via xdg-open (no shell=True, no eval)
                subprocess.Popen(
                    ["xdg-open", path],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            else:
                clean_path = os.path.expanduser(path)
                if os.path.exists(clean_path):
                    subprocess.Popen(
                        ["xdg-open", clean_path],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
        except Exception as err:
            logger.warning("Could not open path/url %s: %s", path, err)

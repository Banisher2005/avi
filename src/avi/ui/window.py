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
from typing import Any

from avi.assistant.intents import AssistantIntentType, detect_assistant_intent

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
    return "I ran into a problem while processing that request. Please try again."


CSS_STYLE = b"""
window.avi-main-window {
    background-color: #1e1e2e;
    color: #cdd6f4;
}

headerbar {
    background-color: #181825;
    color: #cdd6f4;
    border-bottom: 1px solid #313244;
    min-height: 38px;
    padding: 0 6px;
}

.avi-header-title {
    font-weight: bold;
    font-size: 13px;
    color: #89b4fa;
}

.avi-header-box {
    background-color: #181825;
    border-bottom: 1px solid #313244;
    padding: 10px 14px;
}

.avi-prompt-entry {
    background-color: #1e1e2e;
    color: #cdd6f4;
    border: 1px solid #45475a;
    border-radius: 8px;
    padding: 8px 12px;
    font-size: 14px;
}

.avi-prompt-entry:focus {
    border-color: #89b4fa;
}

.avi-send-btn {
    background-color: #89b4fa;
    color: #11111b;
    font-weight: bold;
    border-radius: 8px;
    padding: 6px 14px;
    border: none;
}

.avi-send-btn:hover {
    background-color: #b4befe;
}

.avi-conversation-area {
    background-color: #1e1e2e;
    padding: 12px;
}

.avi-bubble-user {
    background-color: #313244;
    color: #cdd6f4;
    border-radius: 12px 12px 2px 12px;
    padding: 8px 14px;
}

.avi-user-text {
    font-size: 14px;
    color: #cdd6f4;
}

.avi-bubble-assistant {
    background-color: #252636;
    color: #cdd6f4;
    border-radius: 12px 12px 12px 2px;
    border: 1px solid #313244;
    padding: 10px 14px;
}

.avi-assistant-text {
    font-size: 14px;
    color: #cdd6f4;
}

.avi-bubble-error {
    background-color: #2a1f28;
    color: #f38ba8;
    border: 1px solid #f38ba8;
    border-radius: 8px;
    padding: 8px 12px;
}

.avi-confirm-card {
    background-color: #262330;
    border: 1px solid #fab387;
    border-radius: 10px;
    padding: 12px 14px;
}

.avi-confirm-header {
    font-size: 13px;
    font-weight: bold;
    color: #fab387;
}

.avi-command-box {
    background-color: #11111b;
    color: #a6e3a1;
    font-family: monospace;
    font-size: 12px;
    border-radius: 6px;
    padding: 6px 10px;
}

.avi-path-text {
    font-family: monospace;
    font-size: 12px;
    color: #89b4fa;
}

.avi-action-btn {
    background-color: #313244;
    color: #cdd6f4;
    border-radius: 6px;
    padding: 4px 10px;
    font-size: 12px;
}

.avi-action-btn:hover {
    background-color: #45475a;
}

.avi-status-bar {
    background-color: #181825;
    border-top: 1px solid #313244;
    padding: 6px 14px;
    color: #6c7086;
    font-size: 11px;
}

.avi-hint-label {
    color: #6c7086;
    font-size: 11px;
}

.avi-provider-label {
    color: #45475a;
    font-family: monospace;
    font-size: 10px;
}

.avi-result-card {
    background-color: #1e1e2e;
    border: 1px solid #313244;
    border-radius: 8px;
    padding: 6px 10px;
}

.avi-result-card:hover {
    background-color: #252636;
}

.avi-result-num {
    color: #6c7086;
    font-size: 11px;
    font-family: monospace;
    min-width: 16px;
}

.avi-result-title {
    font-size: 13px;
    color: #cdd6f4;
    font-weight: bold;
}

.avi-result-meta {
    font-size: 11px;
    color: #6c7086;
}
"""


class AviWindow:
    """Main AVI assistant popup window."""

    def __init__(
        self,
        app: "Gtk.Application",
        router: Any,
        config: Any,
        orchestrator: Any | None = None,
        max_history: int = 40,
    ) -> None:
        self.app = app
        self.router = router
        self.config = config
        self.orchestrator = orchestrator
        self.max_history = max_history

        self._history_widgets: list[Any] = []
        self._is_busy = False
        self._pending_confirmation: tuple[Any, Any] | None = None
        self._current_stream_box: Any | None = None
        self._current_stream_label: Any | None = None
        self._current_stream_text = ""
        self._worker_thread: threading.Thread | None = None

        self._build_window()

    def _build_window(self) -> None:
        """Build the GTK4 window layout."""
        self.window = Gtk.ApplicationWindow(application=self.app)
        self.window.set_title("⚡ AVI Assistant")
        self.window.set_default_size(680, 520)
        self.window.set_resizable(True)
        self.window.add_css_class("avi-main-window")

        # Native HeaderBar with title
        header_bar = Gtk.HeaderBar()
        title_label = Gtk.Label(label="⚡ AVI Assistant")
        title_label.add_css_class("avi-header-title")
        header_bar.set_title_widget(title_label)
        self.window.set_titlebar(header_bar)

        # Root vertical box
        root_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.window.set_child(root_box)

        # ── Input Header Box ──────────────────────────────────────────────
        header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        header_box.add_css_class("avi-header-box")

        self.prompt_entry = Gtk.Entry()
        self.prompt_entry.add_css_class("avi-prompt-entry")
        self.prompt_entry.set_placeholder_text("Ask anything or tell AVI what to do...")
        self.prompt_entry.set_tooltip_text("Enter your request for AVI")
        self.prompt_entry.set_hexpand(True)
        self.prompt_entry.set_activates_default(False)
        self.prompt_entry.connect("activate", self._on_prompt_submit)
        header_box.append(self.prompt_entry)

        self.send_button = Gtk.Button(label="Send")
        self.send_button.add_css_class("avi-send-btn")
        self.send_button.set_tooltip_text("Send request to AVI")
        self.send_button.connect("clicked", lambda _b: self._on_prompt_submit(self.prompt_entry))
        header_box.append(self.send_button)

        self.spinner = Gtk.Spinner()
        self.spinner.set_size_request(20, 20)
        self.spinner.set_visible(False)
        header_box.append(self.spinner)

        root_box.append(header_box)

        # ── Conversation Area (ScrolledWindow) ─────────────────────────────
        self.scroll_window = Gtk.ScrolledWindow()
        self.scroll_window.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.scroll_window.set_vexpand(True)
        self.scroll_window.set_min_content_height(240)

        self.conversation_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.conversation_box.add_css_class("avi-conversation-area")
        self.conversation_box.set_margin_start(14)
        self.conversation_box.set_margin_end(14)
        self.conversation_box.set_margin_top(12)
        self.conversation_box.set_margin_bottom(12)
        self.scroll_window.set_child(self.conversation_box)

        root_box.append(self.scroll_window)

        # ── Status Bar ────────────────────────────────────────────────────
        status_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        status_box.add_css_class("avi-status-bar")

        self.status_label = Gtk.Label(label="Ready  ·  Esc to close")
        self.status_label.add_css_class("avi-hint-label")
        self.status_label.set_xalign(0.0)
        self.status_label.set_hexpand(True)
        status_box.append(self.status_label)

        provider_text = (
            f"{getattr(self.config, 'provider', 'local')} / "
            f"{getattr(self.config, 'model', 'default')}"
        )
        self.provider_label = Gtk.Label(label=provider_text)
        self.provider_label.add_css_class("avi-provider-label")
        self.provider_label.set_margin_end(4)
        status_box.append(self.provider_label)

        root_box.append(status_box)

        # ── Keyboard controller ───────────────────────────────────────────
        key_ctrl = Gtk.EventControllerKey()
        key_ctrl.connect("key-pressed", self._on_key_pressed)
        self.window.add_controller(key_ctrl)

        # Initial presentation and focus
        self.window.present()
        self.prompt_entry.grab_focus()

    def present(self) -> None:
        """Bring window to foreground and focus the prompt entry."""
        if hasattr(self, "window") and self.window:
            self.window.present()
        if hasattr(self, "prompt_entry") and self.prompt_entry:
            self.prompt_entry.grab_focus()

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
        self._scroll_to_bottom()

    def _scroll_to_bottom(self) -> None:
        """Auto-scroll the scrolled window to the bottom."""
        adj = self.scroll_window.get_vadjustment()
        if adj:
            GLib.idle_add(lambda: adj.set_value(adj.get_upper() - adj.get_page_size()))

    def _add_user_message(self, text: str) -> Any:
        """Add a user message bubble (right-aligned)."""
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        row.set_halign(Gtk.Align.END)

        bubble = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        bubble.add_css_class("avi-bubble-user")
        bubble.set_margin_top(4)
        bubble.set_margin_bottom(4)

        label = Gtk.Label(label=text)
        label.set_wrap(True)
        label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        label.set_selectable(True)
        label.set_xalign(1.0)
        label.add_css_class("avi-user-text")
        bubble.append(label)

        row.append(bubble)
        self._append_message_widget(row)
        return row

    def _add_assistant_message(
        self,
        text: str,
        action_path: str | None = None,
    ) -> Any:
        """Add an assistant bubble with conversational response and optional action buttons."""
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        row.set_halign(Gtk.Align.START)
        row.set_hexpand(True)

        bubble = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        bubble.add_css_class("avi-bubble-assistant")
        bubble.set_margin_top(4)
        bubble.set_margin_bottom(4)
        bubble.set_hexpand(True)

        label = Gtk.Label(label=text)
        label.set_wrap(True)
        label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        label.set_selectable(True)
        label.set_xalign(0.0)
        label.add_css_class("avi-assistant-text")
        bubble.append(label)

        # If an action path was provided, add path and Open action button
        if action_path:
            btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            btn_box.set_margin_top(4)

            open_btn = Gtk.Button(
                label="Open Screenshot" if action_path.endswith(".png") else "Open File"
            )
            open_btn.add_css_class("avi-action-btn")
            open_btn.connect("clicked", lambda _b: self._open_local_path(action_path))
            btn_box.append(open_btn)

            bubble.append(btn_box)

        row.append(bubble)
        self._append_message_widget(row)
        return row

    def _show_assistant_response(self, text: str, action_path: str | None = None) -> None:
        """Display an assistant response message card."""
        self._add_assistant_message(text, action_path=action_path)
        self._set_status("Ready  ·  Esc to close", spinning=False)

    def _add_search_results_widget(self, search_results: list) -> None:
        """Render compact interactive cards for YouTube search results."""
        if not search_results:
            return

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        outer.set_margin_start(8)
        outer.set_margin_end(8)
        outer.set_margin_bottom(6)
        outer.set_hexpand(True)

        # Show at most 5 results in the card list
        for i, result in enumerate(search_results[:5], 1):
            card = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            card.add_css_class("avi-result-card")
            card.set_margin_bottom(2)
            card.set_hexpand(True)

            # Left: number badge
            num_lbl = Gtk.Label(label=str(i))
            num_lbl.add_css_class("avi-result-num")
            num_lbl.set_valign(Gtk.Align.CENTER)
            card.append(num_lbl)

            # Center: metadata
            meta = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
            meta.set_hexpand(True)
            meta.set_valign(Gtk.Align.CENTER)

            title_lbl = Gtk.Label(label=getattr(result, "title", f"Result {i}"))
            title_lbl.set_wrap(True)
            title_lbl.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            title_lbl.set_xalign(0.0)
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
                sub_lbl.add_css_class("avi-result-meta")
                meta.append(sub_lbl)

            card.append(meta)

            # Right: Open button
            url = getattr(result, "url", None)
            if url:
                open_btn = Gtk.Button(label="Open")
                open_btn.add_css_class("avi-action-btn")
                open_btn.set_valign(Gtk.Align.CENTER)
                # Capture url in closure
                open_btn.connect(
                    "clicked",
                    lambda _b, _url=url: self._open_local_path(_url),
                )
                card.append(open_btn)

            outer.append(card)

        self._append_message_widget(outer)
        self._set_status("Ready  ·  Esc to close", spinning=False)

    def _show_error(self, message: str) -> None:
        """Display an error card in the conversation."""
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        row.set_halign(Gtk.Align.START)
        row.set_hexpand(True)

        bubble = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        bubble.add_css_class("avi-bubble-error")
        bubble.set_margin_top(4)
        bubble.set_margin_bottom(4)
        bubble.set_hexpand(True)

        lbl = Gtk.Label(label=f"⚠ {message}")
        lbl.set_wrap(True)
        lbl.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        lbl.set_selectable(True)
        lbl.set_xalign(0.0)
        bubble.append(lbl)

        row.append(bubble)
        self._append_message_widget(row)
        self._set_status("Ready  ·  Esc to close", spinning=False)

    def _show_confirmation(self, proposal: Any, explanation: str) -> None:
        """Display an inline confirmation card."""
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
        """Create a new streaming assistant bubble."""
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        row.set_halign(Gtk.Align.START)
        row.set_hexpand(True)

        bubble = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        bubble.add_css_class("avi-bubble-assistant")
        bubble.set_margin_top(4)
        bubble.set_margin_bottom(4)
        bubble.set_hexpand(True)

        label = Gtk.Label(label="")
        label.set_wrap(True)
        label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        label.set_selectable(True)
        label.set_xalign(0.0)
        label.add_css_class("avi-assistant-text")
        bubble.append(label)

        row.append(bubble)
        self._append_message_widget(row)

        self._current_stream_box = row
        self._current_stream_label = label
        self._current_stream_text = ""

    def _append_stream_chunk(self, chunk: str) -> None:
        """Append a streamed token/chunk to the current assistant bubble."""
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

    def _on_prompt_submit(self, entry: "Gtk.Entry") -> None:
        """Handle prompt submission from Enter key or Send button."""
        prompt = entry.get_text().strip()
        if not prompt:
            return

        if self._is_busy:
            self._set_status("AVI is currently busy working on a request...", spinning=True)
            return

        entry.set_text("")
        self._add_user_message(prompt)
        self._set_status("Thinking…", spinning=True)

        self._worker_thread = threading.Thread(
            target=self._run_query,
            args=(prompt,),
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
        """Handle keyboard shortcuts: Escape, Ctrl+L, Ctrl+Q, y/n confirmation."""
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

        # Escape -> Cancel pending state or close window
        if keyval == Gdk.KEY_Escape:
            if self._pending_confirmation:
                self._handle_confirm_cancel()
                return True
            if self.prompt_entry.get_text():
                self.prompt_entry.set_text("")
                return True
            self.window.close()
            return True

        # Pending confirmation keyboard shortcuts
        if self._pending_confirmation:
            if keyval in (Gdk.KEY_y, Gdk.KEY_Y):
                self._handle_confirm_proceed()
                return True
            if keyval in (Gdk.KEY_n, Gdk.KEY_N):
                self._handle_confirm_cancel()
                return True

        return False

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
                    GLib.idle_add(self._show_assistant_response, res.text, action_path)
                    # Show interactive result cards if search results are available
                    if res.search_results:
                        GLib.idle_add(self._add_search_results_widget, res.search_results)
                    return

            # 2. Router fast-path
            fast_result = self.router.check_fast_path(prompt)
            if isinstance(fast_result, str):
                GLib.idle_add(self._show_assistant_response, fast_result.rstrip("\n"))
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

    def _set_status(self, text: str, spinning: bool = False) -> None:
        """Update status bar text and spinner."""
        self.status_label.set_text(text)
        if spinning:
            self.spinner.set_visible(True)
            self.spinner.start()
        else:
            self.spinner.stop()
            self.spinner.set_visible(False)

    def _restore_idle_state(self) -> None:
        """Reset UI to idle state (clearing spinner and restoring status)."""
        self._is_busy = False
        self.spinner.stop()
        self.spinner.set_visible(False)
        self.prompt_entry.grab_focus()
        if self._pending_confirmation is None:
            self._set_status("Ready  ·  Esc to close", spinning=False)

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

"""AVI Desktop UI — GTK4 keyboard-first popup window.

A minimal, floating GTK4 popup that streams AVI responses inline.
Works natively on both Wayland (via XDG foreign toplevel / layer-shell) and X11.

Architecture:
  AviWindow (GTK4 ApplicationWindow)
  ├── PromptEntry    — Text input, Escape exits, Enter submits
  ├── ResponseView   — Scrollable streamed response area (Pango monospace)
  └── StatusBar      — Mode indicator + keyboard hints

Key design decisions:
  - No extra Python package dependencies: only system-level PyGObject/GTK4
  - Completely headless-safe: exits cleanly with code 1 if no display is found
  - Thread-safe streaming: uses GLib.idle_add to bridge subprocess threads to GTK main loop
  - All AVI routing happens in a background thread via Router; UI never blocks
"""

import sys
import threading
from typing import Any

try:
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Gdk", "4.0")
    from gi.repository import Gdk, GLib, Gtk, Pango
    _GTK_AVAILABLE = True
except (ImportError, ValueError):
    _GTK_AVAILABLE = False


def check_display() -> bool:
    """Return True if a display server (Wayland or X11) is available."""
    import os
    return bool(os.getenv("WAYLAND_DISPLAY") or os.getenv("DISPLAY"))


CSS_STYLE = b"""
window {
    background-color: #1e1e2e;
    border-radius: 12px;
    border: 1px solid #45475a;
}

#avi-prompt-entry {
    background-color: #181825;
    color: #cdd6f4;
    border: none;
    border-radius: 8px 8px 0 0;
    border-bottom: 1px solid #313244;
    padding: 14px 18px;
    font-size: 15px;
    font-family: monospace;
    caret-color: #89b4fa;
}

#avi-prompt-entry:focus {
    outline: none;
}

#avi-response-view {
    background-color: #1e1e2e;
    color: #cdd6f4;
    padding: 12px 18px;
    font-size: 13px;
    font-family: monospace;
}

#avi-status-bar {
    background-color: #181825;
    border-radius: 0 0 12px 12px;
    border-top: 1px solid #313244;
    padding: 6px 18px;
    color: #6c7086;
    font-size: 11px;
}

#avi-hint-label {
    color: #45475a;
    font-size: 11px;
}

#avi-spinner {
    color: #89b4fa;
}

.avi-command-block {
    background-color: #181825;
    color: #a6e3a1;
    border-radius: 4px;
    padding: 6px 12px;
    font-family: monospace;
}

.avi-blocked {
    color: #f38ba8;
}

.avi-confirm {
    color: #fab387;
}
"""


class AviWindow:
    """Main AVI popup window."""

    def __init__(self, app: "Gtk.Application", router: Any, config: Any, orchestrator: Any | None = None) -> None:
        self.app = app
        self.router = router
        self.config = config
        self.orchestrator = orchestrator

        self._response_buffer = ""
        self._current_mode = "idle"  # idle | thinking | streaming | confirm | executed
        self._pending_proposal = None
        self._worker_thread: threading.Thread | None = None

        self._build_window()

    def _build_window(self) -> None:
        """Build the GTK4 window layout."""
        self.window = Gtk.ApplicationWindow(application=self.app)
        self.window.set_title("AVI")
        self.window.set_default_size(680, 360)
        self.window.set_resizable(True)
        self.window.set_decorated(False)  # borderless / frameless popup
        self.window.add_css_class("avi-main-window")

        # Root vertical box
        root_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.window.set_child(root_box)

        # ── Prompt Entry ──────────────────────────────────────────────────
        header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        header_box.set_margin_start(4)
        header_box.set_margin_end(4)
        header_box.set_margin_top(4)

        # AVI label / logo
        avi_label = Gtk.Label(label="⚡ AVI")
        avi_label.set_markup("<span foreground='#89b4fa' font_desc='monospace bold 13'>⚡ AVI</span>")
        avi_label.set_margin_start(14)
        avi_label.set_margin_top(10)
        avi_label.set_margin_bottom(10)
        header_box.append(avi_label)

        self.prompt_entry = Gtk.Entry()
        self.prompt_entry.set_name("avi-prompt-entry")
        self.prompt_entry.set_placeholder_text("Ask anything or type a command...")
        self.prompt_entry.set_hexpand(True)
        self.prompt_entry.set_activates_default(False)
        self.prompt_entry.connect("activate", self._on_prompt_submit)
        header_box.append(self.prompt_entry)

        self.spinner = Gtk.Spinner()
        self.spinner.set_name("avi-spinner")
        self.spinner.set_margin_end(12)
        self.spinner.set_margin_top(8)
        self.spinner.set_margin_bottom(8)
        self.spinner.set_size_request(20, 20)
        header_box.append(self.spinner)

        root_box.append(header_box)

        # ── Response Text Area ─────────────────────────────────────────────
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        scroll.set_min_content_height(80)
        scroll.set_max_content_height(260)

        self.response_view = Gtk.TextView()
        self.response_view.set_name("avi-response-view")
        self.response_view.set_editable(False)
        self.response_view.set_cursor_visible(False)
        self.response_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.response_view.set_left_margin(18)
        self.response_view.set_right_margin(18)
        self.response_view.set_top_margin(10)
        self.response_view.set_bottom_margin(10)

        fd = Pango.FontDescription.from_string("monospace 12")
        self.response_view.override_font(fd)

        self.response_buffer = self.response_view.get_buffer()
        scroll.set_child(self.response_view)
        root_box.append(scroll)

        # ── Confirm Box (hidden by default) ───────────────────────────────
        self.confirm_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.confirm_box.set_margin_start(18)
        self.confirm_box.set_margin_end(18)
        self.confirm_box.set_margin_top(6)
        self.confirm_box.set_margin_bottom(6)
        self.confirm_box.set_visible(False)

        self.confirm_label = Gtk.Label(label="Execute this command?")
        self.confirm_label.set_xalign(0.0)
        self.confirm_label.set_hexpand(True)
        self.confirm_box.append(self.confirm_label)

        yes_button = Gtk.Button(label="✓ Yes [y]")
        yes_button.add_css_class("suggested-action")
        yes_button.connect("clicked", self._on_confirm_yes)
        self.confirm_box.append(yes_button)

        no_button = Gtk.Button(label="✗ No [n]")
        no_button.add_css_class("destructive-action")
        no_button.connect("clicked", self._on_confirm_no)
        self.confirm_box.append(no_button)

        root_box.append(self.confirm_box)

        # ── Status Bar ────────────────────────────────────────────────────
        status_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        status_box.set_name("avi-status-bar")

        self.status_label = Gtk.Label(label="Ready  ·  Esc to close")
        self.status_label.set_name("avi-hint-label")
        self.status_label.set_xalign(0.0)
        self.status_label.set_margin_start(4)
        self.status_label.set_hexpand(True)
        status_box.append(self.status_label)

        provider_label = Gtk.Label()
        provider_label.set_markup(
            f"<span foreground='#45475a' font_desc='monospace 10'>{self.config.provider} / {self.config.model}</span>"
        )
        provider_label.set_margin_end(4)
        status_box.append(provider_label)

        root_box.append(status_box)

        # ── Keyboard controller ───────────────────────────────────────────
        key_ctrl = Gtk.EventControllerKey()
        key_ctrl.connect("key-pressed", self._on_key_pressed)
        self.window.add_controller(key_ctrl)

        # Center on screen
        self.window.set_halign(Gtk.Align.CENTER)
        self.window.set_valign(Gtk.Align.CENTER)

        self.window.present()
        self.prompt_entry.grab_focus()

    # -----------------------------------------------------------------------
    # User interactions
    # -----------------------------------------------------------------------

    def _on_prompt_submit(self, entry: "Gtk.Entry") -> None:
        """Handle Enter key in the prompt entry."""
        prompt = entry.get_text().strip()
        if not prompt:
            return

        entry.set_text("")
        entry.set_editable(False)
        self._set_status("Thinking...", spinning=True)
        self._clear_response()
        self.confirm_box.set_visible(False)
        self._pending_proposal = None

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
        """Global key handler: Escape closes, y/n for confirm prompts."""
        if keyval == Gdk.KEY_Escape:
            self.window.close()
            return True

        if self.confirm_box.get_visible():
            if keyval in (Gdk.KEY_y, Gdk.KEY_Y):
                self._on_confirm_yes(None)
                return True
            if keyval in (Gdk.KEY_n, Gdk.KEY_N, Gdk.KEY_Return):
                self._on_confirm_no(None)
                return True

        return False

    def _on_confirm_yes(self, _button: Any) -> None:
        """User confirmed execution of a CONFIRM-level command."""
        if self._pending_proposal is None:
            return
        proposal = self._pending_proposal
        self._pending_proposal = None
        self.confirm_box.set_visible(False)
        self._append_response("\n⟶ Executing...\n")
        self._set_status("Executing...", spinning=True)

        t = threading.Thread(
            target=self._run_confirmed_command,
            args=(proposal,),
            daemon=True,
        )
        t.start()

    def _on_confirm_no(self, _button: Any) -> None:
        """User rejected execution of a CONFIRM-level command."""
        self._pending_proposal = None
        self.confirm_box.set_visible(False)
        self._append_response("\n✗ Cancelled.\n")
        self._set_status("Ready  ·  Esc to close", spinning=False)
        self._enable_entry()

    # -----------------------------------------------------------------------
    # Background worker
    # -----------------------------------------------------------------------

    def _run_query(self, prompt: str) -> None:
        """Background: route prompt through AssistantOrchestrator / Router / Provider."""
        try:
            # 1. Try Assistant Orchestrator first (intents, actions, read-only tool synthesis)
            if self.orchestrator is not None:
                res = self.orchestrator.handle(prompt, auto_execute_actions=True)
                if res.is_blocked:
                    GLib.idle_add(self._show_error, res.text)
                    return
                if res.requires_confirmation and res.command_request is not None:
                    GLib.idle_add(self._show_confirm_prompt, res.command_request, res.text)
                    return
                if res.action is not None or res.tool_result is not None:
                    GLib.idle_add(self._finish_stream, res.text)
                    return
                if res.execution_result is not None or (res.text and not res.command_request):
                    GLib.idle_add(self._finish_stream, res.text)
                    return

            # 2. Try fast-path
            fast_result = self.router.check_fast_path(prompt)
            if isinstance(fast_result, str):
                GLib.idle_add(self._finish_stream, fast_result.rstrip("\n"))
                return

            # 3. Stream from provider
            chunks = []
            for chunk in self.router.route(prompt, stream=True):
                chunks.append(chunk)
                GLib.idle_add(self._append_response, chunk)

            full_text = "".join(chunks)

            # 4. Check if response is a command proposal
            proposal = self.router.parse_command_proposal(full_text)
            if proposal is not None:
                GLib.idle_add(self._show_confirm_prompt, proposal, full_text)
            else:
                GLib.idle_add(self._finish_stream, None)

        except Exception as err:
            GLib.idle_add(self._show_error, str(err))

    def _run_confirmed_command(self, proposal: Any) -> None:
        """Background: execute a confirmed CONFIRM-level CommandRequest."""
        try:
            from avi.execution.models import CommandRequest
            from avi.safety.models import RiskLevel

            assessment = self.router.evaluate_command(proposal)
            if assessment.is_blocked:
                GLib.idle_add(self._show_error, f"Blocked: {assessment.reason}")
                return

            result = self.router.execute_command(proposal)
            display = result.format_display()
            exit_label = f"\n[exit: {result.exit_code}]"
            GLib.idle_add(self._finish_stream, (display or "(no output)") + exit_label)
        except Exception as err:
            GLib.idle_add(self._show_error, str(err))

    # -----------------------------------------------------------------------
    # GTK main-thread UI updates (called via GLib.idle_add)
    # -----------------------------------------------------------------------

    def _clear_response(self) -> None:
        self.response_buffer.set_text("")
        self._response_buffer = ""

    def _append_response(self, text: str) -> None:
        """Append text to the response buffer (must run on GTK main thread)."""
        self._response_buffer += text
        end_iter = self.response_buffer.get_end_iter()
        self.response_buffer.insert(end_iter, text)
        # Auto-scroll to end
        adj = self.response_view.get_vadjustment()
        if adj:
            adj.set_value(adj.get_upper() - adj.get_page_size())

    def _finish_stream(self, text: str | None) -> None:
        if text is not None:
            self._append_response(text)
        self._set_status("Done  ·  Esc to close", spinning=False)
        self._enable_entry()

    def _show_confirm_prompt(self, proposal: Any, full_text: str) -> None:
        """Show the inline confirm bar for CONFIRM-level proposals."""
        self._pending_proposal = proposal
        cmd_display = getattr(proposal, "command_line", str(proposal))
        self.confirm_label.set_markup(
            f"<span foreground='#fab387'>Execute:</span>  "
            f"<span foreground='#a6e3a1' font_desc='monospace bold 12'>{GLib.markup_escape_text(cmd_display)}</span>"
        )
        self.confirm_box.set_visible(True)
        self._set_status("Confirm execution  [y] Yes  [n] No", spinning=False)

    def _show_error(self, message: str) -> None:
        self._append_response(f"\n⚠ Error: {message}")
        self._set_status("Error  ·  Esc to close", spinning=False)
        self._enable_entry()

    def _set_status(self, text: str, spinning: bool = False) -> None:
        self.status_label.set_text(text)
        if spinning:
            self.spinner.start()
        else:
            self.spinner.stop()

    def _enable_entry(self) -> None:
        self.prompt_entry.set_editable(True)
        self.prompt_entry.grab_focus()

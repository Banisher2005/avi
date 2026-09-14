"""PowerToys-inspired Command Palette GTK4 view with real application icons and keyboard navigation."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

from avi.commands.models import (
    ActionResult,
    CommandCategory,
    PaletteResult,
    PaletteState,
)

logger = logging.getLogger("avi.ui.palette")

try:
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Gdk", "4.0")
    from gi.repository import Gdk, Gtk, Pango

    _GTK_AVAILABLE = True
except (ImportError, ValueError):
    _GTK_AVAILABLE = False
    Gdk = Any  # type: ignore[assignment,misc]
    Gtk = Any  # type: ignore[assignment,misc]
    Pango = Any  # type: ignore[assignment,misc]

# Clean fallback icon theme names for categories (NO EMOJIS)
CATEGORY_ICONS = {
    CommandCategory.APPLICATION: "application-x-executable",
    CommandCategory.COMMAND: "system-run",
    CommandCategory.CAPABILITY: "preferences-system",
    CommandCategory.FILE: "system-file-manager",
    CommandCategory.TASK: "system-run",
    CommandCategory.SYSTEM: "utilities-system-monitor",
    CommandCategory.ACTION: "system-run",
}


class CommandPaletteWidget:
    """GTK4 container managing command palette results, selection state, and row rendering."""

    def __init__(
        self,
        on_execute: Callable[[PaletteResult, ActionResult | None], None] | None = None,
    ) -> None:
        self.on_execute = on_execute
        self.state: PaletteState = PaletteState.CLOSED
        self.results: list[PaletteResult] = []
        self.selected_index: int = -1
        self.selected_action_index: int = 0
        self._row_widgets: list[Any] = []

        if _GTK_AVAILABLE:
            self.container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            self.container.add_css_class("avi-palette-container")
        else:
            self.container = None

    def set_state(self, state: PaletteState) -> None:
        """Update authoritative palette lifecycle state."""
        self.state = state

    def clear(self) -> None:
        """Clear all active palette results and reset selection."""
        self.results.clear()
        self.selected_index = -1
        self.selected_action_index = 0
        if self.container:
            for w in self._row_widgets:
                try:
                    self.container.remove(w)
                except Exception:
                    pass
        self._row_widgets.clear()
        self.set_state(PaletteState.OPEN)

    def set_results(self, results: list[PaletteResult]) -> None:
        """Render new palette results with real application icons and selection highlight."""
        self.clear()
        self.results = list(results)
        if not self.results:
            self.set_state(PaletteState.SHOWING_RESULTS)
            return

        self.selected_index = 0
        self.selected_action_index = 0
        self.set_state(PaletteState.SHOWING_RESULTS)

        if not _GTK_AVAILABLE or not self.container:
            return

        for idx, res in enumerate(self.results):
            row = self._create_row_widget(res, idx == self.selected_index)
            self.container.append(row)
            self._row_widgets.append(row)

    def _create_icon_widget(self, icon_identifier: str, category: CommandCategory) -> Any:
        """Create a Gtk.Image with the real application/system icon. Never uses emojis."""
        fallback = CATEGORY_ICONS.get(category, "application-x-executable")
        clean_id = (icon_identifier or "").strip()

        # Check if icon identifier is an absolute file path (e.g. /usr/share/pixmaps/app.png)
        if clean_id and (clean_id.startswith("/") or clean_id.endswith((".png", ".svg", ".xpm"))):
            p = Path(clean_id)
            if p.is_file():
                try:
                    img = Gtk.Image.new_from_file(str(p))
                    img.set_pixel_size(32)
                    img.set_valign(Gtk.Align.CENTER)
                    return img
                except Exception:
                    pass

        # Use XDG icon theme name
        icon_name = clean_id if clean_id else fallback
        try:
            img = Gtk.Image.new_from_icon_name(icon_name)
            img.set_pixel_size(32)
            img.set_valign(Gtk.Align.CENTER)
            return img
        except Exception:
            img = Gtk.Image.new_from_icon_name("application-x-executable")
            img.set_pixel_size(32)
            img.set_valign(Gtk.Align.CENTER)
            return img

    def _create_row_widget(self, result: PaletteResult, is_selected: bool) -> Any:
        """Construct a compact, clean row for a single palette item."""
        row_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        row_box.add_css_class("avi-palette-item")
        if is_selected:
            row_box.add_css_class("avi-palette-item-selected")

        # 1. Real application/command icon
        icon_widget = self._create_icon_widget(result.icon, result.category)
        row_box.append(icon_widget)

        # 2. Text Column (Title & Subtitle)
        text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        text_box.set_hexpand(True)
        text_box.set_valign(Gtk.Align.CENTER)

        title_lbl = Gtk.Label(label=result.title)
        title_lbl.set_xalign(0.0)
        title_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        title_lbl.add_css_class("avi-palette-title")
        text_box.append(title_lbl)

        sub_lbl = Gtk.Label(label=result.subtitle)
        sub_lbl.set_xalign(0.0)
        sub_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        sub_lbl.add_css_class("avi-palette-subtitle")
        text_box.append(sub_lbl)

        row_box.append(text_box)

        # 3. Category & Action Hint Column
        meta_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        meta_box.set_valign(Gtk.Align.CENTER)

        cat_lbl = Gtk.Label(label=result.category.value)
        cat_lbl.add_css_class("avi-palette-category")
        meta_box.append(cat_lbl)

        action_name = result.primary_action.name if result.actions else "Run"
        hint_lbl = Gtk.Label(label=f"↵ {action_name}")
        hint_lbl.add_css_class("avi-palette-action-hint")
        meta_box.append(hint_lbl)

        row_box.append(meta_box)

        # Mouse click activation
        gesture = Gtk.GestureClick()
        gesture.connect("released", lambda *args: self._on_row_clicked(result))
        row_box.add_controller(gesture)

        return row_box

    def _on_row_clicked(self, result: PaletteResult) -> None:
        """Handle mouse click on palette row."""
        action = result.actions[self.selected_action_index] if result.actions else None
        if self.on_execute:
            self.on_execute(result, action)

    def move_selection(self, delta: int) -> bool:
        """Move keyboard selection up or down."""
        if not self.results:
            return False

        old_idx = self.selected_index
        new_idx = max(0, min(len(self.results) - 1, self.selected_index + delta))

        if new_idx == old_idx:
            return False

        self.selected_index = new_idx
        self.selected_action_index = 0

        # Update CSS classes
        if self._row_widgets:
            if 0 <= old_idx < len(self._row_widgets):
                self._row_widgets[old_idx].remove_css_class("avi-palette-item-selected")
            if 0 <= new_idx < len(self._row_widgets):
                self._row_widgets[new_idx].add_css_class("avi-palette-item-selected")

        return True

    def cycle_action(self, delta: int = 1) -> bool:
        """Cycle through secondary actions for Tab key navigation."""
        if not self.results or self.selected_index < 0:
            return False

        cur_result = self.results[self.selected_index]
        if not cur_result.actions or len(cur_result.actions) <= 1:
            return False

        total_actions = len(cur_result.actions)
        self.selected_action_index = (self.selected_action_index + delta) % total_actions
        # Refresh current row action hint
        if 0 <= self.selected_index < len(self._row_widgets):
            cur_row = self._row_widgets[self.selected_index]
            # Update hint label (last child of meta_box)
            try:
                meta_box = cur_row.get_last_child()
                hint_lbl = meta_box.get_last_child()
                act = cur_result.actions[self.selected_action_index]
                hint_lbl.set_label(f"↵ {act.name}")
            except Exception:
                pass
        return True

    def get_selected(self) -> tuple[PaletteResult, ActionResult | None] | None:
        """Return currently selected result and chosen action."""
        if not self.results or self.selected_index < 0:
            return None
        res = self.results[self.selected_index]
        act = res.actions[self.selected_action_index] if res.actions else res.primary_action
        return res, act

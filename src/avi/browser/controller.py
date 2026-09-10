"""Unified Browser Controller for AVI.

Combines Chrome DevTools Protocol (CDP) direct control with OS-level window
and process fallbacks when remote debugging is unavailable.
"""

from __future__ import annotations

import json
import logging
import subprocess
import time
from typing import Any

from avi.browser.cdp import CdpClient
from avi.browser.downloads import DownloadsWatcher
from avi.browser.models import BrowserState, InteractiveElement, TabInfo

logger = logging.getLogger(__name__)

KNOWN_BROWSERS = ("google-chrome", "chrome", "firefox", "brave", "chromium")

DOM_OBSERVE_JS = """(() => {
  const result = {
    title: document.title || '',
    url: window.location.href || '',
    readyState: document.readyState || '',
    loading: document.readyState !== 'complete',
    viewport: {
      scrollY: Math.round(window.scrollY || 0),
      scrollX: Math.round(window.scrollX || 0),
      innerWidth: window.innerWidth || 0,
      innerHeight: window.innerHeight || 0,
      scrollHeight: document.body ? document.body.scrollHeight : 0,
      scrollWidth: document.body ? document.body.scrollWidth : 0
    },
    visibleText: '',
    elements: []
  };

  try {
    const raw = document.body ? (document.body.innerText || '') : '';
    result.visibleText = raw.slice(0, 3000);
  } catch (e) {}

  try {
    const query = 'a[href], button, input:not([type=hidden]), textarea, select, [role=button], [role=link], [role=searchbox], [role=textbox], [onclick], [tabindex]';
    const nodes = Array.from(document.querySelectorAll(query));
    let id = 1;
    for (const el of nodes) {
      if (id > 50) break;
      const rect = el.getBoundingClientRect();
      const style = window.getComputedStyle(el);
      if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') continue;
      if (rect.width <= 0 && rect.height <= 0) continue;

      el.setAttribute('data-avi-id', String(id));
      const tag = el.tagName.toLowerCase();
      const type = el.type ? String(el.type).toLowerCase() : null;
      const role = el.getAttribute('role') || (tag === 'a' ? 'link' : (tag === 'button' ? 'button' : (tag === 'input' ? (type === 'search' ? 'searchbox' : 'textbox') : null)));
      const text = (el.innerText || el.getAttribute('aria-label') || el.title || el.placeholder || el.value || '').trim().slice(0, 80);
      const placeholder = el.placeholder || null;
      const name = el.name || null;
      let selector = `[data-avi-id="${id}"]`;
      if (el.id) {
        selector = `#${el.id}`;
      } else if (name) {
        selector = `${tag}[name="${name}"]`;
      }

      result.elements.push({
        element_id: id,
        tag: tag,
        element_type: type,
        role: role,
        text: text,
        placeholder: placeholder,
        name: name,
        selector: selector,
        href: el.href || null,
        value: el.value || null,
        is_visible: true,
        is_disabled: !!el.disabled
      });
      id++;
    }
  } catch (e) {}

  return result;
})()"""


class BrowserController:
    """Manages browser discovery, state observation, and hybrid interaction."""

    def __init__(
        self,
        cdp_port: int = 9222,
        cdp_host: str = "127.0.0.1",
        timeout: float = 1.0,
        downloads_watcher: DownloadsWatcher | None = None,
    ) -> None:
        self.cdp = CdpClient(host=cdp_host, port=cdp_port, timeout=timeout)
        self.downloads_watcher = downloads_watcher or DownloadsWatcher()
        self._last_navigated_url: str | None = None
        self._last_page_title: str | None = None
        self._last_observation: BrowserState | None = None

    def get_last_observation(self) -> BrowserState | None:
        """Return the most recently cached observation."""
        return self._last_observation

    def detect_running_browsers(self) -> list[str]:
        """Detect any installed browser processes currently running."""
        running = []
        try:
            res = subprocess.run(
                ["pgrep", "-a", "-l", "-f", "(google-chrome|chrome|firefox|brave|chromium)"],
                capture_output=True,
                text=True,
                timeout=1.0,
                check=False,
            )
            out = res.stdout.lower()
            for b in KNOWN_BROWSERS:
                if b in out and b not in running:
                    running.append(b)
        except Exception as err:
            logger.debug("Failed checking running browsers: %s", err)

        return running

    def observe(self, window_list: list[dict[str, Any]] | None = None) -> BrowserState:
        """Observe current browser state across CDP and system desktop state."""
        state = BrowserState()
        running = self.detect_running_browsers()
        state.is_running = len(running) > 0
        if running:
            state.browser_name = running[0]

        # 1. Try CDP if available
        if self.cdp.is_available():
            state.cdp_connected = True
            raw_tabs = self.cdp.list_tabs()
            state.tab_count = len(raw_tabs) if isinstance(raw_tabs, (list, tuple)) else 0
            active = self.cdp.get_active_tab()
            active_id = active.get("id") if isinstance(active, dict) else None
            state.active_tab_id = active_id

            if isinstance(raw_tabs, (list, tuple)):
                state.tabs = [
                    TabInfo(
                        tab_id=str(t.get("id", "")),
                        title=str(t.get("title", "")),
                        url=str(t.get("url", "")),
                        is_active=(t.get("id") == active_id),
                    )
                    for t in raw_tabs
                    if isinstance(t, dict)
                ]

            if isinstance(active, dict):
                state.url = active.get("url")
                state.title = active.get("title")
                state.status = "ready"
                if state.url:
                    self._last_navigated_url = state.url
                if state.title:
                    self._last_page_title = state.title

                # Evaluate DOM extraction
                try:
                    eval_data = self.cdp.evaluate(DOM_OBSERVE_JS)
                    if isinstance(eval_data, dict):
                        if eval_data.get("title"):
                            state.title = eval_data["title"]
                            self._last_page_title = state.title
                        if eval_data.get("url"):
                            state.url = eval_data["url"]
                            self._last_navigated_url = state.url
                        state.loading = bool(eval_data.get("loading", False))
                        state.visible_text = str(eval_data.get("visibleText", ""))
                        state.viewport = dict(eval_data.get("viewport", {}))

                        raw_elems = eval_data.get("elements", [])
                        state.elements = [
                            InteractiveElement(
                                element_id=int(e.get("element_id", idx + 1)),
                                tag=str(e.get("tag", "div")),
                                element_type=e.get("element_type"),
                                role=e.get("role"),
                                text=str(e.get("text", "")),
                                placeholder=e.get("placeholder"),
                                name=e.get("name"),
                                selector=str(e.get("selector", "")),
                                href=e.get("href"),
                                value=e.get("value"),
                                is_visible=bool(e.get("is_visible", True)),
                                is_disabled=bool(e.get("is_disabled", False)),
                            )
                            for idx, e in enumerate(raw_elems)
                            if isinstance(e, dict)
                        ]
                except Exception as eval_err:
                    logger.debug("DOM observation script evaluation error: %s", eval_err)

            # Downloads
            try:
                state.downloads = [d.to_dict() for d in self.downloads_watcher.get_recent_downloads(limit=5)]
            except Exception as dl_err:
                logger.debug("Failed getting recent downloads: %s", dl_err)

            self._last_observation = state
            return state

        # 2. Desktop Window Inspection fallback
        windows = window_list
        if windows is None:
            try:
                from avi.capabilities.desktop.window import WindowListCapability

                win_cap = WindowListCapability()
                res = win_cap.execute()
                if res.success and isinstance(res.data, dict):
                    windows = res.data.get("windows", [])
            except Exception as err:
                logger.debug("Window inspection error: %s", err)
                windows = []

        active_browser_win = None
        if windows:
            for w in windows:
                w_title = (w.get("title") or "").lower()
                w_class = (w.get("class") or "").lower()
                for b in KNOWN_BROWSERS:
                    if b in w_title or b in w_class:
                        active_browser_win = w
                        break
                if active_browser_win:
                    break

        if active_browser_win:
            state.is_active_window = True
            state.window_title = active_browser_win.get("title")
            state.title = state.window_title
            state.status = "ready"
            if not state.browser_name:
                state.browser_name = active_browser_win.get("class") or "browser"
        elif state.is_running:
            state.status = "ready"
            state.title = self._last_page_title
        else:
            state.status = "unavailable"

        if self._last_navigated_url and not state.url:
            state.url = self._last_navigated_url

        try:
            state.downloads = [d.to_dict() for d in self.downloads_watcher.get_recent_downloads(limit=5)]
        except Exception:
            pass

        self._last_observation = state
        return state

    def record_navigation(self, url: str, title: str | None = None) -> None:
        """Record a newly navigated URL into internal tracker."""
        self._last_navigated_url = url
        if title:
            self._last_page_title = title

    def resolve_element(
        self,
        element_id: int | str | None = None,
        selector: str | None = None,
        text: str | None = None,
    ) -> tuple[InteractiveElement | None, str | None]:
        """Resolve an element from ID, selector, or text with 1-shot stale recovery."""
        if element_id is not None:
            # 1. Try from cached observation
            if self._last_observation:
                el = self._last_observation.get_element(element_id)
                if el:
                    sel = f"[data-avi-id='{el.element_id}']" if not el.selector or el.selector.startswith("[data-avi-id") else el.selector
                    return el, sel

            # 2. 1-shot stale recovery: re-observe and retry
            fresh_state = self.observe()
            el = fresh_state.get_element(element_id)
            if el:
                sel = f"[data-avi-id='{el.element_id}']" if not el.selector or el.selector.startswith("[data-avi-id") else el.selector
                return el, sel

        if text:
            obs = self._last_observation or self.observe()
            matches = obs.find_elements(text=text)
            if matches:
                el = matches[0]
                sel = f"[data-avi-id='{el.element_id}']" if not el.selector or el.selector.startswith("[data-avi-id") else el.selector
                return el, sel

        if selector:
            if self._last_observation:
                for el in self._last_observation.elements:
                    if el.selector == selector:
                        return el, selector
            return None, selector

        return None, None

    def click(
        self,
        element_id: int | str | None = None,
        selector: str | None = None,
        text: str | None = None,
        wait_seconds: float = 0.5,
    ) -> dict[str, Any]:
        """Execute a click on the specified element via CDP or fallback."""
        el, resolved_sel = self.resolve_element(element_id=element_id, selector=selector, text=text)

        target_sel = resolved_sel or selector
        target_text = text or (el.text if el else None)
        target_id = el.element_id if el else (int(element_id) if element_id is not None and str(element_id).isdigit() else None)

        if not target_sel and not target_text and target_id is None:
            return {"success": False, "error": "No target specified to click"}

        if self.cdp.is_available():
            js_click = (
                "(() => {"
                f"  const sel = {json.dumps(target_sel)};"
                f"  const aviId = {json.dumps(target_id)};"
                f"  const text = {json.dumps(target_text)};"
                "  let el = null;"
                "  if (aviId) el = document.querySelector(`[data-avi-id='${aviId}']`);"
                "  if (!el && sel) el = document.querySelector(sel);"
                "  if (!el && text) {"
                "    const candidates = Array.from(document.querySelectorAll('button, a, input[type=button], input[type=submit], [role=button], [role=link], [onclick]'));"
                "    const t = text.toLowerCase();"
                "    el = candidates.find(c => (c.innerText || c.value || '').trim().toLowerCase() === t)"
                "       || candidates.find(c => (c.innerText || c.value || '').toLowerCase().includes(t));"
                "  }"
                "  if (!el) return {success: false, error: 'Element not found in DOM'};"
                "  if (typeof el.scrollIntoView === 'function') el.scrollIntoView({block: 'center', inline: 'center'});"
                "  el.click();"
                "  return {"
                "    success: true,"
                "    tag: el.tagName,"
                "    text: (el.innerText || el.value || '').trim().slice(0, 60),"
                "    href: el.href || null,"
                "    element_id: aviId"
                "  };"
                "})()"
            )
            res = self.cdp.evaluate(js_click)
            if isinstance(res, dict) and res.get("success"):
                if res.get("href"):
                    self.record_navigation(res["href"])
                if wait_seconds > 0:
                    time.sleep(wait_seconds)
                post_obs = self.observe()
                return {
                    "success": True,
                    "clicked_element": res,
                    "observation": post_obs.to_dict(),
                    "method": "cdp",
                }
            elif isinstance(res, dict) and not res.get("success"):
                return {"success": False, "error": res.get("error", "Element not found in DOM")}

        # Desktop fallback
        if target_text and target_text.startswith(("http://", "https://")):
            from avi.capabilities.browser.navigate import BrowserNavigateCapability

            nav_res = BrowserNavigateCapability(controller=self).execute(url=target_text)
            return {"success": nav_res.success, "method": "navigation_fallback", "data": nav_res.data}

        state = self.observe()
        if state.is_running and state.browser_name:
            from avi.capabilities.desktop.window import WindowFocusCapability

            WindowFocusCapability().execute(app=state.browser_name)

        if wait_seconds > 0:
            time.sleep(wait_seconds)
        post_obs = self.observe()
        return {
            "success": True,
            "method": "fallback_focus",
            "observation": post_obs.to_dict(),
            "clicked_element": {"selector": target_sel, "text": target_text, "element_id": target_id},
        }

    def type_text(
        self,
        element_id: int | str | None = None,
        selector: str | None = None,
        text: str = "",
        clear_existing: bool = False,
        press_enter: bool = False,
        wait_seconds: float = 0.5,
    ) -> dict[str, Any]:
        """Type text into element or active field via CDP or fallback."""
        el, resolved_sel = self.resolve_element(element_id=element_id, selector=selector)
        target_sel = resolved_sel or selector
        target_id = el.element_id if el else (int(element_id) if element_id is not None and str(element_id).isdigit() else None)

        if self.cdp.is_available():
            js_type = (
                "(() => {"
                f"  const aviId = {json.dumps(target_id)};"
                f"  const sel = {json.dumps(target_sel)};"
                f"  const text = {json.dumps(text)};"
                f"  const clear = {json.dumps(clear_existing)};"
                f"  const pressEnter = {json.dumps(press_enter)};"
                "  let el = null;"
                "  if (aviId) el = document.querySelector(`[data-avi-id='${aviId}']`);"
                "  if (!el && sel) el = document.querySelector(sel);"
                "  if (!el && !aviId && !sel) el = document.activeElement;"
                "  if (!el || el === document.body) el = document.querySelector('input:not([type=hidden]), textarea');"
                "  if (!el) return {success: false, error: 'Target input element not found'};"
                "  if (typeof el.scrollIntoView === 'function') el.scrollIntoView({block: 'center', inline: 'center'});"
                "  el.focus();"
                "  if (clear) el.value = '';"
                "  el.value = clear ? text : (el.value + text);"
                "  el.dispatchEvent(new Event('input', {bubbles: true}));"
                "  el.dispatchEvent(new Event('change', {bubbles: true}));"
                "  if (pressEnter) {"
                "    el.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true}));"
                "    el.dispatchEvent(new KeyboardEvent('keyup', {key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true}));"
                "    if (el.form) el.form.submit();"
                "  }"
                "  return {success: true, tag: el.tagName, value: el.value, element_id: aviId};"
                "})()"
            )
            res = self.cdp.evaluate(js_type)
            if isinstance(res, dict) and res.get("success"):
                if wait_seconds > 0:
                    time.sleep(wait_seconds)
                post_obs = self.observe()
                return {
                    "success": True,
                    "method": "cdp",
                    "typed": res,
                    "observation": post_obs.to_dict(),
                }
            elif isinstance(res, dict) and not res.get("success"):
                return {"success": False, "error": res.get("error", "Failed to type into element")}

        # Desktop fallback
        state = self.observe()
        if state.is_running and state.browser_name:
            from avi.capabilities.desktop.window import WindowFocusCapability

            WindowFocusCapability().execute(app=state.browser_name)

        from avi.capabilities.desktop.input import PressKeyCapability, TypeTextCapability

        type_res = TypeTextCapability().execute(text=text, clear_first=clear_existing)
        if press_enter:
            PressKeyCapability().execute(key="Return")

        if wait_seconds > 0:
            time.sleep(wait_seconds)
        post_obs = self.observe()
        return {
            "success": type_res.success,
            "method": "desktop_fallback",
            "observation": post_obs.to_dict(),
            "typed": {"text": text, "element_id": target_id, "selector": target_sel},
        }

    def press_key(self, key: str, wait_seconds: float = 0.3) -> dict[str, Any]:
        """Send a key press to the active browser element or window."""
        clean_key = key.strip()
        key_name = clean_key.capitalize() if len(clean_key) > 1 else clean_key

        if self.cdp.is_available():
            js_key = (
                "(() => {"
                f"  const k = {json.dumps(key_name)};"
                "  const el = document.activeElement || window;"
                "  const evDown = new KeyboardEvent('keydown', {key: k, bubbles: true, cancelable: true});"
                "  const evUp = new KeyboardEvent('keyup', {key: k, bubbles: true, cancelable: true});"
                "  el.dispatchEvent(evDown);"
                "  el.dispatchEvent(evUp);"
                "  return {success: true, key: k};"
                "})()"
            )
            self.cdp.evaluate(js_key)
            if wait_seconds > 0:
                time.sleep(wait_seconds)
            post_obs = self.observe()
            return {
                "success": True,
                "key": key_name,
                "method": "cdp",
                "observation": post_obs.to_dict(),
            }

        # Desktop fallback
        from avi.capabilities.desktop.input import PressKeyCapability

        key_map = {
            "Enter": "Return",
            "Escape": "Escape",
            "Tab": "Tab",
            "ArrowDown": "Down",
            "ArrowUp": "Up",
            "Space": "space",
            "Backspace": "BackSpace",
        }
        xkey = key_map.get(key_name, key_name)
        res_desktop = PressKeyCapability().execute(key=xkey)
        if wait_seconds > 0:
            time.sleep(wait_seconds)
        post_obs = self.observe()
        return {
            "success": res_desktop.success,
            "key": key_name,
            "method": "desktop_fallback",
            "observation": post_obs.to_dict(),
        }

    def scroll(
        self,
        direction: str = "down",
        amount: str | int = "page",
        wait_seconds: float = 0.3,
    ) -> dict[str, Any]:
        """Scroll viewport via CDP or fallback."""
        dir_clean = direction.strip().lower()
        if self.cdp.is_available():
            js_scroll = (
                "(() => {"
                f"  const dir = {json.dumps(dir_clean)};"
                f"  const amt = {json.dumps(amount)};"
                "  const scrollYBefore = window.scrollY;"
                "  if (dir === 'top') {"
                "    window.scrollTo({top: 0, behavior: 'smooth'});"
                "  } else if (dir === 'bottom') {"
                "    window.scrollTo({top: document.body ? document.body.scrollHeight : 10000, behavior: 'smooth'});"
                "  } else {"
                "    let dy = 600;"
                "    if (typeof amt === 'number') dy = amt;"
                "    else if (amt === 'half_page') dy = Math.round(window.innerHeight / 2);"
                "    else if (amt === 'page') dy = Math.round(window.innerHeight * 0.85);"
                "    if (dir === 'up') dy = -dy;"
                "    window.scrollBy({top: dy, behavior: 'smooth'});"
                "  }"
                "  return {"
                "    success: true,"
                "    scrollYBefore: scrollYBefore,"
                "    scrollYAfter: window.scrollY,"
                "    direction: dir"
                "  };"
                "})()"
            )
            res = self.cdp.evaluate(js_scroll)
            if wait_seconds > 0:
                time.sleep(wait_seconds)
            post_obs = self.observe()
            return {
                "success": True,
                "direction": dir_clean,
                "amount": amount,
                "scroll_data": res,
                "observation": post_obs.to_dict(),
                "method": "cdp",
            }

        # Desktop fallback
        from avi.capabilities.desktop.input import PressKeyCapability

        key = "Page_Down" if dir_clean in ("down", "bottom") else "Page_Up"
        res_desk = PressKeyCapability().execute(key=key)
        if wait_seconds > 0:
            time.sleep(wait_seconds)
        post_obs = self.observe()
        return {
            "success": res_desk.success,
            "direction": dir_clean,
            "amount": amount,
            "method": "desktop_fallback",
            "observation": post_obs.to_dict(),
        }

    def list_tabs(self) -> list[TabInfo]:
        """List current browser tabs."""
        if self.cdp.is_available():
            raw_tabs = self.cdp.list_tabs()
            active = self.cdp.get_active_tab()
            active_id = active.get("id") if active else None
            return [
                TabInfo(
                    tab_id=str(t.get("id", "")),
                    title=str(t.get("title", "")),
                    url=str(t.get("url", "")),
                    is_active=(t.get("id") == active_id),
                )
                for t in raw_tabs
            ]
        if self._last_observation and self._last_observation.tabs:
            return self._last_observation.tabs
        return []

    def new_tab(self, url: str = "about:blank") -> TabInfo | None:
        """Create a new tab."""
        if self.cdp.is_available():
            info = self.cdp.new_tab(url)
            if isinstance(info, dict):
                tab = TabInfo(
                    tab_id=str(info.get("id", "")),
                    title=str(info.get("title", "")),
                    url=str(info.get("url", url)),
                    is_active=True,
                )
                self.observe()
                return tab
        return None

    def activate_tab(self, tab_id: str) -> bool:
        """Activate tab by ID."""
        if self.cdp.is_available():
            ok = self.cdp.activate_tab(tab_id)
            if ok:
                self.observe()
            return ok
        return False

    def close_tab(self, tab_id: str) -> bool:
        """Close tab by ID."""
        if self.cdp.is_available():
            ok = self.cdp.close_tab(tab_id)
            if ok:
                self.observe()
            return ok
        return False

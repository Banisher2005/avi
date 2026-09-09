"""Chrome DevTools Protocol (CDP) client using pure Python standard library.

Provides zero-dependency control and state observation for Chrome/Chromium
instances running with --remote-debugging-port.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import struct
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)


class CdpClient:
    """Lightweight, standard-library-only Chrome DevTools Protocol client."""

    def __init__(self, host: str = "127.0.0.1", port: int = 9222, timeout: float = 1.0) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.base_url = f"http://{host}:{port}"
        self._msg_id = 0

    def is_available(self) -> bool:
        """Check if CDP endpoint is reachable."""
        try:
            req = urllib.request.Request(f"{self.base_url}/json/version")
            with urllib.request.urlopen(req, timeout=0.3) as resp:
                return resp.status == 200
        except Exception:
            return False

    def list_tabs(self) -> list[dict[str, Any]]:
        """List all inspectable targets/tabs."""
        try:
            req = urllib.request.Request(f"{self.base_url}/json/list")
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return [t for t in data if isinstance(t, dict)]
        except Exception as err:
            logger.debug("CDP list_tabs error: %s", err)
            return []

    def get_active_tab(self) -> dict[str, Any] | None:
        """Get the primary or most recently active page target."""
        tabs = self.list_tabs()
        pages = [t for t in tabs if t.get("type") == "page"]
        if not pages:
            return tabs[0] if tabs else None
        return pages[0]

    def new_tab(self, url: str = "about:blank") -> dict[str, Any] | None:
        """Open a new tab with optional URL."""
        try:
            encoded_url = urllib.parse.quote(url, safe=":/#?=&%")
            req = urllib.request.Request(f"{self.base_url}/json/new?{encoded_url}", method="PUT")
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as err:
            logger.debug("CDP new_tab error: %s", err)
            return None

    def activate_tab(self, tab_id: str) -> bool:
        """Bring target tab to focus."""
        try:
            req = urllib.request.Request(f"{self.base_url}/json/activate/{tab_id}", method="POST")
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return resp.status == 200
        except Exception as err:
            logger.debug("CDP activate_tab error: %s", err)
            return False

    def close_tab(self, tab_id: str) -> bool:
        """Close specified tab."""
        try:
            req = urllib.request.Request(f"{self.base_url}/json/close/{tab_id}", method="POST")
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return resp.status == 200
        except Exception as err:
            logger.debug("CDP close_tab error: %s", err)
            return False

    def send_cdp_command(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        tab_id: str | None = None,
        ws_url: str | None = None,
    ) -> dict[str, Any]:
        """Send a JSON-RPC command to the target via a direct WebSocket connection."""
        if not ws_url:
            if not tab_id:
                active = self.get_active_tab()
                if not active:
                    return {"error": "No active tab found"}
                ws_url = active.get("webSocketDebuggerUrl")
            else:
                for tab in self.list_tabs():
                    if tab.get("id") == tab_id:
                        ws_url = tab.get("webSocketDebuggerUrl")
                        break

        if not ws_url:
            return {"error": "Target webSocketDebuggerUrl unavailable"}

        self._msg_id += 1
        payload = json.dumps({"id": self._msg_id, "method": method, "params": params or {}})

        try:
            return self._ws_request(ws_url, payload)
        except Exception as err:
            logger.debug("CDP command failed: %s", err)
            return {"error": str(err)}

    def evaluate(self, expression: str, tab_id: str | None = None) -> Any:
        """Evaluate a JavaScript expression on the tab."""
        res = self.send_cdp_command(
            "Runtime.evaluate",
            {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": True,
            },
            tab_id=tab_id,
        )
        if "result" in res and "result" in res["result"]:
            inner = res["result"]["result"]
            return inner.get("value")
        return None

    def navigate(self, url: str, tab_id: str | None = None) -> bool:
        """Navigate tab to the given URL."""
        res = self.send_cdp_command("Page.navigate", {"url": url}, tab_id=tab_id)
        return "result" in res and "frameId" in res["result"]

    def _ws_request(self, ws_url: str, text_payload: str) -> dict[str, Any]:
        """Perform a single RFC 6455 WebSocket exchange over a raw TCP socket."""
        parsed = urllib.parse.urlparse(ws_url)
        host = parsed.hostname or self.host
        port = parsed.port or self.port
        path = parsed.path
        if parsed.query:
            path += f"?{parsed.query}"

        sock = socket.create_connection((host, port), timeout=self.timeout)
        try:
            sock.settimeout(self.timeout)
            # 1. Send WebSocket Handshake
            key = "dGhlIHNhbXBsZSBub25jZQ=="
            handshake = (
                f"GET {path} HTTP/1.1\r\n"
                f"Host: {host}:{port}\r\n"
                f"Upgrade: websocket\r\n"
                f"Connection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {key}\r\n"
                f"Sec-WebSocket-Version: 13\r\n\r\n"
            )
            sock.sendall(handshake.encode("ascii"))

            # 2. Read Handshake Response
            resp_bytes = b""
            while b"\r\n\r\n" not in resp_bytes:
                chunk = sock.recv(1024)
                if not chunk:
                    break
                resp_bytes += chunk

            if b" 101 " not in resp_bytes:
                raise RuntimeError("WebSocket handshake failed")

            # 3. Send Client Masked Frame (Opcode 0x1 = text)
            payload_bytes = text_payload.encode("utf-8")
            length = len(payload_bytes)
            mask_key = os.urandom(4)

            header = bytearray([0x81])  # FIN + Text opcode
            if length <= 125:
                header.append(0x80 | length)  # Mask bit set + length
            elif length <= 65535:
                header.append(0x80 | 126)
                header.extend(struct.pack("!H", length))
            else:
                header.append(0x80 | 127)
                header.extend(struct.pack("!Q", length))

            header.extend(mask_key)
            masked_payload = bytearray(
                b ^ mask_key[i % 4] for i, b in enumerate(payload_bytes)
            )
            sock.sendall(header + masked_payload)

            # 4. Read Server Response Frame (Server frames are unmasked)
            header_bytes = sock.recv(2)
            if len(header_bytes) < 2:
                return {}

            byte2 = header_bytes[1]
            payload_len = byte2 & 0x7F
            if payload_len == 126:
                ext = sock.recv(2)
                payload_len = struct.unpack("!H", ext)[0]
            elif payload_len == 127:
                ext = sock.recv(8)
                payload_len = struct.unpack("!Q", ext)[0]

            received_data = b""
            while len(received_data) < payload_len:
                chunk = sock.recv(min(4096, payload_len - len(received_data)))
                if not chunk:
                    break
                received_data += chunk

            return json.loads(received_data.decode("utf-8"))
        finally:
            sock.close()

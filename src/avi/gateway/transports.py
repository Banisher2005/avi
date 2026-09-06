"""Transports for AVI Universal Protocol Gateway (stdio and local TCP)."""

import socket
import sys
import threading
from typing import TextIO

from avi.gateway.jsonrpc import JsonRpcDispatcher


class StdioTransport:
    """Standard input/output transport for MCP and JSON-RPC clients.

    Used by Claude Desktop, Cursor, VS Code extensions, and local process pipes.
    """

    def __init__(
        self,
        dispatcher: JsonRpcDispatcher,
        in_stream: TextIO | None = None,
        out_stream: TextIO | None = None,
    ) -> None:
        self.dispatcher = dispatcher
        self.in_stream = in_stream or sys.stdin
        self.out_stream = out_stream or sys.stdout
        self._running = False

    def run(self) -> int:
        """Run the stdio message loop until EOF or error."""
        self._running = True
        try:
            for line in self.in_stream:
                if not self._running:
                    break
                stripped = line.strip()
                if not stripped:
                    continue

                response = self.dispatcher.handle_message(stripped)
                if response is not None:
                    self.out_stream.write(response + "\n")
                    self.out_stream.flush()

        except (KeyboardInterrupt, BrokenPipeError):
            pass
        finally:
            self._running = False
            self.dispatcher.shutdown()

        return 0

    def stop(self) -> None:
        """Stop the stdio event loop."""
        self._running = False


class TcpTransport:
    """Local TCP transport for local sockets or network-isolated agents.

    Strict Security Invariant:
    Binds strictly to 127.0.0.1 (localhost) by default to prevent remote exposure.
    """

    def __init__(
        self,
        dispatcher: JsonRpcDispatcher,
        host: str = "127.0.0.1",
        port: int = 8765,
    ) -> None:
        self.dispatcher = dispatcher
        self.host = host
        self.port = port
        self._server_socket: socket.socket | None = None
        self._running = False
        self._thread: threading.Thread | None = None

    def start(self, block: bool = True) -> int:
        """Start the local TCP server socket."""
        # Enforce local-only security warning if non-local binding attempted
        if self.host not in ("127.0.0.1", "localhost", "::1"):
            sys.stderr.write(
                f"[SECURITY WARNING] AVI Gateway binding to non-local address '{self.host}'. "
                "Ensure firewall rules or authentication tokens are active.\n"
            )
            sys.stderr.flush()

        self._server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server_socket.bind((self.host, self.port))
        self._server_socket.listen(5)
        self._running = True

        if block:
            return self._serve_forever()

        self._thread = threading.Thread(target=self._serve_forever, daemon=True)
        self._thread.start()
        return 0

    def _serve_forever(self) -> int:
        """Accept and handle client connections."""
        try:
            while self._running and self._server_socket:
                try:
                    client_sock, client_addr = self._server_socket.accept()
                except OSError:
                    break

                client_thread = threading.Thread(
                    target=self._handle_client,
                    args=(client_sock,),
                    daemon=True,
                )
                client_thread.start()
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()
        return 0

    def _handle_client(self, client_sock: socket.socket) -> None:
        """Read requests line-by-line from client socket and send responses."""
        with client_sock:
            try:
                reader = client_sock.makefile("r", encoding="utf-8")
                writer = client_sock.makefile("w", encoding="utf-8")
                for line in reader:
                    stripped = line.strip()
                    if not stripped:
                        continue
                    resp = self.dispatcher.handle_message(stripped)
                    if resp is not None:
                        writer.write(resp + "\n")
                        writer.flush()
            except (ConnectionError, BrokenPipeError, OSError):
                pass

    def stop(self) -> None:
        """Shut down the TCP server cleanly."""
        self._running = False
        if self._server_socket:
            try:
                self._server_socket.close()
            except OSError:
                pass
            self._server_socket = None
        self.dispatcher.shutdown()

"""JSON-RPC 2.0 and Model Context Protocol (MCP) Dispatcher for AVI Gateway."""

import json
from typing import Any, Callable

from avi import __version__
from avi.gateway.core import GatewayCore

# Standard JSON-RPC 2.0 Error Codes
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

# AVI Domain Error Codes
SAFETY_BLOCKED = -32000
CONFIRMATION_REQUIRED = -32001
AUTH_REQUIRED = -32002


def make_jsonrpc_response(req_id: Any, result: Any) -> dict[str, Any]:
    """Format standard JSON-RPC 2.0 successful response."""
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "result": result,
    }


def make_jsonrpc_error(
    req_id: Any,
    code: int,
    message: str,
    data: Any = None,
) -> dict[str, Any]:
    """Format standard JSON-RPC 2.0 error response."""
    err_body: dict[str, Any] = {
        "code": code,
        "message": message,
    }
    if data is not None:
        err_body["data"] = data
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": err_body,
    }


class JsonRpcDispatcher:
    """Dispatches JSON-RPC 2.0 and MCP calls to GatewayCore."""

    def __init__(
        self,
        gateway: GatewayCore | None = None,
        auth_token: str | None = None,
    ) -> None:
        self.gateway = gateway or GatewayCore()
        self.auth_token = auth_token
        self._is_shutdown = False

        # Registered method dispatch table
        self._methods: dict[str, Callable[[Any, Any], Any]] = {
            # MCP & System lifecycle methods
            "initialize": self._handle_initialize,
            "notifications/initialized": self._handle_initialized_notification,
            "ping": self._handle_ping,
            # Gateway status & capabilities
            "health": self._handle_health,
            "capabilities": self._handle_capabilities,
            # Tool discovery & execution (MCP compliant)
            "tools/list": self._handle_tools_list,
            "tools/call": self._handle_tools_call,
            # Agent routing & Context
            "agent/send": self._handle_agent_send,
            "context/get": self._handle_context_get,
            # Command evaluation & execution (strictly protected by SafetyEngine)
            "command/evaluate": self._handle_command_evaluate,
            "command/execute": self._handle_command_execute,
        }

    def register_method(self, name: str, handler: Callable[[Any, Any], Any]) -> None:
        """Register a custom JSON-RPC method."""
        self._methods[name] = handler

    def handle_message(self, raw_input: str) -> str | None:
        """Process a raw JSON-RPC 2.0 request string and return the serialized response."""
        if not raw_input or not raw_input.strip():
            return None

        # 1. Parse JSON
        try:
            payload = json.loads(raw_input)
        except json.JSONDecodeError as err:
            err_resp = make_jsonrpc_error(None, PARSE_ERROR, f"Parse error: {err}")
            return json.dumps(err_resp)

        # 2. Batch request vs single request
        if isinstance(payload, list):
            if not payload:
                err_resp = make_jsonrpc_error(None, INVALID_REQUEST, "Invalid Request: empty batch")
                return json.dumps(err_resp)
            responses = []
            for item in payload:
                res = self._process_single_request(item)
                if res is not None:
                    responses.append(res)
            return json.dumps(responses) if responses else None

        # 3. Single request
        res = self._process_single_request(payload)
        return json.dumps(res) if res is not None else None

    def _process_single_request(self, req: Any) -> dict[str, Any] | None:
        """Execute a single JSON-RPC 2.0 request dict."""
        if not isinstance(req, dict):
            return make_jsonrpc_error(None, INVALID_REQUEST, "Invalid Request: expected JSON object")

        req_id = req.get("id")
        is_notification = "id" not in req

        # Check JSON-RPC version
        if req.get("jsonrpc") != "2.0":
            if is_notification:
                return None
            return make_jsonrpc_error(req_id, INVALID_REQUEST, "Invalid Request: jsonrpc must be '2.0'")

        method = req.get("method")
        if not isinstance(method, str):
            if is_notification:
                return None
            return make_jsonrpc_error(req_id, INVALID_REQUEST, "Invalid Request: method must be string")

        params = req.get("params", {})
        if not isinstance(params, (dict, list)):
            if is_notification:
                return None
            return make_jsonrpc_error(req_id, INVALID_PARAMS, "Invalid params: must be object or array")

        # Optional Auth Token verification
        if self.auth_token:
            client_token = None
            if isinstance(params, dict):
                client_token = params.get("auth_token")
            if client_token != self.auth_token:
                if is_notification:
                    return None
                return make_jsonrpc_error(req_id, AUTH_REQUIRED, "Authentication required: invalid or missing token")

        handler = self._methods.get(method)
        if handler is None:
            if is_notification:
                return None
            return make_jsonrpc_error(req_id, METHOD_NOT_FOUND, f"Method not found: '{method}'")

        try:
            result = handler(params, req_id)
            if is_notification:
                return None
            return make_jsonrpc_response(req_id, result)
        except TypeError as err:
            if is_notification:
                return None
            return make_jsonrpc_error(req_id, INVALID_PARAMS, f"Invalid params: {err}")
        except Exception as err:
            if is_notification:
                return None
            return make_jsonrpc_error(req_id, INTERNAL_ERROR, f"Internal error: {err}")

    # -----------------------------------------------------------------------
    # RPC Handlers
    # -----------------------------------------------------------------------

    def _handle_initialize(self, params: Any, req_id: Any) -> dict[str, Any]:
        """MCP/JSON-RPC initialization handshake."""
        return {
            "protocolVersion": "2024-11-05",
            "serverInfo": {
                "name": "avi",
                "version": __version__,
                "description": "AVI — Fast Local AI Terminal Assistant & Tool Platform",
            },
            "capabilities": {
                "tools": {
                    "listChanged": False,
                },
                "resources": {},
                "prompts": {},
            },
        }

    def _handle_initialized_notification(self, params: Any, req_id: Any) -> None:
        """Client notification confirming initialization."""
        return None

    def _handle_ping(self, params: Any, req_id: Any) -> dict[str, Any]:
        """Connectivity check."""
        return {}

    def _handle_health(self, params: Any, req_id: Any) -> dict[str, Any]:
        """Health diagnostics."""
        return self.gateway.get_health()

    def _handle_capabilities(self, params: Any, req_id: Any) -> dict[str, Any]:
        """Expose capabilities."""
        return self.gateway.get_capabilities()

    def _handle_tools_list(self, params: Any, req_id: Any) -> dict[str, Any]:
        """Discover tools formatted to MCP specification."""
        discovered = self.gateway.list_tools()
        tools_list: list[dict[str, Any]] = []
        for t in discovered:
            tools_list.append({
                "name": t["name"],
                "description": t["description"],
                "inputSchema": t["input_schema"],
            })
        return {"tools": tools_list}

    def _handle_tools_call(self, params: Any, req_id: Any) -> dict[str, Any]:
        """Invoke a tool formatted to MCP specification."""
        if not isinstance(params, dict):
            raise TypeError("Expected object parameters with 'name' and optional 'arguments'")

        tool_name = params.get("name")
        if not isinstance(tool_name, str):
            raise TypeError("Missing required string field 'name'")

        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            raise TypeError("Field 'arguments' must be an object")

        res = self.gateway.call_tool(tool_name, arguments)
        display_text = res.get("display") or ""
        if not display_text and res.get("error"):
            display_text = f"Error: {res['error']}"

        return {
            "content": [
                {
                    "type": "text",
                    "text": display_text,
                }
            ],
            "isError": not res.get("success", False),
            "data": res.get("data"),
        }

    def _handle_agent_send(self, params: Any, req_id: Any) -> dict[str, Any]:
        """Route prompt to configured model provider via Router."""
        if not isinstance(params, dict):
            raise TypeError("Expected object parameters with 'prompt'")

        prompt = params.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            raise TypeError("Missing required string field 'prompt'")

        system_prompt = params.get("system_prompt")
        context = params.get("context")
        return self.gateway.send_agent_request(
            prompt=prompt,
            system_prompt=system_prompt,
            context=context,
        )

    def _handle_context_get(self, params: Any, req_id: Any) -> dict[str, Any]:
        """Inspect environment context."""
        domains = None
        if isinstance(params, dict) and "domains" in params:
            domains = params["domains"]
        return self.gateway.get_context(domains)

    def _handle_command_evaluate(self, params: Any, req_id: Any) -> dict[str, Any]:
        """Evaluate command safety risk level."""
        if not isinstance(params, dict) or "command" not in params:
            raise TypeError("Missing required string field 'command'")
        return self.gateway.evaluate_command(str(params["command"]))

    def _handle_command_execute(self, params: Any, req_id: Any) -> dict[str, Any]:
        """Execute command strictly verified by SafetyEngine."""
        if not isinstance(params, dict) or "command" not in params:
            raise TypeError("Missing required string field 'command'")

        cmd = str(params["command"])
        confirmed = bool(params.get("confirmed", False))
        token = params.get("confirmation_token")
        if token is not None:
            token = str(token)

        return self.gateway.execute_command(
            command=cmd,
            confirmed=confirmed,
            confirmation_token=token,
        )

    def shutdown(self) -> None:
        """Mark dispatcher as shutdown."""
        self._is_shutdown = True

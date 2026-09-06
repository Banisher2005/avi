"""Unit tests for AVI Universal Protocol Gateway, JSON-RPC 2.0, MCP, and Transports."""

import ast
import io
import json
import socket
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from avi.config import Config
from avi.core.router import Router
from avi.gateway.core import GatewayCore
from avi.gateway.jsonrpc import (
    AUTH_REQUIRED,
    CONFIRMATION_REQUIRED,
    INVALID_PARAMS,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    PARSE_ERROR,
    SAFETY_BLOCKED,
    JsonRpcDispatcher,
    make_jsonrpc_error,
    make_jsonrpc_response,
)
from avi.gateway.models import GatewayConfirmation, GatewayExecutionResponse
from avi.gateway.transports import StdioTransport, TcpTransport
from avi.safety.models import SafetyAssessment, RiskLevel


# ===========================================================================
# 1. GatewayCore Tests
# ===========================================================================

class TestGatewayCore:
    """Unit tests for GatewayCore operations."""

    @pytest.fixture
    def gateway(self) -> GatewayCore:
        config = Config.load()
        router = Router(config)
        return GatewayCore(router=router, config=config, confirmation_ttl=10.0)

    def test_list_tools(self, gateway: GatewayCore):
        tools = gateway.list_tools()
        assert isinstance(tools, list)
        assert len(tools) == 8
        tool_names = [t["name"] for t in tools]
        assert "filesystem.file_metadata" in tool_names
        assert "filesystem.list_directory" in tool_names
        assert "git.status" in tool_names
        assert "system.system_info" in tool_names

        for t in tools:
            assert "name" in t
            assert "description" in t
            assert "input_schema" in t
            assert t["input_schema"]["type"] == "object"

    def test_get_tool_schema_found(self, gateway: GatewayCore):
        schema = gateway.get_tool_schema("filesystem.file_metadata")
        assert schema is not None
        assert schema["name"] == "filesystem.file_metadata"
        assert "properties" in schema["input_schema"]
        assert "path" in schema["input_schema"]["properties"]

    def test_get_tool_schema_not_found(self, gateway: GatewayCore):
        schema = gateway.get_tool_schema("non_existent_tool")
        assert schema is None

    def test_call_tool_success(self, gateway: GatewayCore):
        res = gateway.call_tool("system.system_info", {})
        assert res["tool"] == "system.system_info"
        assert res["success"] is True
        assert res["data"] is not None
        assert "os" in res["data"]
        assert res["error"] is None

    def test_call_tool_failure_missing_param(self, gateway: GatewayCore):
        res = gateway.call_tool("filesystem.file_metadata", {})
        assert res["tool"] == "filesystem.file_metadata"
        assert res["success"] is False
        assert "path" in res["error"].lower()

    def test_call_tool_unknown_tool(self, gateway: GatewayCore):
        res = gateway.call_tool("unknown_tool_xyz", {})
        assert res["success"] is False
        assert "unknown tool" in res["error"].lower()

    def test_evaluate_command_safe(self, gateway: GatewayCore):
        res = gateway.evaluate_command("ls -la")
        assert res["command"] == "ls -la"
        assert res["is_safe"] is True
        assert res["level"] == "SAFE"
        assert res["requires_confirmation"] is False
        assert res["is_blocked"] is False

    def test_evaluate_command_confirm(self, gateway: GatewayCore):
        res = gateway.evaluate_command("rm /tmp/test.txt")
        assert res["requires_confirmation"] is True
        assert res["level"] == "CONFIRM"
        assert res["is_safe"] is False

    def test_evaluate_command_blocked(self, gateway: GatewayCore):
        res = gateway.evaluate_command("rm -rf /")
        assert res["is_blocked"] is True
        assert res["level"] == "BLOCK"
        assert res["is_safe"] is False

    def test_execute_command_safe_executes(self, gateway: GatewayCore):
        res = gateway.execute_command("echo test_gateway_execution")
        assert res["status"] == "executed"
        assert res["exit_code"] == 0
        assert "test_gateway_execution" in res["stdout"]
        assert res["duration_ms"] is not None

    def test_execute_command_blocked_refusal(self, gateway: GatewayCore):
        res = gateway.execute_command("rm -rf /")
        assert res["status"] == "blocked"
        assert "blocked" in res["error"].lower()
        assert res.get("exit_code") is None

    def test_execute_command_requires_confirmation(self, gateway: GatewayCore):
        # Without confirmation, a mutating command must return confirmation_required
        res = gateway.execute_command("rm non_existent_dummy_file.txt")
        assert res["status"] == "confirmation_required"
        assert res["confirmation_token"] is not None
        assert res["confirmation_token"].startswith("cf-")
        token = res["confirmation_token"]

        # Attempt with invalid token
        res_invalid = gateway.execute_command(
            "rm non_existent_dummy_file.txt",
            confirmed=True,
            confirmation_token="invalid-token",
        )
        assert res_invalid["status"] == "error"
        assert "invalid or expired" in res_invalid["error"].lower()

        # Attempt with valid token
        res_valid = gateway.execute_command(
            "rm non_existent_dummy_file.txt",
            confirmed=True,
            confirmation_token=token,
        )
        assert res_valid["status"] == "executed"

        # Ensure token is single-use: cannot be reused
        res_reused = gateway.execute_command(
            "rm non_existent_dummy_file.txt",
            confirmed=True,
            confirmation_token=token,
        )
        assert res_reused["status"] == "error"

    def test_execute_command_expired_token(self, gateway: GatewayCore):
        gateway.confirmation_ttl = 0.01
        res = gateway.execute_command("rm non_existent_dummy_file.txt")
        token = res["confirmation_token"]
        time.sleep(0.02)  # Expire token

        res_expired = gateway.execute_command(
            "rm non_existent_dummy_file.txt",
            confirmed=True,
            confirmation_token=token,
        )
        assert res_expired["status"] == "error"
        assert "invalid or expired" in res_expired["error"].lower()

    def test_send_agent_request(self, gateway: GatewayCore):
        with patch.object(gateway.router, "route_full") as mock_route:
            from avi.providers.base import ProviderResponse
            mock_route.return_value = ProviderResponse(text="ls -la")

            res = gateway.send_agent_request("how do i list files?")
            assert res["text"] == "ls -la"
            assert res["tool_calls"] == []

    def test_get_context(self, gateway: GatewayCore):
        ctx = gateway.get_context()
        assert "terminal" in ctx
        assert ctx["terminal"] is not None
        assert "cwd" in ctx["terminal"]
        assert "git" in ctx

    def test_get_health(self, gateway: GatewayCore):
        health = gateway.get_health()
        assert "status" in health
        assert health["gateway"] == "operational"
        assert health["tools_registered"] == 8
        assert health["fastpath_templates"] > 0
        assert health["safety_engine"] == "active"

    def test_get_capabilities(self, gateway: GatewayCore):
        caps = gateway.get_capabilities()
        assert "jsonrpc-2.0" in caps["protocols"]
        assert "mcp-2024-11-05" in caps["protocols"]
        assert "stdio" in caps["transports"]
        assert "tcp" in caps["transports"]
        assert caps["tools"]["count"] == 8
        assert caps["safety"]["fail_closed"] is True


# ===========================================================================
# 2. JsonRpcDispatcher & Standard JSON-RPC 2.0 Tests
# ===========================================================================

class TestJsonRpcDispatcher:
    """Unit tests for JSON-RPC 2.0 and MCP protocol dispatch."""

    @pytest.fixture
    def dispatcher(self) -> JsonRpcDispatcher:
        return JsonRpcDispatcher()

    def test_empty_or_whitespace_message(self, dispatcher: JsonRpcDispatcher):
        assert dispatcher.handle_message("") is None
        assert dispatcher.handle_message("   \n  ") is None

    def test_parse_error(self, dispatcher: JsonRpcDispatcher):
        raw = "{invalid json"
        res_str = dispatcher.handle_message(raw)
        assert res_str is not None
        res = json.loads(res_str)
        assert res["jsonrpc"] == "2.0"
        assert res["id"] is None
        assert res["error"]["code"] == PARSE_ERROR

    def test_invalid_request_not_dict(self, dispatcher: JsonRpcDispatcher):
        res_str = dispatcher.handle_message('"not an object"')
        assert res_str is not None
        res = json.loads(res_str)
        assert res["error"]["code"] == INVALID_REQUEST

    def test_invalid_request_wrong_version(self, dispatcher: JsonRpcDispatcher):
        req = json.dumps({"jsonrpc": "1.0", "id": 1, "method": "ping"})
        res = json.loads(dispatcher.handle_message(req))
        assert res["error"]["code"] == INVALID_REQUEST
        assert "jsonrpc must be '2.0'" in res["error"]["message"]

    def test_invalid_request_missing_method(self, dispatcher: JsonRpcDispatcher):
        req = json.dumps({"jsonrpc": "2.0", "id": 1})
        res = json.loads(dispatcher.handle_message(req))
        assert res["error"]["code"] == INVALID_REQUEST
        assert "method must be string" in res["error"]["message"]

    def test_method_not_found(self, dispatcher: JsonRpcDispatcher):
        req = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "non_existent_method"})
        res = json.loads(dispatcher.handle_message(req))
        assert res["error"]["code"] == METHOD_NOT_FOUND

    def test_invalid_params_type(self, dispatcher: JsonRpcDispatcher):
        req = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping", "params": "string_not_allowed"})
        res = json.loads(dispatcher.handle_message(req))
        assert res["error"]["code"] == INVALID_PARAMS

    def test_notification_silence(self, dispatcher: JsonRpcDispatcher):
        # Notifications have no 'id' field and produce no response
        req = json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"})
        assert dispatcher.handle_message(req) is None

        # Even on method not found or error, notifications should not return responses
        bad_notif = json.dumps({"jsonrpc": "2.0", "method": "unknown_notification"})
        assert dispatcher.handle_message(bad_notif) is None

    def test_batch_requests(self, dispatcher: JsonRpcDispatcher):
        batch = [
            {"jsonrpc": "2.0", "id": 1, "method": "ping"},
            {"jsonrpc": "2.0", "id": 2, "method": "ping"},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},  # notification
        ]
        res_str = dispatcher.handle_message(json.dumps(batch))
        assert res_str is not None
        responses = json.loads(res_str)
        assert isinstance(responses, list)
        assert len(responses) == 2
        assert responses[0]["id"] == 1
        assert responses[0]["result"] == {}
        assert responses[1]["id"] == 2
        assert responses[1]["result"] == {}

    def test_batch_empty(self, dispatcher: JsonRpcDispatcher):
        res = json.loads(dispatcher.handle_message("[]"))
        assert res["error"]["code"] == INVALID_REQUEST

    def test_batch_all_notifications(self, dispatcher: JsonRpcDispatcher):
        batch = [
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
        ]
        assert dispatcher.handle_message(json.dumps(batch)) is None

    def test_auth_token_enforcement(self):
        dispatcher = JsonRpcDispatcher(auth_token="secret-token-42")

        # Without auth_token
        req_unauth = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping", "params": {}})
        res_unauth = json.loads(dispatcher.handle_message(req_unauth))
        assert res_unauth["error"]["code"] == AUTH_REQUIRED

        # With wrong auth_token
        req_wrong = json.dumps({"jsonrpc": "2.0", "id": 2, "method": "ping", "params": {"auth_token": "wrong"}})
        res_wrong = json.loads(dispatcher.handle_message(req_wrong))
        assert res_wrong["error"]["code"] == AUTH_REQUIRED

        # With correct auth_token
        req_ok = json.dumps({"jsonrpc": "2.0", "id": 3, "method": "ping", "params": {"auth_token": "secret-token-42"}})
        res_ok = json.loads(dispatcher.handle_message(req_ok))
        assert res_ok["result"] == {}


# ===========================================================================
# 3. Model Context Protocol (MCP) Compatibility Tests
# ===========================================================================

class TestMcpProtocol:
    """Unit tests for Model Context Protocol (MCP) conformance."""

    @pytest.fixture
    def dispatcher(self) -> JsonRpcDispatcher:
        return JsonRpcDispatcher()

    def test_mcp_initialize_handshake(self, dispatcher: JsonRpcDispatcher):
        req = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test-client", "version": "1.0"},
            },
        })
        res = json.loads(dispatcher.handle_message(req))
        assert res["id"] == 1
        result = res["result"]
        assert result["protocolVersion"] == "2024-11-05"
        assert result["serverInfo"]["name"] == "avi"
        assert "tools" in result["capabilities"]

    def test_mcp_ping(self, dispatcher: JsonRpcDispatcher):
        req = json.dumps({"jsonrpc": "2.0", "id": 10, "method": "ping"})
        res = json.loads(dispatcher.handle_message(req))
        assert res["result"] == {}

    def test_mcp_tools_list(self, dispatcher: JsonRpcDispatcher):
        req = json.dumps({"jsonrpc": "2.0", "id": 11, "method": "tools/list", "params": {}})
        res = json.loads(dispatcher.handle_message(req))
        assert "tools" in res["result"]
        tools = res["result"]["tools"]
        assert len(tools) == 8

        # Verify each tool matches MCP schema (name, description, inputSchema)
        for t in tools:
            assert "name" in t
            assert "description" in t
            assert "inputSchema" in t
            assert isinstance(t["inputSchema"], dict)
            assert t["inputSchema"]["type"] == "object"

    def test_mcp_tools_call_success(self, dispatcher: JsonRpcDispatcher):
        req = json.dumps({
            "jsonrpc": "2.0",
            "id": 12,
            "method": "tools/call",
            "params": {
                "name": "system.system_info",
                "arguments": {},
            },
        })
        res = json.loads(dispatcher.handle_message(req))
        result = res["result"]
        assert result["isError"] is False
        assert len(result["content"]) > 0
        assert result["content"][0]["type"] == "text"
        assert "OS:" in result["content"][0]["text"]
        assert result["data"]["os"] is not None

    def test_mcp_tools_call_failure(self, dispatcher: JsonRpcDispatcher):
        req = json.dumps({
            "jsonrpc": "2.0",
            "id": 13,
            "method": "tools/call",
            "params": {
                "name": "filesystem.file_metadata",
                "arguments": {},  # missing required 'path'
            },
        })
        res = json.loads(dispatcher.handle_message(req))
        result = res["result"]
        assert result["isError"] is True
        assert "path" in result["content"][0]["text"].lower()

    def test_mcp_tools_call_invalid_params(self, dispatcher: JsonRpcDispatcher):
        req = json.dumps({
            "jsonrpc": "2.0",
            "id": 14,
            "method": "tools/call",
            "params": {
                # missing 'name'
                "arguments": {},
            },
        })
        res = json.loads(dispatcher.handle_message(req))
        assert res["error"]["code"] == INVALID_PARAMS


# ===========================================================================
# 4. Extended Gateway RPC Methods Tests
# ===========================================================================

class TestExtendedRpcMethods:
    """Unit tests for AVI gateway extension RPC methods."""

    @pytest.fixture
    def dispatcher(self) -> JsonRpcDispatcher:
        return JsonRpcDispatcher()

    def test_rpc_health(self, dispatcher: JsonRpcDispatcher):
        req = json.dumps({"jsonrpc": "2.0", "id": 20, "method": "health"})
        res = json.loads(dispatcher.handle_message(req))
        assert res["result"]["gateway"] == "operational"

    def test_rpc_capabilities(self, dispatcher: JsonRpcDispatcher):
        req = json.dumps({"jsonrpc": "2.0", "id": 21, "method": "capabilities"})
        res = json.loads(dispatcher.handle_message(req))
        assert "jsonrpc-2.0" in res["result"]["protocols"]

    def test_rpc_context_get(self, dispatcher: JsonRpcDispatcher):
        req = json.dumps({"jsonrpc": "2.0", "id": 22, "method": "context/get"})
        res = json.loads(dispatcher.handle_message(req))
        assert "terminal" in res["result"]

    def test_rpc_command_evaluate(self, dispatcher: JsonRpcDispatcher):
        req = json.dumps({
            "jsonrpc": "2.0",
            "id": 23,
            "method": "command/evaluate",
            "params": {"command": "git status"},
        })
        res = json.loads(dispatcher.handle_message(req))
        assert res["result"]["is_safe"] is True

    def test_rpc_command_execute(self, dispatcher: JsonRpcDispatcher):
        req = json.dumps({
            "jsonrpc": "2.0",
            "id": 24,
            "method": "command/execute",
            "params": {"command": "echo 'gateway_rpc_test'"},
        })
        res = json.loads(dispatcher.handle_message(req))
        assert "result" in res
        assert res["result"]["status"] == "executed"
        assert "gateway_rpc_test" in res["result"]["stdout"]

    def test_rpc_agent_send(self, dispatcher: JsonRpcDispatcher):
        with patch.object(dispatcher.gateway.router, "route_full") as mock_route:
            from avi.providers.base import ProviderResponse
            mock_route.return_value = ProviderResponse(text="echo hello")

            req = json.dumps({
                "jsonrpc": "2.0",
                "id": 25,
                "method": "agent/send",
                "params": {"prompt": "say hello"},
            })
            res = json.loads(dispatcher.handle_message(req))
            assert res["result"]["text"] == "echo hello"


# ===========================================================================
# 5. Transports Tests
# ===========================================================================

class TestTransports:
    """Unit tests for StdioTransport and TcpTransport."""

    def test_stdio_transport_run(self):
        input_data = (
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}) + "\n"
            + json.dumps({"jsonrpc": "2.0", "id": 2, "method": "health"}) + "\n"
        )
        in_stream = io.StringIO(input_data)
        out_stream = io.StringIO()

        dispatcher = JsonRpcDispatcher()
        transport = StdioTransport(dispatcher, in_stream=in_stream, out_stream=out_stream)
        code = transport.run()
        assert code == 0

        output_lines = [line.strip() for line in out_stream.getvalue().strip().split("\n") if line.strip()]
        assert len(output_lines) == 2
        resp1 = json.loads(output_lines[0])
        resp2 = json.loads(output_lines[1])
        assert resp1["id"] == 1
        assert resp1["result"] == {}
        assert resp2["id"] == 2
        assert resp2["result"]["gateway"] == "operational"

    def test_tcp_transport_socket_communication(self):
        # Find an available local port
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]

        dispatcher = JsonRpcDispatcher()
        transport = TcpTransport(dispatcher, host="127.0.0.1", port=port)
        ret = transport.start(block=False)
        assert ret == 0

        try:
            # Connect via client socket
            client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            client.connect(("127.0.0.1", port))
            with client:
                req = json.dumps({"jsonrpc": "2.0", "id": 99, "method": "ping"}) + "\n"
                client.sendall(req.encode("utf-8"))

                # Read line response
                resp_data = b""
                while not resp_data.endswith(b"\n"):
                    chunk = client.recv(1024)
                    if not chunk:
                        break
                    resp_data += chunk

                res = json.loads(resp_data.decode("utf-8").strip())
                assert res["id"] == 99
                assert res["result"] == {}
        finally:
            transport.stop()


# ===========================================================================
# 6. Safety & Security Invariant Tests
# ===========================================================================

class TestSecurityInvariants:
    """Hard architectural security invariant verification using Python AST analysis."""

    def test_no_shell_true_in_source(self):
        """Invariant: Zero instances of shell=True anywhere in src/avi/ executable code."""
        avi_src = Path(__file__).resolve().parents[2] / "src" / "avi"
        py_files = list(avi_src.rglob("*.py"))
        assert len(py_files) > 0

        violations = []
        for py_file in py_files:
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    for keyword in node.keywords:
                        if keyword.arg == "shell":
                            if isinstance(keyword.value, ast.Constant) and keyword.value.value is True:
                                violations.append(f"{py_file}:{node.lineno}: shell=True in call")

        assert violations == [], f"Found shell=True call: {violations}"

    def test_no_os_system_in_source(self):
        """Invariant: Zero instances of os.system() anywhere in src/avi/."""
        avi_src = Path(__file__).resolve().parents[2] / "src" / "avi"
        py_files = list(avi_src.rglob("*.py"))

        violations = []
        for py_file in py_files:
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    func = node.func
                    if isinstance(func, ast.Attribute) and func.attr == "system":
                        if isinstance(func.value, ast.Name) and func.value.id == "os":
                            violations.append(f"{py_file}:{node.lineno}: os.system()")

        assert violations == [], f"Found os.system in: {violations}"

    def test_no_eval_or_exec_in_source(self):
        """Invariant: Zero instances of eval() or exec() anywhere in src/avi/."""
        avi_src = Path(__file__).resolve().parents[2] / "src" / "avi"
        py_files = list(avi_src.rglob("*.py"))

        violations = []
        for py_file in py_files:
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    func = node.func
                    if isinstance(func, ast.Name) and func.id in ("eval", "exec"):
                        violations.append(f"{py_file}:{node.lineno}: {func.id}()")

        assert violations == [], f"Found eval/exec in: {violations}"

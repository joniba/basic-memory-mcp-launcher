import importlib.util
import json
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "launcher.py"
spec = importlib.util.spec_from_file_location("launcher", SCRIPT)
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


class RuntimeTests(unittest.TestCase):
    def test_supported_versions(self):
        versions = {"basic-memory": "0.23.3.dev8", "mcp": "2.2.0", "fastmcp": "4.0.0b1"}
        with patch.object(launcher.metadata, "version", side_effect=versions.__getitem__):
            launcher.check_runtime()

    def test_wrong_mcp_major_is_explicit(self):
        versions = {"basic-memory": "0.23.2", "mcp": "1.26.0", "fastmcp": "4.0.0b1"}
        with patch.object(launcher.metadata, "version", side_effect=versions.__getitem__):
            with self.assertRaisesRegex(launcher.CompatibilityError, r"Requires mcp 2\.x"):
                launcher.check_runtime()

    def test_wrong_fastmcp_major_is_explicit(self):
        versions = {"basic-memory": "0.23.2", "mcp": "2.2.0", "fastmcp": "5.0.0"}
        with patch.object(launcher.metadata, "version", side_effect=versions.__getitem__):
            with self.assertRaisesRegex(launcher.CompatibilityError, r"Requires fastmcp 4\.x"):
                launcher.check_runtime()

    def test_old_python_is_rejected(self):
        with patch.object(launcher.sys, "version_info", (3, 11)):
            with self.assertRaisesRegex(launcher.CompatibilityError, "Python 3.12"):
                launcher.check_runtime()

    def test_missing_package_exits_without_starting(self):
        with (
            patch.object(
                launcher, "check_runtime",
                side_effect=launcher.metadata.PackageNotFoundError("basic-memory"),
            ),
            patch.object(launcher.sys, "argv", [str(SCRIPT)]),
            patch.object(launcher.sys, "stderr") as stderr,
        ):
            self.assertEqual(launcher.main(), 1)
            output = "".join(call.args[0] for call in stderr.write.call_args_list)
            self.assertIn("missing basic-memory", output)
            self.assertIn("installed Python environment", output)


class StdioPeer:
    def __init__(self, delay=0, dual_era=False):
        if dual_era:
            command = [
                sys.executable, "-c",
                "from basic_memory.config import init_mcp_logging; "
                "import basic_memory.mcp.tools, basic_memory.mcp.prompts, "
                "basic_memory.mcp.resources; "
                "from basic_memory.mcp.server import mcp; "
                "init_mcp_logging(); mcp.run(transport='stdio',show_banner=False)",
            ]
        elif delay:
            command = [
                sys.executable, "-c",
                "import runpy,sys,time; time.sleep(float(sys.argv[2])); "
                "sys.argv=[sys.argv[1]]; runpy.run_path(sys.argv[0],run_name='__main__')",
                str(SCRIPT), str(delay),
            ]
        else:
            command = [sys.executable, str(SCRIPT)]
        self.process = subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8", bufsize=1,
        )
        self.output = queue.Queue()
        self.pending = {}
        self.errors = []
        self.reader = threading.Thread(target=self.read_output, daemon=True)
        self.error_reader = threading.Thread(target=self.read_errors, daemon=True)
        self.reader.start()
        self.error_reader.start()

    def read_output(self):
        try:
            for line in self.process.stdout:
                self.output.put(json.loads(line))
        except json.JSONDecodeError as error:
            self.output.put(error)
        finally:
            self.output.put(None)

    def read_errors(self):
        for line in self.process.stderr:
            self.errors.append(line)

    def send(self, method, request_id=None, params=None):
        message = {"jsonrpc": "2.0", "method": method}
        if request_id is not None:
            message["id"] = request_id
        if params is not None:
            message["params"] = params
        self.process.stdin.write(json.dumps(message) + "\n")
        self.process.stdin.flush()

    def response(self, request_id, timeout=60):
        if request_id in self.pending:
            return self.pending.pop(request_id)
        deadline = time.monotonic() + timeout
        while True:
            message = self.output.get(timeout=max(0, deadline - time.monotonic()))
            if message is None:
                raise AssertionError("Server exited before replying:\n" + "".join(self.errors))
            if isinstance(message, Exception):
                raise AssertionError("Server emitted non-JSON protocol output") from message
            if message.get("id") == request_id:
                return message
            if "id" in message:
                self.pending[message["id"]] = message

    def initialize(self, request_id=1):
        self.send("initialize", request_id, {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "launcher-tests", "version": "1.0"},
        })
        response = self.response(request_id)
        if response.get("result", {}).get("protocolVersion") != "2025-11-25":
            raise AssertionError(response)
        self.send("notifications/initialized")
        return response["result"]

    def close(self):
        self.process.stdin.close()
        try:
            self.process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            self.process.wait(timeout=10)
            raise AssertionError("Server did not shut down on stdin EOF")
        finally:
            self.reader.join(timeout=5)
            self.error_reader.join(timeout=5)
            self.process.stdout.close()
            self.process.stderr.close()
        if self.process.returncode != 0:
            raise AssertionError(
                f"Server exited with {self.process.returncode}:\n" + "".join(self.errors)
            )


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.peer = StdioPeer()
        self.addCleanup(self.peer.close)

    def test_direct_initialize_tools_and_ping(self):
        result = self.peer.initialize()
        self.assertEqual(result["serverInfo"]["name"], "Basic Memory")
        self.peer.send("tools/list", 2, {})
        response = self.peer.response(2)
        tools = response["result"]["tools"]
        names = [tool["name"] for tool in tools]
        self.assertEqual(len(names), len(set(names)))
        self.assertTrue({"search_notes", "read_note", "write_note", "edit_note"} <= set(names))
        self.peer.send("ping", 3)
        self.assertEqual(self.peer.response(3)["result"], {})

    def test_discover_fallback_and_pipelined_requests(self):
        self.peer.send("server/discover", 0, {"_meta": {
            "io.modelcontextprotocol/protocolVersion": "2026-07-28",
            "io.modelcontextprotocol/clientCapabilities": {},
        }})
        self.peer.send("initialize", 1, {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "launcher-tests", "version": "1.0"},
        })
        self.peer.send("notifications/initialized")
        self.peer.send("tools/list", 2, {})
        discovery = self.peer.response(0)
        self.assertEqual(discovery["error"]["code"], -32601)
        self.assertEqual(self.peer.response(1)["result"]["protocolVersion"], "2025-11-25")
        self.assertGreater(len(self.peer.response(2)["result"]["tools"]), 0)

    def test_invalid_tool_call_is_an_error(self):
        self.peer.initialize()
        self.peer.send("tools/call", 2, {"name": "not-a-real-tool", "arguments": {}})
        response = self.peer.response(2)
        self.assertTrue("error" in response or response.get("result", {}).get("isError"))

    def test_preserves_registered_tool_schemas(self):
        normal = StdioPeer(dual_era=True)
        self.addCleanup(normal.close)
        self.peer.initialize()
        normal.initialize()
        self.peer.send("tools/list", 2, {})
        normal.send("tools/list", 2, {})
        actual = self.peer.response(2)["result"]["tools"]
        expected = normal.response(2)["result"]["tools"]
        self.assertEqual(
            sorted(actual, key=lambda tool: tool["name"]),
            sorted(expected, key=lambda tool: tool["name"]),
        )

    def test_eof_before_any_request(self):
        pass


class DelayedStartupTests(unittest.TestCase):
    def test_legacy_fallback_after_ten_seconds_on_the_same_stream(self):
        peer = StdioPeer(delay=12)
        self.addCleanup(peer.close)
        peer.send("server/discover", 0, {"_meta": {
            "io.modelcontextprotocol/protocolVersion": "2026-07-28",
            "io.modelcontextprotocol/clientCapabilities": {},
        }})
        with self.assertRaises(queue.Empty):
            peer.response(0, timeout=10)
        peer.send("initialize", 1, {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "launcher-tests", "version": "1.0"},
        })
        self.assertEqual(peer.response(0)["error"]["code"], -32601)
        self.assertEqual(peer.response(1)["result"]["protocolVersion"], "2025-11-25")
        peer.send("notifications/initialized")
        peer.send("tools/list", 2, {})
        self.assertGreater(len(peer.response(2)["result"]["tools"]), 0)


if __name__ == "__main__":
    unittest.main()

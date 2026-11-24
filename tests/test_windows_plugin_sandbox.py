"""Exercise the real Windows token and Job with harmless denied operations."""
import json
import socket
import sys
import threading

import pytest

from core.windows_plugin_sandbox import run_sandboxed


def test_sandbox_is_explicitly_unavailable_off_windows():
    if sys.platform == "win32":
        pytest.skip("The AppContainer fixture exercises this platform.")
    assert "unavailable" in run_sandboxed(b"raise RuntimeError", "fixture", {}, 1, threading.Event())


@pytest.mark.skipif(sys.platform != "win32", reason="Requires the Windows AppContainer API")
def test_appcontainer_restricts_host_file_network_credentials_and_children(tmp_path, monkeypatch):
    private_file = tmp_path / "private.txt"
    private_file.write_text("private fixture", encoding="utf-8")
    monkeypatch.setenv("MARK_SANDBOX_FIXTURE", "local-only test value")
    source = b"""
import json
import os
import socket
import subprocess
import sys

DECLARATION = {"name": "fixture"}

def denied(action):
    try:
        action()
    except OSError:
        return True
    return False

def run(args):
    return json.dumps({
        "host_read_denied": denied(lambda: open(args["path"], "rb").close()),
        "host_write_denied": denied(lambda: open(args["path"], "ab").close()),
        "sandbox_input_read_only": denied(lambda: open("request.json", "ab").close()),
        "network_denied": denied(lambda: socket.create_connection(("127.0.0.1", args["port"]), 2).close()),
        "child_denied": denied(lambda: subprocess.run([sys.executable, "-c", "pass"], check=True)),
        "environment_isolated": os.environ.get("MARK_SANDBOX_FIXTURE") is None,
    })
"""
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        result = run_sandboxed(
            source, "fixture", {"path": str(private_file), "port": listener.getsockname()[1]},
            12, threading.Event(),
        )
    assert not result.startswith("[FAILED]"), result
    assert all(json.loads(result).values()), result
    assert private_file.read_text(encoding="utf-8") == "private fixture"

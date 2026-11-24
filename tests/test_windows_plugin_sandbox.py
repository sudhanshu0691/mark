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
    assert "unavailable" in run_sandboxed(
        b"raise RuntimeError", "fixture", {}, 1, threading.Event()
    )


@pytest.mark.skipif(
    sys.platform != "win32", reason="Requires the Windows AppContainer API"
)
def test_appcontainer_restricts_host_file_network_credentials_and_children(
    tmp_path, monkeypatch
):
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
    import hashlib

    from core import plugins

    directory = tmp_path / "plugins"
    directory.mkdir()
    trust = tmp_path / "config"
    trust.mkdir()
    path = directory / "fixture.py"
    path.write_bytes(source)
    manifest = trust / "sandbox_plugins.json"
    manifest.write_text(
        json.dumps(
            {
                "fixture.py": {
                    "sha256": hashlib.sha256(source).hexdigest(),
                    "tool": {
                        "name": "fixture",
                        "description": "Harmless isolation fixture",
                        "parameters": {
                            "type": "OBJECT",
                            "properties": {
                                "path": {"type": "STRING"},
                                "port": {"type": "INTEGER"},
                            },
                            "required": ["path", "port"],
                        },
                    },
                    "timeout": 12,
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(plugins, "PLUGIN_DIR", directory)
    monkeypatch.setattr(plugins, "_TRUST_DIR", trust)
    manager = plugins.PluginManager(plugins.PluginContext())
    manager.load_all()
    assert manager.has_tool("fixture"), manager.errors
    assert json.loads(manager.discover())[0]["trust"] == "appcontainer"
    try:
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            arguments = {"path": str(private_file), "port": listener.getsockname()[1]}
            result = manager.run("fixture", arguments)
        manifest.unlink()
        assert manager.run("fixture", arguments).startswith("[FAILED]")
    finally:
        manager.stop()
    assert not result.startswith("[FAILED]"), result
    assert all(json.loads(result).values()), result
    assert private_file.read_text(encoding="utf-8") == "private fixture"

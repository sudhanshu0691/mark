"""Opt-in process execution for trusted, stateless plugins.

A process boundary permits termination of the worker, not an OS security
sandbox. Plugins still execute as the current user. No UI/tool bridge is passed.
"""
from __future__ import annotations
import base64
import contextlib
import importlib.util
import inspect
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time

MAX_MESSAGE_BYTES = 1024 * 1024

def _terminate(process):
    # Kill the process group on POSIX; Windows taskkill includes descendants.
    if os.name == "nt":
        try:
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=3, check=False)
        except (OSError, subprocess.TimeoutExpired):
            pass
    else:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
    if process.poll() is None:
        process.kill()
    process.wait(timeout=3)

def run_isolated(path, name, args, timeout, cancelled, *, source=None):
    if cancelled.is_set():
        return "[CANCELLED] Plugin execution cancelled before launch."
    root = Path(__file__).resolve().parent.parent
    # Preserve only runtime/OS necessities, not application API credentials.
    allowed = {"SYSTEMROOT", "WINDIR", "PATH", "PATHEXT", "TEMP", "TMP", "TMPDIR",
               "USERPROFILE", "HOME", "LOCALAPPDATA", "APPDATA", "SYSTEMDRIVE"}
    env = {k: v for k, v in os.environ.items() if k.upper() in allowed}
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        payload = json.dumps({
            "path": str(Path(path).resolve()), "name": name, "args": args,
            "source": base64.b64encode(source).decode("ascii") if source is not None else None,
        })
    except (TypeError, ValueError, RecursionError):
        return "[FAILED] Plugin request is not serializable."
    if len(payload.encode("utf-8")) > MAX_MESSAGE_BYTES:
        return "[FAILED] Plugin request exceeds size limit."
    with tempfile.TemporaryDirectory(prefix="mark-plugin-") as tmp:
        request = Path(tmp) / "request.json"
        response = Path(tmp) / "response.json"
        request.write_text(payload, encoding="utf-8")
        process = subprocess.Popen(
            [sys.executable, "-m", "core.plugin_process", str(request), str(response)],
            cwd=root, env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=(os.name != "nt"))
        deadline = time.monotonic() + timeout
        try:
            while process.poll() is None:
                if cancelled.is_set() or time.monotonic() >= deadline:
                    _terminate(process)
                    return "[UNVERIFIED] Plugin worker terminated; inspect any completed side effects before retrying."
                cancelled.wait(0.025)
            if process.returncode != 0 or not response.is_file():
                return "[FAILED] Isolated plugin worker crashed."
            if response.stat().st_size > MAX_MESSAGE_BYTES:
                return "[FAILED] Plugin result exceeds size limit."
            try:
                data = json.loads(response.read_text(encoding="utf-8"))
                if not isinstance(data, dict) or not isinstance(data.get("result"), str):
                    raise ValueError("Invalid response object")
                return data["result"]
            except (ValueError, KeyError, TypeError, OSError):
                return "[FAILED] Invalid plugin worker response."
        finally:
            if process.poll() is None:
                _terminate(process)

def _worker(request, response):
    from core.plugins import PluginContext
    from core.result import ActionResult
    data = json.loads(Path(request).read_text(encoding="utf-8"))
    spec = importlib.util.spec_from_file_location("mark_isolated_plugin", data["path"])
    if spec is None or spec.loader is None:
        raise ImportError("Invalid plugin")
    module = importlib.util.module_from_spec(spec)
    source = data.get("source")
    if source is None:
        spec.loader.exec_module(module)
    else:
        exec(compile(base64.b64decode(source, validate=True), data["path"], "exec"), module.__dict__)
    if getattr(module, "METADATA", {}).get("execution") != "process":
        raise ValueError("Plugin has not opted into isolated execution")
    tools = list(getattr(module, "TOOLS", []))
    if hasattr(module, "DECLARATION") and hasattr(module, "run"):
        tools.append({"declaration": module.DECLARATION, "run": module.run})
    fn = next(t["run"] for t in tools if t["declaration"]["name"] == data["name"])
    ctx = PluginContext()
    result = fn(data["args"], ctx) if len(inspect.signature(fn).parameters) >= 2 else fn(data["args"])
    out = result.to_llm() if isinstance(result, ActionResult) else str(result or "")
    payload = json.dumps({"result": out})
    if len(payload.encode("utf-8")) > MAX_MESSAGE_BYTES:
        payload = json.dumps({"result": "[FAILED] Plugin result exceeds size limit."})
    Path(response).write_text(payload, encoding="utf-8")

if __name__ == "__main__":
    _worker(sys.argv[1], sys.argv[2])

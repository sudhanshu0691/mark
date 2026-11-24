"""Run manifest-approved standalone plugins in a Windows AppContainer and Job."""
from __future__ import annotations

import ctypes as c
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from ctypes import wintypes as w
from pathlib import Path
from tempfile import TemporaryDirectory

from core.plugin_process import MAX_MESSAGE_BYTES


class _Capabilities(c.Structure):
    _fields_ = [("sid", c.c_void_p), ("capabilities", c.c_void_p),
                ("count", w.DWORD), ("reserved", w.DWORD)]


class _Startup(c.Structure):
    _fields_ = [
        ("cb", w.DWORD), ("reserved", w.LPWSTR), ("desktop", w.LPWSTR), ("title", w.LPWSTR),
        *((name, w.DWORD) for name in ("x", "y", "x_size", "y_size", "x_chars", "y_chars", "fill", "flags")),
        ("show", w.WORD), ("reserved_count", w.WORD), ("reserved_bytes", c.c_void_p),
        ("stdin", w.HANDLE), ("stdout", w.HANDLE), ("stderr", w.HANDLE),
        ("attributes", c.c_void_p),
    ]


class _ProcessInfo(c.Structure):
    _fields_ = [("process", w.HANDLE), ("thread", w.HANDLE), ("pid", w.DWORD), ("tid", w.DWORD)]


_WORKER = """
import inspect
import json
import sys

source, request = sys.argv[1:3]
namespace = {"__name__": "mark_sandboxed_plugin", "__file__": source}
with open(source, "rb") as stream:
    exec(compile(stream.read(), source, "exec"), namespace)
with open(request, encoding="utf-8") as stream:
    name, args = json.load(stream)
declaration = namespace.get("DECLARATION", {})
if declaration.get("name") == name:
    function = namespace["run"]
else:
    function = next(tool["run"] for tool in namespace.get("TOOLS", [])
                    if tool["declaration"]["name"] == name)
value = function(args, None) if len(inspect.signature(function).parameters) >= 2 else function(args)
sys.stdout.write(json.dumps({"result": str(value or "")}, ensure_ascii=True))
"""


def _windows_api():
    kernel = c.WinDLL("kernel32", use_last_error=True)
    userenv = c.WinDLL("userenv", use_last_error=True)
    advapi = c.WinDLL("advapi32", use_last_error=True)
    userenv.CreateAppContainerProfile.argtypes = (w.LPCWSTR, w.LPCWSTR, w.LPCWSTR, c.c_void_p, w.DWORD,
                                                   c.POINTER(c.c_void_p))
    userenv.CreateAppContainerProfile.restype = c.c_long
    userenv.DeleteAppContainerProfile.argtypes = (w.LPCWSTR,)
    advapi.ConvertSidToStringSidW.argtypes = (c.c_void_p, c.POINTER(w.LPWSTR))
    for function in (kernel.LocalFree, advapi.FreeSid):
        function.argtypes = (c.c_void_p,)
        function.restype = c.c_void_p
    kernel.InitializeProcThreadAttributeList.argtypes = (
        c.c_void_p, w.DWORD, w.DWORD, c.POINTER(c.c_size_t),
    )
    kernel.UpdateProcThreadAttribute.argtypes = (
        c.c_void_p, w.DWORD, c.c_size_t, c.c_void_p, c.c_size_t, c.c_void_p, c.c_void_p,
    )
    kernel.DeleteProcThreadAttributeList.argtypes = (c.c_void_p,)
    kernel.CreateProcessW.argtypes = (
        w.LPCWSTR, w.LPWSTR, c.c_void_p, c.c_void_p, w.BOOL, w.DWORD, c.c_void_p, w.LPCWSTR,
        c.POINTER(_Startup), c.POINTER(_ProcessInfo),
    )
    kernel.SetInformationJobObject.argtypes = (w.HANDLE, c.c_int, c.c_void_p, w.DWORD)
    kernel.SetHandleInformation.argtypes = (w.HANDLE, w.DWORD, w.DWORD)
    kernel.AssignProcessToJobObject.argtypes = (w.HANDLE, w.HANDLE)
    kernel.ResumeThread.argtypes = (w.HANDLE,)
    kernel.ResumeThread.restype = w.DWORD
    kernel.CloseHandle.argtypes = (w.HANDLE,)
    kernel.TerminateProcess.argtypes = (w.HANDLE, w.UINT)
    kernel.WaitForSingleObject.argtypes = (w.HANDLE, w.DWORD)
    kernel.GetExitCodeProcess.argtypes = (w.HANDLE, c.POINTER(w.DWORD))
    return kernel, userenv, advapi


def _require(value):
    if not value:
        raise c.WinError(c.get_last_error())


def _stage(root, source, payload):
    base = Path(sys.base_prefix)
    executable = base / "python.exe"
    if not executable.is_file() or not (base / "Lib" / "encodings").is_dir():
        raise RuntimeError("A standard Windows CPython installation is required.")
    for path in [executable, *base.glob("*.dll")]:
        shutil.copyfile(path, root / path.name)
    shutil.copytree(base / "Lib", root / "Lib", ignore=shutil.ignore_patterns(
        "site-packages", "__pycache__", "test", "tests", "ensurepip", "idlelib", "tkinter", "turtledemo",
    ))
    if (base / "DLLs").is_dir():
        shutil.copytree(base / "DLLs", root / "DLLs")
    (root / "worker.py").write_text(_WORKER, encoding="utf-8")
    (root / "plugin.py").write_bytes(source)
    (root / "request.json").write_bytes(payload)


def _job(kernel):
    import win32job

    job = win32job.CreateJobObject(None, "")
    try:
        settings = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
        basic = settings["BasicLimitInformation"]
        basic["LimitFlags"] = (
            win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | win32job.JOB_OBJECT_LIMIT_ACTIVE_PROCESS
            | win32job.JOB_OBJECT_LIMIT_PROCESS_MEMORY
        )
        basic["ActiveProcessLimit"] = 1
        settings["ProcessMemoryLimit"] = 256 * 1024 * 1024
        win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, settings)
        # CPU rate control: enable and cap at 25% of the system's available CPU.
        cpu = (w.DWORD * 2)(0x1 | 0x4, 2500)
        _require(kernel.SetInformationJobObject(int(job), 15, c.byref(cpu), c.sizeof(cpu)))
        return job
    except BaseException:
        job.Close()
        raise


def _run_process(kernel, root, sid, timeout, cancelled):
    import pywintypes
    import win32file
    import win32pipe

    security = pywintypes.SECURITY_ATTRIBUTES()
    security.bInheritHandle = True
    reader, writer = win32pipe.CreatePipe(security, 0)
    info = _ProcessInfo()
    job = None
    startup = _Startup()
    initialized = False
    try:
        _require(kernel.SetHandleInformation(int(reader), 1, 0))
        size = c.c_size_t()
        kernel.InitializeProcThreadAttributeList(None, 2, 0, c.byref(size))
        attributes = c.create_string_buffer(size.value)
        startup.attributes = c.cast(attributes, c.c_void_p)
        _require(kernel.InitializeProcThreadAttributeList(startup.attributes, 2, 0, c.byref(size)))
        initialized = True

        capabilities = _Capabilities(sid, None, 0, 0)
        handles = (w.HANDLE * 1)(int(writer))
        for key, value in ((0x00020009, capabilities), (0x00020002, handles)):
            _require(kernel.UpdateProcThreadAttribute(
                startup.attributes, 0, key, c.byref(value), c.sizeof(value), None, None,
            ))
        startup.cb = c.sizeof(startup)
        startup.flags = 0x100  # STARTF_USESTDHANDLES
        startup.stdin = startup.stdout = startup.stderr = int(writer)
        exe = root / "python.exe"
        command = c.create_unicode_buffer(subprocess.list2cmdline(
            [str(exe), "-I", "-S", "-B", "worker.py", "plugin.py", "request.json"],
        ))
        windows = os.environ.get("SystemRoot", r"C:\Windows")
        environment = c.create_unicode_buffer(f"PATH={windows}\\System32\0SystemRoot={windows}\0\0")
        job = _job(kernel)
        if cancelled.is_set():
            return "[CANCELLED] Plugin cancelled before launch."
        # Suspended and created with the AppContainer token from the first instruction.
        _require(kernel.CreateProcessW(
            str(exe), command, None, None, True, 0x08080404, environment, str(root),
            c.byref(startup), c.byref(info),
        ))
        writer.Close()
        writer = None
        _require(kernel.AssignProcessToJobObject(int(job), info.process))
        if kernel.ResumeThread(info.thread) == 0xFFFFFFFF:
            raise c.WinError(c.get_last_error())

        deadline = time.monotonic() + timeout
        output = bytearray()
        while True:
            if cancelled.is_set() or time.monotonic() >= deadline:
                return "[CANCELLED] AppContainer plugin stopped."
            completed = kernel.WaitForSingleObject(info.process, 0) == 0
            try:
                _, available, _ = win32pipe.PeekNamedPipe(reader, 0)
            except pywintypes.error as exc:
                if exc.winerror != 109:  # ERROR_BROKEN_PIPE
                    raise
                available = 0
            if available:
                output.extend(win32file.ReadFile(reader, min(available, 65_536))[1])
                if len(output) > MAX_MESSAGE_BYTES:
                    return "[FAILED] Plugin output exceeds the allowed size."
                continue
            if completed:
                break
            cancelled.wait(0.02)

        status = w.DWORD()
        _require(kernel.GetExitCodeProcess(info.process, c.byref(status)))
        if status.value != 0:
            return "[FAILED] AppContainer plugin failed."
        decoded = json.loads(output)
        if not isinstance(decoded, dict) or set(decoded) != {"result"} or not isinstance(decoded["result"], str):
            return "[FAILED] Invalid plugin response."
        return decoded["result"]
    finally:
        if info.process:
            kernel.TerminateProcess(info.process, 1)
            if job is not None:
                job.Close()
                job = None
            kernel.WaitForSingleObject(info.process, 3_000)
            kernel.CloseHandle(info.process)
        if info.thread:
            kernel.CloseHandle(info.thread)
        if job is not None:
            job.Close()
        if initialized:
            kernel.DeleteProcThreadAttributeList(startup.attributes)
        reader.Close()
        if writer is not None:
            writer.Close()


def run_sandboxed(source, name, args, timeout, cancelled):
    if sys.platform != "win32":
        return "[FAILED] Windows AppContainer sandbox is unavailable on this platform."
    payload = json.dumps([name, args], allow_nan=False).encode("utf-8")
    if not 0 < len(source) <= MAX_MESSAGE_BYTES or len(payload) > MAX_MESSAGE_BYTES:
        return "[FAILED] Plugin request exceeds its size budget."
    profile = "MARK.Plugin." + uuid.uuid4().hex
    sid = c.c_void_p()
    try:
        kernel, userenv, advapi = _windows_api()
        status = userenv.CreateAppContainerProfile(profile, profile, "MARK plugin", None, 0, c.byref(sid))
        if status != 0:
            raise OSError(f"AppContainer profile creation failed ({status:#x}).")
        sid_text = w.LPWSTR()
        try:
            _require(advapi.ConvertSidToStringSidW(sid, c.byref(sid_text)))
            with TemporaryDirectory(prefix="mark-appcontainer-") as directory:
                root = Path(directory)
                _stage(root, source, payload)
                executable = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "icacls.exe"
                permission = subprocess.run(
                    [str(executable), str(root), "/grant", f"*{sid_text.value}:(OI)(CI)(RX)", "/T", "/Q"],
                    capture_output=True, timeout=25, check=False,
                )
                if permission.returncode != 0:
                    raise RuntimeError("Could not make the staged runtime readable to the AppContainer.")
                return _run_process(kernel, root, sid, timeout, cancelled)
        finally:
            if sid_text:
                kernel.LocalFree(c.cast(sid_text, c.c_void_p))
            advapi.FreeSid(sid)
            userenv.DeleteAppContainerProfile(profile)
    except Exception as exc:
        return f"[FAILED] AppContainer isolation could not be established or maintained ({type(exc).__name__}: {exc})."

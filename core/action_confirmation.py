"""Independent, conservative confirmation of application and file opens."""
import os
import time
from pathlib import Path


def explorer_active_path():
    """Return foreground Explorer's filesystem location, or None when unavailable."""
    import pythoncom
    import win32com.client
    from actions import win_manager
    pythoncom.CoInitialize()
    try:
        active = win_manager.active_window()
        if not active:
            return None
        shell = win32com.client.Dispatch("Shell.Application")
        for window in shell.Windows():
            try:
                if int(window.HWND) == int(active["hwnd"]):
                    return window.Document.Folder.Self.Path
            except Exception:
                continue
        return None
    finally:
        pythoncom.CoUninitialize()


def confirm_result(tool, args, result, monitor=None, task_id=None, timeout=8.0, interval=0.25):
    action = str(args.get("action", "")).lower()
    if tool == "file_controller" and action != "open":
        return result
    if tool not in {"open_app", "file_controller"}:
        return result
    if monitor:
        monitor.task(task_id, {"tool": tool, "action": action})
    target = None
    if tool == "file_controller":
        from actions.fs_full import resolve
        try:
            target = resolve(args.get("path", ""), args.get("name", ""))
            if not target.exists():
                return "[FAILED] Cannot open target\nREASON: Path does not exist."
        except Exception as exc:
            return "[UNVERIFIED] Cannot resolve target\nREASON: " + type(exc).__name__
    app = str(args.get("app_name", "")).strip().lower()
    deadline = time.monotonic() + timeout
    detail = "No matching foreground window was observed."
    while True:
        try:
            from actions import win_manager
            active = win_manager.active_window()
            if target and target.is_dir():
                location = explorer_active_path()
                if location and os.path.normcase(os.path.normpath(location)) == os.path.normcase(os.path.normpath(str(target))):
                    detail = "Foreground Explorer location matches the requested folder."
                    break
            elif target and active:
                title = str(active.get("title", "")).lower()
                # A foreground document title is evidence of the file, not merely its existence.
                if target.name.lower() in title or (target.stem and target.stem.lower() in title):
                    detail = "Foreground window title identifies the requested file."
                    break
            elif app and active:
                text = (str(active.get("title", "")) + " " + str(active.get("exe", ""))).lower()
                if app in text:
                    detail = "Foreground window matches the requested application."
                    break
        except Exception as exc:
            detail = "Windows verification unavailable: " + type(exc).__name__
        if time.monotonic() >= deadline:
            if monitor:
                monitor.task(task_id, {"tool": tool, "action": action}, "UNVERIFIED")
            return "[UNVERIFIED] Action requested; success could not be confirmed.\nREASON: " + detail
        time.sleep(interval)
    if monitor:
        monitor.task(task_id, {"tool": tool, "action": action}, "PASS")
    return "[DONE] Action independently verified.\nEVIDENCE: " + detail

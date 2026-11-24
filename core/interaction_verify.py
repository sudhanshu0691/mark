"""Best-effort comparison of the foreground viewport for zoom and scroll."""
import time


def supports(tool, args):
    action = str(args.get("action", "")).lower()
    return ((tool == "computer_settings" and action in {"type_text", "write_on_screen", "type", "write"})
        or (tool == "computer_settings" and action in {
        "zoom_in", "zoom_out", "zoom_reset", "scroll_up", "scroll_down", "scroll_top", "scroll_bottom"})
        or (tool in {"computer_control", "browser_control"} and action == "scroll"))


def snapshot():
    try:
        from actions import win_manager
        import pyautogui
        window = win_manager.active_window()
        if not window or not window.get("hwnd"):
            return None
        import win32gui
        left, top, right, bottom = win32gui.GetWindowRect(window["hwnd"])
        if right <= left or bottom <= top:
            return None
        image = pyautogui.screenshot(region=(left, top, right - left, bottom - top)).convert("RGB").resize((160, 90))
        return window["hwnd"], image
    except Exception:
        return None



def text_snapshot():
    """Read only the focused editable control, without copying it to logs."""
    try:
        import pythoncom
        pythoncom.CoInitialize()
        try:
            from pywinauto import Desktop
            from actions import win_manager
            window = win_manager.active_window()
            if not window or not window.get("hwnd"):
                return None
            control = Desktop(backend="uia").get_focus()
            info = control.element_info
            if info.control_type not in {"Edit", "Document"}:
                return None
            element = info.element
            if bool(element.CurrentIsPassword):
                return None
            try:
                value = control.iface_value.CurrentValue
            except Exception:
                value = control.iface_text.DocumentRange.GetText(-1)
            if not isinstance(value, str):
                return None
            runtime = tuple(element.GetRuntimeId())
            return window["hwnd"], runtime, value
        finally:
            pythoncom.CoUninitialize()
    except Exception:
        return None


def verify_text(before, args, result, capture=None):
    if not str(result).startswith(("[DONE]", "[UNVERIFIED]")):
        return result
    expected = str(args.get("value") or args.get("text") or "").strip()
    if not expected or before is None:
        return "[UNVERIFIED] Typing could not be confirmed: focused text was unavailable before typing."
    time.sleep(0.3)
    after = (capture or text_snapshot)()
    if not after or before[:2] != after[:2]:
        return "[UNVERIFIED] Typing could not be confirmed: the focused text control changed or is unavailable."
    # Normalise Windows line endings only: fuzzy OCR or a pre-existing match is not proof.
    normalise = lambda text: text.replace("\r\n", "\n").replace("\r", "\n")
    expected, old, new = map(normalise, (expected, before[2], after[2]))
    if new.count(expected) > old.count(expected):
        return "[DONE] Typed text verified in the focused control.\nEVIDENCE: The expected text appeared in the same foreground control after typing."
    return "[UNVERIFIED] Typing could not be confirmed from the focused control."

def verify(before, args, result, capture=snapshot):
    if not str(result).startswith(("[DONE]", "[UNVERIFIED]")):
        return result
    time.sleep(0.3)
    after = capture()
    action = str(args.get("action", ""))
    label = "Zoom" if action.startswith("zoom") else "Scroll"
    if before and after and before[0] == after[0]:
        from PIL import ImageChops, ImageStat
        difference = sum(ImageStat.Stat(ImageChops.difference(before[1], after[1])).mean) / 3
        if difference >= 3:
            return f"[DONE] {label} viewport change observed.\nEVIDENCE: Foreground window remained active and its appearance changed."
    return f"[UNVERIFIED] {label} command execute ho gaya hai, lekin visual change confirm nahi hua."

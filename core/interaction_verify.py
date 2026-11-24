"""Best-effort comparison of the foreground viewport for zoom and scroll."""
import time


def supports(tool, args):
    action = str(args.get("action", "")).lower()
    return ((tool == "computer_settings" and action in {
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

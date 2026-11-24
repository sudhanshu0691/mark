"""Focused window selection and Windows off-screen Win32 capture coverage."""
import sys

import pytest

from core.streaming.window_capture import select_window


def test_selection_requires_one_exact_title(monkeypatch):
    monkeypatch.setattr("actions.win_manager.list_windows", lambda: [
        {"title": "Workbook", "hwnd": 12, "pid": 5},
        {"title": "Other Workbook", "hwnd": 17, "pid": 5},
    ])
    assert select_window("workbook") == (12, 5, "Workbook")
    with pytest.raises(ValueError, match="exact title"):
        select_window("book")


@pytest.mark.skipif(sys.platform != "win32", reason="Requires native Win32 window capture")
def test_win32_capture_rejects_changed_window_title():
    import os

    import win32gui

    from core.streaming.window_capture import _capture

    title = "MARK fixture window"
    hwnd = win32gui.CreateWindow(
        "STATIC", title, 0x10CF0000, 10, 10, 320, 180, 0, 0, 0, None,
    )
    try:
        image = _capture(hwnd, os.getpid(), title)
        assert image.mode == "RGB"
        assert image.width > 100 and image.height > 100
        win32gui.SetWindowText(hwnd, "A different window")
        with pytest.raises(RuntimeError, match="changed"):
            _capture(hwnd, os.getpid(), title)
    finally:
        win32gui.DestroyWindow(hwnd)

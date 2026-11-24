"""Capture a single approved Windows window without sampling the desktop."""

from __future__ import annotations

import io
import os
import struct
import subprocess
import sys
import threading

_MAX_PACKET = 24 * 1024 * 1024


def select_window(title):
    from actions.win_manager import list_windows

    matches = [
        item for item in list_windows() if item["title"].casefold() == title.casefold()
    ]
    if len(matches) != 1:
        raise ValueError("Specify the exact title of one visible window.")
    item = matches[0]
    return item["hwnd"], item["pid"], item["title"]


class WindowCapture:
    def __init__(self, target):
        self.target = target
        self._lock = threading.Lock()
        self._process = None
        self._closed = False

    def frame(self):
        from PIL import Image

        with self._lock:
            if self._closed:
                raise RuntimeError("Window sharing ended.")
            if self._process is None:
                hwnd, pid, title = self.target
                self._process = subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "core.streaming.window_capture",
                        str(hwnd),
                        str(pid),
                        title,
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    stdin=subprocess.DEVNULL,
                )
            output = self._process.stdout
        header = output.read(4)
        if len(header) != 4:
            raise RuntimeError("The approved window cannot be captured.")
        size = struct.unpack("!I", header)[0]
        if not 0 < size <= _MAX_PACKET:
            raise RuntimeError("The captured frame exceeds its packet budget.")
        image = output.read(size)
        if len(image) != size:
            raise RuntimeError("Window capture ended.")
        with Image.open(io.BytesIO(image)) as frame:
            return frame.convert("RGB")

    def close(self):
        with self._lock:
            self._closed = True
            process, self._process = self._process, None
        if process:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=3)
            process.stdout.close()


def _capture(hwnd, pid, title):
    import ctypes as c
    from ctypes import wintypes as w

    import win32gui
    import win32process
    from PIL import Image

    def matches_target():
        return (
            win32gui.IsWindow(hwnd)
            and win32gui.IsWindowVisible(hwnd)
            and not win32gui.IsIconic(hwnd)
            and win32process.GetWindowThreadProcessId(hwnd)[1] == pid
            and win32gui.GetWindowText(hwnd) == title
        )

    if not matches_target():
        raise RuntimeError("The approved window changed or closed.")
    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    width, height = right - left, bottom - top
    if width <= 0 or height <= 0 or width * height > 12_000_000:
        raise ValueError("Invalid window dimensions.")

    class BitmapHeader(c.Structure):
        _fields_ = [
            ("size", w.DWORD),
            ("width", w.LONG),
            ("height", w.LONG),
            ("planes", w.WORD),
            ("bits", w.WORD),
            ("compression", w.DWORD),
            ("image_size", w.DWORD),
            ("x_dpi", w.LONG),
            ("y_dpi", w.LONG),
            ("colors", w.DWORD),
            ("important", w.DWORD),
        ]

    gdi = c.windll.gdi32
    print_window = c.windll.user32.PrintWindow
    print_window.argtypes = (w.HWND, w.HDC, w.UINT)
    gdi.CreateCompatibleDC.argtypes = (w.HDC,)
    gdi.CreateCompatibleDC.restype = w.HDC
    gdi.CreateDIBSection.argtypes = (
        w.HDC,
        c.POINTER(BitmapHeader),
        w.UINT,
        c.POINTER(c.c_void_p),
        w.HANDLE,
        w.DWORD,
    )
    gdi.CreateDIBSection.restype = w.HBITMAP
    gdi.SelectObject.argtypes = (w.HDC, w.HGDIOBJ)
    gdi.SelectObject.restype = w.HGDIOBJ
    gdi.DeleteObject.argtypes = (w.HGDIOBJ,)
    gdi.DeleteDC.argtypes = (w.HDC,)
    device = gdi.CreateCompatibleDC(None)
    if not device:
        raise RuntimeError("The window capture device could not be created.")
    bitmap = previous = None
    try:
        header = BitmapHeader(c.sizeof(BitmapHeader), width, -height, 1, 32)
        pixels = c.c_void_p()
        bitmap = gdi.CreateDIBSection(
            device, c.byref(header), 0, c.byref(pixels), None, 0
        )
        if not bitmap or not pixels:
            raise RuntimeError("The window capture buffer could not be created.")
        previous = gdi.SelectObject(device, bitmap)
        if not previous or previous == c.c_void_p(-1).value:
            previous = None
            raise RuntimeError("The window capture buffer could not be selected.")
        size = width * height * 4
        c.memset(pixels, 0, size)
        if (
            not (print_window(hwnd, device, 2) or print_window(hwnd, device, 0))
            or not matches_target()
        ):
            raise RuntimeError("The approved window cannot be captured.")
        return Image.frombytes(
            "RGB", (width, height), c.string_at(pixels, size), "raw", "BGRX"
        )
    finally:
        if previous:
            gdi.SelectObject(device, previous)
        if bitmap:
            gdi.DeleteObject(bitmap)
        gdi.DeleteDC(device)


if __name__ == "__main__":
    import time

    if os.name != "nt":
        raise SystemExit(1)
    hwnd, pid, title = sys.argv[1:]
    output = sys.stdout.buffer
    while True:
        frame = _capture(int(hwnd), int(pid), title)
        frame.thumbnail((1920, 1080))
        buffer = io.BytesIO()
        frame.save(buffer, format="PNG", compress_level=1)
        payload = buffer.getvalue()
        output.write(struct.pack("!I", len(payload)) + payload)
        output.flush()
        time.sleep(0.025)

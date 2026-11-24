"""Small, independent Tk orb for MARK. All Tk calls stay on its own thread."""
import ctypes
import gc
import math
import queue
import threading
import time
import tkinter as tk
from ctypes import wintypes

STATES = {"idle": ((20, 125, 255), 0.7),
          "listening": ((20, 235, 105), 1.2),
          "speaking": ((190, 65, 255), 2.0)}
KEY = "#010203"


class Orb:
    def __init__(self, on_restore=None, visible=True):
        self._commands = queue.SimpleQueue()
        self._on_restore = on_restore
        self._closed = threading.Event()
        self._activity_lock = threading.Lock()
        self._activities = {}
        self._thread = threading.Thread(target=self._run, name="mark-orb", daemon=True)
        self._thread.start()
        if visible:
            self.show()

    def set_state(self, state):
        if state not in STATES:
            raise ValueError("Unknown orb state: " + str(state))
        if not self._closed.is_set():
            self._commands.put(("state", state))

    def close(self):
        self._commands.put(("close", None))
        if threading.current_thread() is not self._thread:
            self._thread.join(timeout=2)

    def show(self):
        if not self._closed.is_set():
            self._commands.put(("visibility", True))

    def hide(self):
        if not self._closed.is_set():
            self._commands.put(("visibility", False))

    def set_activity(self, source, state):
        """Combine independent microphone and speech activity; speech wins."""
        with self._activity_lock:
            if state is None:
                self._activities.pop(source, None)
            elif state in ("listening", "speaking"):
                self._activities[source] = state
            else:
                raise ValueError("Unknown activity: " + str(state))
            values = self._activities.values()
            self.set_state("speaking" if "speaking" in values else
                           "listening" if "listening" in values else "idle")

    def _run(self):
        try:
            # Set DPI awareness on this thread only; do not change Qt's process settings.
            try:
                ctypes.windll.user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
            except (AttributeError, OSError):
                pass
            root = tk.Tk()
            root.withdraw()
            root.overrideredirect(True)
            root.attributes("-topmost", True)
            root.configure(bg=KEY)
            root.attributes("-transparentcolor", KEY)
            canvas = tk.Canvas(root, bg=KEY, highlightthickness=0, bd=0)
            canvas.pack(fill="both", expand=True)
            menu = tk.Menu(root, tearoff=False)
            menu.add_command(label="Close", command=root.destroy)
            canvas.bind("<Button-3>", lambda e: menu.tk_popup(e.x_root, e.y_root))
            state, mini, phase = "idle", False, 0.0
            click_timer = None
            position = None
            drag_start = None
            dragged = False
            double_click = False
            last = time.monotonic()

            def place():
                nonlocal position
                size = 38 if mini else 150
                if position is None:
                    rect = wintypes.RECT()
                    if ctypes.windll.user32.SystemParametersInfoW(0x30, 0, ctypes.byref(rect), 0):
                        right, bottom = rect.right, rect.bottom
                    else:
                        right, bottom = root.winfo_screenwidth(), root.winfo_screenheight()
                    position = (right - size - 12, bottom - size - 12)
                root.geometry(f"{size}x{size}")
                root.update_idletasks()
                # Tk geometry treats negative coordinates as offsets from the far edge;
                # Windows coordinates also support monitors left/above the primary one.
                user32 = ctypes.windll.user32
                user32.GetParent.argtypes = [wintypes.HWND]
                user32.GetParent.restype = wintypes.HWND
                user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int,
                                                ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
                hwnd = user32.GetParent(root.winfo_id()) or root.winfo_id()
                user32.SetWindowPos(hwnd, None, position[0], position[1], 0, 0, 0x0001 | 0x0004 | 0x0010)

            def toggle(_event):
                nonlocal mini, click_timer, double_click
                double_click = True
                if click_timer is not None:
                    root.after_cancel(click_timer)
                    click_timer = None
                old_size = 38 if mini else 150
                mini = not mini
                new_size = 38 if mini else 150
                position_offset = (old_size - new_size) // 2
                nonlocal position
                position = (position[0] + position_offset, position[1] + position_offset)
                place()

            def press(event):
                nonlocal drag_start, dragged, double_click
                drag_start = (event.x_root, event.y_root, *position)
                dragged = False
                double_click = False

            def move(event):
                nonlocal position, dragged, click_timer
                if drag_start is None:
                    return
                dx, dy = event.x_root - drag_start[0], event.y_root - drag_start[1]
                if not dragged and max(abs(dx), abs(dy)) < 5:
                    return
                dragged = True
                if click_timer is not None:
                    root.after_cancel(click_timer)
                    click_timer = None
                position = (drag_start[2] + dx, drag_start[3] + dy)
                place()

            def release(_event):
                nonlocal click_timer, drag_start
                drag_start = None
                if not dragged and not double_click and self._on_restore is not None:
                    def restore():
                        nonlocal click_timer
                        click_timer = None
                        self._on_restore()
                    click_timer = root.after(ctypes.windll.user32.GetDoubleClickTime() + 50, restore)

            canvas.bind("<ButtonPress-1>", press)
            canvas.bind("<B1-Motion>", move)
            canvas.bind("<ButtonRelease-1>", release)
            canvas.bind("<Double-Button-1>", toggle)
            place()

            def frame():
                nonlocal state, phase, last
                while True:
                    try:
                        command, value = self._commands.get_nowait()
                    except queue.Empty:
                        break
                    if command == "close":
                        root.destroy()
                        return
                    if command == "visibility":
                        if value:
                            place()
                            root.deiconify()
                            root.attributes("-topmost", True)
                        else:
                            root.withdraw()
                    elif command == "state":
                        state = value
                now = time.monotonic()
                rgb, speed = STATES[state]
                phase += min(now - last, 0.2) * speed
                last = now
                size = 38 if mini else 150
                center = size / 2
                canvas.delete("all")

                def color(factor):
                    return "#%02x%02x%02x" % tuple(max(4, min(255, int(c * factor))) for c in rgb)

                def circle(radius, **kwargs):
                    canvas.create_oval(center-radius, center-radius, center+radius, center+radius, **kwargs)

                if not mini:
                    for offset in (0, 0.33, 0.66):
                        progress = (phase * 0.25 + offset) % 1
                        circle(24 + progress * 47, outline=color(0.7 * (1-progress) + 0.04), width=2)
                pulse = 0.5 + 0.5 * math.sin(phase * 3)
                radius = (8 if mini else 23) * (0.94 + pulse * 0.06)
                for scale, brightness in ((1.7, 0.10), (1.4, 0.20), (1.15, 0.36), (1, 0.72), (0.7, 1.0)):
                    circle(radius * scale, fill=color(brightness * (0.8 + 0.2*pulse)), outline="")
                root.after(34, frame)

            frame()
            root.mainloop()
            # Tcl objects must be finalized on the thread that created them.
            del frame, press, move, release, toggle, place, menu, canvas, root
            gc.collect()
        except Exception as exc:
            print(f"[MARK] Orb unavailable: {exc}")
        finally:
            self._closed.set()


def start_orb(on_restore=None, visible=True):
    return Orb(on_restore=on_restore, visible=visible)

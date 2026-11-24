import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
from actions.camera_source import CameraSource


def test_shared_capture_independent_consumers(monkeypatch):
    opened = []
    class Capture:
        def __init__(self, *args):
            self.release = Mock()
            opened.append(self)
        def isOpened(self):
            return True
        def read(self):
            return True, np.zeros((2, 2, 3), dtype=np.uint8)
    monkeypatch.setitem(sys.modules, 'cv2', SimpleNamespace(VideoCapture=Capture, CAP_DSHOW=1, CAP_ANY=0))
    source = CameraSource()
    seen_a, seen_b = threading.Event(), threading.Event()
    source.subscribe('monitor', lambda frame: seen_a.set(), lambda error: None)
    source.subscribe('gesture', lambda frame: seen_b.set(), lambda error: None)
    try:
        assert seen_a.wait(2) and seen_b.wait(2)
        assert len(opened) == 1
        source.unsubscribe('monitor')
        seen_b.clear()
        assert seen_b.wait(2)
        opened[0].release.assert_not_called()
    finally:
        source.unsubscribe('gesture')
    for _ in range(100):
        if opened[0].release.called:
            break
        time.sleep(.01)
    opened[0].release.assert_called_once()


def test_camera_error_notifies_both_consumers(monkeypatch):
    ready = threading.Event()
    errors = []
    class Capture:
        def __init__(self, *args):
            pass
        def isOpened(self):
            ready.wait(1)
            return False
        def release(self):
            pass
    monkeypatch.setitem(sys.modules, 'cv2', SimpleNamespace(VideoCapture=Capture, CAP_DSHOW=1, CAP_ANY=0))
    source = CameraSource()
    source.subscribe('monitor', lambda frame: None, lambda error: errors.append(('monitor', error)))
    source.subscribe('gesture', lambda frame: None, lambda error: errors.append(('gesture', error)))
    ready.set()
    for _ in range(100):
        if len(errors) == 2:
            break
        time.sleep(.01)
    assert {name for name, _ in errors} == {'monitor', 'gesture'}
    assert all('could not be opened' in error for _, error in errors)
    source.unsubscribe('monitor')
    source.unsubscribe('gesture')

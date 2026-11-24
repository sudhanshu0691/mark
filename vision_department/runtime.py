"""Shared runtime instance used by MARK's plugin tools and dashboard."""
from __future__ import annotations

import threading

from .manager import VisionDepartment

_lock = threading.RLock()
_department: VisionDepartment | None = None


def get_department(**callbacks) -> VisionDepartment:
    global _department
    with _lock:
        if _department is None:
            _department = VisionDepartment(**callbacks)
        else:
            _department.set_callbacks(**callbacks)
        return _department


def shutdown() -> None:
    with _lock:
        if _department is not None:
            _department.stop()

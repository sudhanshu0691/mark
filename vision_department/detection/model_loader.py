"""Loads only the configured local checkpoint; never downloads weights."""
from __future__ import annotations

from pathlib import Path
from typing import Any


def load_model(path: Path, device: str = "auto") -> Any:
    if not path.is_file():
        raise FileNotFoundError(f"Configured vision model does not exist: {path}")
    if path.suffix.lower() != ".pt":
        raise ValueError(f"Expected a local Ultralytics .pt model, got: {path.suffix or '(no extension)'}")
    try:
        from ultralytics import YOLO
    except ImportError as exc:
        raise RuntimeError("Install vision dependencies with uv sync --project tooling --extra vision") from exc
    # Loading is local only. Ultralytics is passed a concrete existing path.
    model = YOLO(str(path))
    names = getattr(model, "names", None)
    if not isinstance(names, (dict, list, tuple)) or not names:
        raise ValueError("The checkpoint did not provide a readable class-name mapping")
    if device == "auto":
        import torch
        target_device = "cuda:0" if torch.cuda.is_available() else "cpu"
    else:
        target_device = f"cuda:{device}" if str(device).isdigit() else device
    model.to(target_device)
    model._mark_runtime_device = target_device
    return model


def class_names(model: Any) -> dict[int, str]:
    raw = model.names
    return {int(k): str(v) for k, v in raw.items()} if isinstance(raw, dict) else {i: str(v) for i, v in enumerate(raw)}

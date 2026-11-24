"""Hardware-aware model manager for MARK."""

from __future__ import annotations

import os
from typing import Any, Dict, Optional


class HardwareAwareModelManager:
    """Select optimal models based on available hardware resources."""

    def __init__(self):
        self.cpu_count: Optional[int] = None
        self.total_ram_gb: Optional[int] = None
        self.gpu_available: bool = False
        self.gpu_memory_gb: Optional[int] = None
        self._detect_hardware()

    def _detect_hardware(self) -> None:
        """Detect available CPU, RAM, and GPU resources."""
        try:
            import psutil

            # CPU
            try:
                self.cpu_count = psutil.cpu_count(logical=False) or 1
            except Exception:
                self.cpu_count = 1

            # RAM
            try:
                self.total_ram_gb = round(psutil.virtual_memory().total / (1024 ** 3))
            except Exception:
                self.total_ram_gb = 8

            # GPU (NVIDIA)
            try:
                import pynvml
                pynvml.nvmlInit()
                count = pynvml.nvmlDeviceGetCount()
                if count > 0:
                    handle = pynvml.nvmlDeviceGetHandleByIndex(0)
                    info = pynvml.nvmlDeviceGetMemoryInfo(handle)
                    self.gpu_memory_gb = round(info.total / (1024 ** 3))
                    self.gpu_available = True
                pynvml.nvmlShutdown()
            except Exception:
                self.gpu_available = False
                self.gpu_memory_gb = 0
        except ImportError:
            # psutil not available - use defaults
            self.cpu_count = 4
            self.total_ram_gb = 16
            self.gpu_available = False
            self.gpu_memory_gb = 0

    def recommend_model(self, role: str, preferred: Optional[str] = None) -> str:
        """Recommend the best model for a given role based on hardware."""
        from core.llm import load_config

        cfg = load_config()
        model_map = {
            "text": cfg.get("text_model", "qwen3:4b"),
            "code": cfg.get("code_model", "qwen3:4b"),
            "fast": cfg.get("fast_model", "qwen3:4b"),
            "vision": cfg.get("vision_model", "qwen2.5vl:3b"),
        }

        base_model = model_map.get(role, preferred or "qwen3:4b")

        # Adjust based on RAM
        if self.total_ram_gb and self.total_ram_gb < 16:
            # Small RAM: prefer smaller models
            if role == "text" and "4b" in base_model:
                base_model = base_model.replace("4b", "3b")
            elif role == "vision" and "3b" in base_model:
                base_model = base_model.replace("3b", "1.5b")
        elif self.total_ram_gb and self.total_ram_gb < 8:
            # Very small RAM
            if role == "text" and "4b" in base_model:
                base_model = base_model.replace("4b", "1b")
            elif role == "vision" and "3b" in base_model:
                base_model = base_model.replace("3b", "0.5b")
        elif self.gpu_available and self.gpu_memory_gb and self.gpu_memory_gb < 4:
            # Limited GPU
            if "7b" in base_model or "8b" in base_model:
                base_model = base_model.replace("7b", "3b").replace("8b", "3b")

        return base_model
# core/inference/ollama.py
# Ollama inference provider for MARK

from __future__ import annotations

from typing import Dict, Any, Optional, List
import asyncio
import json
import logging
import os
import subprocess
import shutil
import urllib.request
import urllib.error

from core.inference.base import InferenceProvider
from core.llm import LLMUnavailable, _post, load_config, model_for

logger = logging.getLogger(__name__)


class OllamaProvider(InferenceProvider):
    """Ollama inference provider implementation."""

    def __init__(self, cfg: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(cfg)
        self.host = "http://127.0.0.1:11434"
        self.models: list = []
        self._loaded_models: set = set()

    async def generate(self, messages: List[dict], **kwargs: Any) -> dict:
        """Generate a response from Ollama model."""
        cfg = load_config()
        host = cfg.get("host", "http://127.0.0.1:11434")
        model = kwargs.get("model") or model_for("text", cfg)
        temperature = kwargs.get("temperature")
        max_tokens = kwargs.get("max_tokens")
        json_mode = kwargs.get("json_mode", False)
        think = kwargs.get("think", False)

        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
            "keep_alive": cfg.get("keep_alive", "30m"),
            "options": {"num_ctx": int(cfg.get("num_ctx", 8192))},
            "think": think,
        }

        if temperature is not None:
            payload["options"]["temperature"] = float(temperature)
        payload["options"]["num_predict"] = int(max_tokens or cfg.get("local_max_output_tokens", 384))

        if tools := kwargs.get("tools"):
            byte_budget = int(cfg.get("local_tool_schema_bytes", 5200))
            selected = self._select_tools(messages, tools, byte_budget)
            if selected:
                payload["tools"] = selected

        if json_mode:
            payload["format"] = "json"

        try:
            data = await asyncio.to_thread(_post, "/api/chat", payload)
            if not isinstance(data, dict) or not isinstance(data.get("message"), dict):
                raise LLMUnavailable("Ollama returned a malformed chat response.")
            return data["message"]
        except LLMUnavailable:
            raise
        except Exception as e:
            raise LLMUnavailable(f"Ollama chat error: {e}")

    async def chat(self, messages: List[dict], **kwargs: Any) -> dict:
        """Chat-style generation with tool support."""
        return await self.generate(messages, **kwargs)

    def get_capabilities(self) -> dict[str, bool]:
        """Get provider capabilities."""
        return {
            "supports_tools": True,
            "supports_vision": True,
            "supports_streaming": True,
            "supports_json": True,
        }

    async def cancel_request(self) -> None:
        """Cancel an in-progress request."""
        pass

    async def _ensure_server(self) -> None:
        """Ensure Ollama server is running."""
        import os
        import subprocess
        import shutil

        host = self.host

        async def reachable():
            try:
                with urllib.request.urlopen(host + "/api/tags", timeout=0.5) as response:
                    return response.status == 200
            except Exception:
                return False

        # Check if already reachable
        if await asyncio.to_thread(lambda: reachable() if False else reachable()):
            return

        # Try to start Ollama
        executable = shutil.which("ollama")
        if not executable:
            candidate = os.environ.get("LOCALAPPDATA", "") + "\\Programs\\Ollama\\ollama.exe"
            executable = candidate if os.path.isfile(candidate) else None

        if not executable:
            logger.warning("Ollama executable not found, cannot start server")
            return

        proc = subprocess.Popen(
            [executable, "serve"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env={**os.environ, "OLLAMA_HOST": host},
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )

        deadline = __import__("time").monotonic() + 10
        while __import__("time").monotonic() < deadline:
            if await asyncio.to_thread(lambda: reachable() if False else reachable()) or proc.poll() is not None:
                break
            await asyncio.sleep(0.25)
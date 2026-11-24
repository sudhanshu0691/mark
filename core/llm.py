"""
core/llm.py - the ONE place Mark talks to a language model.

Default routing: local Ollama for text and tools.
Gemini remains an explicit alternative backend for text and live voice.

Every file that used to call google.genai now calls this module instead, through a
drop-in client that looks like the old one:

    from core import llm
    client = llm.Client()
    resp   = client.models.generate_content(model="role:text", contents="hi")
    print(resp.text)

Model "roles" (so no file hard-codes a model name any more):
    role:text    general reasoning, planning, summaries
    role:code    writing / fixing code
    role:fast    tiny, quick classifier-style questions
    role:vision  anything with an image in it (chosen automatically when images are present)
Old Gemini model names ("gemini-3.8-flash", "...-lite") are still accepted and mapped to a role.

Settings live in config/llm.json (created on first run). Environment variables
MARK_LLM_BACKEND overrides the configured provider.
"""
from __future__ import annotations

import base64
import asyncio
from contextlib import contextmanager
import io
import json
import os
import re
import shutil
import subprocess
import urllib.request
from urllib.parse import urlsplit
import ipaddress
import threading
import time
import random
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

try:
    from dotenv import load_dotenv
    _BASE_DIR = Path(__file__).resolve().parent.parent
    load_dotenv(_BASE_DIR / "config" / "runtime.env", override=False)
    load_dotenv(_BASE_DIR / ".env", override=False)
except ImportError:
    pass

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = BASE_DIR / "config" / "llm.json"

DEFAULTS: dict[str, Any] = {
    "profile": "balanced",
    "backend": "ollama",
    "host": "http://127.0.0.1:11434",
    "text_model": "qwen3:4b", "code_model": "qwen3:4b",
    "fast_model": "qwen3:4b", "vision_model": "qwen2.5vl:3b",
    "num_ctx": 4096, "timeout_s": 60, "keep_alive": "30m",
    "web_results": 6,
    "gemini": {"text_model": "gemini-flash-lite-latest", "live_model": "gemini-2.5-flash-native-audio-latest",
               "memory_model": "gemini-flash-lite-latest", "timeout_s": 60,
               "max_retries": 2, "retry_backoff_s": [2, 5], "health_cache_s": 30},
}

_ENV = {"backend": "MARK_LLM_BACKEND", "host": "OLLAMA_HOST", "text_model": "MARK_TEXT_MODEL", "code_model": "MARK_CODE_MODEL", "fast_model": "MARK_FAST_MODEL", "vision_model": "MARK_VISION_MODEL"}


from core import providers


class LLMUnavailable(RuntimeError):
    """The model server is not reachable or the model is not installed."""


_cfg_lock = threading.Lock()
_cfg_cache: dict[str, Any] | None = None
_provider_listener: Callable[[str], None] | None = None
def set_provider_listener(listener: Callable[[str], None] | None) -> None:
    """Set the UI callback used to show the provider handling the current request."""
    global _provider_listener
    _provider_listener = listener


def _set_active_provider(provider: str) -> None:
    listener = _provider_listener
    if listener is not None:
        try:
            listener(provider)
        except Exception as exc:
            print(f"[llm] provider status update failed: {type(exc).__name__}: {exc}")


_active_requests: list[str] = []
_active_requests_lock = threading.RLock()


@contextmanager
def _provider_request(provider: str):
    with _active_requests_lock:
        _active_requests.append(provider)
        _set_active_provider(provider)
    try:
        yield
    finally:
        with _active_requests_lock:
            _active_requests.remove(provider)
            _set_active_provider(_active_requests[-1] if _active_requests else "sleep")


def _run_provider(provider: str, function, *args):
    if provider == "gemini" and load_config().get("profile") == "offline":
        raise LLMUnavailable("Cloud LLM requests are disabled in offline profile.")
    with _provider_request(provider):
        return function(*args)


def load_config(refresh: bool = False) -> dict[str, Any]:
    global _cfg_cache
    with _cfg_lock:
        if _cfg_cache is not None and not refresh:
            return _cfg_cache
        cfg = json.loads(json.dumps(DEFAULTS))
        if CONFIG_PATH.exists():
            try:
                loaded = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
                if isinstance(loaded.get("gemini"), dict):
                    loaded["gemini"] = {**cfg["gemini"], **loaded["gemini"]}
                cfg.update(loaded)
            except Exception as e:  # a broken file must not kill the assistant
                print(f"[llm] config/llm.json unreadable ({e}); using defaults")
        else:
            try:
                CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
                CONFIG_PATH.write_text(json.dumps(DEFAULTS, indent=2), encoding="utf-8")
            except OSError:
                pass
        for key, env in _ENV.items():
            if os.environ.get(env):
                cfg[key] = os.environ[env]
        host = str(cfg["host"]).strip()
        cfg["host"] = (host if host.startswith("http") else "http://" + host).rstrip("/")
        cfg["profile"] = os.getenv("MARK_PROFILE", cfg.get("profile", "balanced")).strip().lower()
        if cfg["profile"] == "offline":
            cfg["backend"] = "ollama"
        if cfg.get("backend") not in {"ollama", "gemini", "multi_provider"}:
            raise LLMUnavailable("Unsupported LLM backend. Choose ollama or gemini.")
        _cfg_cache = cfg
        return cfg


# --------------------------------------------------------------------------- helpers
class _Part(SimpleNamespace):
    pass


def _part(text: str | None = None, function_call: Any = None) -> _Part:
    return _Part(text=text, function_call=function_call)


def _response(text: str, calls: list[tuple[str, dict]] | None = None) -> SimpleNamespace:
    parts: list[_Part] = []
    if text:
        parts.append(_part(text=text))
    for name, args in calls or []:
        parts.append(_part(function_call=SimpleNamespace(name=name, args=args)))
    content = SimpleNamespace(parts=parts, role="model")
    return SimpleNamespace(text=text, candidates=[SimpleNamespace(content=content)])


class _Types:
    """Tiny stand-in for google.genai.types so call sites keep their shape."""

    class Part:
        @staticmethod
        def from_bytes(data: bytes, mime_type: str = "image/png") -> dict:
            return {"mime_type": mime_type, "data": data}

    @staticmethod
    def GenerateContentConfig(**kw) -> dict:
        return dict(kw)


types = _Types()


def _cfg_get(config: Any, key: str, default: Any = None) -> Any:
    if config is None:
        return default
    if isinstance(config, dict):
        return config.get(key, default)
    return getattr(config, key, default)


def role_for(model: str | None, has_images: bool = False) -> str:
    name = (model or "").lower()
    if has_images:
        return "vision"
    if name.startswith("role:"):
        return name.split(":", 1)[1]
    if "lite" in name or "flash-8b" in name:
        return "fast"
    if "coder" in name or "code" in name:
        return "code"
    return "text"


def model_for(role: str, cfg: dict | None = None) -> str:
    cfg = cfg or load_config()
    return cfg.get(f"{role}_model") or cfg["text_model"]


def _png_bytes(img: Any) -> bytes:
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG")
    return buf.getvalue()


def _normalise(contents: Any) -> tuple[str, list[bytes]]:
    """Flatten genai-style `contents` into (text, [image bytes])."""
    texts: list[str] = []
    images: list[bytes] = []

    def add(item: Any) -> None:
        if item is None:
            return
        if isinstance(item, str):
            texts.append(item)
        elif isinstance(item, (list, tuple)):
            for sub in item:
                add(sub)
        elif isinstance(item, dict) and "data" in item:
            mime = str(item.get("mime_type", ""))
            if mime.startswith("image/"):
                images.append(bytes(item["data"]))
            elif mime.startswith("audio/"):
                raise LLMUnavailable("Audio goes through core.stt (faster-whisper), not the text model.")
            else:
                texts.append(bytes(item["data"]).decode("utf-8", "replace"))
        elif isinstance(item, dict) and "text" in item:
            texts.append(str(item["text"]))
        elif hasattr(item, "inline_data") and getattr(item, "inline_data", None) is not None:
            images.append(bytes(item.inline_data.data))           # google Part
        elif hasattr(item, "text") and isinstance(getattr(item, "text"), str):
            texts.append(item.text)
        elif hasattr(item, "parts"):
            add(list(item.parts))
        elif hasattr(item, "save") and hasattr(item, "convert"):   # PIL image
            images.append(_png_bytes(item))
        else:
            texts.append(str(item))

    add(contents)
    return "\n\n".join(t for t in texts if t), images


def _lower_types(schema: Any) -> Any:
    """genai declarations use OBJECT/STRING; the provider uses JSON-schema lowercase."""
    if isinstance(schema, dict):
        out = {}
        for k, v in schema.items():
            if k == "type" and isinstance(v, str):
                out[k] = v.lower()
            else:
                out[k] = _lower_types(v)
        return out
    if isinstance(schema, list):
        return [_lower_types(v) for v in schema]
    return schema


def to_provider_tools(declarations: list[dict]) -> list[dict]:
    tools = []
    for d in declarations:
        params = _lower_types(d.get("parameters") or {"type": "object", "properties": {}})
        tools.append({"type": "function", "function": {
            "name": d["name"], "description": d.get("description", ""), "parameters": params}})
    return tools


_THINK_RE = re.compile(r"<think>.*?</think>", re.S)


def _clean(text: str) -> str:
    if "</think>" in (text or "") and "<think>" not in text:
        text = text.partition("</think>")[2]
    return _THINK_RE.sub("", text or "").strip()


class InferenceProvider:
    """Base class for all inference providers."""

    def __init__(self, cfg: dict | None = None):
        self.cfg = cfg or load_config()
        self.health: dict[str, Any] = {"ok": False, "message": "not initialized"}
        self.stats: dict[str, Any] = {"requests": 0, "tokens": 0, "latency_ms": 0}

    def is_available(self) -> bool:
        return self.health.get("ok", False)

    def health_check(self) -> dict:
        return self.health

    def get_capabilities(self) -> dict[str, bool]:
        return {"supports_tools": False, "supports_vision": False, "supports_streaming": False, "supports_json": False}

    async def cancel_request(self) -> None:
        pass

    def get_model_for_role(self, role: str) -> str:
        raise NotImplementedError
    
    async def generate(self, messages: list[dict], **kwargs: Any) -> dict:
        raise NotImplementedError
    
    async def chat(self, messages: list[dict], **kwargs: Any) -> dict:
        return await self.generate(messages, **kwargs)


# ── Ollama Provider ─────────────────────────────────────────────────────────

class OllamaProvider(InferenceProvider):
    def __init__(self, cfg: dict | None = None):
        super().__init__(cfg)
        self.host = self._ensure_host(cfg.get("host", "http://127.0.0.1:11434")) if cfg else "http://127.0.0.1:11434"
        self.models: list = []
        self._loaded_models: set = set()

    def _ensure_host(self, host: str) -> str:
        host = str(host).strip()
        return (host if host.startswith("http") else "http://" + host).rstrip("/")
    
    def get_model_for_role(self, role: str) -> str:
        cfg = load_config()
        return cfg.get(f"{role}_model") or cfg["text_model"]
    
    def get_capabilities(self) -> dict[str, bool]:
        return {"supports_tools": True, "supports_vision": True,
                "supports_streaming": True, "supports_json": True}
    
    def health_check(self) -> dict:
        return health()
    
    async def generate(self, messages: list[dict], **kwargs: Any) -> dict:
        role = kwargs.get("role", "text")
        result = await asyncio.to_thread(
            chat_raw,
            messages,
            role,
            kwargs.get("tools"),
            kwargs.get("temperature"),
            kwargs.get("max_tokens"),
            kwargs.get("model"),
            kwargs.get("json_mode", False),
        )
        return result


class GeminiProvider(InferenceProvider):
    def __init__(self, cfg: dict | None = None):
        super().__init__(cfg)
    
    def get_model_for_role(self, role: str) -> str:
        cfg = load_config().get("gemini", {})
        return cfg.get(f"{role}_model") or cfg.get("text_model", "")
    
    def get_capabilities(self) -> dict[str, bool]:
        return {"supports_tools": True, "supports_vision": True,
                "supports_streaming": True, "supports_json": True}
    
    def health_check(self) -> dict:
        return gemini_health()
    
    async def generate(self, messages: list[dict], **kwargs: Any) -> dict:
        role = kwargs.get("role", "text")
        result = await asyncio.to_thread(
            _gemini_chat,
            messages,
            role,
            kwargs.get("tools"),
            kwargs.get("temperature"),
            kwargs.get("max_tokens"),
            kwargs.get("json_mode", False),
        )
        return result


# --------------------------------------------------------------------------- web grounding
def web_context(query: str, n: int | None = None) -> str:
    """Free web search (DuckDuckGo via ddgs) used instead of Gemini's google_search tool."""
    n = n or int(load_config().get("web_results", 6))
    q = re.sub(r"\s+", " ", query).strip()[:220]
    if not q:
        return ""
    try:
        from ddgs import DDGS
        d = DDGS()
        use_news = bool(re.search(r"\b(news|headline|headlines|latest|breaking)\b", q, re.I))
        rows = list(d.news(q, max_results=n)) if use_news else list(d.text(q, max_results=n))
    except Exception as e:
        return f"(web search unavailable: {type(e).__name__})"
    lines = []
    for r in rows:
        title = r.get("title", "")
        body = r.get("body", "") or r.get("excerpt", "")
        url = r.get("href") or r.get("url", "")
        lines.append(f"- {title}: {body[:300]} ({url})")
    return "\n".join(lines)


_managed_process = None
_start_lock = threading.Lock()


def _ensure_server():
    """Start a local server only when a local request needs it."""
    global _managed_process
    cfg = load_config()
    host = cfg["host"]
    def reachable():
        try:
            with urllib.request.urlopen(host + "/api/tags", timeout=0.5) as response:
                return response.status == 200
        except Exception:
            return False
    if reachable():
        return
    with _start_lock:
        if reachable():
            return
        hostname = urlsplit(host).hostname or ""
        try:
            local = hostname.lower() == "localhost" or ipaddress.ip_address(hostname).is_loopback
        except ValueError:
            local = False
        if not local:
            return
        executable = shutil.which("ollama")
        if not executable:
            candidate = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe"
            executable = str(candidate) if candidate.is_file() else None
        if not executable:
            return
        if _managed_process is None or _managed_process.poll() is not None:
            kwargs = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
                      "stderr": subprocess.DEVNULL, "env": {**os.environ, "OLLAMA_HOST": host}}
            if os.name == "nt":
                kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            try:
                _managed_process = subprocess.Popen([executable, "serve"], **kwargs)
            except OSError:
                return
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if reachable() or _managed_process.poll() is not None:
                break
            time.sleep(0.25)


def stop_managed_server():
    global _managed_process
    with _start_lock:
        process, _managed_process = _managed_process, None
    if process is not None and process.poll() is None:
        try:
            process.terminate()
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


# --------------------------------------------------------------------------- Ollama transport
def _post(path: str, payload: dict, timeout: float | None = None) -> dict:
    import requests
    import time
    cfg = load_config()
    last_exc = None
    # Fail quickly when Ollama is down; repeating a long inference timeout three
    # times made a single user request appear frozen for many minutes.
    attempts = 2 if timeout is None else 1
    for attempt in range(attempts):
        try:
            r = requests.post(cfg["host"] + path, json=payload, timeout=timeout or cfg["timeout_s"])
            last_exc = None
            break
        except requests.exceptions.ConnectionError as e:
            last_exc = LLMUnavailable(
                f"Ollama is not running at {cfg['host']}. Start it (open the Ollama app, or run `ollama serve`). "
                f"Detail: {e.__class__.__name__}")
            # connection errors are not transient usually; but retry once
            if attempt + 1 < attempts:
                time.sleep(1.0)
                continue
            raise last_exc from e
        except requests.exceptions.Timeout as e:
            last_exc = LLMUnavailable(f"Ollama took longer than {cfg['timeout_s']}s - model too big for this PC? Try a smaller one in config/llm.json.")
            raise last_exc from e
    if last_exc is not None:
        raise last_exc
    if r.status_code == 404 and "not found" in r.text.lower():
        model = payload.get("model", "?")
        raise LLMUnavailable(f"Model '{model}' is not installed. Run:  ollama pull {model}")
    if r.status_code >= 400:
        kind = "recoverable provider error" if r.status_code >= 500 else "request error"
        raise LLMUnavailable(f"Ollama {kind} {r.status_code}.")
    try:
        return r.json()
    except ValueError as exc:
        raise LLMUnavailable("Ollama returned a malformed JSON response.") from exc


def chat_raw(messages: list[dict], role: str = "text", tools: list[dict] | None = None,
             temperature: float | None = None, max_tokens: int | None = None,
             model: str | None = None, json_mode: bool = False) -> dict:
    """Send one chat request to the configured provider."""
    cfg = load_config()
    local_messages = [{k: v for k, v in msg.items() if k != "_local_only"} for msg in messages]
    if cfg.get("backend") == "multi_provider":
        # Free-tier eligibility is account-specific; never assume that a model is free.
        if cfg.get("free_tier_only", True) and not cfg.get("verified_free_models"):
            if not cfg.get("automatic_fallback", True):
                raise LLMUnavailable("Cloud model free-tier eligibility is not verified.")
            return _run_provider("ollama", _ollama_chat, local_messages, role, tools, temperature, max_tokens, model, json_mode)
        cloud_messages = [clean for clean, original in zip(local_messages, messages) if not original.get("_local_only")]
        if not cfg.get("share_history_with_cloud", False):
            last_user = next ( (i for i in range(len(cloud_messages)-1, -1, -1) if cloud_messages[i].get("role") == "user"), -1)
            cloud_messages = [m for m in cloud_messages[:last_user] if m.get("role") == "system"] + cloud_messages[last_user:] if last_user >= 0 else []
        try:
            if role == "vision":
                if not cfg.get("allow_cloud_vision", False):
                    raise LLMUnavailable("Cloud vision is not authorized.")
                vision_model = cfg.get("gemini", {}).get("vision_model")
                if cfg.get("free_tier_only", True) and vision_model not in cfg.get("verified_free_models", []):
                    raise LLMUnavailable("Gemini model free-tier eligibility is not verified.")
                return _run_provider("gemini", _gemini_chat, cloud_messages, role, tools, temperature, max_tokens, json_mode)
            return _run_provider("groq", _groq_chat, cloud_messages, role, tools, temperature, max_tokens, json_mode)
        except (LLMUnavailable, providers.BudgetExceeded):
            if not cfg.get("automatic_fallback", True):
                raise
            return _run_provider("ollama", _ollama_chat, local_messages, role, tools, temperature, max_tokens, model, json_mode)
    if cfg.get("backend") == "gemini":
        cloud_messages = [clean for clean, original in zip(local_messages, messages) if not original.get("_local_only")]
        return _run_provider("gemini", _gemini_chat, cloud_messages, role, tools, temperature, max_tokens, json_mode)
    if cfg.get("backend") == "ollama":
        return _run_provider("ollama", _ollama_chat, local_messages, role, tools, temperature, max_tokens, model, json_mode)
    raise LLMUnavailable("Unsupported LLM backend. Choose ollama or gemini.")


def _groq_chat(messages, role, tools, temperature, max_tokens, json_mode):
    from core.secrets import get_secret
    cfg = load_config()
    models = cfg.get("providers", {}).get("groq", {})
    name = models.get(f"{role}_model", models.get("text_model", "qwen/qwen3.8-27b"))
    if cfg.get("free_tier_only", True) and name not in cfg.get("verified_free_models", []):
        raise LLMUnavailable("Groq model free-tier eligibility is not verified.")
    key = get_secret("groq")
    if not key:
        raise LLMUnavailable("Groq API key is not configured.")
    client = providers.client_for("groq", key)
    if client is None:
        if providers.initialize("groq", cfg) != "CONFIGURED":
            raise LLMUnavailable("Groq client is unavailable.")
        client = providers.client_for("groq", key)
    payload = {"model": name, "messages": messages, "max_completion_tokens": max_tokens or 512}
    if temperature is not None:
        payload["temperature"] = temperature
    if tools:
        payload["tools"] = tools
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    if name == "qwen/qwen3.8-27b":
        payload["reasoning_effort"] = "none"
    start = time.monotonic()
    try:
        with providers.cloud_request("groq", cfg):
            response = client.chat.completions.create(**payload)
        message = response.choices[0].message
        providers.record("groq", name, start, tokens=getattr(response.usage, "total_tokens", 0))
        result = {"role": "assistant", "content": _clean(message.content or "")}
        if message.tool_calls:
            result["tool_calls"] = [call.model_dump(exclude_none=True) for call in message.tool_calls]
        return result
    except providers.BudgetExceeded:
        raise
    except Exception as exc:
        status = getattr(exc, "status_code", 0) or 0
        providers.record("groq", name, start, status=status)
        raise LLMUnavailable(f"Groq request failed ({'HTTP ' + str(status) if status else type(exc).__name__}).") from exc


def local_tool_selection(messages, tools, byte_budget):
    """Fit MARK's tool catalogue into the small local context window."""
    if not tools or len(tools) < 8:
        return tools
    questions = [str(m.get("content", "")) for m in messages if m.get("role") == "user"]
    query = questions[-1].lower() if questions else ""
    if messages and messages[-1].get("tool_name") == "plugins":
        query += " " + str(messages[-1].get("content", "")).lower()
    words = set(re.findall(r"[a-z0-9]+", query)) - {"the", "and", "with", "for", "use", "that", "goal", "please"}
    def relevance(tool):
        fn = tool["function"]
        name = fn["name"]
        tokens = set(re.findall(r"[a-z0-9]+", name + " " + fn.get("description", "").lower()))
        return (name in query, len(words.intersection(name.split("_"))), len(words & tokens))
    discovery = [t for t in tools if t["function"]["name"] == "plugins"]
    ranked = sorted([t for t in tools if t not in discovery], key=relevance, reverse=True)
    selected = []
    for tool in discovery + ranked:
        cost = len(json.dumps(tool, ensure_ascii=False).encode())
        if cost <= byte_budget:
            selected.append(tool)
            byte_budget -= cost
    return selected


def _ollama_chat(messages: list[dict], role: str, tools: list[dict] | None,
                 temperature: float | None, max_tokens: int | None,
                 model: str | None, json_mode: bool) -> dict:
    cfg = load_config()
    _ensure_server()
    payload: dict[str, Any] = {
        "model": model or model_for(role, cfg),
        "messages": messages,
        "stream": False,
        "keep_alive": cfg.get("keep_alive", "30m"),
        "options": {"num_ctx": int(cfg.get("num_ctx", 8192))},
    }
    if temperature is not None:
        payload["options"]["temperature"] = float(temperature)
    payload["options"]["num_predict"] = int(max_tokens or cfg.get("local_max_output_tokens", 384))
    if tools:
        payload["tools"] = local_tool_selection(messages, tools, int(cfg.get("local_tool_schema_bytes", 5200)))
    if json_mode:
        payload["format"] = "json"
    payload["think"] = False                         # fast answers; reasoning models otherwise ramble
    try:
        data = _post("/api/chat", payload)
    except LLMUnavailable as e:
        if "think" in str(e).lower():                # model does not know the flag
            payload.pop("think", None)
            data = _post("/api/chat", payload)
        else:
            raise
    if not isinstance(data, dict) or not isinstance(data.get("message"), dict):
        raise LLMUnavailable("Ollama returned a malformed chat response.")
    return data["message"]


def generate(prompt: Any, role: str = "text", system: str | None = None,
             temperature: float | None = None, max_tokens: int | None = None,
             json_mode: bool = False) -> str:
    """Simple helper: prompt (str / list with images) -> text."""
    text, images = _normalise(prompt)
    role = role_for(f"role:{role}", bool(images))
    msgs: list[dict] = []
    if system:
        msgs.append({"role": "system", "content": system})
    user: dict[str, Any] = {"role": "user", "content": text}
    if images:
        user["images"] = [base64.b64encode(b).decode() for b in images]
    msgs.append(user)
    if load_config()["backend"] == "gemini":
        return _run_provider("gemini", _gemini_generate, role, prompt, {"system_instruction": system, "temperature": temperature,
                                      "max_output_tokens": max_tokens,
                                      "response_mime_type": "application/json" if json_mode else None}).text
    return _clean(chat_raw(msgs, role=role, temperature=temperature, max_tokens=max_tokens,
                           json_mode=json_mode).get("content", ""))


def vision(image: bytes, question: str, system: str | None = None, temperature: float = 0.1) -> str:
    return generate([types.Part.from_bytes(image, "image/png"), question], role="vision",
                    system=system, temperature=temperature)


def chat_tools(messages: list[dict], declarations: list[dict], system: str | None = None,
               role: str = "text", temperature: float = 0.2) -> dict:
    """Tool-calling turn for the brain. `messages` use the internal chat format and are NOT modified."""
    msgs = ([{"role": "system", "content": system}] if system else []) + list(messages)
    msg = chat_raw(msgs, role=role, tools=to_provider_tools(declarations), temperature=temperature)
    msg["content"] = _clean(msg.get("content", ""))
    return msg


def _generate_content_adapter(role: str, contents: Any, config: Any) -> SimpleNamespace:
    text, images = _normalise(contents)
    system = _cfg_get(config, "system_instruction")
    if system is not None and not isinstance(system, str):
        system = _normalise(system)[0]
    tools = _cfg_get(config, "tools") or []
    grounded = any(isinstance(t, dict) and "google_search" in t for t in tools)
    decls: list[dict] = []
    for t in tools:
        if isinstance(t, dict) and "function_declarations" in t:
            decls.extend(t["function_declarations"])

    if grounded:
        ctx = web_context(text)
        if ctx:
            system = (system + "\n\n" if system else "") + (
                "Use these live web results to answer. Say if they do not contain the answer.\n" + ctx)

    msgs: list[dict] = []
    if system:
        msgs.append({"role": "system", "content": system})
    user: dict[str, Any] = {"role": "user", "content": text}
    if images:
        user["images"] = [base64.b64encode(b).decode() for b in images]
    msgs.append(user)

    msg = chat_raw(msgs, role=role, tools=to_provider_tools(decls) if decls else None,
                   temperature=_cfg_get(config, "temperature"),
                   max_tokens=_cfg_get(config, "max_output_tokens"))
    calls = []
    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function", {})
        args = fn.get("arguments") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except ValueError:
                args = {}
        calls.append((fn.get("name", ""), args))
    return _response(_clean(msg.get("content", "")), calls)


class _Models:
    def generate_content(self, model: str | None = None, contents: Any = None, config: Any = None):
        text, images = _normalise(contents)
        role = role_for(model, bool(images))
        cfg = load_config()
        primary = cfg.get("backend", "ollama")
        if primary == "gemini":
            return _run_provider("gemini", _gemini_generate, role, contents, config)
        return _generate_content_adapter(role, contents, config)


class Client:
    """Drop-in for google.genai.Client (only what Mark uses). The api_key is ignored."""

    def __init__(self, api_key: str | None = None, **_: Any) -> None:
        self.models = _Models()


def make_client(*_: Any, **__: Any) -> Client:
    return Client()


# --------------------------------------------------------------------------- health + install help
def missing_models(cfg: dict, installed: list[dict]) -> list[str]:
    def tag(name):
        return name if ":" in name.rsplit("/", 1)[-1] else name + ":latest"
    available = {tag(m["name"]) for m in installed if m.get("name")}
    return sorted({tag(model_for(role, cfg)) for role in ("text", "code", "fast", "vision")} - available)


def health() -> dict:
    cfg = load_config()
    if cfg["backend"] == "gemini":
        from core.secrets import get_secret
        ok = bool(get_secret("gemini") and cfg.get("gemini", {}).get("text_model"))
        return {"ok": ok, "backend": "gemini", "missing": [], "message": "Gemini configured." if ok else "Gemini requires a credential and model."}
    import requests
    try:
        response = requests.get(cfg["host"] + "/api/tags", timeout=4)
        response.raise_for_status()
        missing = missing_models(cfg, response.json().get("models", []))
    except (requests.RequestException, ValueError):
        return {"ok": False, "backend": "ollama", "missing": [], "message": "Ollama is not running."}
    return {"ok": not missing, "backend": "ollama", "missing": missing,
            "message": "ready" if not missing else "missing models: " + ", ".join(missing)}


def pull_missing(progress: Callable[[str], None] = print) -> list[str]:
    """Download any configured model that is not installed. Returns the ones that failed."""
    import requests
    cfg = load_config()
    failed_models: list[str] = []
    try:
        response = requests.get(cfg["host"] + "/api/tags", timeout=5)
        response.raise_for_status()
        missing = missing_models(cfg, response.json()["models"])
    except (requests.RequestException, ValueError, KeyError) as exc:
        progress(f"Ollama inventory unavailable: {type(exc).__name__}")
        return ["Ollama inventory unavailable"]
    for m in missing:
        progress(f"Downloading {m} ...")
        try:
            with requests.post(cfg["host"] + "/api/pull", json={"model": m, "stream": True},
                               stream=True, timeout=(5, 120)) as r:
                r.raise_for_status()
                last = ""
                for line in r.iter_lines():
                    if not line:
                        continue
                    event = json.loads(line)
                    if event.get("error"):
                        raise LLMUnavailable(str(event["error"]))
                    st = event.get("status", "")
                    if st != last:
                        progress(f"  {m}: {st}")
                        last = st
                if last != "success":
                    raise LLMUnavailable("Download ended before success")
        except Exception as e:
            progress(f"  could not pull {m}: {e}")
            failed_models.append(m)
    return failed_models


# --------------------------------------------------------------------------- optional Gemini backend
def _gemini_chat(messages, role, tools, temperature, max_tokens, json_mode):
    """Preserve tool turns in Google's native content representation."""
    from google.genai import types as gt
    contents, instructions = [], []
    for msg in messages:
        if msg.get("_gemini_content") is not None:
            contents.append(msg["_gemini_content"])
            continue
        text = str(msg.get("content") or "")
        if msg["role"] == "system":
            instructions.append(text)
            continue
        parts = [gt.Part.from_text(text=text)] if text else []
        if msg.get("images"):
            parts.extend(gt.Part.from_bytes(data=base64.b64decode(image), mime_type="image/png") for image in msg["images"])
        if msg["role"] == "tool":
            parts = [gt.Part.from_function_response(name=msg["tool_name"], response={"result": text})]
        for call in msg.get("tool_calls") or []:
            fn = call["function"]
            parts.append(gt.Part.from_function_call(name=fn["name"], args=fn.get("arguments") or {}))
        contents.append(gt.Content(role="model" if msg["role"] == "assistant" else "user", parts=parts))
    config = {"system_instruction": "\n".join(instructions), "temperature": temperature,
              "max_output_tokens": max_tokens}
    if tools:
        config["tools"] = [{"function_declarations": [t["function"] for t in tools]}]
    if json_mode:
        config["response_mime_type"] = "application/json"
    reply = _gemini_generate(role, contents, config, native=True)
    text = "".join(part.text for cand in reply.candidates or [] for part in (cand.content.parts or []) if part.text and not part.thought)
    return {"role": "assistant", "content": text, "_gemini_content": reply.candidates[0].content, "tool_calls": [
        {"function": {"name": fc.name, "arguments": dict(fc.args or {})}} for fc in (reply.function_calls or [])]}


def _gemini_generate(role: str, contents: Any, config: Any, *, native=False):
    """Used only when config backend == 'gemini'. Same quota rules as before."""
    from google import genai
    from google.genai import types as gt
    key = ""
    try:
        from core.secrets import get_secret
        key = get_secret("gemini")
    except Exception:
        pass
    key = key or os.environ.get("GEMINI_API_KEY", "")
    if not key:
        raise LLMUnavailable("backend is 'gemini' but no gemini_api_key is set.")
    if native:
        parts = contents
    else:
        text, images = _normalise(contents)
        parts = [gt.Part.from_bytes(data=b, mime_type="image/png") for b in images] + [text]
    kw: dict[str, Any] = {}
    for k in ("system_instruction", "temperature", "tools", "max_output_tokens", "response_mime_type"):
        v = _cfg_get(config, k)
        if v is not None:
            kw[k] = v
    cfg = load_config()
    name = cfg.get("gemini", {}).get("vision_model" if role == "vision" else "text_model", "")
    if not name:
        raise LLMUnavailable("Gemini text model is not configured.")
    retries = max(0, min(int(cfg.get("gemini", {}).get("max_retries", 2)), 5))
    backoffs = cfg.get("gemini", {}).get("retry_backoff_s", [2, 5])
    timeout_ms = int(max(1, float(cfg.get("gemini", {}).get("timeout_s", 60))) * 1000)
    client = providers.client_for("gemini", key) or genai.Client(api_key=key, http_options={"timeout": timeout_ms})
    for attempt in range(retries + 1):
        try:
            start = time.monotonic()
            with providers.cloud_request("gemini", cfg):
                if cfg.get("stream_text") and not native and not kw.get("tools"):
                    chunks = []
                    tokens = 0
                    first_ms = None
                    for event in client.models.generate_content_stream(model=name, contents=parts, config=gt.GenerateContentConfig(**kw)):
                        if event.text:
                            if first_ms is None:
                                first_ms = round((time.monotonic()-start)*1000)
                            chunks.append(event.text)
                        tokens = getattr(event.usage_metadata, "total_token_count", 0) or tokens
                    if not chunks:
                        raise LLMUnavailable("Gemini returned an empty text stream.")
                    providers.record("gemini", name, start, tokens=tokens, first_ms=first_ms)
                    return _response("".join(chunks))
                response = client.models.generate_content(
                    model=name, contents=parts, config=gt.GenerateContentConfig(**kw) if kw else None)
            if not getattr(response, "function_calls", None) and not (getattr(response, "text", None) or "").strip():
                raise LLMUnavailable("Gemini returned an empty text response.")
            providers.record("gemini", name, start, tokens=getattr(getattr(response, "usage_metadata", None), "total_token_count", 0))
            return response
        except (LLMUnavailable, providers.BudgetExceeded):
            raise
        except Exception as exc:
            message = str(exc).lower()
            match = re.search(r"(?:http|status(?: code)?)\s*(\d{3})", message)
            status = getattr(exc, "code", None) or (int(match.group(1)) if match else 0)
            providers.record("gemini", name, start, status=status)
            if status in (401, 403, 404):
                labels = {401: "authentication failed (HTTP 401)", 403: "permission denied (HTTP 403)",
                          404: f"model '{name}' not found (HTTP 404)"}
                raise LLMUnavailable("Gemini " + labels[status]) from exc
            recoverable = status in (429, 500, 502, 503, 504) or "timeout" in message or "temporar" in message
            if not recoverable or attempt >= retries:
                category = f"HTTP {status}" if status else type(exc).__name__
                raise LLMUnavailable(f"Gemini request failed ({category}); credentials and request details were omitted.") from exc
            base = float(backoffs[min(attempt, len(backoffs)-1)]) if backoffs else 2 ** attempt
            delay = max(0, base) + random.uniform(0, min(1.0, max(0.1, base * 0.2)))
            print(f"[Gemini] HTTP {status or 'timeout'}; retrying in {delay:.1f}s.")
            time.sleep(delay)


_health_cache: dict[str, Any] = {"at": 0.0, "value": None}


def gemini_health(refresh: bool = False) -> dict:
    """Probe configured Gemini components with real authenticated API operations."""
    cfg = load_config().get("gemini", {})
    now = time.monotonic()
    ttl = max(0, float(cfg.get("health_cache_s", 30)))
    if not refresh and _health_cache["value"] is not None and now - _health_cache["at"] < ttl:
        return _health_cache["value"]
    from core.secrets import get_secret
    key = get_secret("gemini") or os.environ.get("GEMINI_API_KEY", "")
    result = {"provider": "gemini"}
    for component, model in (("text", cfg.get("text_model", "")),
                             ("live", cfg.get("live_model", "")),
                             ("memory", cfg.get("memory_model", ""))):
        item = {"configured": bool(model), "ready": False, "model": model or None}
        if not model:
            item["error"] = "Model is not configured."
        elif not key:
            item["error"] = "Gemini API credential is not configured."
        else:
            try:
                if component == "live":
                    import asyncio
                    from google import genai
                    async def probe_live():
                        client = genai.Client(api_key=key, http_options={"api_version": "v1beta", "timeout": int(float(cfg.get("timeout_s", 60))*1000)})
                        async with client.aio.live.connect(model=model, config={}) as session:
                            return session is not None
                    item["ready"] = asyncio.run(asyncio.wait_for(probe_live(), timeout=float(cfg.get("timeout_s", 60))))
                else:
                    response = _gemini_generate("text", "Reply with OK.", {"max_output_tokens": 4}) if component == "text" else _gemini_generate_model(model, "Reply with OK.", cfg)
                    item["ready"] = bool((getattr(response, "text", None) or "").strip())
                    if not item["ready"]:
                        item["error"] = "Gemini returned an empty response."
            except Exception as exc:
                detail = str(exc).lower()
                status = re.search(r"(?:http|status(?: code)?)\s*(\d{3})", detail)
                if status:
                    item["error"] = f"Gemini HTTP {status.group(1)}."
                elif "timeout" in detail or isinstance(exc, TimeoutError):
                    item["error"] = "Connection timeout."
                else:
                    item["error"] = f"Gemini connection failed ({type(exc).__name__})."
        result[component] = item
    _health_cache.update(at=time.monotonic(), value=result)
    return result


def _gemini_generate_model(model: str, prompt: str, cfg: dict):
    from google import genai
    from core.secrets import get_secret
    key = get_secret("gemini") or os.environ.get("GEMINI_API_KEY", "")
    client = genai.Client(api_key=key, http_options={"timeout": int(float(cfg.get("timeout_s", 60))*1000)})
    response = client.models.generate_content(model=model, contents=prompt)
    if not (getattr(response, "text", None) or "").strip():
        raise LLMUnavailable("Gemini returned an empty text response.")
    return response


if __name__ == "__main__":                       # python -m core.llm [check|pull]
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "check"
    h = health()
    print(f"backend: {h['backend']}  ->  {h['message']}")
    if cmd == "pull" and load_config().get("backend") == "ollama":
        failed = pull_missing()
        sys.exit(1 if failed else 0)
    sys.exit(0 if h["ok"] else 1)

"""
core/llm.py - the ONE place Mark talks to a language model.

Default routing: Groq for text and tools, Gemini for vision and cloud fallback.

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
    "backend": "multi_provider",
    "text_model": "qwen/qwen3.8-27b", "code_model": "openai/gpt-oss-120b",
    "fast_model": "qwen/qwen3.8-27b", "vision_model": "gemini-3.1-flash-lite",
    "primary_provider": "groq", "fallback_providers": ["gemini"],
    "free_tier_only": True, "verified_free_models": [],
    "share_history_with_cloud": False,
    "num_ctx": 4096, "timeout_s": 60, "keep_alive": "30m",
    "web_results": 6,
    "gemini": {"text_model": "gemini-flash-lite-latest", "live_model": "gemini-2.5-flash-native-audio-latest",
               "memory_model": "gemini-flash-lite-latest", "timeout_s": 60,
               "max_retries": 2, "retry_backoff_s": [2, 5], "health_cache_s": 30},
}

_ENV = {"backend": "MARK_LLM_BACKEND", "text_model": "MARK_TEXT_MODEL", "code_model": "MARK_CODE_MODEL", "fast_model": "MARK_FAST_MODEL", "vision_model": "MARK_VISION_MODEL"}


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
    if load_config().get("profile") == "offline":
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
        if isinstance(cfg.get("providers", {}).get("gemini"), dict):
            cfg["gemini"].update(cfg["providers"]["gemini"])
        cfg["profile"] = os.getenv("MARK_PROFILE", cfg.get("profile", "balanced")).strip().lower()
        if cfg.get("backend") not in {"gemini", "multi_provider"}:
            raise LLMUnavailable("Unsupported LLM backend. Choose multi_provider or gemini.")
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
    provider = "gemini" if role == "vision" or cfg.get("backend") == "gemini" else cfg.get("primary_provider", "groq")
    models = cfg.get("gemini", {}) if provider == "gemini" else cfg.get("providers", {}).get(provider, {})
    return models.get(f"{role}_model") or models.get("text_model") or cfg.get(f"{role}_model") or cfg["text_model"]


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


def chat_raw(messages: list[dict], role: str = "text", tools: list[dict] | None = None,
             temperature: float | None = None, max_tokens: int | None = None,
             model: str | None = None, json_mode: bool = False, *, on_delta=None, cancelled=None, provider_override=None) -> dict:
    """Send one chat request; optional text deltas use the same provider routing."""
    cfg = load_config()
    if cfg.get("profile") == "offline":
        raise LLMUnavailable("Cloud inference is disabled in offline profile.")
    cloud_messages = [{k: v for k, v in msg.items() if k != "_local_only"}
                      for msg in messages if not msg.get("_local_only")]
    # Keep the current tool exchange intact, but exclude prior dialogue by default.
    if not cfg.get("share_history_with_cloud", False):
        last_user = next((i for i in range(len(cloud_messages)-1, -1, -1)
                          if cloud_messages[i].get("role") == "user"), -1)
        if last_user >= 0:
            cloud_messages = [m for m in cloud_messages[:last_user] if m.get("role") == "system"] + cloud_messages[last_user:]
    from core.cloud_privacy import redact_messages
    cloud_messages = redact_messages(cloud_messages, cfg)
    if role == "vision" and not cfg.get("allow_cloud_vision", False):
        raise LLMUnavailable("Cloud vision is not authorized.")
    if provider_override is not None and provider_override not in {"groq", "gemini"}:
        raise LLMUnavailable("Unsupported cloud provider.")
    if role == "vision" and provider_override not in (None, "gemini"):
        raise LLMUnavailable("Vision requires the configured Gemini provider.")
    if provider_override is not None:
        order = [provider_override]
    elif cfg.get("backend") == "gemini" or role == "vision":
        order = ["gemini"]
    else:
        primary = cfg.get("primary_provider", "groq")
        order = [primary]
        if cfg.get("automatic_fallback", True):
            order += cfg.get("fallback_providers", ["gemini"])
    failures = []
    emitted = False
    def emit(delta):
        nonlocal emitted
        emitted = True
        on_delta(delta)
    for provider in dict.fromkeys(order):
        if cancelled and cancelled():
            raise LLMUnavailable("Inference cancelled.")
        if provider not in {"groq", "gemini"}:
            continue
        if provider == "groq" and role == "vision":
            continue
        if provider == "groq" and any(m.get("_gemini_content") is not None for m in cloud_messages):
            continue
        try:
            if provider == "gemini":
                name = cfg.get("gemini", {}).get("vision_model" if role == "vision" else "text_model")
                if cfg.get("free_tier_only", True) and name not in cfg.get("verified_free_models", []):
                    raise LLMUnavailable("Gemini model is not on the configured allowlist.")
                return _run_provider("gemini", _gemini_chat, cloud_messages, role, tools, temperature, max_tokens, json_mode)
            if on_delta is not None and not tools and not json_mode:
                return _run_provider("groq", _groq_chat, cloud_messages, role, tools, temperature, max_tokens, json_mode, emit, cancelled)
            return _run_provider("groq", _groq_chat, cloud_messages, role, tools, temperature, max_tokens, json_mode)
        except (LLMUnavailable, providers.BudgetExceeded) as exc:
            if emitted or (cancelled and cancelled()):
                raise
            failures.append(f"{provider}: {exc}")
    raise LLMUnavailable("Cloud inference unavailable: " + "; ".join(failures))


def _groq_chat(messages, role, tools, temperature, max_tokens, json_mode, on_delta=None, cancelled=None):
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
    wire_messages = []
    for msg in messages:
        item = {"role": msg["role"], "content": msg.get("content", "")}
        if msg.get("tool_calls"):
            item["tool_calls"] = []
            for call in msg["tool_calls"]:
                fn = call["function"]
                args = fn.get("arguments", {})
                item["tool_calls"].append({"id": call["id"], "type": "function",
                    "function": {"name": fn["name"], "arguments": args if isinstance(args, str) else json.dumps(args)}})
        if msg["role"] == "tool":
            if not msg.get("tool_call_id"):
                raise LLMUnavailable("Missing cloud tool call ID.")
            item["tool_call_id"] = msg["tool_call_id"]
        wire_messages.append(item)
    payload = {"model": name, "messages": wire_messages, "max_completion_tokens": max_tokens or 512}
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
            if on_delta is not None:
                chunks = []
                first_ms = None
                tokens = 0
                stream = client.chat.completions.create(**payload, stream=True)
                try:
                    for event in stream:
                        if cancelled and cancelled():
                            raise LLMUnavailable("Inference cancelled.")
                        usage = getattr(event, "usage", None)
                        if usage:
                            tokens = getattr(usage, "total_tokens", 0) or tokens
                        choices = getattr(event, "choices", [])
                        delta = getattr(choices[0].delta, "content", None) if choices else None
                        if delta:
                            if first_ms is None:
                                first_ms = round((time.monotonic()-start)*1000)
                            chunks.append(delta)
                            on_delta(delta)
                finally:
                    stream.close()
                content = _clean("".join(chunks))
                if not content:
                    raise LLMUnavailable("Groq returned an empty stream.")
                providers.record("groq", name, start, tokens=tokens, first_ms=first_ms)
                return {"role": "assistant", "content": content}
            response = client.chat.completions.create(**payload)
        message = response.choices[0].message
        providers.record("groq", name, start, tokens=getattr(response.usage, "total_tokens", 0))
        result = {"role": "assistant", "content": _clean(message.content or "")}
        if message.tool_calls:
            result["tool_calls"] = [call.model_dump(exclude_none=True) for call in message.tool_calls]
        return result
    except (LLMUnavailable, providers.BudgetExceeded):
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
               role: str = "text", temperature: float = 0.2, *, cancelled=None) -> dict:
    """Tool-calling turn for the brain. `messages` use the internal chat format and are NOT modified."""
    msgs = ([{"role": "system", "content": system}] if system else []) + list(messages)
    msg = chat_raw(msgs, role=role, tools=to_provider_tools(declarations),
                   temperature=temperature, cancelled=cancelled)
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
        primary = cfg.get("backend", "multi_provider")
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
def health() -> dict:
    cfg = load_config()
    from core.secrets import get_secret
    names = ["gemini"] if cfg["backend"] == "gemini" else ["groq", "gemini"]
    configured = [name for name in names if get_secret(name)]
    return {"ok": bool(configured), "backend": cfg["backend"], "missing": [],
            "message": "Credentials configured: " + ", ".join(configured) if configured else "Cloud credentials required."}


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
            parts = [gt.Part.from_function_response(name=msg.get("tool_name") or msg.get("name") or "tool", response={"result": text})]
        for call in msg.get("tool_calls") or []:
            fn = call["function"]
            parts.append(gt.Part.from_function_call(name=fn["name"], args=json.loads(fn["arguments"]) if isinstance(fn.get("arguments"), str) else (fn.get("arguments") or {})))
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
    if cfg.get("free_tier_only", True) and name not in cfg.get("verified_free_models", []):
        raise LLMUnavailable("Gemini model is not on the configured allowlist.")
    if role == "vision" and not cfg.get("allow_cloud_vision", False):
        raise LLMUnavailable("Cloud vision is not authorized.")
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


if __name__ == "__main__":                       # python -m core.llm [check]
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "check"
    h = health()
    print(f"backend: {h['backend']}  ->  {h['message']}")
    sys.exit(0 if h["ok"] else 1)

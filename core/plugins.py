"""
Plugin system.

Bundled or explicitly trusted Python plugins load from the plugins directory.
Discovery checks the source fingerprint before importing a plugin.

A plugin file looks like this (see plugins/_template.py):

    NAME = "my_tool"
    DESCRIPTION = "what it does"

    DECLARATION = {                       # Gemini function declaration
        "name": "my_tool",
        "description": "...",
        "parameters": {"type": "OBJECT", "properties": {...}, "required": []},
    }

    def run(args: dict, ctx) -> str:      # return text or core.result.ActionResult
        ...

A file may expose several tools with  TOOLS = [{"declaration": {...}, "run": fn}, ...]
and optional hooks:  on_load(ctx)   on_notification(note, ctx)   on_unload(ctx)
Files or folders starting with "_" are ignored.  A folder plugin uses plugins/<name>/plugin.py.
"""
from __future__ import annotations

import hashlib
import importlib.util
import inspect
import json
import re
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, TimeoutError, Future
from pathlib import Path
from types import ModuleType
from typing import Any, Callable

from core.result import ActionResult

PLUGIN_DIR = Path(__file__).resolve().parent.parent / "plugins"
_TRUST_DIR = PLUGIN_DIR.parent / "config"
_MAX_SOURCE_BYTES = 2 * 1024 * 1024


def _trusted_plugins():
    fingerprints = {}
    for filename in ("bundled_plugins.json", "trusted_plugins.json"):
        try:
            manifest = json.loads((_TRUST_DIR / filename).read_text(encoding="utf-8"))
            if not isinstance(manifest, dict):
                raise ValueError("Expected a path-to-SHA256 mapping")
            fingerprints.update({
                name: digest for name, digest in manifest.items()
                if isinstance(digest, str) and re.fullmatch(r"[a-f0-9]{64}", digest)
            })
        except FileNotFoundError:
            continue
        except (OSError, ValueError) as exc:
            print(f"[plugin] Cannot read {filename}: {type(exc).__name__}")
    return fingerprints


def _verified_source(path, fingerprints):
    relative = path.relative_to(PLUGIN_DIR).as_posix()
    if path.resolve() != PLUGIN_DIR.resolve() / relative:
        raise ValueError("Plugin paths must resolve to their declared location.")
    expected = fingerprints.get(relative)
    if not expected:
        raise ValueError("Plugin is untrusted. OS sandbox execution is unavailable.")
    with path.open("rb") as source_file:
        source = source_file.read(_MAX_SOURCE_BYTES + 1)
    if len(source) > _MAX_SOURCE_BYTES:
        raise ValueError("Plugin source exceeds the size limit.")
    if hashlib.sha256(source.replace(b"\r\n", b"\n")).hexdigest() != expected:
        raise ValueError("Plugin source changed since approval.")
    return source


def _sandbox_manifest():
    try:
        with (_TRUST_DIR / "sandbox_plugins.json").open(encoding="utf-8") as stream:
            manifest = json.loads(stream.read(262_145))
        return manifest if isinstance(manifest, dict) else {}
    except (OSError, ValueError):
        return {}


def _sandbox_source(path, entry):
    relative = path.relative_to(PLUGIN_DIR).as_posix()
    if path.resolve() != PLUGIN_DIR.resolve() / relative or not isinstance(entry, dict):
        raise ValueError("Unsafe sandbox plugin path or manifest.")
    if set(entry) - {"sha256", "tool", "timeout"}:
        raise ValueError("Sandbox plugins support isolated computation only.")
    with path.open("rb") as stream:
        source = stream.read(1024 * 1024 + 1)
    if len(source) > 1024 * 1024:
        raise ValueError("Sandbox plugin source exceeds its size budget.")
    digest = hashlib.sha256(source.replace(b"\r\n", b"\n")).hexdigest()
    if entry.get("sha256") != digest:
        raise ValueError("Sandbox plugin approval is missing or its source changed.")
    return source


class PluginContext:
    """Handed to every plugin so it can talk to Mark."""

    def __init__(self, ui=None, speak: Callable[[str], None] | None = None,
                 call_tool: Callable[[str, dict], str] | None = None, base_dir: Path | None = None,
                 permissions: set[str] | None = None):
        self.ui = ui
        self._speak = speak
        self._call_tool = call_tool
        self.base_dir = base_dir or PLUGIN_DIR.parent
        self.plugin_dir = PLUGIN_DIR
        self.permissions = permissions or set()
        self.cancelled = threading.Event()

    def log(self, text: str) -> None:
        if self.ui:
            self.ui.write_log(f"[plugin] {text}")
        print(f"[plugin] {text}")

    def speak(self, text: str) -> None:
        """Make Mark say something (it will phrase it naturally)."""
        if self._speak and not self.cancelled.is_set():
            self._speak(text)

    def call_tool(self, name: str, args: dict | None = None) -> str:
        """Run any other Mark tool (open_app, file_controller, whatsapp ...)."""
        if self.cancelled.is_set():
            return "[CANCELLED] plugin invocation is no longer active"
        if not self._call_tool:
            return "[FAILED] tool bridge not ready"
        if "*" not in self.permissions and name not in self.permissions:
            return f"[FAILED] permission denied for tool '{name}'"
        return self._call_tool(name, args or {})


class _Loaded:
    def __init__(self, name: str, path: Path, module: ModuleType, tools: dict[str, dict]):
        self.name, self.path, self.module, self.tools = name, path, module, tools
        raw = getattr(module, "METADATA", {})
        self.metadata = raw if isinstance(raw, dict) else {}
        self.version = str(self.metadata.get("version", "1.0.0"))
        self.permissions = set(self.metadata.get("permissions", []))
        self.capabilities = list(self.metadata.get("capabilities", []))
        self.timeout = max(0.1, min(float(self.metadata.get("timeout", 30)), 300))
        self.health = "unknown"
        self.sandboxed = self.metadata.get("execution") == "appcontainer"
        self.isolated = self.metadata.get("execution") in {"process", "appcontainer"}


class PluginManager:
    def __init__(self, ctx: PluginContext):
        self.ctx = ctx
        self.loaded: dict[str, _Loaded] = {}     # plugin name -> loaded
        self.errors: dict[str, str] = {}         # file -> error text
        self._mtimes: dict[str, int] = {}
        self._trust = {}
        self._sandbox = {}
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._inflight: dict[str, tuple[Future, PluginContext]] = {}
        self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix='mark-plugin')
        self.on_change: Callable[[], None] | None = None
        PLUGIN_DIR.mkdir(exist_ok=True)

    def _context_for(self, loaded: _Loaded) -> PluginContext:
        from copy import copy
        context = copy(self.ctx)
        context.permissions = set(loaded.permissions)
        context.cancelled = threading.Event()
        return context

    # ── discovery ────────────────────────────────────────────────────────────
    def _files(self) -> list[Path]:
        out = []
        for p in sorted(PLUGIN_DIR.iterdir()):
            if p.name.startswith(("_", ".")):
                continue
            if p.is_file() and p.suffix == ".py":
                out.append(p)
            elif p.is_dir() and (p / "plugin.py").exists():
                out.append(p / "plugin.py")
        return out

    @staticmethod
    def _stamp(p: Path) -> int:
        try:
            return p.stat().st_mtime_ns
        except OSError:
            return 0

    # ── loading ──────────────────────────────────────────────────────────────
    @staticmethod
    def _validate_declaration(decl: dict, path: Path) -> None:
        parameters = decl.get("parameters")
        if not isinstance(parameters, dict):
            raise ValueError(f"{path.name}: declaration parameters must be an object")
        if "properties" in parameters:
            schema_type = str(parameters.get("type", "")).upper()
            if schema_type != "OBJECT":
                raise ValueError(
                    f"{path.name}: declaration '{decl.get('name')}' has properties "
                    "but parameters.type is not OBJECT"
                )
            if not isinstance(parameters["properties"], dict):
                raise ValueError(
                    f"{path.name}: declaration '{decl.get('name')}' properties must be an object"
                )

    def _load_file(self, path: Path, *, source: bytes | None = None) -> None:
        key = str(path)
        pname = path.parent.name if path.name == "plugin.py" else path.stem
        try:
            spec = importlib.util.spec_from_file_location(f"mark_plugin_{pname}", path)
            if spec is None or spec.loader is None:
                raise ImportError("cannot create import spec")
            mod = importlib.util.module_from_spec(spec)
            exec(compile(source if source is not None else path.read_bytes(), str(path), "exec"), mod.__dict__)
            if getattr(mod, "ENABLED", True) is False:
                self.errors.pop(key, None)
                return
            tools: dict[str, dict] = {}
            specs = list(getattr(mod, "TOOLS", []))
            if hasattr(mod, "DECLARATION") and hasattr(mod, "run"):
                specs.append({"declaration": mod.DECLARATION, "run": mod.run})
            for s in specs:
                decl, fn = s.get("declaration"), s.get("run")
                if not isinstance(decl, dict) or "name" not in decl or not callable(fn):
                    raise ValueError("each tool needs a 'declaration' dict with a name, and a callable 'run'")
                self._validate_declaration(decl, path)
                if decl["name"] in tools:
                    raise ValueError(f"duplicate tool name '{decl['name']}'")
                tools[decl["name"]] = {"declaration": decl, "run": fn}
            if not tools and not hasattr(mod, "on_notification"):
                raise ValueError("plugin defines no tools (need DECLARATION+run or TOOLS)")
            name = getattr(mod, "NAME", pname)
            if name in self.loaded:
                raise ValueError(f"duplicate plugin name '{name}'")
            loaded = _Loaded(name, path, mod, tools)
            loaded.source_digest = hashlib.sha256(source).digest() if source is not None else None
            if hasattr(mod, "health_check"):
                health = mod.health_check(self._context_for(loaded))
                loaded.health = "healthy" if health is not False else "unhealthy"
            else:
                loaded.health = "healthy"
            if hasattr(mod, "on_load"):
                mod.on_load(self._context_for(loaded))
            self.loaded[name] = loaded
            self.errors.pop(key, None)
        except Exception as e:
            self.errors[key] = f"{type(e).__name__}: {e}"
            print(f"[plugin] ❌ {path.name}: {e}")
            traceback.print_exc()

    def _load_sandbox(self, path, entry):
        if sys.platform != "win32":
            raise ValueError("Windows AppContainer sandbox is unavailable on this platform.")
        source = _sandbox_source(path, entry)
        tool = entry.get("tool")
        if not isinstance(tool, dict) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,62}", str(tool.get("name", ""))):
            raise ValueError("A single valid sandbox tool declaration is required.")
        self._validate_declaration(tool, path)
        name = "sandbox." + path.relative_to(PLUGIN_DIR).as_posix()
        module = ModuleType(name)
        module.METADATA = {"execution": "appcontainer", "capabilities": ["isolated_computation"],
                           "timeout": max(1, min(float(entry.get("timeout", 20)), 60))}
        loaded = _Loaded(name, path, module, {tool["name"]: {"declaration": tool, "run": None}})
        loaded.source_digest = hashlib.sha256(source).digest()
        loaded.health = "sandbox_required"
        self.loaded[name] = loaded

    def load_all(self, builtin_names: set[str] | None = None) -> None:
        with self._lock:
            if self._inflight:
                return
            self._builtin = builtin_names if builtin_names is not None else getattr(self, "_builtin", set())
            for l in list(self.loaded.values()):
                self._unload(l)
            self.loaded.clear()
            self.errors.clear()
            self._mtimes.clear()
            self._trust = _trusted_plugins()
            self._sandbox = _sandbox_manifest()
            for f in self._files():
                self._mtimes[str(f)] = self._stamp(f)
                try:
                    relative = f.relative_to(PLUGIN_DIR).as_posix()
                    if relative not in self._trust and relative in self._sandbox:
                        self._load_sandbox(f, self._sandbox[relative])
                        continue
                    source = _verified_source(f, self._trust)
                except (OSError, ValueError) as exc:
                    self.errors[str(f)] = str(exc)
                    continue
                self._load_file(f, source=source)
            self._drop_conflicts()
            names = ", ".join(self.loaded) or "none"
            print(f"[plugin] loaded: {names}")

    def _unload(self, l: _Loaded) -> None:
        try:
            if hasattr(l.module, "on_unload"):
                l.module.on_unload(self._context_for(l))
        except Exception:
            pass

    def _drop_conflicts(self) -> None:
        seen: set[str] = set(getattr(self, "_builtin", set()))
        for l in self.loaded.values():
            for tname in list(l.tools):
                if tname in seen:
                    msg = f"tool name '{tname}' already exists - this tool was skipped"
                    self.errors[str(l.path)] = msg
                    print(f"[plugin] ⚠️ {l.path.name}: {msg}")
                    del l.tools[tname]
                else:
                    seen.add(tname)

    # ── queries ──────────────────────────────────────────────────────────────
    def declarations(self) -> list[dict]:
        with self._lock:
            return [t["declaration"] for l in self.loaded.values() for t in l.tools.values()]

    def has_tool(self, name: str) -> bool:
        with self._lock:
            return any(name in l.tools for l in self.loaded.values())

    def is_sandboxed(self, name):
        with self._lock:
            return any(item.sandboxed and name in item.tools for item in self.loaded.values())

    def tool_names(self) -> list[str]:
        return [d["name"] for d in self.declarations()]

    def describe(self) -> str:
        with self._lock:
            lines = []
            for l in self.loaded.values():
                desc = getattr(l.module, "DESCRIPTION", "")
                lines.append(
                    f"- {l.name} v{l.version} [{l.health}] ({l.path.name}): "
                    f"tools={list(l.tools)} capabilities={l.capabilities} permissions={sorted(l.permissions)} {desc}"
                )
            for f, e in self.errors.items():
                lines.append(f"- ERROR {Path(f).name}: {e}")
            return "\n".join(lines) or "No plugins installed. Put .py files in the plugins/ folder."

    # ── running ──────────────────────────────────────────────────────────────
    def run(self, name: str, args: dict) -> str:
        with self._lock:
            owner = next((l for l in self.loaded.values() if name in l.tools), None)
            fn = owner.tools[name]["run"] if owner else None
        if owner is None or (fn is None and not owner.sandboxed):
            return f"[FAILED] plugin tool '{name}' not found"
        from core.plugin_safety import check_call, verify_result
        if not isinstance(args, dict):
            return "[FAILED] Plugin arguments must be an object"
        error = check_call(name, args)
        if error:
            self.ctx.log(f"{name}: blocked: {error}")
            return f"[FAILED] {error}"
        if owner.health == "unhealthy":
            return f"[FAILED] plugin tool '{name}' is unhealthy"
        from core.risk import validate_arguments
        error = validate_arguments(owner.tools[name]["declaration"], args)
        if error:
            return f"[FAILED] Invalid plugin arguments: {error}"
        with self._lock:
            if self._stop.is_set():
                return "[CANCELLED] plugin manager is stopping"
            if self.loaded.get(owner.name) is not owner:
                return "[FAILED] Plugin changed; refresh tool declarations"
            if owner.name in self._inflight:
                return "[UNVERIFIED] Previous invocation of this plugin is still running. Do not retry."
            if len(self._inflight) >= 4:
                return "[FAILED] Plugin workers are busy"
            source = None
            if str(owner.path) in self._mtimes:
                try:
                    if owner.sandboxed:
                        relative = owner.path.relative_to(PLUGIN_DIR).as_posix()
                        entry = _sandbox_manifest().get(relative)
                        if entry != self._sandbox.get(relative):
                            raise ValueError("Sandbox approval changed.")
                        source = _sandbox_source(owner.path, entry)
                    else:
                        source = _verified_source(owner.path, _trusted_plugins())
                    if hashlib.sha256(source).digest() != owner.source_digest:
                        return "[FAILED] Plugin changed; reload before running it."
                except (OSError, ValueError):
                    return "[FAILED] Plugin approval is missing or its source changed."
            context = self._context_for(owner)
            def invoke():
                if owner.sandboxed:
                    from core.windows_plugin_sandbox import run_sandboxed
                    try:
                        return run_sandboxed(source, name, args, owner.timeout, context.cancelled)
                    finally:
                        self.ctx.log(f"{name}: AppContainer invocation ended")
                if owner.isolated:
                    from core.plugin_process import run_isolated
                    return run_isolated(owner.path, name, args, owner.timeout, context.cancelled, source=source)
                nparams = len(inspect.signature(fn).parameters)
                return fn(args, context) if nparams >= 2 else fn(args)
            future = self._pool.submit(invoke)
            self._inflight[owner.name] = (future, context)
            def finished(done):
                with self._lock:
                    current = self._inflight.get(owner.name)
                    if current and current[0] is done:
                        self._inflight.pop(owner.name, None)
            future.add_done_callback(finished)
        try:
            res = future.result(timeout=owner.timeout + (35 if owner.sandboxed else 3 if owner.isolated else 0))
        except TimeoutError:
            context.cancelled.set()
            future.cancel()
            # Running Python cannot be forcibly stopped safely. Block retries
            # until it actually exits; the restricted bridge is revoked.
            return (f"[UNVERIFIED] Plugin '{name}' timed out after {owner.timeout:g}s; "
                    "execution may still be active. Do not retry; inspect effects.")
        except Exception as e:
            return f"[FAILED] plugin tool '{name}' crashed\nREASON: {type(e).__name__}"
        out = res.to_llm() if isinstance(res, ActionResult) else ("" if res is None else str(res))
        out = verify_result(name, args, out)
        self.ctx.log(f"{name}: {out.splitlines()[0] if out else 'empty result'}")
        return out

    def discover(self, query: str = "") -> str:
        """Return capability metadata for selecting the most suitable plugin."""
        needle = query.lower().strip()
        with self._lock:
            rows = []
            for loaded in self.loaded.values():
                haystack = " ".join([
                    loaded.name, getattr(loaded.module, "DESCRIPTION", ""),
                    *loaded.capabilities, *loaded.tools,
                ]).lower()
                if needle and needle not in haystack:
                    continue
                rows.append({
                    "plugin": loaded.name,
                    "version": loaded.version,
                    "health": loaded.health,
                    "trust": "appcontainer" if loaded.sandboxed else "trusted_native",
                    "tools": sorted(loaded.tools),
                    "capabilities": loaded.capabilities,
                    "timeout": loaded.timeout,
                })
        return json.dumps(rows, ensure_ascii=False)

    def dispatch_notification(self, note: dict) -> None:
        with self._lock:
            mods = [l for l in self.loaded.values() if hasattr(l.module, "on_notification")]
        for loaded in mods:
            try:
                loaded.module.on_notification(note, self._context_for(loaded))
            except Exception as e:
                print(f"[plugin] on_notification error in {loaded.name}: {e}")

    def notification_policy(self, note: dict) -> str:
        """Return the most restrictive policy requested by notification plugins."""
        policies = ("normal", "silent", "dnd")
        selected = 0
        with self._lock:
            mods = [
                l for l in self.loaded.values()
                if hasattr(l.module, "notification_policy")
            ]
        for loaded in mods:
            try:
                policy = str(loaded.module.notification_policy(note, self._context_for(loaded))).lower().strip()
            except Exception as e:
                print(f"[plugin] notification_policy error in {loaded.name}: {e}")
                continue
            if policy in policies:
                selected = max(selected, policies.index(policy))
        return policies[selected]

    # ── hot reload ───────────────────────────────────────────────────────────
    def poll(self) -> bool:
        """Reload if the folder changed. Returns True if the tool list changed."""
        current = {str(f): self._stamp(f) for f in self._files()}
        sandbox = _sandbox_manifest()
        with self._lock:
            for name, (_, context) in self._inflight.items():
                loaded = self.loaded.get(name)
                if loaded and loaded.sandboxed:
                    relative = loaded.path.relative_to(PLUGIN_DIR).as_posix()
                    if sandbox.get(relative) != self._sandbox.get(relative):
                        context.cancelled.set()
        if current == self._mtimes and _trusted_plugins() == self._trust and sandbox == self._sandbox:
            return False
        before = json.dumps(self.declarations(), sort_keys=True, default=str)
        self.load_all()
        return json.dumps(self.declarations(), sort_keys=True, default=str) != before

    def start_watching(self, interval: float = 2.0) -> None:
        def loop():
            while not self._stop.wait(interval):
                try:
                    if self.poll() and self.on_change:
                        self.on_change()
                except Exception as e:
                    print(f"[plugin] watcher error: {e}")
        threading.Thread(target=loop, daemon=True, name="plugin-watch").start()

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            for future, context in self._inflight.values():
                context.cancelled.set()
            self._pool.shutdown(wait=False, cancel_futures=True)

    def remove(self, name: str) -> tuple[bool, str]:
        with self._lock:
            if self._inflight:
                return False, "Wait for active plugins to finish before disabling a plugin."
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", name):
            return False, "Invalid plugin name."
        for cand in (PLUGIN_DIR / f"{name}.py", PLUGIN_DIR / name / "plugin.py"):
            if cand.exists():
                trash = PLUGIN_DIR / "_disabled"
                trash.mkdir(exist_ok=True)
                cand.rename(trash / f"{name}_{int(time.time())}.py")
                self.load_all()
                if self.on_change:
                    self.on_change()
                return True, f"plugin '{name}' disabled (moved to plugins/_disabled)"
        return False, f"no plugin file called '{name}'"

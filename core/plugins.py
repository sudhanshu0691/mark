"""
Plugin system.

Drop a .py file into the  plugins/  folder and Mark picks it up automatically
(no code changes anywhere else).  The folder is watched: add, edit or delete a
plugin and Mark reloads it within ~2 seconds.

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

import importlib.util
import inspect
import json
import re
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, TimeoutError, Future
from pathlib import Path
from types import ModuleType
from typing import Any, Callable

from core.result import ActionResult

PLUGIN_DIR = Path(__file__).resolve().parent.parent / "plugins"


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


class PluginManager:
    def __init__(self, ctx: PluginContext):
        self.ctx = ctx
        self.loaded: dict[str, _Loaded] = {}     # plugin name -> loaded
        self.errors: dict[str, str] = {}         # file -> error text
        self._mtimes: dict[str, float] = {}
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
    def _stamp(p: Path) -> float:
        try:
            return max(f.stat().st_mtime for f in ([p] + list(p.parent.glob("*.py"))))
        except OSError:
            return 0.0

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

    def _load_file(self, path: Path) -> None:
        key = str(path)
        pname = path.parent.name if path.name == "plugin.py" else path.stem
        try:
            spec = importlib.util.spec_from_file_location(f"mark_plugin_{pname}", path)
            if spec is None or spec.loader is None:
                raise ImportError("cannot create import spec")
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
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
            for f in self._files():
                self._mtimes[str(f)] = self._stamp(f)
                self._load_file(f)
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
        if fn is None or owner is None:
            return f"[FAILED] plugin tool '{name}' not found"
        from core.plugin_safety import check_call, verify_result
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
            context = self._context_for(owner)
            def invoke():
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
            res = future.result(timeout=owner.timeout)
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
        if current == self._mtimes:
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

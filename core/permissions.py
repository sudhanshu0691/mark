"""
Access policy for file / system actions.  Edit config/permissions.json to change it.

Default: full access to every drive.  Only the Windows system folders are
protected (writes/deletes there are refused) unless allow_system_paths = true.
"""
from __future__ import annotations

import json
import os
import platform
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
PERM_FILE = BASE_DIR / "config" / "permissions.json"

DEFAULTS = {
    "allowed_roots": [],             # [] = everything. Example: ["C:\\\\Users\\\\me", "D:\\\\"]
    "allow_project_edits": False,    # opt in to editing MARK source files
    "allow_system_paths": False,     # True = may modify C:\\Windows, Program Files ...
    "delete_mode": "recycle",        # "recycle" (recoverable) | "permanent"
    "confirm_permanent_delete": True,
    "max_read_chars": 20000,
    "extra_blocked_paths": [],
}

_SYSTEM_DIRS_WIN = [
    os.environ.get("WINDIR", r"C:\Windows"),
    os.environ.get("ProgramFiles", r"C:\Program Files"),
    os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
    os.environ.get("ProgramData", r"C:\ProgramData") + r"\Microsoft",
    r"C:\System Volume Information",
    r"C:\$Recycle.Bin",
]
_SYSTEM_DIRS_POSIX = ["/bin", "/boot", "/dev", "/etc", "/lib", "/proc", "/sbin", "/sys", "/usr"]


def load() -> dict:
    cfg = dict(DEFAULTS)
    try:
        cfg.update(json.loads(PERM_FILE.read_text(encoding="utf-8")))
    except FileNotFoundError:
        try:
            PERM_FILE.write_text(json.dumps(DEFAULTS, indent=2), encoding="utf-8")
        except Exception:
            pass
    except (OSError, ValueError, TypeError):
        cfg["invalid_policy"] = True
    return cfg


def _norm(p: str | Path) -> str:
    return os.path.normcase(str(Path(p).resolve())).rstrip("\\/")


def _inside(child: str, parent: str) -> bool:
    return child == parent or child.startswith(parent + os.sep)


def check(path: str | Path, write: bool = False) -> tuple[bool, str]:
    """Return (allowed, reason_if_not)."""
    cfg = load()
    if cfg.get("invalid_policy"):
        return False, "File access policy is unreadable. Repair config/permissions.json."
    try:
        target = _norm(path)
    except (OSError, RuntimeError, ValueError):
        return False, "Path could not be resolved safely."
    protected_files = {
        _norm(BASE_DIR / part) for part in (
            "config/api_keys.json", "config/runtime.env", "config/.env", "config/livekit.ini",
            "config/contacts.json", "config/phone.json", "memory/user_memory.json",
            "memory/long_term.json", "memory/conversation_history.json",
        )
    }
    if (
        target in protected_files
        or any(_inside(target, _norm(BASE_DIR / part)) for part in ("memory/identity", "data/calls"))
        or (_inside(target, _norm(BASE_DIR / "data"))
            and Path(target).name.startswith(("mark.db", "tasks.db", "cloud_usage.db", "workflows.db", "workflows.key")))
        or (_inside(target, _norm(BASE_DIR)) and Path(target).name in {".env", "runtime.env"})
    ):
        return False, "Access to MARK credentials and private runtime data is restricted."
    # The user's C: exception applies to reads as well as writes.
    if platform.system() == "Windows" and Path(target).drive.lower() == "c:":
        home = Path.home()
        allowed_c = [home / "Downloads", home / "Desktop", home / "Music", home / "OneDrive" / "Desktop"]
        if not any(_inside(target, _norm(root)) for root in allowed_c):
            return False, "C: access is limited to Downloads, Desktop and Music."
    if write and _inside(target, _norm(BASE_DIR)) and not _inside(target, _norm(BASE_DIR / "data")):
        protected = (".git", ".venv", "venv", "config", "memory/identity", "__pycache__")
        private_file = Path(target).name.lower() in {".env", "runtime.env"}
        blocked_project = any(_inside(target, _norm(BASE_DIR / part)) for part in protected)
        safe_suffixes = {".py", ".md", ".txt", ".yaml", ".yml", ".toml", ".ini", ".bat"}
        source_file = Path(target).suffix.lower() in safe_suffixes
        if (not cfg.get("allow_project_edits") or blocked_project or private_file or
                (not source_file and Path(target) != BASE_DIR and not Path(target).is_dir())):
            return False, "MARK project edits are restricted by the local file access policy."


    roots = [_norm(r) for r in cfg.get("allowed_roots") or []]
    if roots and not any(_inside(target, r) for r in roots):
        return False, f"'{path}' is outside allowed_roots in config/permissions.json"

    blocked = [_norm(b) for b in cfg.get("extra_blocked_paths") or []]
    if any(_inside(target, b) for b in blocked):
        return False, f"'{path}' is listed in extra_blocked_paths"

    if write and not cfg.get("allow_system_paths"):
        sysdirs = _SYSTEM_DIRS_WIN if platform.system() == "Windows" else _SYSTEM_DIRS_POSIX
        for s in sysdirs:
            if _inside(target, _norm(s)):
                return False, (f"'{path}' is a protected system folder. Set "
                               f"\"allow_system_paths\": true in config/permissions.json to allow it.")
    return True, ""

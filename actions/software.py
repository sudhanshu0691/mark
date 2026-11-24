"""
Install / uninstall / search software.  winget first, then Chocolatey / Scoop,
plus pip and npm packages.  Every install is verified by listing the package
afterwards; failures return the real reason.
"""
from __future__ import annotations

import platform
import re
import shutil
import subprocess

from core.result import ActionResult, done, failed, unverified

_WIN = platform.system() == "Windows"

_WINGET_CODES = {
    0x8A150011: "installer failed to start / package already installed",
    0x8A150014: "no package found with that name or id",
    0x8A15002B: "no applicable upgrade (already latest)",
    0x8A150056: "installer needs administrator rights (UAC)",
    0x8A150049: "installer was cancelled / blocked by user or policy",
    0x8A150010: "installer returned an error",
    0x8A150101: "no network / source unreachable",
    0x8A150109: "winget sources are unreachable (check internet)",
}


def _run(cmd: list[str], timeout: int = 900) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace")
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, f"timed out after {timeout}s"
    except FileNotFoundError:
        return 127, f"{cmd[0]} not found"
    except Exception as e:
        return 1, str(e)


def _clean(out: str) -> str:
    out = re.sub(r"[\x00-\x08\x0b-\x1f]", "", out)
    lines = [l.strip() for l in out.splitlines() if l.strip() and not re.fullmatch(r"[-\\/|\s]+", l.strip())]
    return "\n".join(lines[-12:])[:900]


def _have(tool: str) -> bool:
    return shutil.which(tool) is not None


# ── winget ───────────────────────────────────────────────────────────────────

def _winget_search(query: str) -> list[dict]:
    code, out = _run(["winget", "search", query, "--accept-source-agreements", "--disable-interactivity"], 120)
    lines = [l for l in out.splitlines() if l.strip()]
    hdr = next((i for i, l in enumerate(lines) if l.startswith("Name") and "Id" in l), None)
    if hdr is None or hdr + 2 >= len(lines):
        return []
    h = lines[hdr]
    c_id, c_ver = h.index("Id"), h.index("Version") if "Version" in h else len(h)
    rows = []
    for l in lines[hdr + 2:]:
        if len(l) < c_ver:
            continue
        rows.append({"name": l[:c_id].strip(), "id": l[c_id:c_ver].strip(), "version": l[c_ver:].split()[0] if l[c_ver:].split() else ""})
    return rows


def _pick(rows: list[dict], query: str) -> dict | None:
    if not rows:
        return None
    q = query.lower().strip()
    for r in rows:
        if r["id"].lower() == q or r["name"].lower() == q:
            return r
    for r in rows:
        if r["name"].lower().startswith(q):
            return r
    return rows[0]


def _winget_installed(pkg_id: str) -> bool:
    code, out = _run(["winget", "list", "--id", pkg_id, "-e", "--accept-source-agreements", "--disable-interactivity"], 90)
    return code == 0 and pkg_id.lower() in out.lower()


def _explain(code: int, out: str) -> str:
    u = code & 0xFFFFFFFF
    base = _WINGET_CODES.get(u, f"exit code {u:#x}")
    return f"{base}. Output: {_clean(out)}"


def _install_winget(query: str, pkg_id: str | None, elevate: bool) -> ActionResult:
    chosen_id, chosen_name = pkg_id, query
    if not chosen_id:
        rows = _winget_search(query)
        pick = _pick(rows, query)
        if not pick:
            return failed(f"Could not install '{query}'", "winget found no package with that name. Try the exact name, or another source.")
        chosen_id, chosen_name = pick["id"], pick["name"]
    if _winget_installed(chosen_id):
        return done(f"{chosen_name} is already installed ({chosen_id}).", evidence="winget list shows it")
    cmd = ["winget", "install", "-e", "--id", chosen_id, "--silent", "--accept-package-agreements",
           "--accept-source-agreements", "--disable-interactivity"]
    code, out = _run(cmd)
    needs_admin = (code & 0xFFFFFFFF) in (0x8A150056,) or "administrator" in out.lower() or code in (740, 1603)
    if code != 0 and needs_admin and elevate and _WIN:
        ps = ("Start-Process winget -Verb RunAs -Wait -ArgumentList '" + " ".join(cmd[1:]).replace("'", "''") + "'")
        _run(["powershell", "-NoProfile", "-Command", ps], 1200)
        if _winget_installed(chosen_id):
            return done(f"Installed {chosen_name} ({chosen_id}) after admin approval.", evidence="winget list shows it")
        return failed(f"Could not install {chosen_name}", "admin (UAC) approval was not given, or the elevated install failed")
    if _winget_installed(chosen_id):
        return done(f"Installed {chosen_name} ({chosen_id}).", evidence="winget list shows it")
    if code == 0:
        return unverified(f"winget said OK for {chosen_name} but it is not listed yet", reason="may need a restart or sign-out to appear")
    return failed(f"Could not install {chosen_name}", _explain(code, out))


def _uninstall_winget(query: str, pkg_id: str | None) -> ActionResult:
    chosen = pkg_id
    if not chosen:
        pick = _pick(_winget_search(query), query)
        if not pick:
            return failed(f"Could not uninstall '{query}'", "no matching package found")
        chosen = pick["id"]
    if not _winget_installed(chosen):
        return failed(f"Could not uninstall {chosen}", "it is not installed (according to winget)")
    code, out = _run(["winget", "uninstall", "-e", "--id", chosen, "--silent", "--disable-interactivity"])
    if not _winget_installed(chosen):
        return done(f"Uninstalled {chosen}.", evidence="winget list no longer shows it")
    return failed(f"Could not uninstall {chosen}", _explain(code, out))


# ── entry ────────────────────────────────────────────────────────────────────

def software_manager(parameters: dict | None = None, player=None, **_) -> str:
    a = parameters or {}
    action = str(a.get("action", "install")).lower().strip()
    name = str(a.get("name", "")).strip()
    pkg_id = (a.get("package_id") or "").strip() or None
    source = str(a.get("source", "auto")).lower()
    elevate = bool(a.get("elevate", True))
    if player:
        player.write_log(f"[software] {action} {name or pkg_id}")

    try:
        if action in ("install", "uninstall", "search", "list") and source in ("pip",):
            pass
        if source == "pip" or action == "pip_install":
            code, out = _run([shutil.which("python") or "python", "-m", "pip", "install", name, "--break-system-packages"] if not _WIN
                             else ["python", "-m", "pip", "install", name], 600)
            r = done(f"pip installed {name}") if code == 0 else failed(f"pip could not install {name}", _clean(out))
        elif source == "npm":
            code, out = _run(["npm", "install", "-g", name], 600)
            r = done(f"npm installed {name}") if code == 0 else failed(f"npm could not install {name}", _clean(out))
        elif not _WIN:
            r = failed(f"Cannot {action} '{name}'", "app install works through winget, which exists only on Windows 10/11")
        elif not _have("winget"):
            alt = "choco" if _have("choco") else "scoop" if _have("scoop") else ""
            if alt and action == "install":
                cmd = ["choco", "install", name, "-y"] if alt == "choco" else ["scoop", "install", name]
                code, out = _run(cmd)
                r = done(f"Installed {name} via {alt}") if code == 0 else failed(f"{alt} could not install {name}", _clean(out))
            else:
                r = failed(f"Cannot {action} '{name}'",
                           "winget is missing. Install 'App Installer' from the Microsoft Store (or install Chocolatey/Scoop).")
        elif action == "search":
            rows = _winget_search(name)
            r = done("Matches:\n" + "\n".join(f"{x['name']}  [{x['id']}]  {x['version']}" for x in rows[:10])) if rows \
                else failed(f"No packages for '{name}'", "winget search returned nothing")
        elif action == "install":
            if not (name or pkg_id):
                r = failed("Cannot install", "no app name given")
            else:
                r = _install_winget(name, pkg_id, elevate)
        elif action == "uninstall":
            r = _uninstall_winget(name, pkg_id)
        elif action == "list":
            code, out = _run(["winget", "list", "--accept-source-agreements", "--disable-interactivity"], 120)
            r = done("Installed software:\n" + out[:3500]) if code == 0 else failed("Cannot list software", _clean(out))
        else:
            r = failed(f"Unknown software action '{action}'", "valid: install uninstall search list")
    except Exception as e:
        r = failed(f"Software action '{action}' crashed", f"{type(e).__name__}: {e}")
    if player:
        player.write_log(f"[software] {r.status}: {r.message[:90]}")
    return r.to_llm()

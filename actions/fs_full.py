"""
Full file-system control: read / write / copy / move / delete / rename / find / zip ...
Every mutating action is VERIFIED afterwards and returns an ActionResult.
"""
from __future__ import annotations

import hashlib
import os
import platform
import shutil
import string
import time
import zipfile
from datetime import datetime
from pathlib import Path

from core import permissions
from core.result import ActionResult, done, failed, unverified

try:
    import send2trash as _s2t
except Exception:  # pragma: no cover
    _s2t = None

_WIN = platform.system() == "Windows"


# ── path helpers ─────────────────────────────────────────────────────────────

def _known(name: str) -> Path | None:
    home = Path.home()
    cand = {
        "desktop": ["Desktop", "OneDrive/Desktop"],
        "downloads": ["Downloads"],
        "documents": ["Documents", "OneDrive/Documents"],
        "pictures": ["Pictures", "OneDrive/Pictures"],
        "music": ["Music"],
        "videos": ["Videos"],
    }
    if name == "home":
        return home
    if name in ("temp", "tmp"):
        return Path(os.environ.get("TEMP", "/tmp"))
    if name == "appdata":
        return Path(os.environ.get("APPDATA", home))
    if name == "localappdata":
        return Path(os.environ.get("LOCALAPPDATA", home))
    for sub in cand.get(name, []):
        p = home / sub
        if p.exists():
            return p
    return None


def resolve(raw: str, name: str = "") -> Path:
    raw = (raw or "").strip().strip('"')
    if not raw:
        raw = "desktop"
    low = raw.lower().replace("\\", "/")
    first, _, rest = low.partition("/")
    k = _known(first)
    if k is not None:
        base = k / raw.replace("\\", "/").partition("/")[2] if rest else k
    else:
        base = Path(os.path.expandvars(os.path.expanduser(raw)))
    if name:
        base = base / name
    return base


def _fmt_size(n: float) -> str:
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.0f} {u}" if u == "B" else f"{n:.1f} {u}"
        n /= 1024
    return f"{n:.1f} PB"


def _guard(p: Path, write: bool) -> ActionResult | None:
    ok, why = permissions.check(p, write=write)
    if not ok:
        return failed(f"Blocked: {p}", why)
    return None


def _dir_size(p: Path) -> int:
    total = 0
    for root, _d, files in os.walk(p):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def _sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ── actions ──────────────────────────────────────────────────────────────────

def _drives() -> ActionResult:
    out = []
    if _WIN:
        for letter in string.ascii_uppercase:
            root = f"{letter}:\\"
            if os.path.exists(root):
                try:
                    u = shutil.disk_usage(root)
                    out.append(f"{root}  total {_fmt_size(u.total)}, free {_fmt_size(u.free)}")
                except OSError:
                    out.append(f"{root}  (not ready)")
    else:
        u = shutil.disk_usage("/")
        out.append(f"/  total {_fmt_size(u.total)}, free {_fmt_size(u.free)}")
    return done("Drives:\n" + "\n".join(out), data=out)


def _list(p: Path, show_hidden: bool, limit: int = 200) -> ActionResult:
    g = _guard(p, False)
    if g:
        return g
    if not p.exists():
        return failed(f"Cannot list {p}", "path does not exist")
    if p.is_file():
        return done(f"{p} is a file ({_fmt_size(p.stat().st_size)})")
    rows = []
    try:
        entries = sorted(p.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
    except PermissionError:
        return failed(f"Cannot list {p}", "Windows denied access (run Mark as administrator or fix folder permissions)")
    for e in entries:
        if not show_hidden and e.name.startswith("."):
            continue
        try:
            st = e.stat()
            kind = "DIR " if e.is_dir() else "FILE"
            size = "" if e.is_dir() else _fmt_size(st.st_size)
            rows.append(f"{kind} {e.name}  {size}  {datetime.fromtimestamp(st.st_mtime):%Y-%m-%d %H:%M}")
        except OSError:
            rows.append(f"???? {e.name}")
    more = f"\n... and {len(rows) - limit} more" if len(rows) > limit else ""
    return done(f"{p}  ({len(rows)} items)\n" + "\n".join(rows[:limit]) + more)


def _read(p: Path, max_chars: int | None) -> ActionResult:
    g = _guard(p, False)
    if g:
        return g
    if not p.is_file():
        return failed(f"Cannot read {p}", "file does not exist" if not p.exists() else "it is a folder")
    cap = max_chars or permissions.load().get("max_read_chars", 20000)
    ext = p.suffix.lower()
    try:
        if ext == ".pdf":
            try:
                import pdfplumber
                with pdfplumber.open(p) as pdf:
                    text = "\n".join((pg.extract_text() or "") for pg in pdf.pages[:50])
            except ImportError:
                return failed(f"Cannot read {p.name}", "pdfplumber not installed (pip install pdfplumber)")
        elif ext == ".docx":
            try:
                import docx
                text = "\n".join(par.text for par in docx.Document(str(p)).paragraphs)
            except ImportError:
                return failed(f"Cannot read {p.name}", "python-docx not installed (pip install python-docx)")
        else:
            raw = p.read_bytes()
            if b"\x00" in raw[:2048]:
                return failed(f"Cannot read {p.name} as text",
                              f"binary file ({_fmt_size(len(raw))}); use file_processor or open it")
            text = raw.decode("utf-8", errors="replace")
    except PermissionError:
        return failed(f"Cannot read {p}", "access denied by Windows (file locked or needs admin)")
    cut = " ...[truncated]" if len(text) > cap else ""
    return done(f"{p.name} ({len(text)} chars){cut}:\n{text[:cap]}")


def _write(p: Path, content: str, append: bool) -> ActionResult:
    g = _guard(p, True)
    if g:
        return g
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        before = p.stat().st_size if p.exists() else 0
        with open(p, "a" if append else "w", encoding="utf-8", newline="") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
    except PermissionError:
        return failed(f"Cannot write {p}", "access denied by Windows (file open elsewhere / protected folder)")
    except OSError as e:
        return failed(f"Cannot write {p}", str(e))
    if not p.exists():
        return unverified(f"Wrote {p} but it does not exist afterwards", "unknown filesystem problem")
    after = p.stat().st_size
    expected = len(content.encode("utf-8"))
    if (append and after - before != expected) or (not append and after != expected):
        return unverified(f"Wrote {p} but size is unexpected ({after} bytes)", "possible newline translation")
    return done(f"{'Appended to' if append else 'Wrote'} {p} ({after} bytes)", evidence=f"size on disk={after}")


def _mkdir(p: Path) -> ActionResult:
    g = _guard(p, True)
    if g:
        return g
    try:
        p.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return failed(f"Cannot create folder {p}", str(e))
    return done(f"Folder ready: {p}") if p.is_dir() else unverified(f"mkdir ran for {p} but folder not seen")


def _copy(src: Path, dst: Path) -> ActionResult:
    for pth, w in ((src, False), (dst, True)):
        g = _guard(pth, w)
        if g:
            return g
    if not src.exists():
        return failed(f"Cannot copy {src}", "source does not exist")
    if dst.is_dir() or str(dst).endswith(("\\", "/")):
        dst.mkdir(parents=True, exist_ok=True)
        dst = dst / src.name
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(src, dst, dirs_exist_ok=True)
        else:
            shutil.copy2(src, dst)
    except PermissionError:
        return failed(f"Cannot copy to {dst}", "access denied by Windows")
    except OSError as e:
        return failed(f"Cannot copy to {dst}", str(e))
    if not dst.exists():
        return unverified(f"Copy ran but {dst} not found")
    if src.is_file() and _sha(src) != _sha(dst):
        return failed(f"Copy to {dst} is corrupt", "checksum differs from source")
    return done(f"Copied {src.name} -> {dst}", evidence="exists + checksum matches" if src.is_file() else "exists")


def _move(src: Path, dst: Path) -> ActionResult:
    for pth in (src, dst):
        g = _guard(pth, True)
        if g:
            return g
    if not src.exists():
        return failed(f"Cannot move {src}", "source does not exist")
    if dst.is_dir():
        dst = dst / src.name
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
    except PermissionError:
        return failed(f"Cannot move {src}", "access denied / file in use by another program")
    except OSError as e:
        return failed(f"Cannot move {src}", str(e))
    if dst.exists() and not src.exists():
        return done(f"Moved {src.name} -> {dst}", evidence="destination exists, source gone")
    return unverified(f"Move ran but state is odd (dst exists={dst.exists()}, src exists={src.exists()})")


def _rename(src: Path, new_name: str) -> ActionResult:
    g = _guard(src, True)
    if g:
        return g
    if not src.exists():
        return failed(f"Cannot rename {src}", "does not exist")
    if not new_name or any(c in new_name for c in '\\/:*?"<>|'):
        return failed("Cannot rename", f"invalid new name '{new_name}'")
    dst = src.with_name(new_name)
    if dst.exists():
        return failed(f"Cannot rename to {new_name}", "a file with that name already exists")
    try:
        src.rename(dst)
    except OSError as e:
        return failed(f"Cannot rename {src.name}", str(e))
    return done(f"Renamed {src.name} -> {new_name}") if dst.exists() and not src.exists() else unverified("Rename ran, not confirmed")


def _delete(p: Path, permanent: bool, confirmed: bool) -> ActionResult:
    g = _guard(p, True)
    if g:
        return g
    if not p.exists():
        return failed(f"Cannot delete {p}", "does not exist")
    cfg = permissions.load()
    drive_root = Path(p.anchor) if p.anchor else None
    if p == Path.home() or (drive_root and p == drive_root):
        return failed(f"Refusing to delete {p}", "this is your home folder or a drive root")
    permanent = permanent or cfg.get("delete_mode") == "permanent"
    if permanent and cfg.get("confirm_permanent_delete", True) and not confirmed:
        return failed(f"Permanent delete of {p} not done",
                      "permanent delete needs confirmed=true. Ask the user to confirm first.")
    try:
        if not permanent and _s2t:
            _s2t.send2trash(str(p))
            how = "moved to Recycle Bin"
        elif not permanent and not _s2t:
            return failed(f"Cannot delete {p}", "send2trash not installed (pip install send2trash) - refusing to delete permanently without confirmation")
        else:
            shutil.rmtree(p) if p.is_dir() else p.unlink()
            how = "permanently deleted"
    except PermissionError:
        return failed(f"Cannot delete {p}", "access denied / file in use by another program")
    except OSError as e:
        return failed(f"Cannot delete {p}", str(e))
    return done(f"{p.name}: {how}", evidence="path no longer exists") if not p.exists() else unverified(f"Delete ran but {p} still exists")


def _find(root: str, name: str, ext: str, contains: str, limit: int) -> ActionResult:
    roots: list[Path] = []
    if root:
        roots = [resolve(root)]
    elif _WIN:
        roots = [Path(f"{c}:\\") for c in string.ascii_uppercase if os.path.exists(f"{c}:\\")]
    else:
        roots = [Path.home()]
    name_l, ext_l, cont_l = name.lower(), ext.lower().lstrip("."), contains.lower()
    hits: list[str] = []
    t0 = time.time()
    skip = {"$recycle.bin", "system volume information", "windows", "node_modules", ".git", "appdata"}
    for r in roots:
        for dp, dns, fns in os.walk(r):
            dns[:] = [d for d in dns if d.lower() not in skip]
            for fn in fns:
                low = fn.lower()
                if name_l and name_l not in low:
                    continue
                if ext_l and not low.endswith("." + ext_l):
                    continue
                full = os.path.join(dp, fn)
                if cont_l:
                    try:
                        if os.path.getsize(full) > 5_000_000:
                            continue
                        with open(full, "r", encoding="utf-8", errors="ignore") as f:
                            if cont_l not in f.read().lower():
                                continue
                    except OSError:
                        continue
                hits.append(full)
                if len(hits) >= limit:
                    break
            if len(hits) >= limit or time.time() - t0 > 60:
                break
        if len(hits) >= limit or time.time() - t0 > 60:
            break
    if not hits:
        return done("Search finished: no matching files found.", evidence=f"searched {', '.join(map(str, roots))}")
    cut = " (stopped at limit/60s)" if len(hits) >= limit or time.time() - t0 > 60 else ""
    return done(f"Found {len(hits)} file(s){cut}:\n" + "\n".join(hits), data=hits)


def _info(p: Path) -> ActionResult:
    g = _guard(p, False)
    if g:
        return g
    if not p.exists():
        return failed(f"No info for {p}", "does not exist")
    st = p.stat()
    size = _dir_size(p) if p.is_dir() else st.st_size
    return done(
        f"{p}\ntype: {'folder' if p.is_dir() else 'file'}\nsize: {_fmt_size(size)}\n"
        f"modified: {datetime.fromtimestamp(st.st_mtime):%Y-%m-%d %H:%M:%S}\n"
        f"created: {datetime.fromtimestamp(st.st_ctime):%Y-%m-%d %H:%M:%S}"
    )


def _zip(src: Path, dst: Path | None) -> ActionResult:
    g = _guard(src, False)
    if g:
        return g
    if not src.exists():
        return failed(f"Cannot zip {src}", "does not exist")
    dst = dst or src.with_suffix(".zip") if src.is_file() else (dst or Path(str(src) + ".zip"))
    g = _guard(dst, True)
    if g:
        return g
    try:
        with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as z:
            if src.is_file():
                z.write(src, src.name)
            else:
                for dp, _d, fns in os.walk(src):
                    for fn in fns:
                        full = Path(dp) / fn
                        z.write(full, full.relative_to(src.parent))
    except OSError as e:
        return failed(f"Cannot create {dst}", str(e))
    return done(f"Created {dst} ({_fmt_size(dst.stat().st_size)})") if dst.exists() else unverified("zip ran, file not seen")


def _unzip(src: Path, dst: Path | None) -> ActionResult:
    g = _guard(src, False)
    if g:
        return g
    if not src.is_file():
        return failed(f"Cannot unzip {src}", "zip file does not exist")
    dst = dst or src.with_suffix("")
    g = _guard(dst, True)
    if g:
        return g
    try:
        with zipfile.ZipFile(src) as z:
            base = dst.resolve()
            for m in z.namelist():
                if not str((base / m).resolve()).startswith(str(base)):
                    return failed("Unzip stopped", f"unsafe path inside archive: {m}")
            z.extractall(dst)
            n = len(z.namelist())
    except (OSError, zipfile.BadZipFile) as e:
        return failed(f"Cannot unzip {src}", str(e))
    return done(f"Extracted {n} entries to {dst}", evidence="extraction completed without error")


def _open(p: Path) -> ActionResult:
    if not p.exists():
        return failed(f"Cannot open {p}", "does not exist")
    try:
        if _WIN:
            os.startfile(str(p))  # type: ignore[attr-defined]
        else:
            import subprocess
            subprocess.Popen(["xdg-open", str(p)])
    except OSError as e:
        return failed(f"Cannot open {p}", str(e))
    return unverified(f"Asked Windows to open {p}", reason="Windows gives no confirmation that the app window appeared")


# ── entry point ──────────────────────────────────────────────────────────────

def fs_control(parameters: dict | None = None, player=None, **_) -> str:
    a = parameters or {}
    action = str(a.get("action", "")).lower().strip()
    if action in {"delete", "move"}:
        return failed("Action blocked", "Delete and move are disabled by user policy.").to_llm()
    path = a.get("path", "")
    name = a.get("name", "")
    if player:
        player.write_log(f"[fs] {action} {name or path}")
    try:
        if action == "drives":
            r = _drives()
        elif action == "list":
            r = _list(resolve(path, name), bool(a.get("show_hidden")))
        elif action == "read":
            r = _read(resolve(path, name), int(a["max_chars"]) if a.get("max_chars") else None)
        elif action in ("write", "create_file"):
            r = _write(resolve(path, name), str(a.get("content", "")), bool(a.get("append")))
        elif action == "append":
            r = _write(resolve(path, name), str(a.get("content", "")), True)
        elif action in ("mkdir", "create_folder"):
            r = _mkdir(resolve(path, name))
        elif action == "copy":
            r = _copy(resolve(path, name), resolve(a.get("destination", "")))
        elif action == "move":
            r = _move(resolve(path, name), resolve(a.get("destination", "")))
        elif action == "rename":
            r = _rename(resolve(path, name), str(a.get("new_name", "")))
        elif action == "delete":
            r = _delete(resolve(path, name), bool(a.get("permanent")), bool(a.get("confirmed")))
        elif action == "find":
            r = _find(str(resolve(path)) if a.get("path") else "", str(name), str(a.get("extension", "")),
                      str(a.get("contains", "")), min(int(a.get("max_results", 30)), 200))
        elif action == "info":
            r = _info(resolve(path, name))
        elif action == "zip":
            r = _zip(resolve(path, name), resolve(a["destination"]) if a.get("destination") else None)
        elif action == "unzip":
            r = _unzip(resolve(path, name), resolve(a["destination"]) if a.get("destination") else None)
        elif action == "open":
            r = _open(resolve(path, name))
        elif action == "disk_usage":
            u = shutil.disk_usage(resolve(path or "home"))
            r = done(f"total {_fmt_size(u.total)}, used {_fmt_size(u.used)}, free {_fmt_size(u.free)}")
        else:
            r = failed(f"Unknown file action '{action}'",
                       "valid: drives list read write append mkdir copy move rename delete find info zip unzip open disk_usage")
    except Exception as e:  # never let a crash look like success
        r = failed(f"File action '{action}' crashed", f"{type(e).__name__}: {e}")
    if player:
        player.write_log(f"[fs] {r.status}: {r.message.splitlines()[0][:90]}")
    return r.to_llm()

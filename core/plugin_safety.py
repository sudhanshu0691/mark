"""Policy and output checks for the existing work plugins."""
from pathlib import Path
import re
from core import permissions
from core.paths import VIZ_DIR


def check_call(name: str, args: dict) -> str:
    action = str(args.get("action", "")).lower().strip()
    if (name in {"workspace", "notes"} and action == "delete") or (name == "ppt_studio" and action in {"delete_slide", "rename"}):
        return "Delete and move operations are disabled by user policy."
    checks = []
    if name == "excel":
        checks.append((args.get("path"), action not in {"inspect", "profile"}))
    elif name == "ppt_studio":
        checks.append((args.get("path"), action not in {"read", "list", "save_as"}))
        if args.get("image_path"):
            checks.append((args["image_path"], False))
        if args.get("new_path"):
            checks.append((args["new_path"], True))
    elif name == "media_studio":
        checks.append((args.get("path"), False))
        if action not in {"info", "image_info", "audio_info", "video_info"}:
            checks.append((args.get("out") or args.get("path"), True))
    elif name == "create_visualization":
        slug = str(args.get("output_name") or args.get("title") or "chart")
        if "/" in slug or "\\" in slug or slug in {".", ".."}:
            return "Chart output name must be a file name, not a path."
        checks.append((VIZ_DIR, True))
    for path, write in checks:
        if path:
            ok, reason = permissions.check(Path(str(path)).expanduser(), write=write)
            if not ok:
                return reason
    return ""


def verify_result(name: str, args: dict, result: str) -> str:
    if not result.startswith("[DONE]"):
        return result
    action = str(args.get("action", "")).lower().strip()
    path = None
    if name == "create_visualization":
        match = re.search(r"Chart saved: ([^\r\n]+)", result)
        path = Path(match.group(1)) if match else None
        if path is None:
            return "[UNVERIFIED] Chart tool did not report an output path.\n" + result
    elif name == "ppt_studio" and action not in {"read", "list"}:
        path = Path(str(args.get("new_path") if action == "save_as" else args.get("path") or ""))
        if path.suffix.lower() != ".pptx":
            path = path.with_suffix(".pptx")
    elif name == "excel" and action not in {"inspect", "profile"} and args.get("path"):
        path = Path(str(args["path"]))
    if path is not None:
        try:
            if not path.is_file() or path.stat().st_size == 0:
                return f"[FAILED] Output verification failed: {path} is missing or empty.\n" + result
            if name == "create_visualization":
                from PIL import Image
                with Image.open(path) as image:
                    image.verify()
        except Exception as exc:
            return f"[FAILED] Output verification failed for {path}: {exc}"
        return result + f"\nVERIFIED OUTPUT: {path.resolve()} ({path.stat().st_size} bytes)"
    return result

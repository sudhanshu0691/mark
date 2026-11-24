"""
PPT Studio - create and edit PowerPoint presentations (.pptx) with python-pptx.

Every save RE-OPENS the file and counts its slides before reporting DONE, so a
half-written deck can never be claimed as "done".
"""
from __future__ import annotations

from pathlib import Path

from core.result import done, failed

NAME = "ppt_studio"
DESCRIPTION = ("Create and edit PowerPoint presentations: new deck, add title/bullet slides, "
               "insert images, read, edit, reorder and delete slides. Verifies by reopening the file.")
METADATA = {
    "version": "1.0.0",
    "capabilities": ["pptx", "powerpoint", "presentation", "slides", "office"],
    "permissions": [],
    "timeout": 60,
}

DECLARATION = {
    "name": "ppt_studio",
    "description": (
        "Create or edit a .pptx presentation. "
        "action: create | add_slide | add_text | add_image | read | list | "
        "delete_slide | duplicate | rename | save_as. "
        "path = file path, slides = list of {title, bullets} for create."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "description": "create | add_slide | add_text | add_image | read | list | delete_slide | duplicate | rename | save_as"},
            "path": {"type": "STRING", "description": "Path to the .pptx file"},
            "title": {"type": "STRING", "description": "Slide title / heading"},
            "bullets": {
                "type": "ARRAY",
                "items": {"type": "STRING"},
                "description": "Bullet lines for the slide body",
            },
            "text": {"type": "STRING", "description": "Text to append or set"},
            "index": {"type": "INTEGER", "description": "1-based slide number for edit/delete/duplicate"},
            "image_path": {"type": "STRING", "description": "Image file to insert"},
            "slides": {
                "type": "ARRAY",
                "items": {
                    "type": "OBJECT",
                    "properties": {
                        "title": {"type": "STRING"},
                        "bullets": {
                            "type": "ARRAY",
                            "items": {"type": "STRING"},
                        },
                    },
                    "required": ["title"],
                },
                "description": "For create: [{title, bullets}]",
            },
            "new_path": {"type": "STRING", "description": "Target path for save_as / rename"},
        },
        "required": ["action", "path"],
    },
}


def _path(p) -> Path:
    return Path(str(p or "")).expanduser()


def _open(path: Path, must_exist: bool = True):
    from pptx import Presentation
    if must_exist or path.exists():
        if not path.exists():
            return None, f"presentation not found: {path}"
        try:
            return Presentation(str(path)), None
        except Exception as e:
            return None, f"cannot open {path.name}: {type(e).__name__}: {e}"
    return Presentation(), None


def _titles(prs) -> list[str]:
    out = []
    for sl in prs.slides:
        t = ""
        for sh in sl.shapes:
            if sh.has_text_frame and sh.text_frame.text.strip():
                t = sh.text_frame.text.strip().splitlines()[0]
                break
        out.append(t)
    return out



def run(args: dict, ctx):
    act = str(args.get("action", "create")).lower().strip()
    path = _path(args.get("path"))
    if not path:
        return failed("ppt_studio: no path given", "a .pptx file path is required")
    if path.suffix.lower() != ".pptx":
        path = path.with_suffix(".pptx")

    # ── create ────────────────────────────────────────────────────────────────
    if act in ("create", "new"):
        prs, err = _open(path, must_exist=False)
        if err:
            return failed("Could not create presentation", err)
        specs = args.get("slides")
        if not isinstance(specs, list) or not specs:
            specs = [{"title": str(args.get("title") or "Untitled presentation"),
                      "bullets": list(args.get("bullets") or [])}]
        path.parent.mkdir(parents=True, exist_ok=True)
        for i, spec in enumerate(specs):
            if not isinstance(spec, dict):
                spec = {"title": str(spec)}
            layout = prs.slide_layouts[0] if i == 0 and len(prs.slides._sldIdLst) == 0 \
                else (prs.slide_layouts[1] if len(prs.slide_layouts) > 1 else prs.slide_layouts[0])
            sl = prs.slides.add_slide(layout)
            if sl.shapes.title is not None:
                sl.shapes.title.text = str(spec.get("title") or f"Slide {i + 1}")
            bl = spec.get("bullets")
            if bl:
                for ph in sl.placeholders:
                    if ph.placeholder_format.idx == 1:
                        ph.text_frame.text = "\n".join(str(b) for b in bl)
                        break
        err = _save(prs, path)
        if err:
            return failed("Presentation could not be saved", err)
        prs2, _ = _open(path)
        n, titles = len(prs2.slides), _titles(prs2)
        if n == 0:
            return failed("Presentation is empty after save", "reopen found 0 slides")
        return done(f"Created {path.name} with {n} slide(s)",
                    evidence=f"reopened: {n} slide(s); titles={titles}")

    if not path.exists():
        return failed(f"No presentation at {path}", "file does not exist yet - use action=create first")

    # ── read / list ───────────────────────────────────────────────────────────
    if act in ("read", "list", "text"):
        prs, err = _open(path)
        if err:
            return failed("Could not open presentation", err)
        n, titles = len(prs.slides), _titles(prs)
        body = []
        for i, sl in enumerate(prs.slides, 1):
            lines = [sh.text_frame.text.strip() for sh in sl.shapes
                     if sh.has_text_frame and sh.text_frame.text.strip()]
            body.append(f"{i}. " + " | ".join(lines)[:400])
        return done(f"{path.name}: {n} slide(s)",
                    evidence="\n".join(body)[:3500] or "(no text on any slide)")

    # ── add_slide ─────────────────────────────────────────────────────────────
    if act == "add_slide":
        prs, err = _open(path)
        if err:
            return failed("Could not open presentation", err)
        before = len(prs.slides)
        layout = prs.slide_layouts[1] if len(prs.slide_layouts) > 1 else prs.slide_layouts[0]
        sl = prs.slides.add_slide(layout)
        if sl.shapes.title is not None:
            sl.shapes.title.text = str(args.get("title") or "New slide")
        bl = args.get("bullets")
        if bl:
            for ph in sl.placeholders:
                if ph.placeholder_format.idx == 1:
                    ph.text_frame.text = "\n".join(str(b) for b in bl)
                    break
        err = _save(prs, path)
        if err:
            return failed("Slide not saved", err)
        prs2, _ = _open(path)
        n = len(prs2.slides)
        if n != before + 1:
            return failed("Slide count did not increase", f"expected {before + 1}, got {n}")
        return done(f"Added slide {n} to {path.name}",
                    evidence=f"reopened: {n} slide(s); titles={_titles(prs2)}")

    # ── add_text ──────────────────────────────────────────────────────────────
    if act in ("add_text", "append_text"):
        text = str(args.get("text") or "").strip()
        if not text:
            return failed("No text given", "text is required")
        prs, err = _open(path)
        if err:
            return failed("Could not open presentation", err)
        idx = max(1, min(int(args.get("index") or len(prs.slides)), len(prs.slides)))
        box = prs.slides[idx - 1].shapes.add_textbox(prs.slide_width // 10, prs.slide_height * 3 // 4,
                                                     prs.slide_width * 8 // 10, prs.slide_height // 5)
        box.text_frame.text = text
        err = _save(prs, path)
        if err:
            return failed("Text not saved", err)
        prs2, _ = _open(path)
        return done(f"Added text to slide {idx} of {path.name}",
                    evidence=f"reopened: {len(prs2.slides)} slide(s). Added: {text[:120]}")

    # ── add_image ─────────────────────────────────────────────────────────────
    if act == "add_image":
        img = _path(args.get("image_path"))
        if not img.exists():
            return failed(f"Image not found: {img}", "image_path does not exist")
        prs, err = _open(path)
        if err:
            return failed("Could not open presentation", err)
        idx = max(1, min(int(args.get("index") or len(prs.slides)), len(prs.slides)))
        prs.slides[idx - 1].shapes.add_picture(str(img), prs.slide_width // 6,
                                               prs.slide_height // 4,
                                               width=prs.slide_width * 2 // 3)
        err = _save(prs, path)
        if err:
            return failed("Image not saved into slide", err)
        prs2, _ = _open(path)
        return done(f"Inserted {img.name} into slide {idx}",
                    evidence=f"reopened: {len(prs2.slides)} slide(s)")

    # ── delete_slide ──────────────────────────────────────────────────────────
    if act == "delete_slide":
        prs, err = _open(path)
        if err:
            return failed("Could not open presentation", err)
        idx = int(args.get("index") or 0)
        if not 1 <= idx <= len(prs.slides):
            return failed(f"Slide {idx} does not exist", f"file has {len(prs.slides)} slide(s)")
        before = len(prs.slides)
        xml_slides = prs.slides._sldIdLst
        slides = list(xml_slides)
        prs.part.drop_rel(slides[idx - 1].rId)
        xml_slides.remove(slides[idx - 1])
        err = _save(prs, path)
        if err:
            return failed("Slide not deleted", err)
        prs2, _ = _open(path)
        n = len(prs2.slides)
        if n != before - 1:
            return failed("Slide count unchanged after delete", f"expected {before - 1}, got {n}")
        return done(f"Deleted slide {idx} from {path.name}", evidence=f"reopened: {n} slide(s) left")

    # ── duplicate ─────────────────────────────────────────────────────────────
    if act == "duplicate":
        prs, err = _open(path)
        if err:
            return failed("Could not open presentation", err)
        idx = int(args.get("index") or 1)
        if not 1 <= idx <= len(prs.slides):
            return failed(f"Slide {idx} does not exist", f"file has {len(prs.slides)} slide(s)")
        before = len(prs.slides)
        src = prs.slides[idx - 1]
        dst = prs.slides.add_slide(src.slide_layout)
        if dst.shapes.title is not None and src.shapes.title is not None:
            dst.shapes.title.text = f"{src.shapes.title.text} (copy)"
        err = _save(prs, path)
        if err:
            return failed("Copy not saved", err)
        prs2, _ = _open(path)
        n = len(prs2.slides)
        if n != before + 1:
            return failed("Slide count did not increase", f"expected {before + 1}, got {n}")
        return done(f"Duplicated slide {idx}", evidence=f"reopened: {n} slide(s)")

    # ── rename / save_as ──────────────────────────────────────────────────────
    if act in ("rename", "save_as"):
        dest = _path(args.get("new_path") or path)
        if dest.suffix.lower() != ".pptx":
            dest = dest.with_suffix(".pptx")
        prs, err = _open(path)
        if err:
            return failed("Could not open presentation", err)
        dest.parent.mkdir(parents=True, exist_ok=True)
        err = _save(prs, dest)
        if err:
            return failed("Copy could not be written", err)
        prs2, _ = _open(dest)
        return done(f"Saved as {dest.name}",
                    evidence=f"reopened: {len(prs2.slides)} slide(s); titles={_titles(prs2)}")

    return failed(f"Unknown ppt_studio action '{act}'",
                  "valid: create read list add_slide add_text add_image delete_slide duplicate rename save_as")


def _save(prs, path: Path) -> str | None:
    """Save, then REOPEN to prove the file really works.  Returns an error or None."""
    from pptx import Presentation
    prs.save(str(path))
    if not path.exists() or path.stat().st_size < 512:
        return f"file was not written (size={path.stat().st_size if path.exists() else 0} bytes)"
    try:
        Presentation(str(path))
    except Exception as e:
        return f"saved file cannot be reopened: {type(e).__name__}: {e}"
    return None
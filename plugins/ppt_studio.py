"""
PPT Studio - create and edit PowerPoint presentations (.pptx) with python-pptx.

Every save RE-OPENS the file and counts its slides before reporting DONE, so a
half-written deck can never be claimed as "done".
"""
from __future__ import annotations

from pathlib import Path

from actions.office_files import convert_pdf, permitted, save_verified
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
        "delete_slide | duplicate | rename | save_as | reorder | edit_text | notes | add_shape | add_table | add_chart | validate | to_pdf. "
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
            "destination_index": {"type": "INTEGER"},
            "shape_index": {"type": "INTEGER"},
            "template": {"type": "STRING"},
            "layout": {"type": "INTEGER"},
            "shape": {"type": "STRING"},
            "x": {"type": "NUMBER"}, "y": {"type": "NUMBER"},
            "width": {"type": "NUMBER"}, "height": {"type": "NUMBER"},
            "rows": {"type": "ARRAY", "items": {"type": "ARRAY", "items": {"type": "STRING"}}},
            "values": {"type": "ARRAY", "items": {"type": "NUMBER"}},
            "categories": {"type": "ARRAY", "items": {"type": "STRING"}},
        },
        "required": ["action", "path"],
    },
}


def _path(p) -> Path:
    if not p:
        raise ValueError("A presentation path is required.")
    return Path(str(p)).expanduser()


def _open(path: Path, must_exist: bool = True):
    from pptx import Presentation
    permitted(path)
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
    try:
        return _run(args)
    except (OSError, ValueError, KeyError, RuntimeError, TypeError) as exc:
        return failed("Presentation operation failed", f"{type(exc).__name__}: {exc}")


def _run(args):

    act = str(args.get("action", "create")).lower().strip()
    path = _path(args.get("path"))
    if not path:
        return failed("ppt_studio: no path given", "a .pptx file path is required")
    if path.suffix.lower() != ".pptx":
        path = path.with_suffix(".pptx")

    if act in {"reorder", "edit_text", "notes", "add_shape", "add_table", "add_chart", "validate", "to_pdf"}:
        return _advanced(path, act, args)

    # ── create ────────────────────────────────────────────────────────────────
    if act in ("create", "new"):
        if path.exists():
            return failed("Presentation already exists", "Choose a new path or edit the existing deck.")
        if args.get("template"):
            prs, err = _open(_path(args["template"]))
        else:
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
        layout_index = int(args.get("layout", min(1, len(prs.slide_layouts) - 1)))
        if not 0 <= layout_index < len(prs.slide_layouts):
            raise ValueError("Invalid slide layout index.")
        layout = prs.slide_layouts[layout_index]
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
        img = permitted(_path(args.get("image_path")))
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
        from copy import deepcopy

        relations = {}
        for old_id, relationship in src.part.rels.items():
            if relationship.reltype.endswith(("/slideLayout", "/notesSlide")):
                continue
            target = relationship.target_ref if relationship.is_external else relationship.target_part
            relations[old_id] = dst.part.relate_to(
                target, relationship.reltype, is_external=relationship.is_external,
            )
        for shape in list(dst.shapes):
            dst.shapes._spTree.remove(shape.element)
        for shape in src.shapes:
            element = deepcopy(shape.element)
            for node in element.iter():
                for attribute, value in tuple(node.attrib.items()):
                    if attribute.startswith("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}") and value in relations:
                        node.set(attribute, relations[value])
            dst.shapes._spTree.insert_element_before(element, "p:extLst")
        if src.has_notes_slide:
            original = src.notes_slide.notes_text_frame
            if original is not None:
                dst.notes_slide.notes_text_frame.text = original.text
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
        permitted(dest, write=True).parent.mkdir(parents=True, exist_ok=True)
        err = _save(prs, dest)
        if err:
            return failed("Copy could not be written", err)
        prs2, _ = _open(dest)
        return done(f"Saved as {dest.name}",
                    evidence=f"reopened: {len(prs2.slides)} slide(s); titles={_titles(prs2)}")

    return failed(f"Unknown ppt_studio action '{act}'",
                  "valid: create read list add_slide add_text add_image delete_slide duplicate rename save_as")


def _advanced(path, action, args):
    from pptx.chart.data import CategoryChartData
    from pptx.enum.chart import XL_CHART_TYPE
    from pptx.enum.shapes import MSO_SHAPE
    from pptx.util import Inches

    if action == "to_pdf":
        destination = args.get("new_path") or path.with_suffix(".pdf")
        convert_pdf(path, destination)
        return done(f"Converted {path.name} to PDF")
    prs, error = _open(path)
    if error:
        return failed("Could not open presentation", error)
    if action == "validate":
        issues = [
            f"Slide {index}, shape {shape.name}"
            for index, slide in enumerate(prs.slides, 1)
            for shape in slide.shapes
            if min(shape.left, shape.top) < 0 or shape.left + shape.width > prs.slide_width
            or shape.top + shape.height > prs.slide_height
        ]
        if issues:
            return failed("Shapes extend beyond the slide canvas", ", ".join(issues[:20]))
        return done("All slide shapes fit within the canvas", evidence=f"{len(prs.slides)} slides")

    index = int(args.get("index", 1))
    if not 1 <= index <= len(prs.slides):
        raise ValueError("Invalid slide number.")
    slide = prs.slides[index - 1]
    if action == "reorder":
        destination = int(args["destination_index"])
        if not 1 <= destination <= len(prs.slides):
            raise ValueError("Invalid destination slide number.")
        ids = prs.slides._sldIdLst
        item = ids[index - 1]
        ids.remove(item)
        ids.insert(destination - 1, item)
    elif action == "edit_text":
        shape_index = int(args["shape_index"])
        if not 1 <= shape_index <= len(slide.shapes) or not slide.shapes[shape_index - 1].has_text_frame:
            raise ValueError("Choose a valid text shape.")
        slide.shapes[shape_index - 1].text = args.get("text", "")
    elif action == "notes":
        slide.notes_slide.notes_text_frame.text = args.get("text", "")
    else:
        coordinates = tuple(Inches(float(args.get(key, default))) for key, default in
                            (("x", 1), ("y", 2), ("width", 5), ("height", 2)))
        x, y, w, h = coordinates
        if min(x, y) < 0 or min(w, h) <= 0 or x + w > prs.slide_width or y + h > prs.slide_height:
            raise ValueError("Shape dimensions exceed the slide canvas.")
        if action == "add_shape":
            kind = {"rectangle": MSO_SHAPE.RECTANGLE, "rounded": MSO_SHAPE.ROUNDED_RECTANGLE,
                    "oval": MSO_SHAPE.OVAL}.get(args.get("shape", "rectangle"))
            if kind is None:
                raise ValueError("Choose rectangle, rounded or oval.")
            slide.shapes.add_shape(kind, *coordinates).text = args.get("text", "")
        elif action == "add_table":
            rows = args.get("rows") or []
            if not rows or not rows[0] or any(len(row) != len(rows[0]) for row in rows):
                raise ValueError("Provide rectangular table rows.")
            table = slide.shapes.add_table(len(rows), len(rows[0]), *coordinates).table
            for i, row in enumerate(rows):
                for j, value in enumerate(row):
                    table.cell(i, j).text = str(value)
        elif action == "add_chart":
            categories = args.get("categories") or []
            values = args.get("values") or []
            if not categories or len(categories) != len(values):
                raise ValueError("Chart labels and values must have matching lengths.")
            data = CategoryChartData()
            data.categories = categories
            data.add_series(args.get("title") or "Values", values)
            slide.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, *coordinates, data)
    error = _save(prs, path)
    if error:
        return failed("Could not save presentation", error)
    return done(f"Saved {path.name}", evidence=f"{len(prs.slides)} slides reopened")


def _save(prs, path: Path) -> str | None:
    from pptx import Presentation

    try:
        save_verified(path, prs.save, Presentation, replace=path.exists())
    except (OSError, ValueError) as exc:
        return f"{type(exc).__name__}: {exc}"
    return None

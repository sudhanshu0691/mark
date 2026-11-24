"""Native operations for the existing file processor's DOCX route."""
from pathlib import Path

from actions.office_files import convert_pdf, permitted, save_verified
from core.result import done

ACTIONS = {"create", "edit", "find_replace", "to_pdf"}


def operate(path, action, params):
    from docx import Document
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Inches

    path = permitted(path, write=action != "to_pdf")
    if action == "to_pdf":
        destination = Path(params.get("destination") or path.with_suffix(".pdf"))
        convert_pdf(path, destination)
        return done(f"Converted {path.name} to PDF").to_llm()

    template = params.get("template")
    document = (Document(permitted(template)) if template else Document()) if action == "create" else Document(path)
    changes = 0

    def paragraphs(container):
        yield from container.paragraphs
        for table in container.tables:
            cells = set()
            for row in table.rows:
                for cell in row.cells:
                    if cell._tc not in cells:
                        cells.add(cell._tc)
                        yield from paragraphs(cell)

    if action == "find_replace":
        needle = params.get("find")
        if not needle:
            raise ValueError("find is required.")
        sections = [document]
        for section in document.sections:
            for part in (section.header, section.footer):
                if not part.is_linked_to_previous:
                    sections.append(part)
        for container in sections:
            for para in paragraphs(container):
                runs = para.runs
                original = "".join(run.text for run in runs)
                matches = []
                start = original.find(needle)
                while start >= 0:
                    matches.append(start)
                    start = original.find(needle, start + len(needle))
                for start in reversed(matches):
                    offset = 0
                    for run in runs:
                        length = len(run.text)
                        if start < offset + length and start + len(needle) > offset:
                            left = max(0, start - offset)
                            right = min(length, start + len(needle) - offset)
                            replacement = params.get("replacement", "") if offset <= start else ""
                            run.text = run.text[:left] + replacement + run.text[right:]
                        offset += length
                changes += len(matches)
        if changes == 0:
            raise ValueError("No matching text found.")
    else:
        if params.get("title"):
            document.add_heading(params["title"], 0)
        for block in params.get("blocks", []):
            kind = block.get("type")
            value = block.get("text", "")
            if kind == "heading":
                document.add_heading(value, int(block.get("level", 1)))
            elif kind == "paragraph":
                document.add_paragraph(value, style=block.get("style"))
            elif kind == "table":
                rows = block.get("rows") or []
                if not rows or not rows[0] or any(len(row) != len(rows[0]) for row in rows):
                    raise ValueError("Tables require rectangular rows.")
                table = document.add_table(rows=0, cols=len(rows[0]), style="Table Grid")
                for row in rows:
                    for cell, text in zip(table.add_row().cells, row):
                        cell.text = str(text)
            elif kind == "image":
                document.add_picture(str(permitted(block["path"])), width=Inches(float(block.get("width", 5))))
            elif kind == "page_break":
                document.add_page_break()
            else:
                raise ValueError(f"Unsupported Word block: {kind}")
            changes += 1
        section = document.sections[-1]
        for name in ("header", "footer"):
            if name in params:
                getattr(section, name).paragraphs[0].text = params[name]
        if params.get("page_numbers"):
            paragraph = section.footer.add_paragraph()
            field = OxmlElement("w:fldSimple")
            field.set(qn("w:instr"), "PAGE")
            paragraph._p.append(field)
    save_verified(path, document.save, Document, replace=action != "create")
    return done(f"Saved {path.name}", evidence=f"{changes} content changes").to_llm()

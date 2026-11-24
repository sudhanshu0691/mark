"""Local PDF page operations for the existing file processor."""

from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from actions.office_files import permitted, save_verified
from core.result import done

ACTIONS = {
    "create",
    "merge",
    "extract_pages",
    "split",
    "rotate",
    "reorder",
    "to_images",
    "images_to_pdf",
    "annotate",
    "ocr",
    "searchable_pdf",
}


def operate(path, action, params):
    from PIL import Image
    from pypdf import PdfReader, PdfWriter

    destination = Path(
        params.get("destination") or path.with_name(f"{path.stem}-{action}.pdf")
    )
    if action in {"ocr", "searchable_pdf", "to_images"}:
        import pypdfium2

        with (
            pypdfium2.PdfDocument(str(permitted(path))) as document,
            TemporaryDirectory() as directory,
        ):
            writer = PdfWriter()
            text = []
            for index in range(len(document)):
                with closing(document[index]) as page:
                    bitmap = page.render(scale=2)
                    try:
                        image = bitmap.to_pil().copy()
                    finally:
                        bitmap.close()
                if action == "to_images":
                    out = destination.with_suffix("").with_name(
                        f"{destination.stem}-{index + 1}.png"
                    )
                    save_verified(
                        out,
                        lambda p: image.save(p, format="PNG"),
                        lambda p: Image.open(p).verify(),
                    )
                else:
                    import pytesseract

                    if action == "ocr":
                        text.append(pytesseract.image_to_string(image))
                    else:
                        temporary = Path(directory) / f"{index}.pdf"
                        temporary.write_bytes(
                            pytesseract.image_to_pdf_or_hocr(image, extension="pdf")
                        )
                        writer.append(temporary)
            if action == "ocr":
                return done("OCR complete", evidence="\n".join(text)).to_llm()
            if action == "to_images":
                return done(f"Rendered {len(document)} PDF pages").to_llm()
    elif action in {"create", "images_to_pdf"}:
        if action == "create":
            import textwrap

            lines = [
                line
                for paragraph in str(params.get("text", "")).splitlines()
                for line in (textwrap.wrap(paragraph, width=75) or [""])
            ]
            if not lines:
                raise ValueError("PDF creation requires text.")
            from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

            writer = PdfWriter()
            name = NameObject
            font = writer._add_object(
                DictionaryObject(
                    {
                        name("/Type"): name("/Font"),
                        name("/Subtype"): name("/Type1"),
                        name("/BaseFont"): name("/Helvetica"),
                        name("/Encoding"): name("/WinAnsiEncoding"),
                    }
                )
            )
            for start in range(0, len(lines), 48):
                page = writer.add_blank_page(width=612, height=792)
                page[name("/Resources")] = DictionaryObject(
                    {name("/Font"): DictionaryObject({name("/F1"): font})}
                )
                try:
                    operations = [
                        b"<" + line.encode("cp1252").hex().encode("ascii") + b"> Tj T*"
                        for line in lines[start : start + 48]
                    ]
                except UnicodeEncodeError:
                    from docx import Document
                    from actions.office_files import convert_pdf

                    with TemporaryDirectory(prefix="mark-pdf-") as directory:
                        source = Path(directory) / "document.docx"
                        doc = Document()
                        for line in params["text"].splitlines():
                            doc.add_paragraph(line)
                        doc.save(source)
                        convert_pdf(
                            source,
                            params.get("destination") or path,
                            internal_source=True,
                        )
                    return done("Created searchable PDF using LibreOffice").to_llm()
                stream = DecodedStreamObject()
                stream.set_data(
                    b"BT /F1 11 Tf 14 TL 54 744 Td\n" + b"\n".join(operations) + b"\nET"
                )
                page[name("/Contents")] = writer._add_object(stream)
            save_verified(
                params.get("destination") or path,
                writer.write,
                lambda p: len(PdfReader(p).pages),
            )
            return done(
                "Created searchable PDF", evidence=f"{len(writer.pages)} pages"
            ).to_llm()
        images = []
        for item in params.get("sources", []):
            with Image.open(permitted(item)) as image:
                images.append(image.convert("RGB"))
        if not images:
            raise ValueError("Provide image sources.")
        save_verified(
            params.get("destination") or path,
            lambda p: images[0].save(
                p, format="PDF", save_all=True, append_images=images[1:]
            ),
            lambda p: len(PdfReader(p).pages),
        )
        return done(f"Created PDF from {len(images)} images").to_llm()
    else:
        writer = PdfWriter()
        if action == "merge":
            sources = params.get("sources") or []
            if len(sources) < 2:
                raise ValueError("Provide at least two source PDFs.")
            for item in sources:
                writer.append(permitted(item))
        else:
            reader = PdfReader(permitted(path))
            pages = params.get("pages")
            if pages is None:
                pages = list(range(1, len(reader.pages) + 1))
            if not pages or any(
                type(i) is not int or not 1 <= i <= len(reader.pages) for i in pages
            ):
                raise ValueError("Provide valid 1-based page numbers.")
            if action == "split":
                for i in dict.fromkeys(pages):
                    part = PdfWriter()
                    part.add_page(reader.pages[i - 1])
                    out = destination.with_name(f"{destination.stem}-{i}.pdf")
                    save_verified(out, part.write, lambda p: len(PdfReader(p).pages))
                return done(f"Split {len(set(pages))} PDF pages").to_llm()
            if action in {"rotate", "annotate"}:
                writer.append(reader)
                for i in dict.fromkeys(pages):
                    if action == "rotate":
                        writer.pages[i - 1].rotate(int(params.get("degrees", 90)))
                    else:
                        from pypdf.annotations import Text

                        writer.add_annotation(
                            i - 1, Text(rect=(20, 20, 45, 45), text=params["text"])
                        )
            else:
                for i in pages:
                    writer.add_page(reader.pages[i - 1])
    save_verified(destination, writer.write, lambda p: len(PdfReader(p).pages))
    return done(
        f"Saved {destination.name}", evidence=f"{len(writer.pages)} pages"
    ).to_llm()

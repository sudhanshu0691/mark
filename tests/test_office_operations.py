"""Round-trip the existing Office entry points using real file formats."""
import pytest

from actions.file_processor import file_processor
from plugins import ppt_studio
from plugins.excel import plugin as excel


@pytest.fixture(autouse=True)
def local_paths(monkeypatch):
    # Hosted Windows work directories differ from the user's allowed folders.
    monkeypatch.setattr("actions.office_files.check", lambda *a, **k: (True, ""))
    monkeypatch.setattr("core.permissions.check", lambda *a, **k: (True, ""))


def test_workbook_sheet_range_table_and_dropdown(tmp_path):
    from openpyxl import load_workbook

    path = tmp_path / "report.xlsx"

    def run(action, **values):
        result = excel.run({"path": str(path), "action": action, **values}, None)
        assert result.to_llm().startswith("[DONE]"), result

    run("create", data=[["Item", "Amount"], ["Tool", 4]])
    run("set_range", range="A2:B3", data=[["Desk", 15], ["Chair", 20]])
    run("table", range="A1:B3", title="Inventory")
    run("dropdown", range="A2:A3", choices=["Desk", "Chair"])
    run("copy_sheet", target_sheet="Archive")
    run("rename_sheet", sheet="Archive", target_sheet="Previous")
    run("dashboard", group_by="Item", column="Amount")
    run("export_csv", sheet="Data")
    assert (tmp_path / "report.csv").read_text(encoding="utf-8-sig").splitlines()[1] == "Desk,15"
    workbook = load_workbook(path)
    try:
        assert workbook.sheetnames == ["Data", "Previous", "Dashboard"]
        assert len(workbook["Dashboard"]._charts) == 1
        assert workbook["Data"]["B3"].value == 20
        assert "Inventory" in workbook["Data"].tables
        assert len(workbook["Data"].data_validations.dataValidation) == 1
    finally:
        workbook.close()
    assert list(tmp_path.glob("*.backup-*.xlsx"))
    path.with_name("~$" + path.name).touch()
    assert excel.run({"path": str(path), "action": "delete_sheet", "sheet": "Previous"}, None).to_llm().startswith("[FAILED]")


def test_word_create_and_replace_preserves_formatting(tmp_path):
    from docx import Document

    path = tmp_path / "report.docx"
    result = file_processor({
        "file_path": str(path), "action": "create", "header": "Quarterly",
        "title": "Report", "page_numbers": True,
        "blocks": [{"type": "table", "rows": [["Item", "Cost"], ["Desk", "15"]]}],
    })
    assert result.startswith("[DONE]"), result
    document = Document(path)
    para = document.add_paragraph()
    para.add_run("Find ").bold = True
    para.add_run("me twice: Find me")
    document.save(path)
    assert file_processor({"file_path": str(path), "action": "find_replace",
                           "find": "Find me", "replacement": "Changed"}).startswith("[DONE]")
    document = Document(path)
    assert document.paragraphs[-1].text == "Changed twice: Changed"
    assert document.paragraphs[-1].runs[0].bold
    assert document.sections[0].header.paragraphs[0].text == "Quarterly"
    assert document.tables[0].cell(1, 1).text == "15"


def test_pdf_creation_reorder_annotation_and_render(tmp_path):
    from pypdf import PdfReader

    path = tmp_path / "report.pdf"

    def run(action, **values):
        result = file_processor({"file_path": str(path), "action": action, **values})
        assert result.startswith("[DONE]"), result

    run("create", text="First page\n" + "\n" * 47 + "Second page")
    assert "First page" in PdfReader(path).pages[0].extract_text()
    run("reorder", pages=[2, 1], destination=str(tmp_path / "reverse.pdf"))
    assert "Second page" in PdfReader(tmp_path / "reverse.pdf").pages[0].extract_text()
    run("annotate", pages=[1], text="Reviewed")
    assert len(PdfReader(tmp_path / "report-annotate.pdf").pages[0]["/Annots"]) == 1
    run("to_images")
    assert (tmp_path / "report-to_images-1.png").stat().st_size > 100


def test_presentation_notes_reorder_and_chart(tmp_path):
    from pptx import Presentation

    path = tmp_path / "results.pptx"

    def run(action, **values):
        result = ppt_studio.run({"path": str(path), "action": action, **values}, None)
        assert result.to_llm().startswith("[DONE]"), result

    run("create", slides=[{"title": "First"}, {"title": "Second"}])
    run("notes", index=2, text="Detailed notes")
    run("reorder", index=2, destination_index=1)
    run("add_chart", index=1, categories=["A", "B"], values=[1, 2])
    run("validate")
    run("duplicate", index=1)
    presentation = Presentation(path)
    assert presentation.slides[0].shapes.title.text == "Second"
    assert presentation.slides[0].notes_slide.notes_text_frame.text == "Detailed notes"
    assert any(shape.has_chart for shape in presentation.slides[0].shapes)
    assert any(shape.has_chart for shape in presentation.slides[2].shapes)
    assert presentation.slides[2].notes_slide.notes_text_frame.text == "Detailed notes"


def test_document_cloud_requires_outbound_approval(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock

    from core import llm

    source = tmp_path / "document.txt"
    source.write_text("Private document", encoding="utf-8")
    generate = Mock(return_value=SimpleNamespace(text="Summary"))
    monkeypatch.setattr(llm, "Client", lambda: SimpleNamespace(models=SimpleNamespace(generate_content=generate)))
    args = {"file_path": str(source), "action": "summarize", "confirmed": True}
    assert "requires approval" in file_processor(args)
    generate.assert_not_called()
    assert file_processor(args, authorize_cloud=lambda path: path == source) == "Summary"
    generate.assert_called_once()

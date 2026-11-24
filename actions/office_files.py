"""Local document path policy, verified saves and optional Office conversion."""

import os
import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

from core.permissions import check


def permitted(path, *, write=False):
    path = Path(path).expanduser().resolve()
    allowed, reason = check(path, write=write)
    if not allowed:
        raise PermissionError(reason)
    return path


def save_verified(path, save, verify, *, replace=False):
    path = permitted(path, write=True)
    if path.exists() and not replace:
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".mark-office-", dir=path.parent) as directory:
        temporary = Path(directory) / path.name
        save(temporary)
        verify(temporary)
        if replace:
            os.replace(temporary, path)
        else:
            # Exclusive publication also handles another creator racing this save.
            os.link(temporary, path)


OFFICE_APPS = {
    ".docx": "Word.Application",
    ".xlsx": "Excel.Application",
    ".xlsm": "Excel.Application",
    ".pptx": "PowerPoint.Application",
}


def office_available(program):
    if os.name != "nt":
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, program + "\\CLSID"):
            return True
    except OSError:
        return False


def libreoffice_path():
    candidates = [shutil.which("soffice"), shutil.which("libreoffice")]
    if os.name == "nt":
        candidates.extend(
            Path(os.environ.get(key, "C:/Program Files"))
            / "LibreOffice/program/soffice.com"
            for key in ("ProgramFiles", "ProgramFiles(x86)")
        )
    return next(
        (str(path) for path in candidates if path and Path(path).is_file()), None
    )


def _microsoft_pdf(source, output):
    from win32com.client import DispatchEx

    program = OFFICE_APPS[source.suffix.lower()]
    application = DispatchEx(program)
    document = None
    try:
        # Office automation must disable macros before any input file is opened.
        application.AutomationSecurity = 3
        if program.startswith("Word"):
            application.DisplayAlerts = 0
            document = application.Documents.Open(
                str(source), ReadOnly=True, AddToRecentFiles=False, Visible=False
            )
            document.ExportAsFixedFormat(str(output), 17)
        elif program.startswith("Excel"):
            application.DisplayAlerts = False
            document = application.Workbooks.Open(
                str(source), UpdateLinks=0, ReadOnly=True, AddToMru=False
            )
            document.ExportAsFixedFormat(0, str(output))
        else:
            document = application.Presentations.Open(
                str(source), ReadOnly=True, WithWindow=False
            )
            document.SaveAs(str(output), 32)
    finally:
        try:
            if document is not None:
                document.Close()
        finally:
            application.Quit()


def _excel_native(source, settings):
    from win32com.client import DispatchEx

    application = DispatchEx("Excel.Application")
    workbook = None
    try:
        application.AutomationSecurity = 3
        application.DisplayAlerts = False
        workbook = application.Workbooks.Open(
            str(source), UpdateLinks=0, ReadOnly=False, AddToMru=False
        )
        if workbook.ReadOnly:
            raise PermissionError("Excel opened the isolated workbook read-only.")
        if settings["action"] == "pivot":
            sheet = workbook.Worksheets(settings["sheet"])
            output = workbook.Worksheets.Add()
            output.Name = settings["title"]
            source_range = sheet.Range(settings["range"]).Address(
                True, True, -4150, True
            )
            cache = workbook.PivotCaches().Create(1, source_range)
            pivot = cache.CreatePivotTable(
                output.Range("A3"), "MarkPivot_" + str(output.Index)
            )
            pivot.PivotFields(settings["group_by"]).Orientation = 1
            field = settings["column"]
            pivot.AddDataField(
                pivot.PivotFields(field),
                f"{settings['aggregate'].title()} of {field}",
                {
                    "sum": -4157,
                    "count": -4112,
                    "average": -4106,
                    "min": -4139,
                    "max": -4136,
                }[settings["aggregate"]],
            )
        else:
            application.CalculateFullRebuild()
        workbook.Save()
    finally:
        try:
            if workbook is not None:
                workbook.Close(SaveChanges=False)
        finally:
            application.Quit()


def edit_open_workbook(source, sheet_name, address, values):
    """Apply a bounded edit to an already-open Excel workbook without saving it."""
    import pythoncom
    from win32com.client import GetActiveObject

    pythoncom.CoInitialize()
    try:
        excel = GetActiveObject("Excel.Application")
        source = os.path.normcase(str(source.resolve()))
        workbook = next((item for item in excel.Workbooks
                         if os.path.normcase(str(Path(item.FullName).resolve())) == source), None)
        if workbook is None or workbook.ReadOnly:
            raise PermissionError("The workbook must be open and writable in desktop Excel.")
        worksheet = workbook.Worksheets(sheet_name) if sheet_name else workbook.ActiveSheet
        if worksheet.ProtectContents:
            raise PermissionError("The selected worksheet is protected.")
        target = worksheet.Range(address)
        if target.MergeCells is not False or target.HasArray is not False:
            raise PermissionError("Merged cells or array formulas cannot be edited together.")
        previous = target.Formula

        def normalize(value):
            rows = value if isinstance(value, (tuple, list)) and value and isinstance(value[0], (tuple, list)) else ((value,),)
            return tuple(tuple(None if cell == "" else cell for cell in row) for row in rows)

        expected = normalize(values)
        try:
            target.Formula = values
            result = normalize(target.Formula)
            if expected != result:
                raise ValueError("Excel did not report the requested values.")
        except Exception:
            target.Formula = previous
            raise
    finally:
        pythoncom.CoUninitialize()


def edit_excel_native(workbook, settings):
    import json
    import sys

    if not office_available("Excel.Application"):
        raise RuntimeError("Microsoft Excel is required for native pivot tables.")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "actions.office_files",
            "--excel-edit",
            str(workbook),
            json.dumps(settings),
        ],
        cwd=Path(__file__).resolve().parent.parent,
        capture_output=True,
        timeout=90,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError("Microsoft Excel could not complete the native operation.")


def convert_pdf(source, destination, *, internal_source=False):
    import sys

    from pypdf import PdfReader

    source = Path(source) if internal_source else permitted(source)
    program = OFFICE_APPS.get(source.suffix.lower())
    use_office = bool(program and office_available(program))
    engine = libreoffice_path()
    if not use_office and not engine:
        raise RuntimeError(
            "Install Microsoft Office or LibreOffice for this conversion."
        )

    def convert(temporary):
        if use_office:
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "actions.office_files",
                    str(source),
                    str(temporary),
                ],
                cwd=Path(__file__).resolve().parent.parent,
                capture_output=True,
                timeout=90,
                check=False,
            )
            if result.returncode == 0 and temporary.is_file():
                return
            temporary.unlink(missing_ok=True)
        if not engine:
            raise RuntimeError("Microsoft Office could not export the document to PDF.")
        with TemporaryDirectory(prefix="mark-office-engine-") as directory:
            profile = (Path(directory) / "profile").as_uri()
            result = subprocess.run(
                [
                    engine,
                    f"-env:UserInstallation={profile}",
                    "--headless",
                    "--convert-to",
                    "pdf",
                    "--outdir",
                    directory,
                    str(source),
                ],
                capture_output=True,
                timeout=90,
                check=False,
            )
            output = Path(directory) / (source.stem + ".pdf")
            if result.returncode or not output.is_file():
                raise RuntimeError("LibreOffice did not produce a PDF.")
            shutil.copyfile(output, temporary)

    save_verified(destination, convert, lambda p: len(PdfReader(p).pages))


if __name__ == "__main__":
    import sys

    if len(sys.argv) == 4 and sys.argv[1] == "--excel-edit":
        import json

        _excel_native(Path(sys.argv[2]), json.loads(sys.argv[3]))
        raise SystemExit(0)
    if len(sys.argv) != 3:
        raise SystemExit(2)
    _microsoft_pdf(Path(sys.argv[1]), Path(sys.argv[2]))

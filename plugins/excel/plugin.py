"""Single, verified Excel workbook capability for MARK.

Uses openpyxl for native .xlsx/.xlsm edits, with atomic replacement and a
timestamped backup before modifying an existing workbook. Formula calculation
is intentionally delegated to an installed spreadsheet engine; openpyxl stores
formulas but does not evaluate them.
"""
from __future__ import annotations

import csv
import json
import os
import re
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from actions.office_files import convert_pdf, permitted, save_verified
from core.result import done, failed

NAME = "excel"
DESCRIPTION = "Create, inspect, edit, format, analyze, chart, and verify Excel workbooks."
METADATA = {
    "version": "2.0.0",
    "capabilities": ["excel", "xlsx", "workbook", "formula", "spreadsheet", "chart", "data-analysis"],
    "permissions": [],
    "timeout": 90,
}
DECLARATION = {
    "name": NAME,
    "description": (
        "Excel workbook tool. Actions: inspect, profile, create, append, set_cell, "
        "formula, format, sort, remove_duplicates, summary, chart, recalculate. "
        "Also supports add_sheet, delete_sheet, rename_sheet, copy_sheet, set_range, "
        "insert_rows, delete_rows, insert_columns, delete_columns, merge, unmerge, style, "
        "dropdown, conditional_format, table, filter, import_csv, export_csv, to_pdf. "
        "Existing workbooks are backed up before edits."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "description": "inspect | profile | create | append | set_cell | formula | format | sort | remove_duplicates | summary | chart | recalculate"},
            "path": {"type": "STRING", "description": "Workbook path (.xlsx/.xlsm); create may use a new path"},
            "sheet": {"type": "STRING", "description": "Worksheet name; defaults to the active sheet"},
            "data": {"type": "ARRAY", "items": {}, "description": "Rows as arrays; first row is headers for create"},
            "cell": {"type": "STRING", "description": "Cell reference, such as B4"},
            "value": {"type": "STRING", "description": "Cell value or formula text"},
            "column": {"type": "STRING", "description": "Header name or 1-based column number"},
            "order": {"type": "STRING", "description": "ascending | descending"},
            "group_by": {"type": "STRING", "description": "Header to group summary rows by"},
            "aggregate": {"type": "STRING", "description": "sum | average | count | min | max"},
            "chart_type": {"type": "STRING", "description": "bar | line | pie"},
            "title": {"type": "STRING", "description": "Sheet, table, or chart title"},
            "engine": {"type": "STRING", "description": "Optional engine name: soffice"},
            "target_sheet": {"type": "STRING"},
            "range": {"type": "STRING", "description": "Range such as A1:D10"},
            "index": {"type": "INTEGER"},
            "count": {"type": "INTEGER"},
            "source": {"type": "STRING"},
            "destination": {"type": "STRING"},
            "choices": {"type": "ARRAY", "items": {"type": "STRING"}},
            "options": {"type": "OBJECT", "properties": {
                "font": {"type": "STRING"}, "size": {"type": "NUMBER"}, "bold": {"type": "BOOLEAN"},
                "color": {"type": "STRING"}, "fill": {"type": "STRING"},
                "border": {"type": "STRING"}, "alignment": {"type": "STRING"},
                "number_format": {"type": "STRING"},
            }},
        },
        "required": ["action"],
    },
}

_EXCEL_EXTS = {".xlsx", ".xlsm"}
_MAX_ROWS = 100_000
_HEADER_FILL = "153047"
_ACCENT = "32C5D2"


def _openpyxl():
    try:
        import openpyxl
        return openpyxl
    except ImportError as exc:
        raise RuntimeError("Excel support needs openpyxl; install the project requirements") from exc


def _path(value: Any, *, create: bool = False) -> Path:
    raw = os.path.expandvars(str(value or "")).strip().strip('"')
    if not raw:
        raise ValueError("path is required")
    path = permitted(raw, write=create)
    if path.suffix.lower() not in _EXCEL_EXTS:
        raise ValueError("supported workbook formats are .xlsx and .xlsm")
    if create:
        if path.exists():
            raise ValueError(f"refusing to overwrite existing workbook: {path}")
        path.parent.mkdir(parents=True, exist_ok=True)
    elif not path.is_file():
        raise FileNotFoundError(f"workbook not found: {path}")
    return path


def _workbook(path: Path):
    return _openpyxl().load_workbook(path, keep_vba=path.suffix.lower() == ".xlsm")


def _sheet(wb, name: str | None):
    if name:
        if name not in wb.sheetnames:
            raise ValueError(f"worksheet {name!r} does not exist; available: {', '.join(wb.sheetnames)}")
        return wb[name]
    return wb.active


def _backup(path: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    backup = path.with_name(f"{path.stem}.backup-{stamp}{path.suffix}")
    shutil.copy2(path, backup)
    return backup


def _atomic_save(wb, path: Path, *, is_new: bool = False) -> tuple[Path | None, dict[str, Any]]:
    backup = None if is_new else _backup(path)
    evidence = {}

    def verify(temporary):
        check = _workbook(temporary)
        try:
            evidence.update(worksheets=check.sheetnames, bytes=temporary.stat().st_size)
        finally:
            check.close()

    save_verified(path, wb.save, verify, replace=not is_new)
    return backup, evidence


def _headers(ws) -> tuple[list[str], dict[str, int]]:
    values = [c.value for c in ws[1]] if ws.max_row else []
    names = [str(v).strip() if v is not None else "" for v in values]
    return names, {name.casefold(): i + 1 for i, name in enumerate(names) if name}


def _column(ws, value: Any) -> int:
    if value is None or str(value).strip() == "":
        raise ValueError("column is required")
    if str(value).strip().isdigit():
        idx = int(value)
        if 1 <= idx <= ws.max_column:
            return idx
    _, mapping = _headers(ws)
    if str(value).strip().casefold() in mapping:
        return mapping[str(value).strip().casefold()]
    raise ValueError(f"column {value!r} not found in the header row")


def _data_rows(ws) -> list[tuple[Any, ...]]:
    if ws.max_row * max(1, ws.max_column) > 2_000_000:
        raise ValueError("workbook is too large for this operation (limit: 2 million cells)")
    return list(ws.iter_rows(values_only=True))


def _inspect(path: Path) -> dict[str, Any]:
    wb = _workbook(path)
    try:
        result = {"file": path.name, "worksheets": []}
        for ws in wb.worksheets:
            headers, _ = _headers(ws)
            result["worksheets"].append({"name": ws.title, "rows": ws.max_row, "columns": ws.max_column,
                "headers": headers, "tables": list(ws.tables), "charts": len(ws._charts),
                "freeze_panes": str(ws.freeze_panes or "")})
        return result
    finally:
        wb.close()


def _profile(path: Path, sheet_name: str | None) -> dict[str, Any]:
    wb = _workbook(path)
    try:
        ws = _sheet(wb, sheet_name)
        rows = _data_rows(ws)
        headers = [str(x) if x is not None else f"Column {i+1}" for i, x in enumerate(rows[0] if rows else ())]
        out = []
        for i, header in enumerate(headers):
            values = [r[i] if i < len(r) else None for r in rows[1:]]
            numeric = [v for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]
            out.append({"name": header, "non_empty": sum(v is not None and v != "" for v in values),
                        "missing": sum(v is None or v == "" for v in values),
                        "numeric": len(numeric), "unique": len({str(v) for v in values if v is not None})})
        return {"sheet": ws.title, "data_rows": max(0, len(rows)-1), "columns": out,
                "duplicate_rows": len(rows[1:]) - len({tuple(map(str, r)) for r in rows[1:]})}
    finally:
        wb.close()


def _style_sheet(ws) -> None:
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    for cell in ws[1]:
        cell.fill = PatternFill("solid", fgColor=_HEADER_FILL)
        cell.font = Font(name="Aptos", bold=True, color="FFFFFF")
        cell.alignment = Alignment(vertical="center", wrap_text=True)
    ws.row_dimensions[1].height = 26
    for col in range(1, ws.max_column + 1):
        letter = get_column_letter(col)
        width = max((len(str(ws.cell(row, col).value or "")) for row in range(1, min(ws.max_row, 150) + 1)), default=10)
        ws.column_dimensions[letter].width = min(48, max(12, width + 2))


_EXTRA_EDITS = {"add_sheet", "delete_sheet", "rename_sheet", "copy_sheet", "set_range",
                "insert_rows", "delete_rows", "insert_columns", "delete_columns",
                "merge", "unmerge", "style", "dropdown", "conditional_format", "table",
                "filter", "import_csv"}


def _check_unlocked(path):
    if path.with_name("~$" + path.name).exists() or path.with_name(f".~lock.{path.name}#").exists():
        raise PermissionError("Close the workbook in the spreadsheet application before editing.")


def _edit(wb, ws, action, args):
    from openpyxl.formatting.rule import ColorScaleRule
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.worksheet.datavalidation import DataValidation
    from openpyxl.worksheet.table import Table, TableStyleInfo
    from openpyxl.utils.cell import range_boundaries

    if action in {"add_sheet", "rename_sheet", "copy_sheet"}:
        name = args.get("target_sheet") or args.get("title")
        if not name or len(name) > 31 or name.casefold() in {n.casefold() for n in wb.sheetnames}:
            raise ValueError("Choose a unique worksheet name of at most 31 characters.")
        if action == "add_sheet":
            wb.create_sheet(name)
        elif action == "rename_sheet":
            ws.title = name
        else:
            wb.copy_worksheet(ws).title = name
        return

    if action == "delete_sheet":
        if sum(sheet.sheet_state == "visible" for sheet in wb) <= 1 and ws.sheet_state == "visible":
            raise ValueError("At least one visible worksheet is required.")
        wb.remove(ws)
        return

    if action in {"insert_rows", "delete_rows", "insert_columns", "delete_columns"}:
        index, count = int(args.get("index", 0)), int(args.get("count", 1))
        limit = 16_384 if action.endswith("columns") else _MAX_ROWS
        if not 1 <= index <= limit or not 1 <= count <= limit - index + 1:
            raise ValueError("Invalid row or column index/count.")
        method = action.replace("columns", "cols")
        getattr(ws, method)(index, count)
        return

    if action == "import_csv":
        with permitted(args["source"]).open(encoding="utf-8-sig", newline="") as stream:
            for index, row in enumerate(csv.reader(stream), start=ws.max_row + 1):
                if index > _MAX_ROWS:
                    raise ValueError("CSV exceeds worksheet row limit.")
                ws.append(row)
                # CSV import treats external values literally, including leading '='.
                for cell in ws[index]:
                    cell.data_type = "s"
        return

    cell_range = args.get("range") or args.get("cell")
    if not cell_range:
        raise ValueError("Provide a cell or a range.")
    left, top, right, bottom = range_boundaries(cell_range)
    if not all((left, top, right, bottom)) or not (
        1 <= left <= right <= 16_384 and 1 <= top <= bottom <= _MAX_ROWS
        and (right - left + 1) * (bottom - top + 1) <= 100_000
    ):
        raise ValueError("Invalid or excessive cell range.")

    if action == "set_range":
        data = args.get("data") or []
        if len(data) != bottom - top + 1 or any(
            not isinstance(row, list) or len(row) != right - left + 1 for row in data
        ):
            raise ValueError("Data dimensions must match the target range.")
        for i, row in enumerate(data, top):
            for j, value in enumerate(row, left):
                ws.cell(i, j).value = value
    elif action == "merge":
        ws.merge_cells(cell_range)
    elif action == "unmerge":
        ws.unmerge_cells(cell_range)
    elif action == "filter":
        ws.auto_filter.ref = cell_range
    elif action == "table":
        title = args.get("title") or "MarkTable"
        if top == bottom or any(not isinstance(ws.cell(top, j).value, str) or not ws.cell(top, j).value
                                for j in range(left, right + 1)):
            raise ValueError("Tables require text headings and at least one data row.")
        table = Table(displayName=title, ref=cell_range)
        table.tableStyleInfo = TableStyleInfo(name="TableStyleMedium9", showRowStripes=True)
        ws.add_table(table)
    elif action == "conditional_format":
        ws.conditional_formatting.add(cell_range, ColorScaleRule(
            start_type="min", start_color="F8696B", end_type="max", end_color="63BE7B",
        ))
    elif action == "dropdown":
        choices = args.get("choices") or []
        encoded = ",".join(choices)
        if not choices or len(encoded) > 250 or any(
            not option or any(character in option for character in (",", '"', "\r", "\n"))
            for option in choices
        ):
            raise ValueError("Provide literal dropdown options totalling at most 250 characters.")
        validation = DataValidation(type="list", formula1=f'"{encoded}"', showErrorMessage=True)
        ws.add_data_validation(validation)
        validation.add(cell_range)
    elif action == "style":
        from copy import copy
        options = args.get("options") or {}
        for row in ws.iter_rows(min_row=top, max_row=bottom, min_col=left, max_col=right):
            for cell in row:
                font = copy(cell.font)
                for key, attribute in (("font", "name"), ("size", "size"), ("bold", "bold"), ("color", "color")):
                    if key in options:
                        setattr(font, attribute, options[key])
                cell.font = font
                if "fill" in options:
                    cell.fill = PatternFill("solid", fgColor=options["fill"])
                if "border" in options:
                    side = Side(style="thin", color=options["border"])
                    cell.border = Border(left=side, right=side, top=side, bottom=side)
                if "alignment" in options:
                    cell.alignment = Alignment(horizontal=options["alignment"])
                if "number_format" in options:
                    cell.number_format = options["number_format"]


def _mutate(args: dict[str, Any]):
    action = str(args.get("action", "")).strip().lower()
    path = _path(args.get("path"), create=action == "create")
    from core.permissions import check as check_path
    allowed, reason = check_path(path, write=True)
    if not allowed:
        raise PermissionError(reason)
    _check_unlocked(path)
    openpyxl = _openpyxl()
    is_new = action == "create"
    if is_new:
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = str(args.get("sheet") or args.get("title") or "Data")[:31]
        rows = args.get("data") or []
        if not rows:
            raise ValueError("create requires data with a header row")
        if len(rows) > _MAX_ROWS:
            raise ValueError(f"row limit is {_MAX_ROWS}")
        for row in rows:
            ws.append(list(row) if isinstance(row, (list, tuple)) else [row])
        _style_sheet(ws)
    else:
        wb = _workbook(path)
        ws = _sheet(wb, args.get("sheet"))
    backup = None
    try:
        if action in _EXTRA_EDITS:
            _edit(wb, ws, action, args)
        elif action == "append":
            rows = args.get("data") or []
            if not isinstance(rows, list) or not rows:
                raise ValueError("append requires data as a non-empty list of rows")
            if ws.max_row + len(rows) > _MAX_ROWS:
                raise ValueError(f"row limit is {_MAX_ROWS}")
            for row in rows:
                ws.append(list(row) if isinstance(row, (list, tuple)) else [row])
        elif action in {"set_cell", "formula"}:
            cell = str(args.get("cell") or "").strip().upper()
            if not re.fullmatch(r"[A-Z]{1,3}[1-9][0-9]{0,6}", cell):
                raise ValueError("cell must be a valid reference such as B4")
            value = args.get("value")
            if action == "formula" and value is not None and not str(value).startswith("="):
                value = "=" + str(value)
            ws[cell] = value
        elif action == "format":
            _style_sheet(ws)
        elif action == "sort":
            col = _column(ws, args.get("column"))
            rows = list(ws.iter_rows(min_row=2, values_only=True))
            reverse = str(args.get("order", "ascending")).lower() in {"descending", "desc", "reverse"}
            def sort_key(row):
                value = row[col - 1] if col - 1 < len(row) else None
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    return (value is None, "number", float(value) if value is not None else 0.0)
                return (value is None, type(value).__name__, str(value).casefold())
            rows.sort(key=sort_key, reverse=reverse)
            if rows:
                ws.delete_rows(2, ws.max_row)
                for row in rows:
                    ws.append(list(row))
        elif action == "remove_duplicates":
            selected = args.get("column")
            cols = [_column(ws, x) for x in selected] if isinstance(selected, list) else ([_column(ws, selected)] if selected else list(range(1, ws.max_column+1)))
            seen, keep = set(), []
            for row in ws.iter_rows(min_row=2, values_only=True):
                key = tuple(str(row[i-1]) if i-1 < len(row) else "" for i in cols)
                if key not in seen:
                    seen.add(key); keep.append(row)
            removed = max(0, ws.max_row-1-len(keep))
            if ws.max_row > 1:
                ws.delete_rows(2, ws.max_row-1)
            for row in keep:
                ws.append(list(row))
        elif action == "summary":
            group_col = _column(ws, args.get("group_by"))
            value_col = _column(ws, args.get("column"))
            aggregate = str(args.get("aggregate", "sum")).lower()
            if aggregate not in {"sum", "average", "count", "min", "max"}:
                raise ValueError("aggregate must be sum, average, count, min, or max")
            groups: dict[str, list[Any]] = {}
            for row in ws.iter_rows(min_row=2, values_only=True):
                key = str(row[group_col-1] if group_col-1 < len(row) else "(blank)")
                val = row[value_col-1] if value_col-1 < len(row) else None
                groups.setdefault(key, []).append(val)
            name = str(args.get("title") or "Summary")[:31]
            if name in wb.sheetnames:
                raise ValueError(f"worksheet {name!r} already exists")
            summary = wb.create_sheet(name)
            summary.append([ws.cell(1, group_col).value, f"{aggregate.title()} of {ws.cell(1, value_col).value}"])
            for key, vals in sorted(groups.items()):
                nums = [v for v in vals if isinstance(v, (int, float)) and not isinstance(v, bool)]
                result = {"sum": lambda: sum(nums), "average": lambda: sum(nums)/len(nums) if nums else None,
                          "count": lambda: len([v for v in vals if v is not None]),
                          "min": lambda: min(nums) if nums else None, "max": lambda: max(nums) if nums else None}[aggregate]()
                summary.append([key, result])
            _style_sheet(summary)
        elif action == "chart":
            chart_type = str(args.get("chart_type", "bar")).lower()
            chart_cls = {"bar": openpyxl.chart.BarChart, "line": openpyxl.chart.LineChart, "pie": openpyxl.chart.PieChart}.get(chart_type)
            if not chart_cls:
                raise ValueError("chart_type must be bar, line, or pie")
            if ws.max_row < 2 or ws.max_column < 2:
                raise ValueError("chart needs a header row, category column, and data rows")
            chart = chart_cls()
            chart.title = str(args.get("title") or f"{ws.title} chart")
            chart.style = 10
            chart.height, chart.width = 9, 16
            chart.add_data(openpyxl.chart.Reference(ws, min_col=2, max_col=ws.max_column, min_row=1, max_row=ws.max_row), titles_from_data=True)
            chart.set_categories(openpyxl.chart.Reference(ws, min_col=1, min_row=2, max_row=ws.max_row))
            if chart_type == "bar":
                chart.type = "col"
                chart.grouping = "clustered"
            ws.add_chart(chart, f"A{ws.max_row+2}")
        elif action == "recalculate":
            raise ValueError("recalculate is handled outside the workbook edit path")
        elif action != "create":
            raise ValueError(f"unsupported action: {action}")
        if action in {"append", "create", "remove_duplicates"}:
            _style_sheet(ws)
        elif action == "summary":
            _style_sheet(wb[str(args.get("title") or "Summary")[:31]])
        backup, evidence = _atomic_save(wb, path, is_new=is_new)
        if action == "remove_duplicates":
            evidence["duplicates_removed"] = removed
        return path, backup, evidence
    finally:
        wb.close()


def _recalculate(args: dict[str, Any]):
    import subprocess
    path = _path(args.get("path"))
    from core.permissions import check as check_path
    allowed, reason = check_path(path, write=True)
    if not allowed:
        return failed("Workbook path is not approved for writing", reason)
    _check_unlocked(path)
    engine = str(args.get("engine") or "soffice")
    if engine not in {"soffice", "libreoffice"}:
        return failed("Unsupported spreadsheet recalculation engine", "Choose soffice or libreoffice.")
    exe = shutil.which(engine)
    if not exe:
        return failed("Spreadsheet recalculation engine is not installed", f"Install LibreOffice or provide engine path; formula values were not changed: {path.name}")
    backup = _backup(path)
    with tempfile.TemporaryDirectory(prefix="mark-excel-") as temp_dir:
        try:
            result = subprocess.run([exe, "--headless", "--convert-to", path.suffix.lstrip("."), "--outdir", temp_dir, str(path)],
                capture_output=True, text=True, timeout=90, check=False)
            converted = Path(temp_dir) / path.name
            if result.returncode != 0 or not converted.is_file() or converted.stat().st_size == 0:
                return failed("Workbook recalculation failed", (result.stderr or result.stdout or "No output from LibreOffice")[:1000])
            _workbook(converted).close()
            save_verified(path, lambda p: shutil.copyfile(converted, p), lambda p: _workbook(p).close(), replace=True)
        except subprocess.TimeoutExpired:
            return failed("Workbook recalculation timed out", f"The workbook remains unchanged; backup: {backup.name}")
        except Exception as exc:
            return failed("Workbook recalculation failed", f"{type(exc).__name__}: {exc}; backup: {backup.name}")
    return done(f"Recalculated {path.name}", evidence=json.dumps({"backup": backup.name, "engine": Path(exe).name}))


def run(args: dict[str, Any], ctx):
    action = str(args.get("action", "inspect")).strip().lower()
    try:
        if action == "recalculate":
            return _recalculate(args)
        if action in {"export_csv", "to_pdf"}:
            path = _path(args.get("path"))
            if action == "to_pdf":
                convert_pdf(path, args.get("destination") or path.with_suffix(".pdf"))
            else:
                wb = _workbook(path)
                try:
                    ws = _sheet(wb, args.get("sheet"))

                    def export(temporary):
                        with temporary.open("w", encoding="utf-8-sig", newline="") as stream:
                            csv.writer(stream).writerows(ws.values)

                    save_verified(args.get("destination") or path.with_suffix(".csv"), export,
                                  lambda p: p.stat())
                finally:
                    wb.close()
            return done(f"Exported {path.name}")
        if action in {"inspect", "profile"}:
            path = _path(args.get("path"))
            info = _inspect(path) if action == "inspect" else _profile(path, args.get("sheet"))
            label = "Inspected" if action == "inspect" else "Profiled"
            return done(f"{label} {path.name}", evidence=json.dumps(info, ensure_ascii=False, default=str))
        if action not in _EXTRA_EDITS | {"create", "append", "set_cell", "formula", "format", "sort", "remove_duplicates", "summary", "chart"}:
            return failed("Unknown Excel action", "use inspect, profile, create, append, set_cell, formula, format, sort, remove_duplicates, summary, chart, or recalculate")
        path, backup, evidence = _mutate(args)
        evidence["backup"] = backup.name if backup else None
        evidence["verified"] = True
        return done(f"{action.replace('_', ' ').title()} in {path.name}", evidence=json.dumps(evidence, ensure_ascii=False, default=str))
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
        return failed("Excel operation failed", f"{type(exc).__name__}: {exc}")


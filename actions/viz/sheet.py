"""ISO-style drawing sheet around a diagram: ISO 5457 sheet sizes + frame + centring marks + zone grid,
ISO 7200 title block, notation legend. Exports SVG, PNG and PDF from one drawing routine."""
from __future__ import annotations

import time
from pathlib import Path

from actions.viz.canvas import PilCanvas, SvgCanvas
from actions.viz.model import Edge, Graph
from actions.viz.shapes import INK, draw_edge, draw_graph

MM = 4.0                                    # sheet px per mm
SHEETS = [("A4", 297, 210), ("A3", 420, 297), ("A2", 594, 420), ("A1", 841, 594), ("A0", 1189, 841)]   # ISO 216 landscape
MARGIN, FILING = 10, 20                     # mm: frame offset, left filing margin
TB_W, TB_H = 170, 36                        # mm: title block (ISO 7200 fields)
EDGE_SAMPLE = {"dependency": "dependency (uses)", "inherit": "generalisation", "realize": "realisation", "assoc": "association",
               "flow": "flow", "fk": "relationship", "compose": "composition", "aggregate": "aggregation"}


def pick_sheet(cw: float, ch: float, legend_rows: int) -> tuple[str, int, int, bool]:
    for name, w, h in SHEETS:
        inner_w = (w - MARGIN - FILING) * MM
        inner_h = (h - 2 * MARGIN - TB_H) * MM - 20
        if cw + 30 <= inner_w and ch + 30 <= inner_h:
            return name, w, h, True
    n, w, h = SHEETS[-1]
    return n, w, h, False


def _draw_sheet(cv, g: Graph, sheet, meta: dict, ox: float, oy: float, fits: bool) -> None:
    name, wmm, hmm = sheet
    W, H = wmm * MM, hmm * MM
    L, T, R, B = FILING * MM, MARGIN * MM, W - MARGIN * MM, H - MARGIN * MM
    cv.rect(MARGIN * MM / 2, MARGIN * MM / 2, W - MARGIN * MM, H - MARGIN * MM, "none", INK, 0.6)      # trimmed-sheet edge
    cv.rect(L, T, R - L, B - T, "none", INK, 2.0)                                                       # drawing frame
    for x in (W / 2, ):                                                                                 # centring marks
        cv.line((x, T - 18), (x, T + 18), INK, 1.5); cv.line((x, B - 18), (x, B + 18), INK, 1.5)
    for y in (H / 2, ):
        cv.line((L - 18, y), (L + 18, y), INK, 1.5); cv.line((R - 18, y), (R + 18, y), INK, 1.5)
    nz_x, nz_y = max(2, int(round((R - L) / (50 * MM) / 2)) * 2), max(2, int(round((B - T) / (50 * MM) / 2)) * 2)   # zone grid, even count
    for i in range(nz_x):
        x0 = L + (R - L) * i / nz_x
        if i:
            cv.line((x0, T - 12), (x0, T), INK, 1.0); cv.line((x0, B), (x0, B + 12), INK, 1.0)
        cv.text(x0 + (R - L) / nz_x / 2, T - 18, str(i + 1), 11, INK); cv.text(x0 + (R - L) / nz_x / 2, B + 18, str(i + 1), 11, INK)
    for j in range(nz_y):
        y0 = T + (B - T) * j / nz_y
        if j:
            cv.line((L - 12, y0), (L, y0), INK, 1.0); cv.line((R, y0), (R + 12, y0), INK, 1.0)
        c = chr(65 + j)
        cv.text(L - 18, y0 + (B - T) / nz_y / 2, c, 11, INK); cv.text(R + 18, y0 + (B - T) / nz_y / 2, c, 11, INK)

    # content
    draw_graph_shifted(cv, g, ox, oy)

    # title block (ISO 7200 fields), bottom-right inside the frame
    tw, th = TB_W * MM, TB_H * MM
    x0, y0 = R - tw, B - th
    cv.rect(x0, y0, tw, th, "#ffffff", INK, 2.0)
    rows = [th * 0.34, th * 0.55, th * 0.78]
    for ry in rows:
        cv.line((x0, y0 + ry), (x0 + tw, y0 + ry), INK, 1.0)
    cv.line((x0 + tw * 0.68, y0 + rows[0]), (x0 + tw * 0.68, y0 + rows[1]), INK, 1.0)
    for fx in (0.25, 0.5, 0.75):
        cv.line((x0 + tw * fx, y0 + rows[1]), (x0 + tw * fx, y0 + th), INK, 1.0)
    cv.line((x0 + tw * 0.5, y0 + rows[2]), (x0 + tw * 0.5, y0 + th), INK, 1.0)

    def cell(fx, ry0, ry1, label, value, size=13, bold=False):
        cv.text(x0 + tw * fx + 6, y0 + ry0 + 9, label, 8, "#555555", "start")
        cv.text(x0 + tw * fx + 6, y0 + (ry0 + ry1) / 2 + 5, value, size, INK, "start", bold=bold)
    cell(0.0, 0, rows[0], "TITLE", meta["title"][:60], 18, True)
    cell(0.0, rows[0], rows[1], "DOCUMENT TYPE / NOTATION", meta["notation"][:48], 11)
    cell(0.68, rows[0], rows[1], "DOCUMENT No.", meta["doc_no"], 12)
    cell(0.0, rows[1], rows[2], "CREATED BY", meta["author"])
    cell(0.25, rows[1], rows[2], "DATE", meta["date"])
    cell(0.5, rows[1], rows[2], "REVISION", meta["revision"])
    cell(0.75, rows[1], rows[2], "SHEET", "1 / 1")
    cell(0.0, rows[2], th, "SIZE", name + (" (oversized content)" if not fits else ""), 12)
    cell(0.5, rows[2], th, "SCALE", meta["scale"], 12)
    cell(0.5 + 0.25, rows[2], th, "OWNER / PROJECT", meta["owner"][:20], 11)

    # legend (left of the title block)
    ly = y0 + 4
    lx = x0 - 360
    if g.legend and lx > L + 20:
        cv.rect(lx, y0, 350, th, "#ffffff", INK, 1.2)
        cv.text(lx + 8, y0 + 12, "LEGEND", 9, "#555555", "start", bold=True)
        row, col = 0, 0
        for kind, meaning in g.legend[:6]:
            cx_ = lx + 10 + col * 172
            cy_ = y0 + 30 + row * 20
            if kind in EDGE_SAMPLE:
                draw_edge(cv, Edge("a", "b", kind, points=[(cx_, cy_), (cx_ + 42, cy_)]))
                cv.text(cx_ + 52, cy_, meaning, 10, INK, "start")
            else:
                cv.rect(cx_, cy_ - 6, 24, 12, "#ffffff", INK, 1.0)
                cv.text(cx_ + 32, cy_, meaning, 10, INK, "start")
            row += 1
            if row == 3:
                row, col = 0, col + 1
    for i, note in enumerate(g.notes[:2]):
        cv.text(L + 14, B - 14 - i * 14, note, 10, "#444444", "start", italic=True)


def draw_graph_shifted(cv, g: Graph, ox: float, oy: float) -> None:
    for n in g.nodes:
        n.x += ox; n.y += oy
    for e in g.edges:
        e.points = [(x + ox, y + oy) for x, y in e.points]
    extra = getattr(g, "extra_draw", None)
    draw_graph(cv, g)
    if extra:
        extra(cv, g, ox, oy)


def export(g: Graph, content_w: float, content_h: float, out_dir: Path, base: str, meta: dict | None = None,
           formats=("svg", "png", "pdf")) -> dict:
    meta = dict(meta or {})
    name, wmm, hmm, fits = pick_sheet(content_w, content_h, len(g.legend))
    meta.setdefault("title", g.title)
    meta.setdefault("notation", g.notation)
    meta.setdefault("author", "Mark")
    meta.setdefault("date", time.strftime("%Y-%m-%d"))
    meta.setdefault("revision", "A")
    meta.setdefault("scale", "NTS (not to scale)")
    meta.setdefault("owner", "")
    meta.setdefault("doc_no", "JRV-" + time.strftime("%Y%m%d-%H%M"))
    W, H = wmm * MM, hmm * MM
    area_l, area_t = FILING * MM, MARGIN * MM
    area_w = (wmm - MARGIN - FILING) * MM
    area_h = (hmm - 2 * MARGIN - TB_H) * MM - 20
    ox = area_l + max(15, (area_w - content_w) / 2)
    oy = area_t + max(15, (area_h - content_h) / 2) + 6
    out_dir.mkdir(parents=True, exist_ok=True)
    out: dict[str, str] = {"sheet": name + ("" if fits else " (content larger than A0)")}
    import copy
    if "svg" in formats:
        cv = SvgCanvas(W, H)
        _draw_sheet(cv, copy.deepcopy(g), (name, wmm, hmm), meta, ox, oy, fits)
        p = out_dir / f"{base}.svg"
        p.write_text(cv.tostring(), encoding="utf-8")
        out["svg"] = str(p)
    if "png" in formats or "pdf" in formats:
        ss = max(1, min(3, int((55e6 / (W * H)) ** 0.5)))
        cv = PilCanvas(W, H, ss=ss)
        _draw_sheet(cv, copy.deepcopy(g), (name, wmm, hmm), meta, ox, oy, fits)
        img = cv.finish(zoom=min(2.0, float(ss)))
        if "png" in formats:
            p = out_dir / f"{base}.png"
            img.save(p, dpi=(MM * 25.4 * min(2.0, ss),) * 2)
            out["png"] = str(p)
        if "pdf" in formats:
            p = out_dir / f"{base}.pdf"
            img.save(p, "PDF", resolution=MM * 25.4 * min(2.0, ss) * 1.0)
            out["pdf"] = str(p)
    return out

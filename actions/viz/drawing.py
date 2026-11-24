"""2D engineering drawings: a JSON spec becomes a DXF (ezdxf) plus SVG / PNG / PDF previews.

Conventions followed: ISO 5457 sheet + frame, ISO 7200 title block fields, ISO 128 line types by layer
(visible / hidden / centre / dimension), ISO 129 style dimensions (extension lines, arrows, text above the line),
ISO 3098 upright lettering (3.5 mm default). A person responsible for the design must still check the result.

Spec (all lengths in mm, y up, model coordinates):
  {"title": "Bracket", "sheet": "A3", "scale": "1:2", "owner": "ACME", "drawing_no": "BR-001",
   "entities": [
     {"type":"rect","origin":[0,0],"w":100,"h":40,"layer":"visible"},
     {"type":"circle","center":[20,20],"r":6,"layer":"visible"},
     {"type":"line","from":[0,20],"to":[100,20],"layer":"center"},
     {"type":"arc","center":[50,20],"r":10,"start":0,"end":180},
     {"type":"polyline","points":[[0,0],[10,5],[20,0]],"closed":false},
     {"type":"text","at":[5,45],"text":"NOTE","height":3.5},
     {"type":"dim","from":[0,0],"to":[100,0],"offset":-12}   // linear dimension, value measured automatically
   ]}
"""
from __future__ import annotations

import math
import time
from pathlib import Path

from actions.viz.canvas import PilCanvas, SvgCanvas
from actions.viz.sheet import FILING, MARGIN, MM, SHEETS, TB_H, TB_W

LAYERS = {   # name: (ISO 128 linetype, lineweight mm, colour hex for preview)
    "visible": ("CONTINUOUS", 0.50, "#000000"), "hidden": ("DASHED", 0.25, "#000000"), "center": ("CENTER", 0.25, "#000000"),
    "dimension": ("CONTINUOUS", 0.25, "#000000"), "text": ("CONTINUOUS", 0.25, "#000000"),
    "frame": ("CONTINUOUS", 0.70, "#000000"), "title": ("CONTINUOUS", 0.35, "#000000"),
}
ALIAS = {"dim": "dimension", "dimensions": "dimension", "outline": "visible", "centre": "center", "centerline": "center", "dashed": "hidden"}
SCALES = {"1:1": 1.0, "1:2": 0.5, "1:5": 0.2, "1:10": 0.1, "1:20": 0.05, "1:50": 0.02, "1:100": 0.01, "2:1": 2.0, "5:1": 5.0, "10:1": 10.0}


class Prims:
    def __init__(self):
        self.items: list[tuple] = []

    def line(self, a, b, layer="visible"):
        self.items.append(("line", tuple(a), tuple(b), layer))

    def circle(self, c, r, layer="visible"):
        self.items.append(("circle", tuple(c), float(r), layer))

    def arc(self, c, r, a0, a1, layer="visible"):
        self.items.append(("arc", tuple(c), float(r), float(a0), float(a1), layer))

    def text(self, p, s, h=3.5, layer="text", anchor="start"):
        self.items.append(("text", tuple(p), str(s), float(h), layer, anchor))

    def poly(self, pts, closed=False, layer="visible"):
        for a, b in zip(pts, pts[1:]):
            self.line(a, b, layer)
        if closed and len(pts) > 2:
            self.line(pts[-1], pts[0], layer)

    def bbox(self):
        xs, ys = [], []
        for it in self.items:
            k = it[0]
            if k == "line":
                xs += [it[1][0], it[2][0]]; ys += [it[1][1], it[2][1]]
            elif k in ("circle", "arc"):
                c, r = it[1], it[2]
                xs += [c[0] - r, c[0] + r]; ys += [c[1] - r, c[1] + r]
            elif k == "text":
                xs.append(it[1][0]); ys.append(it[1][1])
                xs.append(it[1][0] + len(it[2]) * it[3] * 0.6); ys.append(it[1][1] + it[3])
        return (min(xs), min(ys), max(xs), max(ys)) if xs else (0, 0, 1, 1)


def _arrow(P: Prims, tip, direction, size=3.0):
    """ISO 129 style open 30 degree arrowhead drawn with two lines."""
    ang = math.atan2(direction[1], direction[0])
    for s in (+1, -1):
        a = ang + s * math.radians(15)
        P.line(tip, (tip[0] + size * math.cos(a), tip[1] + size * math.sin(a)), "dimension")


def _dim(P: Prims, e: dict, k: float):
    (x1, y1), (x2, y2) = e["from"], e["to"]
    off = float(e.get("offset", 10))
    dx, dy = x2 - x1, y2 - y1
    L = math.hypot(dx, dy)
    if L == 0:
        return
    ux, uy = dx / L, dy / L
    nx, ny = -uy, ux                                           # normal; positive offset = left of the direction
    p1 = (x1 + nx * off, y1 + ny * off)
    p2 = (x2 + nx * off, y2 + ny * off)
    sgn = 1 if off >= 0 else -1
    for (px, py), q in (((x1, y1), p1), ((x2, y2), p2)):       # extension lines: 1 mm gap, 2 mm past the dimension line
        P.line((px + nx * sgn * 1.0, py + ny * sgn * 1.0), (q[0] + nx * sgn * 2.0, q[1] + ny * sgn * 2.0), "dimension")
    P.line(p1, p2, "dimension")
    _arrow(P, p1, (ux, uy)); _arrow(P, p2, (-ux, -uy))
    val = e.get("text") or (f"{L:.1f}".rstrip("0").rstrip("."))
    mid = ((p1[0] + p2[0]) / 2 + nx * 1.5 * sgn, (p1[1] + p2[1]) / 2 + ny * 1.5 * sgn)
    P.text(mid, val, 3.5, "dimension", "middle")


def _entity(P: Prims, e: dict) -> None:
    t = str(e.get("type", "")).lower()
    layer = ALIAS.get(str(e.get("layer", "visible")).lower(), str(e.get("layer", "visible")).lower())
    if layer not in LAYERS:
        layer = "visible"
    if t == "line":
        P.line(e["from"], e["to"], layer)
    elif t == "rect":
        x, y = e["origin"]; w, h = float(e["w"]), float(e["h"])
        P.poly([(x, y), (x + w, y), (x + w, y + h), (x, y + h)], True, layer)
    elif t == "circle":
        P.circle(e["center"], e["r"], layer)
    elif t == "arc":
        P.arc(e["center"], e["r"], e.get("start", 0), e.get("end", 180), layer)
    elif t == "polyline":
        P.poly([tuple(p) for p in e["points"]], bool(e.get("closed")), layer)
    elif t == "text":
        P.text(e["at"], e["text"], float(e.get("height", 3.5)), "text", e.get("anchor", "start"))
    elif t == "dim":
        _dim(P, e, 1.0)
    else:
        raise ValueError(f"unknown entity type '{t}'")


def _frame(P: Prims, name: str, W: float, H: float, meta: dict) -> None:
    """ISO 5457 frame + ISO 7200 title block in sheet mm (origin bottom-left, y up)."""
    P.poly([(MARGIN / 2, MARGIN / 2), (W - MARGIN / 2, MARGIN / 2), (W - MARGIN / 2, H - MARGIN / 2), (MARGIN / 2, H - MARGIN / 2)], True, "title")
    L, B, R, T = FILING, MARGIN, W - MARGIN, H - MARGIN
    P.poly([(L, B), (R, B), (R, T), (L, T)], True, "frame")
    P.line((W / 2, B - 2), (W / 2, B + 4), "frame"); P.line((W / 2, T - 4), (W / 2, T + 2), "frame")
    P.line((L - 2, H / 2), (L + 4, H / 2), "frame"); P.line((R - 4, H / 2), (R + 2, H / 2), "frame")
    x0, y0 = R - TB_W, B
    P.poly([(x0, y0), (R, y0), (R, y0 + TB_H), (x0, y0 + TB_H)], True, "frame")
    rows = [TB_H * 0.66, TB_H * 0.45, TB_H * 0.22]
    for r in rows:
        P.line((x0, y0 + r), (R, y0 + r), "title")
    for fx in (0.25, 0.5, 0.75):
        P.line((x0 + TB_W * fx, y0 + rows[2]), (x0 + TB_W * fx, y0 + rows[1]), "title")
    P.line((x0 + TB_W * 0.68, y0 + rows[1]), (x0 + TB_W * 0.68, y0 + rows[0]), "title")
    P.line((x0 + TB_W * 0.5, y0), (x0 + TB_W * 0.5, y0 + rows[2]), "title")

    def cell(fx, ya, yb, label, value, h=3.5):
        P.text((x0 + TB_W * fx + 1.5, y0 + yb - 3.2), label, 1.8, "title")
        P.text((x0 + TB_W * fx + 1.5, y0 + ya + 1.8), value, h, "text")
    top = TB_H
    cell(0.0, rows[0], top, "TITLE", meta["title"][:44], 5)
    cell(0.0, rows[1], rows[0], "DOCUMENT TYPE", "Engineering drawing - 2D", 3)
    cell(0.68, rows[1], rows[0], "DRAWING No.", meta["drawing_no"], 3.2)
    cell(0.0, rows[2], rows[1], "CREATED BY", meta["author"], 3)
    cell(0.25, rows[2], rows[1], "DATE", meta["date"], 3)
    cell(0.5, rows[2], rows[1], "REVISION", meta["revision"], 3)
    cell(0.75, rows[2], rows[1], "SHEET", "1 / 1", 3)
    cell(0.0, 0, rows[2], "SIZE / UNITS", f"{name}  /  mm", 3)
    cell(0.5, 0, rows[2], "SCALE", meta["scale"], 3.2)
    cell(0.75, 0, rows[2], "OWNER", meta["owner"][:18], 3)


def build(spec: dict) -> tuple[Prims, str, float, float, dict]:
    ents = spec.get("entities") or []
    if not ents:
        raise ValueError("spec has no entities")
    model = Prims()
    for e in ents:
        _entity(model, e)
    scale_txt = str(spec.get("scale", "1:1"))
    k = SCALES.get(scale_txt)
    if k is None and ":" in scale_txt:
        a, b = scale_txt.split(":", 1)
        k = float(a) / float(b)
    k = k or 1.0
    x0, y0, x1, y1 = model.bbox()
    dw, dh = (x1 - x0) * k, (y1 - y0) * k
    meta = {"title": str(spec.get("title") or "Drawing"), "scale": scale_txt, "owner": str(spec.get("owner", "")),
            "drawing_no": str(spec.get("drawing_no") or "JRV-D-" + time.strftime("%Y%m%d-%H%M")), "author": "Mark",
            "date": time.strftime("%Y-%m-%d"), "revision": "A"}
    want = str(spec.get("sheet", "")).upper()
    choices = [s for s in SHEETS if s[0] == want] or SHEETS
    chosen = None
    for name, W, H in choices:
        if dw + 30 <= W - MARGIN - FILING and dh + 30 + TB_H <= H - 2 * MARGIN:
            chosen = (name, W, H)
            break
    fits = chosen is not None
    name, W, H = chosen or SHEETS[-1]
    out = Prims()
    _frame(out, name, W, H, meta)
    ox = FILING + ((W - MARGIN - FILING) - dw) / 2
    oy = MARGIN + TB_H + ((H - 2 * MARGIN - TB_H) - dh) / 2

    def T(p):
        return (ox + (p[0] - x0) * k, oy + (p[1] - y0) * k)
    for it in model.items:
        kind = it[0]
        if kind == "line":
            out.line(T(it[1]), T(it[2]), it[3])
        elif kind == "circle":
            out.circle(T(it[1]), it[2] * k, it[3])
        elif kind == "arc":
            out.arc(T(it[1]), it[2] * k, it[3], it[4], it[5])
        elif kind == "text":
            out.text(T(it[1]), it[2], it[3], it[4], it[5])        # text height stays ISO 3098 sized, not scaled
    info = {"sheet": name if fits else f"{name} (drawing larger than A0 at this scale)", "scale": scale_txt, "entities": len(ents)}
    return out, name, W, H, info


def write_dxf(P: Prims, path: Path, W: float, H: float) -> None:
    import ezdxf
    from ezdxf.enums import TextEntityAlignment
    doc = ezdxf.new("R2018", setup=True)
    doc.units = 4                                              # millimetres
    for name, (lt, lw, _c) in LAYERS.items():
        if name not in doc.layers:
            doc.layers.add(name.upper())
        doc.layers.get(name.upper()).dxf.lineweight = int(lw * 100)
        try:
            doc.layers.get(name.upper()).dxf.linetype = lt if lt in doc.linetypes else "CONTINUOUS"
        except Exception:
            pass
    msp = doc.modelspace()
    for it in P.items:
        k = it[0]
        if k == "line":
            msp.add_line(it[1], it[2], dxfattribs={"layer": it[3].upper()})
        elif k == "circle":
            msp.add_circle(it[1], it[2], dxfattribs={"layer": it[3].upper()})
        elif k == "arc":
            msp.add_arc(it[1], it[2], it[3], it[4], dxfattribs={"layer": it[5].upper()})
        elif k == "text":
            t = msp.add_text(it[2], height=it[3], dxfattribs={"layer": it[4].upper()})
            al = {"start": TextEntityAlignment.LEFT, "middle": TextEntityAlignment.MIDDLE_CENTER, "end": TextEntityAlignment.RIGHT}[it[5]]
            t.set_placement(it[1], align=al)
    doc.saveas(str(path))


def render_preview(P: Prims, W: float, H: float, cv) -> None:
    def px(p):
        return (p[0] * MM, (H - p[1]) * MM)
    for it in P.items:
        k = it[0]
        lay = it[-1] if k != "text" else it[4]
        lt, lw, col = LAYERS.get(lay, LAYERS["visible"])
        wpx = max(1.0, lw * MM * 0.9)
        dash = (6 * MM / 4 * 2, 2 * MM / 2) if lt == "DASHED" else ((14 * MM / 2, 3 * MM / 2) if lt == "CENTER" else None)
        if k == "line":
            cv.line(px(it[1]), px(it[2]), col, wpx, dash)
        elif k == "circle":
            cx, cy = px(it[1])
            cv.ellipse(cx, cy, it[2] * MM, it[2] * MM, "none", col, wpx)
        elif k == "arc":
            cx, cy = px(it[1])
            cv.arc(cx, cy, it[2] * MM, it[3], it[4] if it[4] > it[3] else it[4] + 360, col, wpx)
        elif k == "text":
            x, y = px(it[1])
            cv.text(x, y - it[3] * MM * 0.5, it[2], it[3] * MM * 0.95, "#000000", it[5])


def create(spec: dict, out_dir: Path, base: str, formats=("dxf", "svg", "png", "pdf")) -> dict:
    P, name, W, H, info = build(spec)
    out_dir.mkdir(parents=True, exist_ok=True)
    res = dict(info)
    if "dxf" in formats:
        p = out_dir / f"{base}.dxf"
        write_dxf(P, p, W, H)
        res["dxf"] = str(p)
    if "svg" in formats:
        cv = SvgCanvas(W * MM, H * MM)
        render_preview(P, W, H, cv)
        p = out_dir / f"{base}.svg"
        p.write_text(cv.tostring(), encoding="utf-8")
        res["svg"] = str(p)
    if "png" in formats or "pdf" in formats:
        ss = max(1, min(3, int((55e6 / (W * MM * H * MM)) ** 0.5)))
        cv = PilCanvas(W * MM, H * MM, ss=ss)
        render_preview(P, W, H, cv)
        img = cv.finish(zoom=min(2.0, float(ss)))
        if "png" in formats:
            p = out_dir / f"{base}.png"
            img.save(p)
            res["png"] = str(p)
        if "pdf" in formats:
            p = out_dir / f"{base}.pdf"
            img.save(p, "PDF", resolution=MM * 25.4 * min(2.0, ss))
            res["pdf"] = str(p)
    return res

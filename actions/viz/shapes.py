"""Standard symbols drawn on a canvas: UML class/package, C4 box, ERD entity, ISO 5807 flowchart symbols,
and the line-end decorations (hollow triangle, open arrow, crow's foot...)."""
from __future__ import annotations

import math

from actions.viz.canvas import text_width
from actions.viz.layout import BASE, LINE_H, PAD, node_lines
from actions.viz.model import Edge, Graph, Node

INK = "#1a1a1a"
FILL = {"package": "#FFF4CC", "class": "#FFFFFF", "class_head": "#E6EEF8", "entity": "#FFFFFF", "entity_head": "#E3F0E3",
        "process": "#FFFFFF", "decision": "#FFF4CC", "terminator": "#E6EEF8", "io": "#F3E8FA", "document": "#FFFFFF",
        "predefined": "#FFFFFF", "connector": "#FFFFFF", "box": "#438DD5", "db": "#438DD5", "actor": "#08427B"}
WHITE_TEXT = {"box", "db", "actor"}


def _txt_color(n: Node) -> str:
    return "#ffffff" if (n.kind in WHITE_TEXT and not n.fill) or (n.fill and _dark(n.fill)) else INK


def _dark(c: str) -> bool:
    c = c.lstrip("#")
    r, g, b = int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
    return (0.299 * r + 0.587 * g + 0.114 * b) < 140


def draw_node(cv, n: Node) -> None:
    if n.dummy:
        return
    k = n.kind
    fill = n.fill or FILL.get(k, "#FFFFFF")
    tc = _txt_color(n)
    head, sections = node_lines(n)
    x, y, w, h = n.x, n.y, n.w, n.h

    def header(top: float) -> float:
        yy = top + PAD / 2 + LINE_H / 2
        for t, bold, ital in head:
            cv.text(x + w / 2, yy, t, BASE if bold else BASE - 1, tc, "middle", bold=bold, italic=not bold)
            yy += LINE_H
        return yy - LINE_H / 2 + PAD / 2

    if k in ("class", "entity"):
        hh = len(head) * LINE_H + PAD
        cv.rect(x, y, w, h, FILL[k], INK, 1.4)
        cv.rect(x, y, w, hh, FILL[k + "_head"], INK, 1.4)
        header(y)
        yy = y + hh
        secs = sections or [[]]
        for si, sec in enumerate(secs):
            sh = h - (yy - y) if si == len(secs) - 1 else max(1, len(sec)) * (LINE_H - 2) + PAD
            if si > 0 or True:
                cv.line((x, yy), (x + w, yy), INK, 1.0)
            ty = yy + PAD / 2 + (LINE_H - 2) / 2
            for t, mono in sec:
                cv.text(x + PAD, ty, t, BASE - 1, INK, "start", mono=True)
                ty += LINE_H - 2
            yy += sh
    elif k == "package":
        tab_w = min(w * 0.5, 80)
        cv.rect(x, y, tab_w, 16, fill, INK, 1.4)
        cv.rect(x, y + 16, w, h - 16, fill, INK, 1.4)
        yy = y + 16 + (h - 16) / 2 - (len(head) - 1) * LINE_H / 2
        for t, bold, ital in head:
            cv.text(x + w / 2, yy, t, BASE if bold else BASE - 1, INK, "middle", bold=bold)
            yy += LINE_H
    elif k == "terminator":
        cv.rect(x, y, w, h, fill, INK, 1.4, radius=h / 2)
        _center_text(cv, n, head, tc)
    elif k == "process":
        cv.rect(x, y, w, h, fill, INK, 1.4)
        _center_text(cv, n, head, tc)
    elif k == "predefined":
        cv.rect(x, y, w, h, fill, INK, 1.4)
        cv.line((x + 8, y), (x + 8, y + h), INK, 1.2)
        cv.line((x + w - 8, y), (x + w - 8, y + h), INK, 1.2)
        _center_text(cv, n, head, tc)
    elif k == "decision":
        cv.polygon([(x + w / 2, y), (x + w, y + h / 2), (x + w / 2, y + h), (x, y + h / 2)], fill, INK, 1.4)
        _center_text(cv, n, head, tc)
    elif k == "io":
        sk = 14
        cv.polygon([(x + sk, y), (x + w, y), (x + w - sk, y + h), (x, y + h)], fill, INK, 1.4)
        _center_text(cv, n, head, tc)
    elif k == "document":
        pts = [(x, y), (x + w, y), (x + w, y + h - 8)]
        for i in range(0, 21):
            t = i / 20
            pts.append((x + w - t * w, y + h - 8 + 8 * math.sin(t * 2 * math.pi)))
        cv.polygon(pts, fill, INK, 1.4)
        _center_text(cv, n, head, tc, dy=-3)
    elif k == "connector":
        cv.ellipse(n.cx, n.cy, w / 2, h / 2, fill, INK, 1.4)
        _center_text(cv, n, head, tc)
    elif k == "db":
        ry = 9
        cv.rect(x, y + ry, w, h - 2 * ry, fill, INK, 1.4)
        cv.ellipse(x + w / 2, y + ry, w / 2, ry, fill, INK, 1.4)
        cv.ellipse(x + w / 2, y + h - ry, w / 2, ry, fill, INK, 1.4)
        cv.rect(x + 1, y + ry, w - 2, h - 2 * ry, fill, "none", 0)
        _center_text(cv, n, head, tc)
    elif k == "actor":
        cx = n.cx
        cv.ellipse(cx, y + 9, 8, 8, "#ffffff", INK, 1.4)
        cv.line((cx, y + 17), (cx, y + 38), INK, 1.4)
        cv.line((cx - 14, y + 24), (cx + 14, y + 24), INK, 1.4)
        cv.line((cx, y + 38), (cx - 12, y + 54), INK, 1.4)
        cv.line((cx, y + 38), (cx + 12, y + 54), INK, 1.4)
        yy = y + 62
        for t, bold, _ in head:
            cv.text(cx, yy, t, BASE, INK, "middle", bold=bold)
            yy += LINE_H
    else:                                                   # C4 box / container
        cv.rect(x, y, w, h, fill, "#2E6DA4" if not n.fill else INK, 1.4, radius=6)
        yy = y + PAD / 2 + LINE_H / 2
        for t, bold, ital in head:
            cv.text(x + w / 2, yy, t, BASE if bold else BASE - 1, tc, "middle", bold=bold)
            yy += LINE_H
        for sec in sections:
            for t, _ in sec:
                cv.text(x + w / 2, yy, t, BASE - 2, tc, "middle")
                yy += LINE_H - 2


def _center_text(cv, n: Node, head, color, dy: float = 0.0) -> None:
    total = len(head) * LINE_H
    yy = n.cy - total / 2 + LINE_H / 2 + dy
    for t, bold, ital in head:
        cv.text(n.cx, yy, t, BASE if bold else BASE - 1, color, "middle", bold=bold)
        yy += LINE_H


# ------------------------------------------------------------------------ edges
def _dir(a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    L = math.hypot(dx, dy) or 1.0
    return dx / L, dy / L


def _decor(cv, kind: str, tip, toward, color=INK) -> None:
    """Line-end decoration at `tip`; `toward` = the next point along the line (away from the tip)."""
    ux, uy = _dir(tip, toward)                  # unit vector pointing along the line, away from the tip
    px, py = -uy, ux
    if kind in ("arrow_open", "assoc", "dependency"):
        a = 11.0
        cv.polyline([(tip[0] + ux * a + px * 5, tip[1] + uy * a + py * 5), tip, (tip[0] + ux * a - px * 5, tip[1] + uy * a - py * 5)], color, 1.4)
    elif kind in ("arrow_filled", "flow"):
        a = 12.0
        cv.polygon([tip, (tip[0] + ux * a + px * 5, tip[1] + uy * a + py * 5), (tip[0] + ux * a - px * 5, tip[1] + uy * a - py * 5)], color, color, 1.0)
    elif kind in ("triangle_hollow", "inherit", "realize"):
        a = 14.0
        cv.polygon([tip, (tip[0] + ux * a + px * 7, tip[1] + uy * a + py * 7), (tip[0] + ux * a - px * 7, tip[1] + uy * a - py * 7)], "#ffffff", color, 1.4)
    elif kind == "diamond_filled" or kind == "compose":
        a = 18.0
        cv.polygon([tip, (tip[0] + ux * a / 2 + px * 5, tip[1] + uy * a / 2 + py * 5), (tip[0] + ux * a, tip[1] + uy * a),
                    (tip[0] + ux * a / 2 - px * 5, tip[1] + uy * a / 2 - py * 5)], color, color, 1.2)
    elif kind == "diamond_hollow" or kind == "aggregate":
        a = 18.0
        cv.polygon([tip, (tip[0] + ux * a / 2 + px * 5, tip[1] + uy * a / 2 + py * 5), (tip[0] + ux * a, tip[1] + uy * a),
                    (tip[0] + ux * a / 2 - px * 5, tip[1] + uy * a / 2 - py * 5)], "#ffffff", color, 1.4)
    elif kind in ("many", "zero_many"):               # crow's foot
        a = 13.0
        base = (tip[0] + ux * a, tip[1] + uy * a)
        cv.line(base, (tip[0] + px * 7, tip[1] + py * 7), color, 1.4)
        cv.line(base, (tip[0] - px * 7, tip[1] - py * 7), color, 1.4)
        cv.line(base, tip, color, 1.4)
        if kind == "zero_many":
            c = (tip[0] + ux * (a + 7), tip[1] + uy * (a + 7))
            cv.ellipse(c[0], c[1], 4.5, 4.5, "#ffffff", color, 1.4)
    elif kind in ("one", "zero_one"):
        for d in (9.0, 14.0) if kind == "one" else (9.0,):
            c = (tip[0] + ux * d, tip[1] + uy * d)
            cv.line((c[0] + px * 6, c[1] + py * 6), (c[0] - px * 6, c[1] - py * 6), color, 1.4)
        if kind == "zero_one":
            c = (tip[0] + ux * 17, tip[1] + uy * 17)
            cv.ellipse(c[0], c[1], 4.5, 4.5, "#ffffff", color, 1.4)


_STYLE = {            # kind -> (dash, dst decoration)
    "dependency": ((7, 5), "arrow_open"), "inherit": (None, "inherit"), "realize": ((7, 5), "inherit"),
    "assoc": (None, "arrow_open"), "flow": (None, "flow"), "fk": (None, ""), "compose": (None, ""), "aggregate": (None, ""),
}


def draw_edge(cv, e: Edge) -> None:
    pts = e.points
    if len(pts) < 2:
        return
    dash, dec = _STYLE.get(e.kind, (None, "arrow_open"))
    cv.polyline(pts, INK, 1.3, dash)
    if e.kind in ("compose", "aggregate"):
        _decor(cv, e.kind, pts[0], pts[1])
    if e.dst_end:
        _decor(cv, e.dst_end, pts[-1], pts[-2])
    elif dec:
        _decor(cv, dec, pts[-1], pts[-2])
    if e.src_end:
        _decor(cv, e.src_end, pts[0], pts[1])
    if e.label:
        mid = len(pts) // 2
        mx, my = ((pts[mid - 1][0] + pts[mid][0]) / 2, (pts[mid - 1][1] + pts[mid][1]) / 2) if len(pts) % 2 == 0 else pts[mid]
        w = text_width(e.label, BASE - 2) + 8
        cv.rect(mx - w / 2, my - 9, w, 18, "#ffffff", "none", 0)
        cv.text(mx, my, e.label, BASE - 2, INK, "middle")


def draw_graph(cv, g: Graph) -> None:
    for e in g.edges:
        draw_edge(cv, e)
    for n in g.nodes:
        draw_node(cv, n)

"""Flowcharts (ISO 5807 symbols) from a spec or straight from a Python function, and UML sequence diagrams from a spec."""
from __future__ import annotations

import ast
from pathlib import Path

from actions.viz.canvas import text_width
from actions.viz.model import Edge, Graph, Node

_TYPES = {"start": "terminator", "end": "terminator", "terminator": "terminator", "process": "process", "decision": "decision",
          "io": "io", "input": "io", "output": "io", "document": "document", "predefined": "predefined", "subroutine": "predefined",
          "connector": "connector"}
FLOW_LEGEND = [("terminator", "terminator (start / end)"), ("process", "process"), ("decision", "decision"),
               ("io", "input / output"), ("flow", "flow line")]


def flowchart_from_spec(spec: dict) -> Graph:
    g = Graph(str(spec.get("title") or "Flowchart"), "ISO 5807 - Flowchart symbols", legend=FLOW_LEGEND)
    steps = spec.get("steps") or spec.get("nodes") or []
    for i, s in enumerate(steps):
        sid = str(s.get("id", f"n{i}"))
        kind = _TYPES.get(str(s.get("type", "process")).lower(), "process")
        g.nodes.append(Node(sid, _wrap(str(s.get("text") or s.get("label") or sid)), kind))
    edges = spec.get("edges") or spec.get("links")
    if not edges:
        edges = [{"from": a.id, "to": b.id} for a, b in zip(g.nodes, g.nodes[1:])]
    for e in edges:
        g.edges.append(Edge(str(e.get("from")), str(e.get("to")), "flow", str(e.get("label", ""))))
    return g


def _wrap(t: str, width: int = 26) -> str:
    out, line = [], ""
    for w in t.split():
        if len(line) + len(w) + 1 > width and line:
            out.append(line)
            line = w
        else:
            line = f"{line} {w}".strip()
    out.append(line)
    return "\n".join(out[:4])


# ------------------------------------------------------------------------- Python function -> flowchart
class _FC:
    def __init__(self):
        self.g = Graph("", "ISO 5807 - Flowchart symbols", legend=FLOW_LEGEND)
        self.n = 0

    def add(self, text: str, kind: str = "process") -> str:
        self.n += 1
        nid = f"s{self.n}"
        self.g.nodes.append(Node(nid, _wrap(text), kind))
        return nid

    def link(self, a: str, b: str, label: str = "") -> None:
        self.g.edges.append(Edge(a, b, "flow", label))

    def block(self, stmts, entry_from: list[tuple[str, str]], end: str) -> list[tuple[str, str]]:
        """entry_from = [(node, edge label)] that flow into the first statement. Returns the open exits."""
        pending = entry_from
        for st in stmts:
            pending = self.stmt(st, pending, end)
            if not pending:
                break
        return pending

    def _in(self, pending, nid):
        for p, lab in pending:
            self.link(p, nid, lab)

    def stmt(self, st: ast.stmt, pending, end: str):
        if isinstance(st, ast.If):
            d = self.add(_short(st.test) + "?", "decision")
            self._in(pending, d)
            yes = self.block(st.body, [(d, "yes")], end)
            no = self.block(st.orelse, [(d, "no")], end) if st.orelse else [(d, "no")]
            return yes + no
        if isinstance(st, (ast.For, ast.AsyncFor, ast.While)):
            cond = f"for {_short(st.target)} in {_short(st.iter)}?" if not isinstance(st, ast.While) else _short(st.test) + "?"
            d = self.add(cond, "decision")
            self._in(pending, d)
            body_exit = self.block(st.body, [(d, "yes")], end)
            for p, lab in body_exit:
                self.link(p, d, lab)
            return [(d, "no")]
        if isinstance(st, ast.Return):
            r = self.add("return " + (_short(st.value) if st.value else ""), "process")
            self._in(pending, r)
            self.link(r, end)
            return []
        if isinstance(st, ast.Raise):
            r = self.add("raise " + (_short(st.exc) if st.exc else ""), "process")
            self._in(pending, r)
            self.link(r, end, "error")
            return []
        if isinstance(st, ast.Try):
            t = self.add("try", "process")
            self._in(pending, t)
            ok = self.block(st.body, [(t, "")], end)
            outs = list(ok)
            for h in st.handlers:
                outs += self.block(h.body, [(t, "except " + (_short(h.type) if h.type else ""))], end)
            return outs
        if isinstance(st, (ast.With, ast.AsyncWith)):
            return self.block(st.body, pending, end)
        if isinstance(st, ast.Expr) and isinstance(st.value, ast.Constant):
            return pending                                      # docstring
        text = _short(st)
        kind = "io" if any(k in text for k in ("print(", "input(", ".write(", ".read(", "open(")) else "process"
        if isinstance(st, ast.Expr) and isinstance(st.value, ast.Call) and kind == "process":
            kind = "predefined"                                  # call of another routine
        n = self.add(text, kind)
        self._in(pending, n)
        return [(n, "")]


def _short(n: ast.AST | None, limit: int = 60) -> str:
    try:
        s = ast.unparse(n).replace("\n", " ")
    except Exception:
        s = "?"
    return s if len(s) <= limit else s[: limit - 1] + "…"


def flowchart_from_function(file: str | Path, func: str) -> Graph:
    tree = ast.parse(Path(file).read_text(encoding="utf-8", errors="replace"))
    target = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func:
            target = node
            break
    if target is None:
        raise ValueError(f"function '{func}' not found in {file}")
    fc = _FC()
    args = ", ".join(a.arg for a in target.args.args)
    start = fc.add(f"{func}({args})", "terminator")
    end = fc.add("end", "terminator")
    out = fc.block(target.body, [(start, "")], end)
    for p, lab in out:
        fc.link(p, end, lab)
    fc.g.title = f"{func}() - control flow"
    if len(fc.g.nodes) > 45:
        fc.g.notes.append("long function - consider splitting it; chart is large")
    return fc.g


# ------------------------------------------------------------------------- sequence diagram
def sequence_from_spec(spec: dict) -> tuple[Graph, float, float]:
    """UML sequence diagram. Returns (graph, content_w, content_h). Drawing of lifelines/messages is attached as g.extra_draw."""
    parts = [str(p) for p in spec.get("participants", [])]
    msgs = spec.get("messages", [])
    for m in msgs:
        for k in ("from", "to"):
            if str(m.get(k)) not in parts:
                parts.append(str(m.get(k)))
    g = Graph(str(spec.get("title") or "Sequence diagram"), "UML 2.5 (ISO/IEC 19505) - Sequence diagram", kind="sequence")
    g.legend = [("assoc", "synchronous message"), ("dependency", "return / reply")]
    gap = max([text_width(p, 13, True) for p in parts] + [120]) + 70
    for i, p in enumerate(parts):
        n = Node(p, p, "box", fill="#E6EEF8")
        n.w, n.h = max(text_width(p, 13, True) + 24, 90), 34
        n.x, n.y = 20 + i * gap + gap / 2 - n.w / 2, 20
        g.nodes.append(n)
    y0 = 20 + 34 + 34
    ystep = 44
    H = y0 + ystep * (len(msgs) + 1)
    W = 20 + gap * len(parts) + 20

    def extra(cv, gg, ox, oy):
        x_of = {n.id: n.cx for n in gg.nodes}              # gg = the shifted copy being drawn
        for n in gg.nodes:
            cv.line((n.cx, n.y + n.h), (n.cx, H + oy), "#1a1a1a", 1.0, (6, 5))
        for i, m in enumerate(msgs):
            y = y0 + i * ystep + oy
            a, b = x_of[str(m["from"])], x_of[str(m["to"])]
            text = str(m.get("text", ""))
            ret = str(m.get("kind", "call")).lower() in ("return", "reply")
            if a == b:                                           # self message
                cv.polyline([(a, y - 8), (a + 40, y - 8), (a + 40, y + 10), (a, y + 10)], "#1a1a1a", 1.3)
                from actions.viz.shapes import _decor
                _decor(cv, "arrow_open", (a, y + 10), (a + 12, y + 10))
                cv.text(a + 46, y, text, 12, "#1a1a1a", "start")
                continue
            from actions.viz.shapes import _decor
            cv.line((a, y), (b, y), "#1a1a1a", 1.3, (7, 5) if ret else None)
            _decor(cv, "arrow_open" if ret else "arrow_filled", (b, y), (a, y))
            tw = text_width(text, 12) + 8
            cv.rect((a + b) / 2 - tw / 2, y - 19, tw, 16, "#ffffff", "none", 0)
            cv.text((a + b) / 2, y - 11, text, 12, "#1a1a1a", "middle")
    g.extra_draw = extra                                          # type: ignore[attr-defined]
    return g, W, H

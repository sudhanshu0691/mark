"""Layered (Sugiyama-style) layout: layers by longest path, dummy nodes for long edges,
barycentre ordering, coordinate assignment, border-clipped edge routing, and a lint pass."""
from __future__ import annotations

import math
from collections import defaultdict

from actions.viz.canvas import text_width
from actions.viz.model import Edge, Graph, Node

BASE = 13.0                # base font size (layout px)
PAD = 10.0
GAP_X = 46.0
GAP_Y = 70.0
LINE_H = 18.0


def node_lines(n: Node) -> tuple[list[tuple[str, bool, bool]], list[list[tuple[str, bool]]]]:
    """(header lines [(text, bold, italic)], sections [[(text, mono)]])"""
    head: list[tuple[str, bool, bool]] = []
    if n.stereotype:
        head.append((f"«{n.stereotype}»", False, False))
    for ln in str(n.label).split("\n"):
        head.append((ln, True, False))
    sections = [[(t, True) for t in sec] for sec in n.sections]
    return head, sections


def measure(n: Node) -> None:
    head, sections = node_lines(n)
    k = n.kind
    if n.dummy:
        n.w = n.h = 1.0
        return
    wtxt = max([text_width(t, BASE, b) for t, b, _ in head] + [text_width(t, BASE - 1, False, True) for sec in sections for t, _ in sec] + [30.0])
    hh = len(head) * LINE_H + PAD
    if k in ("class", "entity"):
        h = hh + sum((max(1, len(sec)) * (LINE_H - 2) + PAD) for sec in sections) if sections else hh + PAD
        n.w, n.h = wtxt + 2 * PAD, max(h, 40.0)
        if k == "class" and len(sections) < 2:
            n.h += (LINE_H - 2 + PAD) * (2 - len(sections))
    elif k == "decision":
        n.w, n.h = (wtxt + 2 * PAD) * 1.9, max(76.0, hh * 2.3)
    elif k in ("terminator", "process", "predefined", "document", "io", "connector"):
        n.w, n.h = wtxt + 2.6 * PAD + (16 if k == "predefined" else 0), max(44.0, hh + PAD)
        if k == "connector":
            n.w = n.h = 36.0
    elif k == "package":
        n.w, n.h = max(wtxt + 2 * PAD, 110.0), hh + PAD + 18
    elif k == "db":
        n.w, n.h = wtxt + 2 * PAD, hh + 2 * PAD + 14
    elif k == "actor":
        n.w, n.h = max(wtxt, 40.0), hh + 56
    else:  # box / C4 container
        extra = sum(len(sec) * (LINE_H - 2) for sec in sections) + (PAD if sections else 0)
        n.w, n.h = wtxt + 2.5 * PAD, hh + extra + PAD


def _break_cycles(nodes: list[Node], edges: list[Edge]) -> set[int]:
    adj = defaultdict(list)
    for i, e in enumerate(edges):
        adj[e.src].append((e.dst, i))
    state: dict[str, int] = {}
    rev: set[int] = set()
    for root in [n.id for n in nodes]:
        if state.get(root):
            continue
        stack = [(root, iter(adj[root]))]
        state[root] = 1
        while stack:
            u, it = stack[-1]
            for v, i in it:
                if state.get(v) == 1:
                    rev.add(i)
                elif not state.get(v):
                    state[v] = 1
                    stack.append((v, iter(adj[v])))
                    break
            else:
                state[u] = 2
                stack.pop()
    return rev


def layout(g: Graph) -> tuple[float, float]:
    """Fills x,y,w,h on nodes and `points` on edges. Returns (width, height) of the content."""
    for n in g.nodes:
        measure(n)
    ids = {n.id: n for n in g.nodes}
    edges = [e for e in g.edges if e.src in ids and e.dst in ids and e.src != e.dst]
    used = {e.src for e in edges} | {e.dst for e in edges}
    iso = [n for n in g.nodes if n.id not in used] if len(used) >= 2 else []
    all_real = list(g.nodes)
    nodes = [n for n in g.nodes if n not in iso] if iso else list(g.nodes)
    rev = _break_cycles(nodes, edges)
    # effective (acyclic) direction of every edge
    eff = [(e.dst, e.src) if i in rev else (e.src, e.dst) for i, e in enumerate(edges)]

    # --- layering: longest path ---
    preds = defaultdict(list)
    for a, b in eff:
        preds[b].append(a)
    layer: dict[str, int] = {}

    def depth(u: str, seen=()) -> int:
        if u in layer:
            return layer[u]
        layer[u] = 0
        layer[u] = 1 + max((depth(p) for p in preds[u]), default=-1)
        return layer[u]
    for n in nodes:
        depth(n.id)
    for n in nodes:
        n.layer = layer[n.id]

    # --- dummy nodes for edges spanning > 1 layer ---
    chains: list[list[str]] = []
    allnodes = list(nodes)
    for i, (a, b) in enumerate(eff):
        la, lb = layer[a], layer[b]
        chain = [a]
        for k in range(la + 1, lb):
            d = Node(f"_d{i}_{k}", "", "box", dummy=True)
            d.layer = k
            d.w = d.h = 1.0
            allnodes.append(d)
            ids[d.id] = d
            chain.append(d.id)
        chain.append(b)
        chains.append(chain)

    layers: dict[int, list[Node]] = defaultdict(list)
    for n in allnodes:
        layers[n.layer].append(n)
    nbrs = defaultdict(list)
    for ch in chains:
        for u, v in zip(ch, ch[1:]):
            nbrs[u].append(v)
            nbrs[v].append(u)

    order = {n.id: i for L in layers.values() for i, n in enumerate(L)}
    top = max(layers) if layers else 0
    for _ in range(6):                                    # barycentre sweeps (down, then up)
        for rng in (range(1, top + 1), range(top - 1, -1, -1)):
            for li in rng:
                prev = li - 1 if rng.step == 1 else li + 1
                def bary(n: Node) -> float:
                    ns = [order[m] for m in nbrs[n.id] if ids[m].layer == prev]
                    return sum(ns) / len(ns) if ns else order[n.id]
                layers[li].sort(key=bary)
                for i, n in enumerate(layers[li]):
                    order[n.id] = i

    TB = g.direction == "TB"
    main_gap, cross_gap = (GAP_Y, GAP_X) if TB else (GAP_X * 1.6, GAP_Y * 0.55)
    # --- coordinates: stack layers along main axis, spread along cross axis ---
    sizes = {li: (max(((n.h if TB else n.w) for n in L), default=0)) for li, L in layers.items()}
    pos_main, acc = {}, 0.0
    for li in range(top + 1):
        pos_main[li] = acc
        acc += sizes.get(li, 0) + main_gap
    cross: dict[str, float] = {}
    for li in range(top + 1):
        x = 0.0
        for n in layers[li]:
            ext = (n.w if TB else n.h)
            cross[n.id] = x + ext / 2
            x += ext + (cross_gap if not n.dummy else cross_gap * 0.35)
    for _ in range(4):                                    # pull nodes toward neighbours, keep order + spacing
        for li in list(range(top + 1)) + list(range(top, -1, -1)):
            L = layers[li]
            want = {}
            for n in L:
                ns = [cross[m] for m in nbrs[n.id]]
                want[n.id] = sum(ns) / len(ns) if ns else cross[n.id]
            x_prev = -1e9
            for n in L:
                ext = (n.w if TB else n.h)
                gap = cross_gap if not n.dummy else cross_gap * 0.35
                lo = x_prev + gap + ext / 2 if x_prev > -1e8 else -1e9
                cross[n.id] = max(want[n.id], lo)
                x_prev = cross[n.id] + ext / 2
            if L:                                         # stop rightward drift: re-centre the layer on where it wanted to be
                shift = sum(want[n.id] - cross[n.id] for n in L) / len(L)
                for n in L:
                    cross[n.id] += shift
    # normalise so everything starts at margin
    lo_c = min(cross[n.id] - (n.w if TB else n.h) / 2 for n in allnodes)
    for n in allnodes:
        c = cross[n.id] - lo_c + 20
        m = pos_main[n.layer] + (sizes[n.layer] - (n.h if TB else n.w)) / 2 + 20
        if TB:
            n.x, n.y = c - n.w / 2, m
        else:
            n.x, n.y = m, c - n.h / 2

    # --- edge routing through dummy points, clipped to node borders ---
    for (e, ch, flip) in zip(edges, chains, [i in rev for i in range(len(edges))]):
        pts = [(ids[c].cx, ids[c].cy) for c in ch]
        if flip:
            pts.reverse()
        a, b = ids[e.src], ids[e.dst]
        pts[0] = _clip(a, pts[1] if len(pts) > 1 else (b.cx, b.cy))
        pts[-1] = _clip(b, pts[-2] if len(pts) > 1 else (a.cx, a.cy))
        e.points = pts
    W = max(n.x + n.w for n in allnodes) + 20
    H = max(n.y + n.h for n in allnodes) + 20
    if iso:                                               # unconnected nodes: a tidy grid under the diagram
        row_w = max(W, 900.0)
        x = y = 20.0
        row_h = 0.0
        y = H + 10
        for n in sorted(iso, key=lambda n: n.label):
            if x + n.w > row_w and x > 20:
                x, y, row_h = 20.0, y + row_h + 30, 0.0
            n.x, n.y = x, y
            x += n.w + 30
            row_h = max(row_h, n.h)
            W = max(W, x)
        H = y + row_h + 20
    return W, H


def _clip(n: Node, toward: tuple[float, float]) -> tuple[float, float]:
    """Point where the line from n's centre to `toward` leaves n's bounding shape."""
    cx, cy = n.cx, n.cy
    dx, dy = toward[0] - cx, toward[1] - cy
    if dx == 0 and dy == 0:
        return cx, cy
    if n.kind in ("decision",):
        t = 1.0 / (abs(dx) / (n.w / 2) + abs(dy) / (n.h / 2) + 1e-9)
    elif n.kind in ("connector",):
        t = (n.w / 2) / math.hypot(dx, dy)
    else:
        t = min((n.w / 2) / abs(dx) if dx else 1e9, (n.h / 2) / abs(dy) if dy else 1e9)
    return cx + dx * t, cy + dy * t


def lint(g: Graph) -> list[str]:
    """Quality checks that need no AI: overlapping boxes, boxes with no room for their text."""
    issues: list[str] = []
    ns = [n for n in g.nodes if not n.dummy]
    for i, a in enumerate(ns):
        for b in ns[i + 1:]:
            if a.x < b.x + b.w and b.x < a.x + a.w and a.y < b.y + b.h and b.y < a.y + a.h:
                issues.append(f"overlap: {a.id} and {b.id}")
    ids = {n.id for n in g.nodes}
    for e in g.edges:
        if e.src not in ids or e.dst not in ids:
            issues.append(f"dangling edge {e.src}->{e.dst}")
    return issues

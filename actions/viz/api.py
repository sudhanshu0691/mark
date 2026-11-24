"""Public API used by the `visualize` tool: analyze a path, build standard diagrams, export ISO-style sheets."""
from __future__ import annotations

import re
import time
from pathlib import Path

from actions.viz import analyzer, flow
from actions.viz.layout import layout, lint
from actions.viz.model import Graph
from actions.viz.sheet import export

AUTO_ORDER = ["component", "package", "module", "class", "erd"]


def _slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_").lower()[:40] or "diagram"


def build(model: dict, kind: str) -> Graph | None:
    kind = kind.lower()
    if kind in ("component", "architecture", "c4"):
        return analyzer.component_graph(model)
    if kind in ("package", "packages"):
        return analyzer.package_graph(model) if len(model["modules"]) > 1 else None
    if kind in ("module", "modules", "dependency", "dependencies", "imports"):
        return analyzer.module_graph(model) if len(model["modules"]) > 1 else None
    if kind in ("class", "classes", "uml"):
        return analyzer.class_graph(model) if model["classes"] else None
    if kind in ("erd", "er", "database", "data"):
        return analyzer.erd_graph(model) if model["tables"] else None
    raise ValueError(f"unknown diagram type '{kind}'")


def render_graph(g: Graph, out_dir: Path, base: str, meta: dict | None = None, formats=("svg", "png", "pdf")) -> dict:
    W, H = layout(g)
    issues = lint(g)
    res = export(g, W, H, out_dir, base, meta, formats)
    res["issues"] = issues
    res["nodes"] = len([n for n in g.nodes if not n.dummy])
    res["edges"] = len(g.edges)
    res["title"] = g.title
    res["notation"] = g.notation
    return res


def render_sequence(spec: dict, out_dir: Path, base: str, meta: dict | None = None, formats=("svg", "png", "pdf")) -> dict:
    g, W, H = flow.sequence_from_spec(spec)
    res = export(g, W, H, out_dir, base, meta, formats)
    res.update(issues=[], nodes=len(g.nodes), edges=len(spec.get("messages", [])), title=g.title, notation=g.notation)
    return res


def diagrams_for_project(path: str, kinds: list[str], out_root: Path, owner: str = "", formats=("svg", "png", "pdf")) -> dict:
    model = analyzer.analyze(path)
    out_dir = out_root / (_slug(model["name"]) + "_" + time.strftime("%Y%m%d_%H%M%S"))
    made, skipped = [], []
    wanted = AUTO_ORDER if (not kinds or kinds == ["auto"]) else kinds
    for k in wanted:
        g = build(model, k)
        if g is None or not g.nodes:
            skipped.append(f"{k}: nothing to draw for this project")
            continue
        made.append((k, render_graph(g, out_dir, f"{_slug(model['name'])}_{k}", {"owner": owner or model["name"]}, formats)))
    return {"model": model, "summary": analyzer.describe(model), "out_dir": str(out_dir), "diagrams": made, "skipped": skipped}

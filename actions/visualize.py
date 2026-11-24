"""
visualize - analyse a project folder and draw standard diagrams, flowcharts, sequence diagrams and 2D engineering drawings.

    action=analyze     path                        -> what is in the project (files, modules, classes, tables)
    action=diagram     path [diagram_type]         -> auto | component | package | module | class | erd
    action=flowchart   spec  |  path + function    -> ISO 5807 flowchart from a spec or straight from a Python function
    action=sequence    spec                        -> UML sequence diagram
    action=drawing     spec                        -> DXF + preview of a 2D engineering drawing (ISO 5457 / 7200 / 128 / 129)

Results are saved under data/visualisations/ as SVG, PNG, PDF (and DXF for drawings). The PNG is shown in the middle of
the Mark screen. The tool only says [DONE] when every output file really exists on disk.
"""
from __future__ import annotations

import json
from pathlib import Path

from core.paths import VIZ_DIR, ensure
from core.result import ActionResult, done, failed, unverified

_FORMATS = ("svg", "png", "pdf")


def _load_spec(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    if not raw:
        raise ValueError("spec is required (JSON text)")
    return json.loads(str(raw))


def _check_files(res: dict, keys=("svg", "png", "pdf", "dxf")) -> list[str]:
    missing = []
    for k in keys:
        p = res.get(k)
        if p and (not Path(p).exists() or Path(p).stat().st_size == 0):
            missing.append(f"{k}: {p}")
    return missing


def _show(player, res: dict, caption: str) -> None:
    png = res.get("png")
    if player is not None and png and hasattr(player, "show_work_image"):
        try:
            player.show_work_image(png, caption, 15.0)
        except Exception:
            pass


def visualize(parameters: dict | None = None, player=None, **_) -> ActionResult:
    p = parameters or {}
    action = str(p.get("action", "diagram")).lower().strip()
    ensure(VIZ_DIR)
    try:
        from actions.viz import analyzer, api, drawing, flow
    except Exception as e:                                           # Pillow / ezdxf missing
        return failed("Visualization libraries are missing", f"{type(e).__name__}: {e}. Run: pip install pillow ezdxf")
    try:
        if action == "analyze":
            model = analyzer.analyze(_need_path(p))
            return done("Project analysed", evidence=analyzer.describe(model) +
                        "\nTop external libraries: " + ", ".join(list(model["external"])[:8]))

        if action == "diagram":
            kinds = p.get("diagram_type") or p.get("type") or "auto"
            kinds = [k.strip() for k in str(kinds).split(",") if k.strip()]
            if player is not None and hasattr(player, "show_work"):
                player.show_work("ANALYSING PROJECT", [f"reading {_need_path(p)} ..."], 6.0)
            r = api.diagrams_for_project(_need_path(p), kinds, VIZ_DIR, owner=str(p.get("owner", "")), formats=_FORMATS)
            if not r["diagrams"]:
                return failed("Nothing to draw", "; ".join(r["skipped"]) or "no analysable source files found")
            problems, lines = [], []
            for kind, res in r["diagrams"]:
                problems += [f"{kind}: missing {m}" for m in _check_files(res)] + [f"{kind}: layout {i}" for i in res["issues"]]
                lines.append(f"- {kind}: {res['notation']} | sheet {res['sheet']} | {res['nodes']} nodes, {res['edges']} edges | {res.get('png')}")
            _show(player, r["diagrams"][0][1], f"{r['diagrams'][0][1]['title']}")
            ev = f"{r['summary']}\nFolder: {r['out_dir']}\n" + "\n".join(lines) + (f"\nNot drawn: {'; '.join(r['skipped'])}" if r["skipped"] else "")
            if problems:
                return unverified("Diagrams were written but have problems", reason="; ".join(problems[:5]), evidence=ev)
            return done(f"{len(r['diagrams'])} diagram(s) created", evidence=ev)

        if action == "flowchart":
            if p.get("function") and p.get("path"):
                g = flow.flowchart_from_function(_need_path(p), str(p["function"]))
            else:
                g = flow.flowchart_from_spec(_load_spec(p.get("spec")))
            return _finish_graph(api, g, p, player)

        if action == "sequence":
            res = api.render_sequence(_load_spec(p.get("spec")), VIZ_DIR / "sequence", _slug_title(p), None, _FORMATS)
            return _finish(res, player)

        if action == "drawing":
            spec = _load_spec(p.get("spec"))
            res = drawing.create(spec, VIZ_DIR / "drawings", _slug_title(p, spec.get("title")), ("dxf",) + _FORMATS)
            return _finish(res, player, kind="drawing")

        return failed(f"Unknown action '{action}'", "use analyze | diagram | flowchart | sequence | drawing")
    except FileNotFoundError as e:
        return failed("Path not found", str(e))
    except (ValueError, KeyError, json.JSONDecodeError) as e:
        return failed("Invalid input", f"{type(e).__name__}: {e}")
    except Exception as e:
        return failed("Visualization crashed", f"{type(e).__name__}: {e}")


def _need_path(p: dict) -> str:
    path = str(p.get("path") or "").strip().strip('"')
    if not path:
        raise ValueError("path is required")
    return path


def _slug_title(p: dict, title=None) -> str:
    import re
    import time
    t = str(title or p.get("title") or p.get("function") or "diagram")
    return re.sub(r"[^A-Za-z0-9]+", "_", t).strip("_").lower()[:40] + time.strftime("_%H%M%S")


def _finish_graph(api, g, p, player) -> ActionResult:
    res = api.render_graph(g, VIZ_DIR / "flowcharts", _slug_title(p, g.title), None, _FORMATS)
    return _finish(res, player, extra_issues=res["issues"])


def _finish(res: dict, player, kind: str = "diagram", extra_issues=()) -> ActionResult:
    missing = _check_files(res)
    files = ", ".join(f"{k}={res[k]}" for k in ("png", "pdf", "svg", "dxf") if res.get(k))
    ev = f"{res.get('title', kind)} | sheet {res.get('sheet')} | {files}"
    _show(player, res, res.get("title", kind))
    if missing or extra_issues:
        return unverified(f"{kind} written with problems", reason="; ".join(missing + list(extra_issues))[:300], evidence=ev)
    return done(f"{kind} created", evidence=ev)

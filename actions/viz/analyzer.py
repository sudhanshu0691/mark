"""Project analyzer: walks a folder and builds a structure model (modules, classes, imports, tables),
then turns it into UML / C4 / ERD graphs. Python is parsed with `ast`; JS/TS/Java use light regex parsing."""
from __future__ import annotations

import ast
import os
import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

from actions.viz.model import Edge, Graph, Node

SKIP_DIRS = {".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv", "env", "dist", "build", ".idea",
             ".vscode", ".pytest_cache", ".mypy_cache", "site-packages", "data", "visualisations", ".tox", "target", "bin", "obj"}
LANG = {".py": "Python", ".js": "JavaScript", ".jsx": "JavaScript", ".ts": "TypeScript", ".tsx": "TypeScript", ".java": "Java",
        ".cs": "C#", ".go": "Go", ".rs": "Rust", ".c": "C", ".cpp": "C++", ".h": "C/C++", ".kt": "Kotlin", ".php": "PHP",
        ".rb": "Ruby", ".sql": "SQL", ".html": "HTML", ".css": "CSS"}
MAX_FILES = 4000
MAX_BYTES = 1_500_000
STD = set(getattr(__import__("sys"), "stdlib_module_names", set())) | {"typing", "os", "sys", "re", "json"}


def analyze(root: str | Path) -> dict:
    root = Path(root).expanduser().resolve()
    if not root.exists():
        raise FileNotFoundError(f"path not found: {root}")
    files: list[Path] = []
    if root.is_file():
        files, base = [root], root.parent
    else:
        base = root
        for dp, dns, fns in os.walk(root):
            dns[:] = [d for d in dns if d not in SKIP_DIRS and not d.startswith(".")]
            for fn in fns:
                files.append(Path(dp) / fn)
                if len(files) >= MAX_FILES:
                    break
    model: dict = {"root": str(root), "name": root.stem if root.is_file() else root.name, "base": str(base),
                   "languages": Counter(), "loc": 0, "modules": {}, "classes": [], "tables": [], "entry_points": [],
                   "external": Counter(), "truncated": len(files) >= MAX_FILES, "files": 0}
    py_mods: dict[str, Path] = {}
    for f in files:
        if f.suffix == ".py":
            py_mods[_modname(f, base)] = f
    for f in files:
        ext = f.suffix.lower()
        lang = LANG.get(ext)
        if not lang or f.stat().st_size > MAX_BYTES:
            if ext in (".db", ".sqlite", ".sqlite3"):
                _sqlite_tables(f, model)
            continue
        model["languages"][lang] += 1
        model["files"] += 1
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        model["loc"] += text.count("\n") + 1
        rel = f.relative_to(base).as_posix()
        if ext == ".py":
            _py(f, text, base, rel, model, py_mods)
        elif ext in (".js", ".jsx", ".ts", ".tsx"):
            _js(f, text, base, rel, model)
        elif ext == ".java":
            _java(f, text, rel, model)
        elif ext == ".sql":
            _sql(text, model)
    _resolve_edges(model)
    model["languages"] = dict(model["languages"])
    model["external"] = dict(model["external"].most_common(12))
    return model


def _modname(f: Path, base: Path) -> str:
    rel = f.relative_to(base).with_suffix("")
    parts = list(rel.parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1] or [base.name]
    return ".".join(parts)


def _new_module(model, name, rel, lang, doc="", loc=0):
    m = model["modules"].setdefault(name, {"name": name, "path": rel, "lang": lang, "doc": doc, "imports": set(), "ext": set(), "loc": loc})
    return m


def _py(f: Path, text: str, base: Path, rel: str, model: dict, py_mods: dict) -> None:
    name = _modname(f, base)
    try:
        tree = ast.parse(text)
    except SyntaxError:
        _new_module(model, name, rel, "Python", "(syntax error - not parsed)")
        return
    doc = (ast.get_docstring(tree) or "").strip().splitlines()
    mod = _new_module(model, name, rel, "Python", doc[0][:80] if doc else "", text.count("\n") + 1)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                _py_import(a.name, mod, py_mods, model)
        elif isinstance(node, ast.ImportFrom):
            if node.level:                                     # relative import
                pkg = name.split(".")[:-node.level] if not rel.endswith("__init__.py") else name.split(".")[:len(name.split(".")) - node.level + 1]
                target = ".".join(pkg + ([node.module] if node.module else []))
                cand = [target] + [f"{target}.{a.name}" for a in node.names]
                for c in cand:
                    if c in py_mods:
                        mod["imports"].add(c)
            elif node.module:
                _py_import(node.module, mod, py_mods, model)
                for a in node.names:
                    if f"{node.module}.{a.name}" in py_mods:
                        mod["imports"].add(f"{node.module}.{a.name}")
        elif isinstance(node, ast.If) and isinstance(node.test, ast.Compare) and "__main__" in ast.dump(node.test):
            model["entry_points"].append(name)
    ctx = [c.name for c in tree.body if isinstance(c, ast.ClassDef)]
    for c in tree.body:
        if isinstance(c, ast.ClassDef):
            model["classes"].append(_py_class(c, name, ctx, text))
        if isinstance(c, ast.Assign) and isinstance(c.value, ast.Call):   # SQLAlchemy / Django style tables
            pass


def _py_import(modname: str, mod: dict, py_mods: dict, model: dict) -> None:
    parts = modname.split(".")
    for i in range(len(parts), 0, -1):
        cand = ".".join(parts[:i])
        if cand in py_mods:
            mod["imports"].add(cand)
            return
    top = parts[0]
    if top not in STD and top not in ("__future__",):
        mod["ext"].add(top)
        model["external"][top] += 1


def _expr(n: ast.AST) -> str:
    try:
        return ast.unparse(n)
    except Exception:
        return "?"


def _py_class(c: ast.ClassDef, module: str, siblings: list[str], text: str) -> dict:
    bases = [_expr(b) for b in c.bases]
    attrs: list[str] = []
    methods: list[str] = []
    uses: set[str] = set()
    cols: list[tuple[str, str, bool]] = []                 # for ORM-style models: (name, type, is_fk)
    for st in c.body:
        if isinstance(st, ast.AnnAssign) and isinstance(st.target, ast.Name):
            attrs.append(f"{st.target.id}: {_expr(st.annotation)}")
        elif isinstance(st, ast.Assign) and len(st.targets) == 1 and isinstance(st.targets[0], ast.Name):
            v = _expr(st.value)
            attrs.append(st.targets[0].id)
            if isinstance(st.value, ast.Call) and re.search(r"(Column|Field|ForeignKey|models\.)", _expr(st.value.func)):
                cols.append((st.targets[0].id, _expr(st.value.func).split(".")[-1], "ForeignKey" in v))
        elif isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = [a.arg for a in st.args.args if a.arg not in ("self", "cls")]
            vis = "-" if st.name.startswith("_") and not st.name.startswith("__") else "+"
            methods.append(f"{vis} {st.name}({', '.join(args)[:30]})")
            for sub in ast.walk(st):
                if isinstance(sub, ast.Assign):
                    for t in sub.targets:
                        if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) and t.value.id == "self":
                            if f"{t.attr}" not in [a.split(":")[0] for a in attrs]:
                                attrs.append(t.attr)
                            if isinstance(sub.value, ast.Call):
                                callee = _expr(sub.value.func).split(".")[-1]
                                uses.add(callee)
    return {"name": c.name, "module": module, "bases": bases, "attrs": attrs, "methods": methods, "uses": sorted(uses),
            "orm": bool(cols) and any(b.split(".")[-1] in ("Model", "Base", "DeclarativeBase") for b in bases), "cols": cols}


_JS_IMPORT = re.compile(r"""(?:import\s+(?:[^'"]*?from\s+)?|require\(\s*|import\(\s*)['"]([^'"]+)['"]""")


def _js(f: Path, text: str, base: Path, rel: str, model: dict) -> None:
    name = Path(rel).with_suffix("").as_posix().replace("/", ".")
    mod = _new_module(model, name, rel, "JS/TS", "", text.count("\n") + 1)
    for m in _JS_IMPORT.finditer(text):
        spec = m.group(1)
        if spec.startswith("."):
            tgt = (f.parent / spec).resolve()
            for ext in ("", ".js", ".jsx", ".ts", ".tsx", "/index.js", "/index.ts"):
                c = Path(str(tgt) + ext)
                if c.is_file():
                    try:
                        mod["imports"].add(Path(c.relative_to(base)).with_suffix("").as_posix().replace("/", "."))
                    except ValueError:
                        pass
                    break
        else:
            pkg = spec.split("/")[0] if not spec.startswith("@") else "/".join(spec.split("/")[:2])
            mod["ext"].add(pkg)
            model["external"][pkg] += 1
    for cm in re.finditer(r"\bclass\s+(\w+)(?:\s+extends\s+(\w+))?", text):
        model["classes"].append({"name": cm.group(1), "module": name, "bases": [cm.group(2)] if cm.group(2) else [],
                                 "attrs": [], "methods": [], "uses": [], "orm": False, "cols": []})


def _java(f: Path, text: str, rel: str, model: dict) -> None:
    pk = re.search(r"^\s*package\s+([\w.]+);", text, re.M)
    pkg = pk.group(1) if pk else ""
    name = (pkg + "." if pkg else "") + f.stem
    mod = _new_module(model, name, rel, "Java", "", text.count("\n") + 1)
    for im in re.finditer(r"^\s*import\s+(?:static\s+)?([\w.]+);", text, re.M):
        mod["imports"].add(im.group(1))
    for cm in re.finditer(r"\b(?:class|interface)\s+(\w+)(?:\s+extends\s+(\w+))?", text):
        model["classes"].append({"name": cm.group(1), "module": name, "bases": [cm.group(2)] if cm.group(2) else [],
                                 "attrs": [], "methods": [], "uses": [], "orm": False, "cols": []})


_CREATE = re.compile(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[`\"\[]?(\w+)[`\"\]]?\s*\((.*?)\)\s*;", re.I | re.S)


def _sql(text: str, model: dict) -> None:
    for m in _CREATE.finditer(text):
        tname, body = m.group(1), m.group(2)
        cols, fks = [], []
        for part in re.split(r",\s*(?![^()]*\))", body):
            p = part.strip()
            if not p:
                continue
            fk = re.search(r"FOREIGN\s+KEY\s*\((\w+)\)\s*REFERENCES\s+[`\"\[]?(\w+)", p, re.I)
            if fk:
                fks.append((fk.group(1), fk.group(2)))
                continue
            if re.match(r"(PRIMARY|UNIQUE|CONSTRAINT|CHECK|KEY|INDEX)\b", p, re.I):
                continue
            cn = re.match(r"[`\"\[]?(\w+)[`\"\]]?\s+(\w+)", p)
            if cn:
                ref = re.search(r"REFERENCES\s+[`\"\[]?(\w+)", p, re.I)
                if ref:
                    fks.append((cn.group(1), ref.group(1)))
                cols.append((cn.group(1), cn.group(2).upper(), bool(re.search(r"PRIMARY\s+KEY", p, re.I))))
        model["tables"].append({"name": tname, "cols": cols, "fks": fks})


def _sqlite_tables(f: Path, model: dict) -> None:
    try:
        con = sqlite3.connect(f"file:{f}?mode=ro", uri=True)
        for (t,) in con.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%'"):
            cols = [(r[1], (r[2] or "").upper(), bool(r[5])) for r in con.execute(f'pragma table_info("{t}")')]
            fks = [(r[3], r[2]) for r in con.execute(f'pragma foreign_key_list("{t}")')]
            model["tables"].append({"name": t, "cols": cols, "fks": fks})
        con.close()
    except sqlite3.Error:
        pass


def _resolve_edges(model: dict) -> None:
    names = set(model["modules"])
    for m in model["modules"].values():
        m["imports"] = sorted(i for i in m["imports"] if i in names and i != m["name"])
        m["ext"] = sorted(m["ext"])
    for c in model["classes"]:                              # ORM-style models become tables too
        if c["orm"]:
            model["tables"].append({"name": c["name"], "cols": [(n, t, False) for n, t, fk in c["cols"]],
                                    "fks": [(n, _fk_target(c, n)) for n, t, fk in c["cols"] if fk]})


def _fk_target(c: dict, col: str) -> str:
    return col.removesuffix("_id").removesuffix("Id").capitalize()


# ---------------------------------------------------------------------------- graph builders
def _top(name: str, depth: int) -> str:
    parts = name.split(".")
    return ".".join(parts[:depth]) if len(parts) > depth else (parts[0] if len(parts) == 1 else ".".join(parts[:-1]) or name)


def _group_depth(model: dict) -> int:
    for depth in (1, 2):
        groups = {_top(m, depth) for m in model["modules"]}
        if 3 <= len(groups) <= 30:
            return depth
    return 1


def package_graph(model: dict, max_nodes: int = 36) -> Graph:
    depth = _group_depth(model)
    groups: dict[str, list[str]] = defaultdict(list)
    for m in model["modules"]:
        groups[_top(m, depth)].append(m)
    root_label = model["name"]
    g = Graph(f"{root_label} - package structure", "UML 2.5 (ISO/IEC 19505) - Package diagram")
    ordered = sorted(groups.items(), key=lambda kv: -len(kv[1]))[:max_nodes]
    keep = {k for k, _ in ordered}
    for k, mods in ordered:
        loc = sum(model["modules"][m]["loc"] for m in mods)
        g.nodes.append(Node(k, f"{k}\n{len(mods)} modules, {loc} LOC", "package"))
    weight: Counter = Counter()
    for m, d in model["modules"].items():
        a = _top(m, depth)
        for i in d["imports"]:
            b = _top(i, depth)
            if a != b and a in keep and b in keep:
                weight[(a, b)] += 1
    for (a, b), n in weight.items():
        g.edges.append(Edge(a, b, "dependency", str(n) if n > 1 else ""))
    g.legend = [("package", "package (folder / namespace)"), ("dependency", "dependency (imports; n = count)")]
    if len(groups) > max_nodes:
        g.notes.append(f"{len(groups) - max_nodes} smaller packages omitted for readability")
    return g


def module_graph(model: dict, max_nodes: int = 28) -> Graph:
    g = Graph(f"{model['name']} - module dependencies", "UML 2.5 (ISO/IEC 19505) - Component dependency view")
    mods = sorted(model["modules"].values(), key=lambda m: -(len(m["imports"]) + sum(1 for o in model["modules"].values() if m["name"] in o["imports"])))[:max_nodes]
    keep = {m["name"] for m in mods}
    for m in mods:
        short = m["name"] if len(m["name"]) < 28 else "…" + m["name"][-27:]
        g.nodes.append(Node(m["name"], short, "box", [[f"{m['lang']}, {m['loc']} LOC"]], stereotype="module",
                            fill="#438DD5" if m["name"] not in model["entry_points"] else "#1168BD"))
    for m in mods:
        for i in m["imports"]:
            if i in keep:
                g.edges.append(Edge(m["name"], i, "dependency"))
    g.legend = [("box", "module (file)"), ("dependency", "imports")]
    if len(model["modules"]) > max_nodes:
        g.notes.append(f"showing the {max_nodes} most connected of {len(model['modules'])} modules")
    return g


def class_graph(model: dict, max_nodes: int = 24) -> Graph:
    g = Graph(f"{model['name']} - classes", "UML 2.5 (ISO/IEC 19505) - Class diagram")
    allc = list(model["classes"])
    cnames = {c["name"] for c in allc}
    linked = {c["name"] for c in allc if any(b.split(".")[-1] in cnames for b in c["bases"]) or any(u in cnames and u != c["name"] for u in c["uses"])}
    linked |= {b.split(".")[-1] for c in allc for b in c["bases"] if b.split(".")[-1] in cnames}
    allc.sort(key=lambda c: (c["name"] not in linked, -(len(c["methods"]) + len(c["attrs"]))))   # connected classes first, then biggest
    classes = allc[:max_nodes]
    names = {c["name"] for c in classes}
    for c in classes:
        attrs = c["attrs"][:7] + ([f"… +{len(c['attrs']) - 7} more"] if len(c["attrs"]) > 7 else [])
        meths = c["methods"][:8] + ([f"… +{len(c['methods']) - 8} more"] if len(c["methods"]) > 8 else [])
        g.nodes.append(Node(c["name"], c["name"], "class", [attrs, meths]))
    seen = set()
    for c in classes:
        for b in c["bases"]:
            bn = b.split(".")[-1]
            if bn in names:
                g.edges.append(Edge(c["name"], bn, "inherit"))
        for u in c["uses"]:
            if u in names and u != c["name"] and (c["name"], u) not in seen:
                seen.add((c["name"], u))
                g.edges.append(Edge(c["name"], u, "assoc", "uses"))
    g.legend = [("inherit", "generalisation (is-a)"), ("assoc", "association (creates / holds)"), ("class", "class: name / attributes / operations")]
    if len(model["classes"]) > max_nodes:
        g.notes.append(f"{len(model['classes']) - max_nodes} classes omitted; showing the {max_nodes} largest")
    return g


def component_graph(model: dict, max_ext: int = 6) -> Graph:
    """C4 'container/component' level overview: internal packages, a database if found, main external libraries."""
    depth = _group_depth(model)
    groups: dict[str, list[str]] = defaultdict(list)
    for m in model["modules"]:
        groups[_top(m, depth)].append(m)
    g = Graph(f"{model['name']} - architecture", "C4 model - Container / component view", direction="TB")
    langs = ", ".join(sorted(model["languages"], key=model["languages"].get, reverse=True)[:2]) or "code"
    for k, mods in sorted(groups.items(), key=lambda kv: -len(kv[1]))[:14]:
        doc = next((model["modules"][m]["doc"] for m in mods if model["modules"][m]["doc"]), "")
        g.nodes.append(Node(k, k, "box", [[f"[{langs}]"], [_cut(doc, 42)] if doc else []], stereotype="component"))
    keep = {n.id for n in g.nodes}
    for m, d in model["modules"].items():
        a = _top(m, depth)
        for i in d["imports"]:
            b = _top(i, depth)
            if a != b and a in keep and b in keep and not any(e.src == a and e.dst == b for e in g.edges):
                g.edges.append(Edge(a, b, "flow"))
    ext_use: dict[str, Counter] = defaultdict(Counter)
    for m, d in model["modules"].items():
        for e in d["ext"]:
            ext_use[e][_top(m, depth)] += 1
    for lib, _ in sorted(model["external"].items(), key=lambda kv: -kv[1])[:max_ext]:
        nid = f"ext:{lib}"
        g.nodes.append(Node(nid, lib, "box", [["[external library]"]], stereotype="external", fill="#999999"))
        for pkg, _n in ext_use[lib].most_common(2):
            if pkg in keep:
                g.edges.append(Edge(pkg, nid, "flow"))
    if model["tables"]:
        g.nodes.append(Node("db", f"Database\n{len(model['tables'])} tables", "db"))
        for n in g.nodes[:3]:
            pass
    g.legend = [("box", "component / external system (grey)"), ("flow", "uses / calls"), ("db", "database")]
    return g


def erd_graph(model: dict) -> Graph:
    g = Graph(f"{model['name']} - data model", "ERD - crow's foot (information engineering) notation")
    names = {t["name"].lower(): t["name"] for t in model["tables"]}
    for t in model["tables"][:30]:
        fkcols = {c for c, _ in t["fks"]}
        rows = []
        for cn, ct, pk in t["cols"][:12]:
            tag = "PK " if pk else ("FK " if cn in fkcols else "   ")
            rows.append(f"{tag}{cn}  {ct}"[:34])
        if len(t["cols"]) > 12:
            rows.append(f"… +{len(t['cols']) - 12} more")
        g.nodes.append(Node(t["name"], t["name"], "entity", [rows]))
    for t in model["tables"][:30]:
        for col, ref in t["fks"]:
            tgt = names.get(ref.lower())
            if tgt and tgt != t["name"]:
                g.edges.append(Edge(tgt, t["name"], "fk", col, src_end="one", dst_end="zero_many"))
    g.legend = [("fk", "relationship: 1 (bar) to 0..many (crow's foot)"), ("class", "entity: name / attributes (PK, FK)")]
    return g


def _cut(t: str, n: int) -> str:
    if len(t) <= n:
        return t
    return t[:n].rsplit(" ", 1)[0].rstrip(" ,;:-(") + "\u2026"


def describe(model: dict) -> str:
    langs = ", ".join(f"{k} {v}" for k, v in sorted(model["languages"].items(), key=lambda kv: -kv[1])[:5])
    return (f"{model['name']}: {model['files']} files, {model['loc']} lines ({langs}); {len(model['modules'])} modules, "
            f"{len(model['classes'])} classes, {len(model['tables'])} tables, entry points: {', '.join(model['entry_points'][:4]) or 'none found'}")

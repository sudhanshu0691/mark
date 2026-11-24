"""viz: analyzer -> graphs -> ISO sheet exports, flowchart from code, sequence, DXF drawing."""
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import pytest  # noqa: E402

pytest.importorskip("PIL")
from actions.viz import analyzer, api, drawing, flow  # noqa: E402


def make_project(root: Path) -> Path:
    (root / "app").mkdir(parents=True)
    (root / "app" / "__init__.py").write_text("")
    (root / "app" / "models.py").write_text(textwrap.dedent('''
        """Data layer."""
        class Base:
            def save(self): ...
        class User(Base):
            def __init__(self, name):
                self.name = name
                self.profile = Profile()
        class Profile:
            bio = ""
    '''))
    (root / "app" / "service.py").write_text("from app.models import User\nimport requests\n\ndef run():\n    return User('a')\n")
    (root / "main.py").write_text("from app import service\nif __name__ == '__main__':\n    service.run()\n")
    (root / "db").mkdir()
    (root / "db" / "schema.sql").write_text("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT);\n"
                                           "CREATE TABLE posts (id INTEGER PRIMARY KEY, user_id INTEGER, FOREIGN KEY (user_id) REFERENCES users(id));")
    return root


def test_analyzer_finds_structure(tmp_path):
    m = analyzer.analyze(make_project(tmp_path / "proj"))
    assert {"main", "app.models", "app.service"} <= set(m["modules"])
    assert "app.models" in m["modules"]["app.service"]["imports"]
    assert "requests" in m["external"]
    assert [c["name"] for c in m["classes"]] == ["Base", "User", "Profile"]
    assert "main" in m["entry_points"]
    assert {t["name"] for t in m["tables"]} == {"users", "posts"}
    g = analyzer.class_graph(m)
    kinds = {(e.src, e.dst, e.kind) for e in g.edges}
    assert ("User", "Base", "inherit") in kinds and ("User", "Profile", "assoc") in kinds
    erd = analyzer.erd_graph(m)
    assert any(e.src == "users" and e.dst == "posts" and e.dst_end == "zero_many" for e in erd.edges)


def test_all_diagrams_export_valid_files(tmp_path):
    proj = make_project(tmp_path / "proj")
    r = api.diagrams_for_project(str(proj), ["auto"], tmp_path / "out")
    kinds = {k for k, _ in r["diagrams"]}
    assert {"component", "class", "erd"} <= kinds
    for k, res in r["diagrams"]:
        assert res["issues"] == [], (k, res["issues"])
        for ext in ("svg", "png", "pdf"):
            assert Path(res[ext]).stat().st_size > 1000
        assert res["sheet"].startswith("A")
        svg = Path(res["svg"]).read_text()
        assert "TITLE" in svg and "LEGEND" in svg            # ISO 7200 title block + legend present


def test_no_overlapping_boxes_on_bigger_graph():
    from actions.viz.layout import layout, lint
    from actions.viz.model import Edge, Graph, Node
    g = Graph("t", "n")
    for i in range(30):
        g.nodes.append(Node(f"n{i}", f"node {i}", "class", [["a: int", "b: str"], ["+ run()"]]))
    for i in range(1, 30):
        g.edges.append(Edge(f"n{i}", f"n{i // 2}", "inherit"))
    layout(g)
    assert lint(g) == []


def test_flowchart_from_function(tmp_path):
    f = tmp_path / "m.py"
    f.write_text("def f(x):\n    if x > 1:\n        return 1\n    for i in range(3):\n        print(i)\n    return 0\n")
    g = flow.flowchart_from_function(f, "f")
    kinds = [n.kind for n in g.nodes]
    assert kinds.count("decision") == 2 and "terminator" in kinds
    res = api.render_graph(g, tmp_path / "o", "fc")
    assert res["issues"] == [] and Path(res["png"]).exists()


def test_sequence_and_drawing(tmp_path):
    r = api.render_sequence({"participants": ["A", "B"], "messages": [{"from": "A", "to": "B", "text": "hi"}]}, tmp_path, "seq")
    assert Path(r["png"]).exists()
    ezdxf = pytest.importorskip("ezdxf")
    spec = {"title": "Plate", "entities": [{"type": "rect", "origin": [0, 0], "w": 100, "h": 50},
                                            {"type": "circle", "center": [20, 25], "r": 5},
                                            {"type": "dim", "from": [0, 0], "to": [100, 0], "offset": -10}]}
    res = drawing.create(spec, tmp_path, "plate")
    doc = ezdxf.readfile(res["dxf"])
    types = {e.dxftype() for e in doc.modelspace()}
    assert {"LINE", "CIRCLE", "TEXT"} <= types
    dim_text = [e.dxf.text for e in doc.modelspace() if e.dxftype() == "TEXT" and e.dxf.layer == "DIMENSION"]
    assert dim_text == ["100"]                                 # value measured from geometry, not typed by the model


def test_tool_reports_failure_honestly(tmp_path):
    from actions.visualize import visualize
    r = visualize({"action": "diagram", "path": str(tmp_path / "does_not_exist")})
    assert r.status == "FAILED"
    r = visualize({"action": "drawing", "spec": "{not json"})
    assert r.status == "FAILED"

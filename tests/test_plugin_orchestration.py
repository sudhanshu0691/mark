from core.plugin_router import plan
from core.plugin_safety import check_call
from core import brain


def test_router_uses_registered_names_and_reports_missing():
    declarations = [{"name": name} for name in ("excel", "create_visualization", "ppt_studio")]
    result = plan("Analyze an Excel workbook and make a PowerPoint presentation", declarations)
    assert result.index("excel") < result.index("create_visualization") < result.index("ppt_studio")
    assert "workspace" in plan("project planning", declarations)
    assert "Unavailable registered tools" in plan("project planning", declarations)


def test_plugin_delete_and_move_blocked():
    for name, action in [("notes", "delete"), ("workspace", "delete"), ("ppt_studio", "delete_slide"), ("ppt_studio", "rename")]:
        assert check_call(name, {"action": action})
    assert check_call("create_visualization", {"output_name": "../escape"})


def test_workflow_passes_results_through_registered_plugins(tmp_path, monkeypatch):
    from core.plugins import PluginContext, PluginManager
    from core import plugin_safety
    from core.paths import BASE_DIR
    # Real plugins and real files, with only the output directory redirected.
    import plugins.visualization as visualization
    out_dir = tmp_path / "charts"
    monkeypatch.setattr(visualization, "VIZ_DIR", out_dir)
    monkeypatch.setattr(plugin_safety, "VIZ_DIR", out_dir)
    manager = PluginManager(PluginContext())
    manager._load_file(BASE_DIR / "plugins" / "excel" / "plugin.py")
    manager._load_file(BASE_DIR / "plugins" / "visualization.py")
    manager._load_file(BASE_DIR / "plugins" / "ppt_studio.py")
    # The dynamic loader has its own module instance.
    manager.loaded["visualization_tools"].module.VIZ_DIR = out_dir
    xlsx = tmp_path / "sales.xlsx"
    pptx = tmp_path / "report.pptx"
    assert manager.run("excel", {"action": "create", "path": str(xlsx), "data": [["Item", "Sales"], ["A", 5], ["B", 9]]}).startswith("[DONE]")
    profile = manager.run("excel", {"action": "profile", "path": str(xlsx)})
    assert profile.startswith("[DONE]") and "Sales" in profile
    chart = manager.run("create_visualization", {"type": "bar", "labels": ["A", "B"], "values": [5, 9], "title": "Sales"})
    assert chart.startswith("[DONE]") and "VERIFIED OUTPUT" in chart
    image = next(out_dir.glob("*.png"))
    assert manager.run("ppt_studio", {"action": "create", "path": str(pptx), "slides": [{"title": "Sales", "bullets": ["A: 5", "B: 9"]}]}).startswith("[DONE]")
    result = manager.run("ppt_studio", {"action": "add_image", "path": str(pptx), "image_path": str(image), "index": 1})
    assert result.startswith("[DONE]") and "VERIFIED OUTPUT" in result

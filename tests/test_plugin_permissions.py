from core.plugins import PluginContext, PluginManager


def test_empty_permissions_deny_tool_calls():
    calls = []
    ctx = PluginContext(call_tool=lambda name, args: calls.append(name) or "ok")
    assert "permission denied" in ctx.call_tool("open_app", {})
    assert calls == []


def test_plugin_hooks_are_scoped_and_failed_load_is_not_registered(tmp_path):
    calls = []
    manager = PluginManager(PluginContext(call_tool=lambda name, args: calls.append(name) or "ok"))
    plugin = tmp_path / "sample.py"
    plugin.write_text('''NAME = "sample"
METADATA = {"permissions": []}
DECLARATION = {"name": "sample_tool", "parameters": {"type": "OBJECT"}}
def run(args, ctx): return ctx.call_tool("open_app")
def on_load(ctx): ctx.call_tool("open_app")
def on_notification(note, ctx): ctx.call_tool("open_app")
def notification_policy(note, ctx):
    ctx.call_tool("open_app")
    return "normal"
''', encoding="utf-8")
    manager._load_file(plugin)
    manager.dispatch_notification({})
    manager.notification_policy({})
    assert "permission denied" in manager.run("sample_tool", {})
    assert calls == []
    broken = tmp_path / "broken.py"
    broken.write_text('''NAME = "broken"
DECLARATION = {"name": "broken_tool", "parameters": {"type": "OBJECT"}}
def run(args, ctx): return "ok"
def on_load(ctx): raise RuntimeError("broken")
''', encoding="utf-8")
    manager._load_file(broken)
    assert "broken" not in manager.loaded
    assert str(broken) in manager.errors

import threading
from core.plugins import PluginContext, PluginManager

def test_timed_out_plugin_cannot_repeat_or_call_bridge(tmp_path):
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    calls = []
    manager = PluginManager(PluginContext(call_tool=lambda *args: calls.append(args) or "ok"))
    plugin = tmp_path / "slow.py"
    plugin.write_text('NAME="slow"\nMETADATA={"timeout":0.1,"permissions":["open_app"]}\nDECLARATION={"name":"slow_tool","parameters":{"type":"OBJECT"}}\ndef run(args,ctx): return "unused"\n', encoding="utf-8")
    manager._load_file(plugin)
    def run(args, ctx):
        entered.set()
        release.wait(5)
        ctx.call_tool("open_app")
        finished.set()
        return "done"
    manager.loaded["slow"].tools["slow_tool"]["run"] = run
    try:
        assert manager.run("slow_tool", {}).startswith("[UNVERIFIED]")
        assert entered.is_set()
        assert "still running" in manager.run("slow_tool", {})
        release.set()
        assert finished.wait(2)
        assert calls == []
    finally:
        release.set()
        manager.stop()

import importlib.util
from pathlib import Path

from core.agents import AGENTS, for_tool


def test_vision_tools_register_with_mark_root_agent():
    assert for_tool("vision_start") is AGENTS["vision"]
    assert for_tool("vision_history") is AGENTS["vision"]
    path = Path(__file__).resolve().parents[1] / "plugins" / "vision" / "plugin.py"
    spec = importlib.util.spec_from_file_location("mark_vision_plugin_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert {tool["declaration"]["name"] for tool in module.TOOLS} == {
        "vision_start", "vision_stop", "vision_status", "vision_history"
    }

import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from core import brain, intent, llm, risk
from memory import db
from memory.conversation import recent_messages
from main import MarkLive


@pytest.fixture
def history(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "memory.db")
    monkeypatch.setattr(db, "LEGACY_LONG_TERM", tmp_path / "unused.json")
    monkeypatch.setattr(db, "LEGACY_CONVERSATION", tmp_path / "unused2.json")
    return db


@pytest.mark.parametrize("utterance,name", [("Notepad kholo", "open_app"), ("volume 0 karo", "computer_settings"),
                                           ("show downloads files", "file_controller"), ("mark status", "runtime_status")])
def test_local_routes(utterance, name):
    assert intent.match_local_command(utterance)[0] == name
    assert intent.match_local_command(utterance + " and delete a file") is None


def test_offline_adapter_rejects_cloud_inference(monkeypatch):
    monkeypatch.setattr(llm, "load_config", lambda: {"backend": "multi_provider", "profile": "offline"})
    with pytest.raises(llm.LLMUnavailable):
        llm.Client().models.generate_content(contents="hi")


def test_history_survives_new_instance_and_is_bounded(history):
    history.append_conversation_turn("private typed question", "local answer", channel="typed")
    history.append_conversation_turn("remember blue", "yes", channel="voice")
    assert recent_messages("voice") == [{"role":"user","content":"remember blue"},{"role":"assistant","content":"yes"}]
    assert len(recent_messages()) == 4
    assert len(recent_messages(budget=5)) == 0
    history.clear_conversation_turns()
    assert recent_messages() == []


def test_live_reconnect_reuses_handle_then_restores_history(history, monkeypatch):
    monkeypatch.setattr(llm, "load_config", lambda: {**llm.DEFAULTS, "share_history_with_cloud": True})
    history.append_conversation_turn("previous voice topic", "blue")
    live = MarkLive(Mock())
    live._all_declarations = lambda: []
    class FakeConnection:
        async def receive(self):
            yield SimpleNamespace(session_resumption_update=SimpleNamespace(new_handle="token", resumable=True),
                                  data=None, server_content=None, tool_call=None)
            raise ConnectionError("closed")
    live.session = FakeConnection()
    with pytest.raises(ConnectionError):
        asyncio.run(live._receive_audio())
    config = live._build_config()
    assert config.session_resumption.handle == "token"
    assert "previous voice topic" not in config.system_instruction
    live._resume_handle = None
    assert "previous voice topic" in live._build_config().system_instruction


def test_typed_commands_save_and_restore(history, monkeypatch):
    monkeypatch.setattr(llm, "load_config", lambda: {**llm.DEFAULTS, "share_history_with_cloud": True})
    live = MarkLive(Mock())
    live._all_declarations = lambda: []
    live._dispatch_sync = Mock(return_value="[DONE] system status")
    monkeypatch.setattr("main.TASKS", Mock())
    live._run_local_command("system info")
    planner = Mock(return_value="That was system status.")
    monkeypatch.setattr(brain, "run_goal", planner)
    MarkLive(live.ui)._run_local_command("plan a related task")
    assert any(msg["content"] == "system info" for msg in planner.call_args.kwargs["history"])
    monkeypatch.setattr(llm, "load_config", lambda: {**llm.DEFAULTS, "share_history_with_cloud": False})
    MarkLive(live.ui)._run_local_command("plan another related task")
    assert planner.call_args.kwargs["history"] == []
    assert any(turn["user"] == "system info" for turn in history.load_conversation_turns())


@pytest.mark.parametrize("args", [
    {"action": "delete", "path": "sample", "confirmed": True},
    {"action": "delete", "path": "sample", "permanent": True, "confirmed": True},
    {"action": "move", "path": "sample", "destination": "elsewhere"},
])
def test_dispatch_requires_real_approval_even_if_args_confirmed(monkeypatch, history, args):
    live = MarkLive(Mock())
    live.ui.confirm_action = AsyncMock(return_value=False)
    operation = Mock(side_effect=AssertionError("denied action executed"))
    monkeypatch.setitem(__import__("main").NATIVE, "calls", operation)
    monkeypatch.setattr("main.TASKS", Mock())
    monkeypatch.setitem(__import__("main").NATIVE, "file_controller", operation)
    call = SimpleNamespace(name="file_controller", args=args, id="t")
    result = asyncio.run(live._execute_tool(call))
    assert result.response["result"].startswith("[CANCELLED]")
    operation.assert_not_called()


def test_intent_record_and_schema():
    data = intent.decision_record(intent.Intent("test", 1, risk.READ_ONLY, False), "ok", True)
    assert data["facts"] == []
    schema = {"parameters":{"required":["n"],"properties":{"n":{"type":"INTEGER"}}}}
    assert risk.validate_arguments(schema, {"n": True})
    assert risk.validate_arguments(schema, {"n": 2}) == ""


def test_cloud_setup_initializes_without_inference(monkeypatch):
    from core import providers
    monkeypatch.setattr("core.secrets.get_secret", lambda _: "fake-key")
    google = Mock()
    monkeypatch.setattr("google.genai.Client", lambda **kwargs: google)
    assert providers.initialize("gemini", llm.DEFAULTS) == "CONFIGURED"
    google.models.generate_content.assert_not_called()
    providers.close()


def test_offline_startup_and_local_dispatch(history, monkeypatch):
    import main
    from core.plugins import PluginManager

    ui = Mock()
    ui.task_start.return_value = 1
    ui.name = "MARK"
    live = MarkLive(ui)
    monkeypatch.setattr(live, "_start_default_camera_features", lambda: None)
    monkeypatch.setattr(main, "_get_api_key", lambda: pytest.fail("offline mode looked up voice key"))
    monkeypatch.setattr(main, "NOTIF_WATCHER", SimpleNamespace(listeners=[], start=lambda: SimpleNamespace(ok=True), stop=lambda: None))
    monkeypatch.setattr(main, "TASKS", Mock())
    monkeypatch.setattr(llm, "load_config", lambda: {**llm.DEFAULTS, "profile":"offline", "backend":"multi_provider"})
    monkeypatch.setattr(llm, "health", lambda: {"message":"Cloud disabled"})
    monkeypatch.setattr(main, "get_system_status", lambda: "CPU test")
    monkeypatch.setattr(live, "_report_verification", AsyncMock(return_value="UNVERIFIED"))
    monkeypatch.setattr(live, "_notification_pump", AsyncMock())
    async def scenario():
        task = asyncio.create_task(live.run())
        await asyncio.sleep(0.1)
        assert len(live.plugins.loaded) >= 10
        assert not live.plugins.errors
        declarations = [d["name"] for d in live._all_declarations()]
        assert len(set(declarations)) == len(declarations)
        await asyncio.to_thread(live._run_local_command, "system info")
        assert "CPU test" in recent_messages()[-1]["content"]
        live.stop()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(scenario())


def test_persistent_and_per_task_budgets(tmp_path, monkeypatch):
    from core import providers
    monkeypatch.setattr(providers, "USAGE_PATH", tmp_path / "usage.db")
    cfg = {"cloud_limits":{"requests_per_day":2,"requests_per_task":1}}
    with providers.task_budget():
        providers.reserve("gemini", cfg)
        with pytest.raises(providers.BudgetExceeded, match="per-task"):
            providers.reserve("gemini", cfg)
    providers.reserve("gemini", cfg)
    with pytest.raises(providers.BudgetExceeded, match="daily"):
        providers.reserve("gemini", cfg)


def test_transcript_fragment_is_held_across_socket_drop(history):
    live = MarkLive(Mock())
    response = SimpleNamespace(session_resumption_update=None, data=None, tool_call=None,
           server_content=SimpleNamespace(output_transcription=None,input_transcription=SimpleNamespace(text="blue"),
                                          turn_complete=False))
    class Socket:
        async def receive(self):
            yield response
            raise ConnectionError("test disconnect")
    live.session = Socket()
    with pytest.raises(ConnectionError):
        asyncio.run(live._receive_audio())
    assert live._live_in == ["blue"]


def test_replayed_live_tool_call_runs_once(history):
    from types import SimpleNamespace as NS
    live = MarkLive(Mock())
    live._execute_tool = AsyncMock(return_value=NS(response={"result":"[DONE]"}))
    call = NS(name="example", id="stable-id", args={})
    event = NS(data=None,server_content=None,tool_call=NS(function_calls=[call]))
    class Socket:
        send_tool_response = AsyncMock()
        async def receive(self):
            yield event
            yield event
            raise ConnectionError("done")
    live.session = Socket()
    with pytest.raises(ConnectionError):
        asyncio.run(live._receive_audio())
    assert live._execute_tool.await_count == 1
    assert live.session.send_tool_response.await_count == 2


def test_local_catalog_is_bounded_and_discoverable():
    from main import TOOL_DECLARATIONS, NEW_DECLARATIONS
    import json
    all_tools = llm.to_provider_tools(TOOL_DECLARATIONS + NEW_DECLARATIONS)
    selected = llm.local_tool_selection([{"role":"user","content":"Use file_controller to list files"}], all_tools, 5200)
    assert {"file_controller","plugins"} <= {t["function"]["name"] for t in selected}
    assert sum(len(json.dumps(t, ensure_ascii=False).encode()) for t in selected) <= 5200


def test_recall_uses_previous_turn_without_planner(history, monkeypatch):
    history.append_conversation_turn("my test colour is blue", "ok", channel="typed")
    live = MarkLive(Mock())
    monkeypatch.setattr("main.TASKS", Mock())
    monkeypatch.setattr(llm, "load_config", lambda: {**llm.DEFAULTS,"backend":"multi_provider", "share_history_with_cloud": True})
    inference = Mock(return_value={"content":"blue"})
    monkeypatch.setattr(llm, "chat_raw", inference)
    monkeypatch.setattr(brain, "run_goal", Mock(side_effect=AssertionError("unneeded planner")))
    live._run_local_command("What colour did I tell you earlier?")
    assert any("my test colour" in m["content"] for m in inference.call_args.args[0])
    assert recent_messages()[-1]["content"] == "blue"


def test_model_requested_memory_requires_local_approval(history):
    live = MarkLive(Mock(muted=True))
    live.ui.task_start.return_value = None
    live.ui.confirm_action = AsyncMock(side_effect=[False, True])
    request = SimpleNamespace(name="save_memory", id="memory",
                              args={"category": "notes", "key": "color", "value": "blue"})

    async def run():
        refused = await live._execute_tool(request)
        assert refused.response["result"].startswith("[CANCELLED]")
        assert "color" not in history.load_memory_dict()["notes"]

        approved = await live._execute_tool(request)
        assert approved.response["result"].startswith("[DONE]")
        assert history.load_memory_dict()["notes"]["color"]["source"] == "user_confirmed"

    asyncio.run(run())


def test_archives_reject_aliased_or_escaping_names_before_writing(monkeypatch, tmp_path):
    import zipfile

    from actions import fs_full

    monkeypatch.setattr(fs_full, "_guard", lambda *args: None)
    archive = tmp_path / "example.zip"
    destination = tmp_path / "output"
    with zipfile.ZipFile(archive, "w") as zip_file:
        zip_file.writestr("safe.txt", "safe")
        zip_file.writestr("nested/../../output-sibling/escape.txt", "outside")
    assert fs_full._unzip(archive, destination).to_llm().startswith("[FAILED]")
    assert not destination.exists()
    with zipfile.ZipFile(archive, "w") as zip_file:
        zip_file.writestr("nested/safe.txt", "safe")
    assert fs_full._unzip(archive, destination).to_llm().startswith("[DONE]")
    assert (destination / "nested" / "safe.txt").read_text() == "safe"

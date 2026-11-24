import asyncio
import json
import threading
from hashlib import sha256
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core import llm, plugins, providers
from core.agents.specialist import SpecialistExecutor
from core.agents.workflow import Workflow
from core.cloud_privacy import CloudPolicyError, approved_capture, require_vision
from core.task_manager import TaskManager
from memory import db


def test_discovery_never_executes_untrusted_or_modified_source(monkeypatch, tmp_path):
    config = tmp_path / "config"
    config.mkdir()
    directory = tmp_path / "plugins"
    monkeypatch.setattr(plugins, "PLUGIN_DIR", directory)
    monkeypatch.setattr(plugins, "_TRUST_DIR", config)
    manager = plugins.PluginManager(plugins.PluginContext())
    marker = tmp_path / "executed"
    payload = f"""from pathlib import Path
Path({str(marker)!r}).touch()
DECLARATION = {{"name": "fixture", "parameters": {{"type": "OBJECT"}}}}
def run(args): return "[DONE]"
""".encode()
    path = directory / "fixture.py"
    path.write_bytes(payload)
    try:
        manager.load_all()
        assert not marker.exists()
        assert "OS sandbox execution is unavailable" in manager.describe()
        trust = config / "trusted_plugins.json"
        trust.write_text(
            json.dumps({path.name: sha256(payload).hexdigest()}), encoding="utf-8"
        )
        assert manager.poll()
        assert marker.exists()
        assert manager.has_tool("fixture")
        path.write_bytes(payload + b"\n# edited")
        marker.unlink()
        assert manager.run("fixture", {}).startswith("[FAILED]")
        assert manager.poll()
        assert not marker.exists()
    finally:
        manager.stop()


def test_private_capture_approval_cannot_override_offline_or_escape_scope():
    cfg = {"allow_cloud_vision": True}
    with pytest.raises(CloudPolicyError, match="approval"):
        require_vision(cfg)
    with approved_capture():
        require_vision(cfg)
        with pytest.raises(CloudPolicyError, match="disabled"):
            require_vision({**cfg, "profile": "offline"})
    with pytest.raises(CloudPolicyError, match="approval"):
        require_vision(cfg)


def test_fallback_receives_minimized_payload(monkeypatch):
    seen = []

    def groq(messages, *args):
        seen.append(messages)
        raise llm.LLMUnavailable("Unavailable")

    def gemini(messages, *args):
        seen.append(messages)
        return {"content": "OK"}

    monkeypatch.setattr(llm, "load_config", lambda: {"free_tier_only": False})
    monkeypatch.setattr(llm, "_groq_chat", groq)
    monkeypatch.setattr(llm, "_gemini_chat", gemini)
    assert (
        llm.chat_raw(
            [
                {"role": "user", "content": "read the document"},
                {
                    "role": "tool",
                    "tool_name": "file_controller",
                    "content": "[DONE] private document bytes",
                },
            ]
        )["content"]
        == "OK"
    )
    assert len(seen) == 2
    assert all(
        "private document" not in str(request) and "[DONE]" in str(request)
        for request in seen
    )


def test_provider_cools_down_and_recovers(monkeypatch):
    monkeypatch.setattr(providers, "_circuits", {})
    monkeypatch.setattr(providers, "_stats", {})
    now = [100.0]
    monkeypatch.setattr(providers, "monotonic", lambda: now[0])
    providers.record("groq", "model", 99, status=429, retry_after=20)
    with pytest.raises(providers.BudgetExceeded, match="cooling down"):
        with providers.cloud_request("groq", {"provider_circuit_breaker": True}):
            pytest.fail("cooling provider must not submit another request")
    now[0] = 121
    with providers.cloud_request("groq", {"provider_circuit_breaker": True}):
        providers.record("groq", "model", 121, tokens=4)
    assert providers.snapshot()["groq"]["state"] == "READY"


def test_workflow_parallelizes_analysis_and_orders_actions(monkeypatch):
    barrier = threading.Barrier(2, timeout=3)
    calls = []

    def analyse(*args, **kwargs):
        barrier.wait()
        return {"content": "analysis"}

    monkeypatch.setattr(llm, "chat_raw", analyse)
    specialist = SpecialistExecutor(
        [{"name": "calculate"}], lambda name, args: calls.append(name) or "[DONE]"
    )
    workflow = Workflow(specialist, None, lambda _: None)
    result = workflow.execute(
        {
            "tasks": [
                {
                    "id": "read",
                    "agent": "reasoning",
                    "instruction": "Analyse constraints.",
                },
                {"id": "plan", "agent": "planner", "instruction": "Plan the steps."},
                {
                    "id": "calc",
                    "agent": "productivity",
                    "tool": "calculate",
                    "depends_on": ["read", "plan"],
                },
            ]
        }
    )
    assert result.startswith("[DONE]")
    assert calls == ["calculate"]
    assert workflow.execute({"tasks": []}).startswith("[FAILED]")


def test_workflow_rejects_cycle_before_action():
    specialist = SpecialistExecutor(
        [{"name": "calculate"}], lambda *_: pytest.fail("invalid graph ran")
    )
    result = Workflow(specialist, None, lambda _: None).execute(
        {
            "tasks": [
                {
                    "id": "calc",
                    "agent": "productivity",
                    "tool": "calculate",
                    "depends_on": ["calc"],
                },
            ]
        }
    )
    assert result.startswith("[FAILED]")


def test_task_restart_preserves_only_safe_checkpoint_metadata(monkeypatch, tmp_path):
    audit = tmp_path / "audit.jsonl"
    monkeypatch.setattr("core.task_manager._EVENT_LOG", audit)
    database = tmp_path / "tasks.db"
    first = TaskManager(database=database)
    first.start("task", "send_email", {"body": "private email"})
    first.checkpoint("task", "private document label")
    resumed = TaskManager(database=database).recent()[0]
    assert resumed["status"] == "UNVERIFIED"
    assert resumed["recovered"] is True
    assert resumed["checkpoints"][0]["status"] == "DONE"
    assert b"private" not in database.read_bytes()
    assert "private" not in audit.read_text()


def test_conversation_checkpoint_is_idempotent_and_does_not_cross_conversations(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "memory.db")
    monkeypatch.setattr(db, "LEGACY_LONG_TERM", tmp_path / "legacy.json")
    monkeypatch.setattr(db, "LEGACY_CONVERSATION", tmp_path / "old-conversation.json")
    cid = db.get_active_conversation()["id"]
    request = {"channel": "voice", "conversation_id": cid}
    assert db.checkpoint_conversation_turn("turn", "hello", **request)
    assert db.load_conversation_turns(conversation_id=cid)[0]["assistant"].startswith(
        "[Interrupted"
    )
    assert db.load_conversation_turns(conversation_id=cid, include_pending=False) == []
    assert (
        db.search_conversation_turns(
            "hello", conversation_id=cid, include_pending=False
        )
        == []
    )
    other = db.start_conversation()
    assert not db.checkpoint_conversation_turn(
        "turn", "different", channel="voice", conversation_id=other
    )
    assert db.checkpoint_conversation_turn(
        "turn", "hello", "hi", complete=True, **request
    )
    assert not db.checkpoint_conversation_turn("turn", "again", **request)
    assert [
        row["assistant"] for row in db.load_conversation_turns(conversation_id=cid)
    ] == ["hi"]
    assert db.load_conversation_turns(conversation_id=other) == []


def test_video_requires_local_approval_and_discards_stale_consent():
    from core._livekit_worker import LiveKitAgentWorker

    async def run():
        worker = LiveKitAgentWorker()
        worker.desktop_video_allowed = True
        worker.allowed_identity = "owner"
        worker._room = SimpleNamespace(remote_participants={"owner": object()})
        worker._is_running = True
        worker.markdown_adapter = SimpleNamespace(
            ui=SimpleNamespace(confirm_action=AsyncMock(return_value=False))
        )
        assert (await worker.start_desktop_video(720, 1)).startswith("[CANCELLED]")
        worker.markdown_adapter.ui.confirm_action.assert_awaited_once()

        async def stale(*args):
            await worker.stop_desktop_sharing()
            return True

        worker.markdown_adapter.ui.confirm_action = stale
        assert (await worker.start_desktop_video(1080, 2)).startswith("[CANCELLED]")
        assert worker._desktop_video is None

    asyncio.run(run())


def test_video_publishes_screen_track_and_disposes_source(monkeypatch):
    import sys

    from core.streaming.desktop import DesktopVideo

    async def run():
        frames = []
        indicators = []
        capture_started = asyncio.Event()

        class Source:
            def __init__(self, width, height, *, is_screencast):
                assert (width, height, is_screencast) == (1280, 720, True)
                self.aclose = AsyncMock()
                sources.append(self)

            def capture_frame(self, frame):
                frames.append(frame)
                capture_started.set()

        sources = []
        rtc = SimpleNamespace(
            VideoSource=Source,
            VideoFrame=lambda *args: args,
            VideoBufferType=SimpleNamespace(RGB24=1),
            LocalVideoTrack=SimpleNamespace(
                create_video_track=lambda name, source: name
            ),
            TrackPublishOptions=SimpleNamespace,
            TrackSource=SimpleNamespace(SOURCE_SCREENSHARE=3),
            VideoEncoding=SimpleNamespace,
        )
        monkeypatch.setitem(sys.modules, "livekit", SimpleNamespace(rtc=rtc))
        participant = SimpleNamespace(
            publish_track=AsyncMock(return_value=SimpleNamespace(sid="sharetrack")),
            unpublish_track=AsyncMock(),
        )
        room = SimpleNamespace(
            local_participant=participant, remote_participants={"owner": object()}
        )
        video = DesktopVideo(room, "owner", 1, 720, indicators.append)
        monkeypatch.setattr(video, "_capture_frame", lambda: bytes(1280 * 720 * 3))
        assert await video.start()
        await asyncio.wait_for(capture_started.wait(), timeout=1)
        await video.stop()
        assert frames[0][:3] == (1280, 720, 1)
        assert indicators == [True, False]
        name, options = participant.publish_track.await_args.args
        assert (name, options.source, options.video_encoding.max_framerate) == (
            "mark-desktop",
            3,
            30,
        )
        participant.unpublish_track.assert_awaited_once_with("sharetrack")
        sources[0].aclose.assert_awaited_once()

    asyncio.run(run())


def test_gemini_stream_closes_when_cancelled(monkeypatch):
    from google import genai

    stop = threading.Event()
    closed = threading.Event()

    def events(**kwargs):
        try:
            yield SimpleNamespace(text="first", usage_metadata=None)
            yield SimpleNamespace(text="second", usage_metadata=None)
        finally:
            closed.set()

    monkeypatch.setattr("core.secrets.get_secret", lambda provider: "test-key")
    monkeypatch.setattr(
        genai,
        "Client",
        lambda **kwargs: SimpleNamespace(
            models=SimpleNamespace(generate_content_stream=events)
        ),
    )
    monkeypatch.setattr(
        llm,
        "load_config",
        lambda: {
            "backend": "gemini",
            "free_tier_only": False,
            "gemini": {"text_model": "fake"},
        },
    )
    seen = []

    def delta(value):
        seen.append(value)
        stop.set()

    with pytest.raises(llm.LLMUnavailable, match="cancelled"):
        llm.chat_raw(
            [{"role": "user", "content": "hello"}],
            on_delta=delta,
            cancelled=stop.is_set,
        )
    assert seen == ["first"]
    assert closed.is_set()


def test_structured_memory_keeps_provenance_and_supports_correction(
    monkeypatch, tmp_path
):
    from memory.learning import handle_memory_command

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "memory.db")
    monkeypatch.setattr(db, "LEGACY_LONG_TERM", tmp_path / "legacy.json")
    monkeypatch.setattr(db, "LEGACY_CONVERSATION", tmp_path / "old-conversation.json")
    assert handle_memory_command("yaad rakho editor=Vim").startswith("[DONE]")
    assert handle_memory_command("remember editor=Neovim").startswith("[DONE]")
    note = db.load_memory_dict()["notes"]["editor"]
    assert (note["value"], note["source"], note["confidence"]) == (
        "Neovim",
        "user_explicit",
        1,
    )
    assert handle_memory_command("remember api key=private").startswith("[FAILED]")
    assert handle_memory_command("bhool jao editor").startswith("[DONE]")
    assert "editor" not in db.load_memory_dict()["notes"]


def test_repeated_specialist_write_is_not_dispatched_twice():
    actions = []
    specialist = SpecialistExecutor(
        [{"name": "send_email"}],
        lambda name, args: actions.append(name) or "[DONE]",
    )
    assert specialist.execute("send_email", {}) == "[DONE]"
    assert specialist.execute("send_email", {}).startswith("[UNVERIFIED]")
    assert actions == ["send_email"]


def test_workflow_binds_analysis_only_to_literal_presentation_content(monkeypatch):
    from core.agents.specialist import SpecialistExecutor
    from core.agents.workflow import Workflow
    from plugins.ppt_studio import DECLARATION

    monkeypatch.setattr(llm, "chat_raw", lambda *a, **k: {"content": "Drafted notes"})
    calls = []
    specialist = SpecialistExecutor(
        [DECLARATION],
        lambda name, args: calls.append((name, args)) or "[DONE]",
    )
    draft = {"id": "draft", "agent": "reasoning", "instruction": "Write notes"}
    step = {
        "id": "save",
        "agent": "presentations",
        "tool": "ppt_studio",
        "depends_on": ["draft"],
        "arguments": '{"action":"notes","path":"report.pptx","text":{"$result":"draft"}}',
    }
    result = Workflow(specialist, None, lambda _: None).execute(
        {"tasks": [draft, step]}
    )
    assert result.startswith("[DONE]"), result
    assert calls[0][1]["text"] == "Drafted notes"
    step["arguments"] = '{"action":"notes","path":{"$result":"draft"}}'
    result = Workflow(specialist, None, lambda _: None).execute(
        {"tasks": [draft, step]}
    )
    assert result.startswith("[FAILED]")
    assert len(calls) == 1


def test_recovery_claim_replays_only_known_reads(tmp_path, monkeypatch):
    from core.task_manager import TaskManager

    monkeypatch.setattr("core.task_manager._EVENT_LOG", tmp_path / "audit.jsonl")
    database = tmp_path / "tasks.db"
    original = TaskManager(database=database)
    original.start(1, "system_status")
    original.start(2, "send_email", {"body": "private"})
    original.start(3, "runtime_status", {"unexpected": "private"})
    resumed = TaskManager(database=database)
    assert resumed.claim_recovered_read(2) is None
    assert resumed.claim_recovered_read(3) is None
    assert resumed.claim_recovered_read(1) == "system_status"
    assert resumed.claim_recovered_read(1) is None
    assert TaskManager(database=database).claim_recovered_read(1) is None


def test_recovered_workflow_keeps_completed_actions_and_blocks_uncertain_writes(
    tmp_path, monkeypatch
):
    from core.agents.recovery import WorkflowJournal
    from core.agents.specialist import SpecialistExecutor
    from core.agents.workflow import Outcome, Workflow
    from plugins.ppt_studio import DECLARATION

    monkeypatch.setattr(
        llm, "chat_raw", lambda *args, **kwargs: {"content": "Independent analysis"}
    )
    graph = {
        "tasks": [
            {
                "id": "done",
                "agent": "presentations",
                "tool": "ppt_studio",
                "arguments": '{"action":"notes","path":"private.pptx","text":"first"}',
            },
            {
                "id": "interrupted",
                "agent": "presentations",
                "tool": "ppt_studio",
                "arguments": '{"action":"delete_slide","path":"private.pptx","index":1}',
            },
            {
                "id": "dependent",
                "agent": "reasoning",
                "depends_on": ["interrupted"],
                "instruction": "Report",
            },
            {"id": "independent", "agent": "reasoning", "instruction": "Check status"},
        ]
    }
    path = tmp_path / "workflow.db"
    first = WorkflowJournal(path)
    identifier = first.create(graph)
    assert b"private.pptx" not in b"".join(
        file.read_bytes() for file in tmp_path.glob("workflow.db*")
    )
    first.write(
        running="interrupted",
        outcomes={"done": Outcome("DONE", "Verified")},
        status="paused",
    )
    second = WorkflowJournal(path)
    assert identifier in second.recoverable()
    calls = []
    specialist = SpecialistExecutor([DECLARATION], lambda *args: calls.append(args))
    result = Workflow(specialist, None, lambda _: None, second).execute(
        {}, resume=identifier
    )
    assert "Prior side effects are uncertain" in result
    assert "Independent analysis" in result
    assert "A dependency did not complete" in result
    assert calls == []
    assert identifier not in WorkflowJournal(path).recoverable()


def test_workflow_lease_recovers_after_worker_process_exits(tmp_path):
    import subprocess
    import sys

    from core.agents.recovery import WorkflowJournal

    path = tmp_path / "workflow.db"
    child = """
from core.agents.recovery import WorkflowJournal
import os
import sys

journal = WorkflowJournal(sys.argv[1])
identifier = journal.create({'tasks': []})
journal.write(running='last_action')
print(identifier, flush=True)
os._exit(17)
"""
    result = subprocess.run(
        [sys.executable, "-c", child, str(path)],
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    assert result.returncode == 17, result.stderr
    identifier = result.stdout.strip()
    recovery = WorkflowJournal(path)
    assert recovery.recoverable() == [identifier]
    assert recovery.claim(identifier)["running"] == "last_action"
    assert recovery.recoverable() == []
    with pytest.raises(ValueError, match="already running"):
        WorkflowJournal(path).claim(identifier)

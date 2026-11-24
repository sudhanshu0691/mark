from core.agents import orchestrator as o

def test_direct_does_not_load_cloud(monkeypatch):
    monkeypatch.setattr(o.llm, "load_config", lambda: (_ for _ in ()).throw(AssertionError("cloud config")))
    calls = []
    result = o.MasterOrchestrator().execute("Notepad kholo", dispatch=lambda *args: calls.append(args) or "[DONE]", declarations=lambda: [])
    assert result == "[DONE]"
    assert calls[0][0] == "open_app"

def test_complex_route_uses_planning():
    assert o.select_route("Research renewable energy and analyze the results").role == "planning"

def test_cancelled_request_never_dispatches():
    assert o.MasterOrchestrator().execute("Notepad kholo", dispatch=lambda *args: 1/0, declarations=lambda: [], cancelled=lambda: True) == "RESULT: CANCELLED"

def test_agent_scope_and_model_role(monkeypatch):
    monkeypatch.setattr(o.llm, "load_config", lambda: {})
    monkeypatch.setattr(o, "build_turn_context", lambda *a, **k: ([], ""))
    monkeypatch.setattr(o, "is_conversation", lambda _: False)
    roles = []
    monkeypatch.setattr(o.llm, "chat_tools", lambda *a, **k: roles.append(k["role"]) or {})
    def run(**kwargs):
        assert kwargs["dispatch"]("unknown", {}).startswith("[FAILED]")
        kwargs["chat"]([], [])
        return "[DONE]"
    monkeypatch.setattr(o.brain, "run_goal", run)
    assert o.MasterOrchestrator().execute("Research renewable energy", dispatch=lambda *a: 1/0, declarations=lambda: []) == "[DONE]"
    assert roles == ["planning"]


def test_specialist_validates_arguments_before_dispatch():
    from core.agents.specialist import SpecialistExecutor
    calls = []
    agent = SpecialistExecutor([{"name": "excel", "parameters": {"properties": {"action": {"type": "STRING"}}, "required": ["action"]}}], lambda *a: calls.append(a) or "[DONE]")
    assert agent.execute("excel", {}).startswith("[FAILED]")
    assert agent.execute("excel", {"action": 1}).startswith("[FAILED]")
    assert agent.execute("unknown", {}).startswith("[FAILED]")
    assert not calls
    assert agent.execute("excel", {"action": "read"}) == "[DONE]"
    assert len(calls) == 1
    assert "Excel Specialist" in agent.guidance()

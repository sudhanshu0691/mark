"""Request-scoped specialist execution over the trusted dispatcher."""
from core.agents import for_tool
from core.risk import validate_arguments

class SpecialistExecutor:
    def __init__(self, declarations, dispatch, cancelled=None, log=lambda _: None):
        self.tools = {d["name"]: d for d in declarations if d.get("name")}
        self.dispatch = dispatch
        self.cancelled = cancelled
        self.log = log
        self.completed = []
        self.previous_specialist = None

    def execute(self, name, args):
        if self.cancelled and self.cancelled():
            return "[CANCELLED] Request cancelled."
        declaration = self.tools.get(name)
        if declaration is None:
            return "[FAILED] Tool is not registered for this request."
        error = validate_arguments(declaration, args)
        if error:
            return "[FAILED] " + error
        profile = for_tool(name)
        if self.previous_specialist and self.previous_specialist != profile.name:
            self.log(f"[handoff] {self.previous_specialist} -> {profile.name}")
        self.previous_specialist = profile.name
        self.log(f"[agent] {profile.name}: {name}")
        # The existing dispatcher retains permissions and confirmation policy.
        try:
            result = self.dispatch(name, args)
        except Exception as exc:
            result = f"[FAILED] Specialist execution raised {type(exc).__name__}."
        if not isinstance(result, str):
            result = "[UNVERIFIED] Invalid dispatcher response."
        status = next((tag for tag in ("[DONE]", "[FAILED]", "[CANCELLED]", "[UNVERIFIED]") if result.startswith(tag)), "[UNVERIFIED]")
        self.completed.append((profile.name, name, status))
        return result

    def guidance(self):
        groups = {}
        for name in self.tools:
            profile = for_tool(name)
            groups.setdefault(profile.name, []).append(name)
        capabilities = "Registered specialist capabilities: " + "; ".join(
            name + ": " + ", ".join(tools) for name, tools in groups.items())
        outcomes = "; ".join(f"{agent}/{tool}: {status}" for agent, tool, status in self.completed[-12:])
        return capabilities + ("\nDispatcher outcomes (independent verification remains authoritative): " + outcomes if outcomes else "")

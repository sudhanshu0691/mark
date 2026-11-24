"""Request-scoped specialist execution over the trusted dispatcher."""
from core.agents import for_tool
from core.risk import validate_arguments

class SpecialistExecutor:
    def __init__(self, declarations, dispatch, cancelled=None, log=lambda _: None):
        self.tools = {d["name"]: d for d in declarations if d.get("name")}
        self.dispatch = dispatch
        self.cancelled = cancelled
        self.log = log

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
        self.log(f"[agent] {profile.name}: {name}")
        # The existing dispatcher retains permissions and confirmation policy.
        result = self.dispatch(name, args)
        return result if isinstance(result, str) else "[UNVERIFIED] Invalid dispatcher response."

    def guidance(self):
        groups = {}
        for name in self.tools:
            profile = for_tool(name)
            groups.setdefault(profile.name, []).append(name)
        return "Registered specialist capabilities: " + "; ".join(
            name + ": " + ", ".join(tools) for name, tools in groups.items())

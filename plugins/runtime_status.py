"""Read-only diagnostics for the currently running MARK runtime."""
import json

from core import llm, providers
from core.agents import AGENTS
from core.result import done

NAME = "runtime_status"
DESCRIPTION = "Inspect configured models, provider status and specialist capabilities."
DECLARATION = {"name": NAME, "description": DESCRIPTION,
               "parameters": {"type": "OBJECT", "properties": {}}}


def run(args, ctx):
    cfg = llm.load_config()
    data = {"profile": cfg["profile"], "backend": cfg["backend"],
            "local_models": sorted({llm.model_for(r, cfg) for r in ("text", "fast", "code", "vision")}),
            "providers": providers.snapshot(),
            "specialists": {p.name: sorted(p.tools) for p in AGENTS.values() if p.tools}}
    return done(json.dumps(data, ensure_ascii=False))

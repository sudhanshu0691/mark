"""Compatibility entry point for the single command orchestrator."""
from core.agents.orchestrator import MasterOrchestrator

def execute_turn(text, **kwargs):
    return MasterOrchestrator().execute(text, **kwargs)

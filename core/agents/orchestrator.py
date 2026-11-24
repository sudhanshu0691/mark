# core/agents/orchestrator.py
# Master Agent Orchestrator for MARK - hierarchical delegation and task coordination

from __future__ import annotations

from typing import Dict, Any, Optional, List
import asyncio
import logging

from core.events import Event, EventBus, EventType, get_event_bus
from core.inference.manager import InferenceManager
from core.intelligence.intent import classify_intent, assess_complexity, Intent, Complexity


class MasterOrchestrator:
    """Master Agent Orchestrator - hierarchical delegation and task coordination."""
    
    def __init__(self, event_bus: Optional[EventBus] = None, 
                 inference_manager: Optional[InferenceManager] = None) -> None:
        """
        Initialize the master orchestrator.
        
        Args:
            event_bus: Event bus for system communication
            inference_manager: Inference manager for LLM routing
        """
        self.event_bus = event_bus or get_event_bus()
        self.inference_manager = inference_manager or InferenceManager()
        self._task_store: dict = {}
        self._active_tasks: dict = {}

    def _determine_execution_strategy(self, intent: Intent, complexity: Complexity,
                                     extraction: dict) -> str:
        """Determine execution strategy based on intent and complexity."""
        if intent == Intent.SYSTEM_MGMT and complexity == Complexity.LOW:
            return "deterministic"
        if intent == Intent.AUTOMATION and complexity == Complexity.LOW:
            return "deterministic"
        if complexity == Complexity.LOW and extraction.get("mode") != "research":
            return "direct"
        if complexity == Complexity.DEEP:
            return "full_reasoning"
        if complexity == Complexity.HIGH:
            return "enhanced_reasoning"
        return "standard"

    async def handle_user_command(self, user_query: str, context: Optional[dict] = None) -> dict:
        """Handle a user command through the orchestration pipeline."""
        intent, complexity, extraction = classify_intent(user_query)
        complexity = assess_complexity(user_query, intent, extraction)

        await self.event_bus.publish(Event(
            event_type="intent_classified",
            source="orchestrator",
            data={"intent": intent.value, "complexity": complexity.value, "extraction": extraction}
        ))
        
        execution_strategy = self._determine_execution_strategy(intent, complexity, extraction)
        
        result = await self._execute_strategy(
            user_query, intent, complexity, extraction, execution_strategy
        )
        
        await self._post_process(result, intent, extraction)
        
        return result

    async def _execute_deterministic(self, user_query: str, intent: Intent,
                                    extraction: dict) -> dict:
        """Execute deterministic operations without LLM."""
        q_lower = user_query.lower()
        if "exit" in q_lower or "quit" in q_lower:
            return {"status": "terminated", "action": "exit"}
        if "status" in q_lower or "health" in q_lower:
            return {"status": "checked", "operation": "system_health"}
        if any(kw in q_lower for kw in ["help", "what can you do"]):
            return {"status": "information", "content": "MARK can help with conversation, commands, coding, automation, vision, research, file management, productivity tools"}
        return await self._execute_direct(user_query, intent, Complexity.MEDIUM, extraction)

    async def _execute_direct(self, user_query: str, intent: Intent,
                             complexity: Complexity, extraction: dict) -> dict:
        """Execute direct inference without deep reasoning."""
        result = await self.inference_manager.process_query(user_query)
        return {
            "status": result.get("status", "failed"),
            "response": result.get("response", ""),
            "model": result.get("model", "text"),
            "intent": intent.value,
            "complexity": complexity.value,
        }

    async def _execute_strategy(self, user_query: str, intent: Intent, 
                               complexity: Complexity, extraction: dict,
                               strategy: str) -> dict:
        """Execute the determined strategy."""
        if strategy == "deterministic":
            return await self._execute_deterministic(user_query, intent, extraction)
        if strategy == "direct":
            return await self._execute_direct(user_query, intent, complexity, extraction)
        if strategy == "full_reasoning":
            return await self._execute_full_reasoning(user_query, intent, complexity, extraction)
        if strategy == "enhanced_reasoning":
            return await self._execute_enhanced_reasoning(user_query, intent, complexity, extraction)
        return await self._execute_standard(user_query, intent, complexity, extraction)

    async def _execute_full_reasoning(self, user_query: str, intent: Intent,
                                     complexity: Complexity, extraction: dict) -> dict:
        """Execute full ReAct-style iterative reasoning."""
        max_turns = 5
        observations = []
        for turn in range(max_turns):
            result = await self.inference_manager.process_query(user_query)
            observations.append({
                "turn": turn,
                "response": result.get("response", ""),
                "model": result.get("model", ""),
            })
            if result.get("status") == "completed" and result.get("response"):
                break
            if result.get("error"):
                break
        return {
            "status": result.get("status", "failed"),
            "response": observations[-1].get("response", "") if observations else "",
            "reasoning_turns": len(observations),
            "observations": observations,
            "intent": intent.value,
            "complexity": complexity.value,
        }

    async def _execute_enhanced_reasoning(self, user_query: str, intent: Intent,
                                         complexity: Complexity, extraction: dict) -> dict:
        """Execute enhanced reasoning with structured planning."""
        result = await self.inference_manager.process_query(user_query)
        return {
            "status": result.get("status", "failed"),
            "response": result.get("response", ""),
            "reasoning_depth": "enhanced",
            "intent": intent.value,
            "complexity": complexity.value,
        }

    async def _execute_standard(self, user_query: str, intent: Intent,
                               complexity: Complexity, extraction: dict) -> dict:
        """Execute standard reasoning."""
        return await self._execute_direct(user_query, intent, complexity, extraction)

    async def _post_process(self, result: dict, intent: Intent, extraction: dict) -> None:
        """Post-process execution results."""
        if result.get("status") == "completed":
            await self.event_bus.publish(Event(
                event_type="task_completed",
                source="orchestrator",
                data={
                    "intent": intent.value,
                    "complexity": result.get("complexity", "unknown"),
                    "had_tool_calls": bool(result.get("tool_calls")),
                }
            ))
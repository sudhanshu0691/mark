"""Single routing table for MARK's specialist roles.

Specialists map work to the existing tool/plugin implementations. This avoids
spinning up a separate model conversation for every capability and keeps
latency and permission enforcement centralized in the supervisor runtime.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AgentProfile:
    name: str
    purpose: str
    tools: frozenset[str] = frozenset()


AGENTS: dict[str, AgentProfile] = {
    "supervisor": AgentProfile("Supervisor", "Owns request scope, policy, delegation and final response.", frozenset({"shutdown_mark", "plugins"})),
    "conversation": AgentProfile("Conversation", "Respond in the user's language with clear, direct dialogue."),
    "reasoning": AgentProfile("Reasoning", "Analyse constraints and derive conclusions grounded in supplied evidence."),
    "planner": AgentProfile("Planner", "Breaks multi-step goals into bounded actions.", frozenset({"run_goal"})),
    "executor": AgentProfile("Executor", "Runs approved actions through plugins and platform adapters."),
    "verifier": AgentProfile("Verifier", "Checks postconditions and reports DONE, FAILED or UNVERIFIED."),
    "excel": AgentProfile("Excel Specialist", "Creates, edits, analyzes and verifies workbooks.", frozenset({"excel"})),
    "visualization": AgentProfile("Visualization & Diagram", "Builds charts, diagrams and supported drawings.", frozenset({"visualize", "create_visualization"})),
    "developer": AgentProfile("Developer", "Handles code and project workflows.", frozenset({"code_helper", "dev_agent"})),
    "browser": AgentProfile("Browser Automation", "Uses the registered browser controls.", frozenset({"browser_control"})),
    "computer": AgentProfile("Desktop Automation", "Coordinates approved desktop operations.", frozenset({"desktop_control", "computer_control", "window_manager"})),
    "communications": AgentProfile("Communications", "Coordinates messages, mail and calls.", frozenset({"send_email", "send_message", "message_delivery", "calls", "whatsapp", "call_agent"})),
    "files": AgentProfile("File Management", "Manages local files.", frozenset({"file_controller"})),
    "documents": AgentProfile("Document Processing", "Processes registered document and media formats.", frozenset({"file_processor", "media_studio"})),
    "presentations": AgentProfile("Presentation Generation", "Builds and edits supported presentations.", frozenset({"ppt_studio"})),
    "memory": AgentProfile("Memory", "Maintains user-approved facts.", frozenset({"save_memory", "notes"})),
    "scheduling": AgentProfile("Scheduling", "Coordinates timers and reminders.", frozenset({"timer", "reminder"})),
    "security": AgentProfile("Security", "Reports security-relevant system observations and permission decisions."),
    "workflow": AgentProfile("Workflow Execution", "Runs validated dependency graphs.", frozenset({"delegate_tasks"})),
    "system": AgentProfile("Windows Operations", "Handles machine status and settings.", frozenset({"system_status", "runtime_status", "hardware_control", "computer_settings", "processes", "network", "software_manager", "open_app", "app_integration", "game_updater"})),
    "research": AgentProfile("Search and Research", "Retrieves web and travel information.", frozenset({"web_search", "youtube_video", "weather_report", "weather_plugin", "news_plugin", "flight_finder"})),
    "productivity": AgentProfile("Productivity", "Manages notes, reminders and calculations.", frozenset({"calculate", "clipboard", "workspace", "notifications", "notification_mode", "manage_monitor"})),
    "vision": AgentProfile("Vision Department", "Monitors configured cameras, detects people, and queries stored vision events.", frozenset({"vision_start", "vision_stop", "vision_status", "vision_history", "screen_process", "screen_read", "close_camera"})),
}


def for_tool(tool: str) -> AgentProfile:
    for profile in AGENTS.values():
        if tool in profile.tools:
            return profile
    return AGENTS["executor"]


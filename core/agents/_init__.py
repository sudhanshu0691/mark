# core/agents/__init__.py
# MARK Agents - specialized agent capabilities

from __future__ import annotations

from .orchestrator import MasterOrchestrator
from core.agents.conversation import ConversationAgent
from .reasoning import ReasoningAgent
from .coding import CodingAgent
from .pc_automation import PCAutomationAgent
from .file_management import FileManagementAgent
from .vision import VisionAgent
from .data_analysis import DataAnalysisAgent
from .document import DocumentAgent
from .email import EmailAgent
from .calendar import CalendarAgent
from .productivity import ProductivityAgent
from .system_mgmt import SystemManagementAgent
from .monitoring import MonitoringAgent
from .proactive import ProactiveAgent
from .operative import PersistentOperativeAgent
from .morning_digest import MorningDigestAgent
from .verification import VerificationAgent
"""Intent classification and decision support for MARK."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from core import llm
from core import risk

INTENT_KEYWORDS = {
    "open_app": {"open", "launch", "start", "run"},
    "file_controller": {"file", "folder", "directory", "create", "write", "delete", "copy", "move", "read", "list", "find"},
    "whatsapp": {"whatsapp", "message", "send message", "chat"},
    "calls": {"call", "answer", "decline", "incoming"},
    "browser_control": {"browser", "website", "url", "web", "search"},
    "computer_control": {"volume", "mute", "shutdown", "restart", "sleep", "brightness"},
    "computer_settings": {"settings", "theme", "dark mode", "wallpaper", "display"},
    "desktop_control": {"desktop", "icon", "shortcut", "wallpaper"},
    "window_manager": {"window", "minimize", "maximize", "close", "switch"},
    "screen_read": {"screen", "screenshot", "what do you see", "ocr", "capture"},
    "screen_processor": {"camera", "webcam", "vision", "analyze image"},
    "weather_report": {"weather", "forecast", "temperature"},
    "web_search": {"search", "lookup", "find online", "google"},
    "youtube_video": {"youtube", "video", "watch"},
    "send_message": {"sms", "text message", "send"},
    "reminder": {"remind", "alarm", "timer", "schedule"},
    "software": {"install", "uninstall", "update software", "app store"},
    "code_helper": {"code", "program", "script", "debug", "fix code"},
    "dev_agent": {"build project", "create app", "develop", "software project"},
    "file_processor": {"transcribe", "ocr document", "extract text", "convert"},
    "flight_finder": {"flight", "travel", "airline", "ticket"},
    "notifications": {"notify", "alert", "notification"},
    "proactive": {"proactive", "suggest", "remind me"},
    "remember": {"remember", "save this", "note that", "my name is", "i am"},
    "forget": {"forget", "delete memory", "remove"},
}

_KEYWORD_SCORE = 0.6
_LLM_SCORE = 0.4


@dataclass
class Intent:
    label: str
    confidence: float
    risk: str
    requires_confirmation: bool
    clarification: str | None = None
    entities: dict[str, Any] = field(default_factory=dict)
    raw_text: str = ""
    verified_facts: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)

async def classify_intent(text: str, context: str = "") -> Intent:
    text = (text or "").strip()
    if not text:
        return Intent("unknown", 0.0, risk.READ_ONLY, False,
                      clarification="I didn't catch that. Could you repeat?",
                      raw_text=text)

    kw_label, kw_score = _keyword_score(text)
    llm_label = kw_label
    llm_conf = kw_score
    if kw_score < 0.5 or kw_label == "unknown":
        try:
            system = (
                "Classify the user's utterance into ONE of these intents: "
                + ", ".join(sorted(INTENT_KEYWORDS.keys())) + ", unknown. "
                "Reply with just the intent name, nothing else."
            )
            import asyncio
            resp = await asyncio.to_thread(llm.generate, text, role="fast", system=system, temperature=0.1, max_tokens=16)
            llm_label = (resp or "").strip().lower()
            if llm_label not in INTENT_KEYWORDS and llm_label != "unknown":
                llm_label = "unknown"
            llm_conf = 0.7 if llm_label != "unknown" else 0.2
        except Exception:
            llm_conf = 0.0

    if kw_label == llm_label and kw_label != "unknown":
        confidence = min(1.0, kw_score * _KEYWORD_SCORE + llm_conf * _LLM_SCORE + 0.2)
    elif llm_conf > kw_score:
        confidence = llm_conf * _LLM_SCORE + kw_score * _KEYWORD_SCORE * 0.5
        label = llm_label
    else:
        confidence = kw_score * _KEYWORD_SCORE
        label = kw_label

    label = llm_label if (llm_conf > kw_score and llm_label != "unknown") else kw_label
    if label == "unknown" and confidence < 0.3:
        label = "unknown"

    risk_level = risk.classify(label, {}) if label != "unknown" else risk.READ_ONLY
    needs_confirm = risk.requires_confirmation(label, {})

    clarification = None
    if confidence < 0.45:
        clarification = f"I'm not sure I understood. Did you want me to {label.replace('_', ' ')}?"
    elif len(text.split()) < 3 and label != "unknown":
        clarification = f"Should I {label.replace('_', ' ')}? Please confirm."

    entities = _extract_entities(text, label)

    return Intent(
        label=label,
        confidence=round(confidence, 2),
        risk=risk_level,
        requires_confirmation=needs_confirm,
        clarification=clarification,
        entities=entities,
        raw_text=text,
    )


def _extract_entities(text: str, label: str) -> dict[str, Any]:
    ent: dict[str, Any] = {}
    text_l = text.lower()
    if label == "open_app":
        m = re.search(r"(?:open|launch|start|run)\s+(?:the\s+)?(\w+(?:\s+\w+)?)", text_l)
        if m:
            ent["app_name"] = m.group(1)
    if label == "file_controller":
        m = re.search(r"(?:file|folder|path)\s+([A-Za-z]:[\\/][\w\/ .-]+)", text)
        if m:
            ent["path"] = m.group(1)
    if label == "whatsapp":
        m = re.search(r"(?:to|for)\s+(\w+)", text_l)
        if m:
            ent["contact"] = m.group(1)
    if label == "reminder":
        m = re.search(r"(?:in|at|after)\s+(\d+\s*(?:minute|min|hour|sec|second)s?)", text_l)
        if m:
            ent["duration"] = m.group(1)
    return ent


def decision_record(intent: Intent, tool_result: str, verified: bool) -> dict:
    return {
        "intent": intent.label,
        "confidence": intent.confidence,
        "risk": intent.risk,
        "requires_confirmation": intent.requires_confirmation,
        "tool_result": tool_result,
        "verified": verified,
        "facts": intent.verified_facts,
        "assumptions": intent.assumptions,
        "clarification_needed": intent.clarification is not None,
    }


def _keyword_score(text: str) -> tuple[str, float]:
    text_l = text.lower()
    best_label = "unknown"
    best_hits = 0
    for label, kws in INTENT_KEYWORDS.items():
        hits = sum(1 for kw in kws if kw in text_l)
        if hits > best_hits:
            best_hits = hits
            best_label = label
    if best_hits == 0:
        return "unknown", 0.0
    return best_label, min(1.0, best_hits / 3.0)


def match_local_command(text: str) -> tuple[str, dict] | None:
    """Match complete, unambiguous English or Hinglish commands."""
    command = " ".join(text.casefold().strip(" .!?").split())
    if command in {"mark status", "provider status", "mark ka status"}:
        return "runtime_status", {}
    if command in {"system status", "system information", "system info", "system status batao"}:
        return "system_status", {}
    if command in {"list windows", "show windows", "khuli windows dikhao"}:
        return "window_manager", {"action": "list"}
    if command in {"volume up", "volume badhao", "volume down", "volume kam karo"}:
        action = "volume_up" if command in {"volume up", "volume badhao"} else "volume_down"
        return "computer_settings", {"action": action}
    if command in {"mute volume", "mute sound", "awaaz band karo", "unmute volume", "unmute sound", "awaaz chalu karo"}:
        mode = "off" if command.startswith("unmute") or "chalu" in command else "on"
        return "hardware_control", {"action": "mute", "mode": mode}
    for site, address in {"wikipedia":"https://www.wikipedia.org/", "youtube":"https://www.youtube.com/"}.items():
        if command in {f"open {site}", f"{site} kholo"}:
            return "open_app", {"app_name":"browser", "url":address}
    for action in ("copy", "paste", "select_all", "undo", "redo"):
        if command == action.replace("_", " ") and action != "paste":
            return "computer_settings", {"action":action}
    search = re.fullmatch(r"find ([\w .-]{1,80}) in (downloads|documents|pictures)", command)
    if search:
        return "file_controller", {"action":"find", "name":search.group(1), "path":search.group(2)}
    volume = re.fullmatch(r"(?:set volume (?:to )?|volume )(100|[1-9]?[0-9])%?(?: karo)?", command)
    if volume:
        return "computer_settings", {"action": "volume_set", "value": volume.group(1)}
    apps = {"notepad", "calculator", "paint", "chrome", "edge", "firefox", "spotify"}
    for app in apps:
        if command in {f"open {app}", f"launch {app}", f"{app} kholo"}:
            return "open_app", {"app_name": app}
    for folder in ("desktop", "downloads", "documents", "pictures"):
        if command in {f"list {folder}", f"show {folder} files", f"{folder} ki files dikhao"}:
            return "file_controller", {"action": "list", "path": folder}
    return None


def is_conversation(text):
    """Recognize chat and recall requests that require no computer operation."""
    question = " ".join(text.lower().split())
    if question.startswith("chat:") or question in {"hi", "hello", "namaste", "thanks", "thank you"}:
        return True
    return bool(re.search(r"\b(?:what (?:did (?:i|we|you)|was (?:it|that|my)|were we)|"
                          r"(?:did i (?:say|tell|give|mention))|do you remember|"
                          r"(?:you|i) (?:said|mentioned) earlier|what is my name|"
                          r"maine .*?(?:kaha|bola|bataya)|pichli baat|hum kya baat|mera naam kya)\b", question))

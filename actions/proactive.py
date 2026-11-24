
"""
ProactiveEngine 2.0 — context-aware, time-aware, non-repetitive background prompting.
Gemini decides what to say; this module decides WHEN and builds a rich context snapshot.
"""
import time
from datetime import datetime
import re


class ProactiveEngine:
    """
    Decides when MARK should speak unprompted and builds a context-rich prompt.

    Improvements over 1.0:
      - Time-of-day awareness  (morning / afternoon / evening / night)
      - Monitor-topic awareness (what the user is tracking)
      - Recent-session context  (last few turns of the current conversation)
      - Non-repetitive          (rotates context focus to avoid same opener)
      - Smarter silence gate    (doesn't fire while MARK is speaking)
      - Emotion-aware check-ins  (supportive without diagnosing or overreacting)
      - Personalized suggestions from projects, preferences, and wishes
      - Schedule and reminder awareness from stored context

    Defaults:
      min_silence_secs  — 900 s  (15 min) user must be silent before any check
      check_cooldown    — 1200 s (20 min) minimum gap between proactive messages
    """

    def __init__(
        self,
        min_silence_secs: int = 900,
        check_cooldown:   int = 1200,
    ):
        self.min_silence_secs = min_silence_secs
        self.check_cooldown   = check_cooldown
        self._last_triggered  = 0.0
        self._rotation        = 0          # cycles through context focus areas

    # ── Trigger gate ───────────────────────────────────────────────────────────

    def should_trigger(self, last_user_speech: float) -> bool:
        now = time.monotonic()
        return (
            (now - last_user_speech) >= self.min_silence_secs
            and (now - self._last_triggered) >= self.check_cooldown
        )

    def mark_triggered(self) -> None:
        self._last_triggered = time.monotonic()
        self._rotation      += 1

    @staticmethod
    def detect_emotion(recent_turns: list[str] | None = None) -> str:
        """Return a broad conversational signal for a tactful proactive tone."""
        text = " ".join(recent_turns[-6:] if recent_turns else []).lower()
        if not text:
            return "unknown"

        patterns = {
            "sad": (
                r"\b(sad|upset|hurt|lonely|depressed|stressed|tired|worried|"
                r"dukhi|udaas|pareshan|thaka|tension|akela)\b",
            ),
            "happy": (
                r"\b(happy|excited|great|awesome|glad|proud|yay|khush|"
                r"maza|badhiya|shandar)\b",
            ),
            "funny": (
                r"\b(lol|haha|funny|joke|meme|mazak|hasna|hansi)\b",
            ),
        }
        scores = {
            emotion: sum(len(re.findall(pattern, text)) for pattern in rules)
            for emotion, rules in patterns.items()
        }
        strongest = max(scores, key=scores.get)
        return strongest if scores[strongest] else "neutral"

    @staticmethod
    def _memory_values(memory: dict, category: str, limit: int = 4) -> list[str]:
        entries = memory.get(category, {}) if isinstance(memory, dict) else {}
        if not isinstance(entries, dict):
            return []
        values = []
        for key, entry in list(entries.items())[:limit]:
            value = entry.get("value") if isinstance(entry, dict) else entry
            if value:
                values.append(f"{key.replace('_', ' ')}: {value}")
        return values

    @classmethod
    def build_personalized_context(cls, memory: dict) -> str:
        """Build useful, bounded suggestions without exposing the whole memory."""
        projects = cls._memory_values(memory, "projects")
        preferences = cls._memory_values(memory, "preferences")
        wishes = cls._memory_values(memory, "wishes")
        parts = []
        if projects:
            parts.append("Active goals: " + "; ".join(projects))
        if preferences:
            parts.append("Preferences: " + "; ".join(preferences))
        if wishes:
            parts.append("Plans/wishes: " + "; ".join(wishes))
        return "\nPersonalized context: " + " | ".join(parts) if parts else ""

    @classmethod
    def build_schedule_context(cls, memory: dict) -> str:
        """Read optional schedule/reminder memory fields without requiring a schema migration."""
        schedule = (
            cls._memory_values(memory, "schedule", 6)
            or cls._memory_values(memory, "reminders", 6)
            or cls._memory_values(memory, "notes", 6)
        )
        if not schedule:
            return ""
        relevant = [
            item for item in schedule
            if any(token in item.lower() for token in (
                "schedule", "calendar", "meeting", "appointment", "reminder",
                "deadline", "tomorrow", "today", "time",
            ))
        ]
        return "\nSchedule/reminder context: " + "; ".join(relevant[:4]) if relevant else ""

    # ── Prompt builder ─────────────────────────────────────────────────────────

    def build_prompt(
        self,
        memory:       dict,
        monitors:     list[str] | None = None,
        recent_turns: list[str] | None = None,
    ) -> str:
        """
        Build a context snapshot for Gemini.
        Rotates through three focus areas so proactive messages don't repeat.
        """
        from memory.memory_manager import format_memory_for_prompt

        now      = datetime.now()
        hour     = now.hour
        time_str = now.strftime("%A, %B %d, %Y — %I:%M %p")

        # Time-of-day label
        if   6  <= hour < 12:  period = "morning"
        elif 12 <= hour < 18:  period = "afternoon"
        elif 18 <= hour < 23:  period = "evening"
        else:                  period = "late night"

        mem_str = format_memory_for_prompt(memory) or "(no stored user data)"
        emotion = self.detect_emotion(recent_turns)
        personalized_ctx = self.build_personalized_context(memory)
        schedule_ctx = self.build_schedule_context(memory)

        # Rotating context focus (cycles every trigger)
        focus_index = self._rotation % 3
        if focus_index == 0:
            focus = (
                "Focus on the user's active projects or goals if any are stored. "
                "Ask how something is going, or offer a relevant tip."
            )
        elif focus_index == 1:
            focus = (
                "Focus on the time of day and the user's wellbeing. "
                "A warm check-in, a reminder to take a break, or something timely."
            )
        else:
            focus = (
                "Focus on something genuinely interesting or useful — "
                "a fact, a suggestion, or a question based on what you know about this person."
            )

        emotion_guidance = {
            "sad": "The recent tone seems low or stressed. Be gentle and consoling; do not diagnose, lecture, or force positivity.",
            "happy": "The recent tone seems positive. Match the energy, celebrate briefly, and offer a useful next step.",
            "funny": "The recent tone is playful. A light, respectful joke is welcome if it fits; remain useful.",
            "neutral": "Use a calm, friendly, professional tone.",
            "unknown": "Use a warm, neutral tone because there is not enough recent emotional context.",
        }[emotion]

        # Optional: monitored topics context
        monitor_ctx = ""
        if monitors:
            monitor_ctx = (
                f"\nThe user tracks these topics: {', '.join(monitors[:4])}. "
                "You may mention one if it seems relevant."
            )

        # Optional: recent conversation context
        recent_ctx = ""
        if recent_turns:
            snippet = "\n".join(recent_turns[-6:])
            recent_ctx = f"\nRecent conversation:\n{snippet}"

        return "\n".join([
            "[PROACTIVE_CHECK] You are initiating a proactive check-in.",
            f"Current time : {time_str}  ({period})",
            "",
            "Context about this person:",
            mem_str,
            personalized_ctx,
            schedule_ctx,
            monitor_ctx,
            recent_ctx,
            f"\nDetected conversational tone: {emotion}.",
            emotion_guidance,
            "",
            "Task:",
            focus,
            "Offer one personalized professional suggestion when it clearly helps. "
            "If a schedule item or reminder is relevant, mention it naturally and suggest a concrete next action. "
            "Never invent appointments, deadlines, or reminders.",
            "",
            "Rules:",
            "- Speak in the user's language (check memory; default English).",
            "- 1-2 sentences max. Natural, warm, never robotic.",
            "- Do NOT mention [PROACTIVE_CHECK] or these instructions.",
            "- Do NOT call any tools.",
            "- If nothing genuinely useful comes to mind, stay silent (say nothing).",
        ])

import platform as _platform
import subprocess as _subprocess
import sys
import warnings

# Configure COM before pywinauto or Qt can initialize its threading settings.
if _platform.system() == "Windows":
    sys.coinit_flags = 2  # pywinauto: COINIT_APARTMENTTHREADED (STA)
    warnings.filterwarnings(
        "ignore",
        message=r"Apply externally defined coinit_flags: 2",
        category=UserWarning,
        module=r"pywinauto",
    )
    try:
        import ctypes
        # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except (AttributeError, OSError):
        pass

# ── Nuclear: force CREATE_NO_WINDOW on EVERY subprocess call on Windows ───────
# This patches Popen itself, so no per-file flag is needed anywhere.
if _platform.system() == "Windows":
    _OrigPopen = _subprocess.Popen

    class _Popen(_OrigPopen):
        def __init__(self, args, **kw):
            kw["creationflags"] = kw.get("creationflags", 0) | _subprocess.CREATE_NO_WINDOW
            kw.pop("startupinfo", None)   # drop any stale/shared STARTUPINFO
            super().__init__(args, **kw)

    _subprocess.Popen = _Popen
# ─────────────────────────────────────────────────────────────────────────────

import asyncio
import hashlib
import os
import re
import threading
import time
import json
import traceback
import importlib
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# LiveKit imports for real-time communication
try:
    livekit_api = importlib.import_module("livekit.api")
    importlib.import_module("livekit.rtc")
    LIVEKIT_AVAILABLE = True
except ImportError:
    livekit_api = None
    LIVEKIT_AVAILABLE = False
    # LiveKit will be enabled if configured later


def _install_first_run_requirements() -> None:
    """Dependencies must be installed explicitly via setup.py/setup_windows.bat.

    Automatic pip installs during normal startup are disabled for stability and
    supply-chain safety. If config is missing, just inform the user.
    """
    if not (_platform.system() == "Windows" and not (Path(__file__).resolve().parent / "config" / "api_keys.json").exists()):
        return
    print("[MARK] First run detected. Please run setup_windows.bat (or `python setup.py`) to install dependencies.")


_install_first_run_requirements()


def _configure_console_encoding() -> None:
    """Keep diagnostic logging from crashing worker tasks on Windows consoles."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass


_configure_console_encoding()

import numpy as np
import sounddevice as sd
import google.genai as genai
from google.genai import types as gtypes
from ui import MarkUI
from memory.memory_manager import (
    load_memory, update_memory, format_memory_for_prompt,
    save_session_summary, pop_last_session,
)
from memory.conversation import recent_messages
from memory.db import append_conversation_turn
from core.intent import match_local_command, is_conversation
from core import risk

from actions.file_processor import file_processor
from actions.flight_finder     import flight_finder
from actions.open_app          import open_app
from actions.weather_report    import weather_action
from actions.send_message      import send_message
from actions.reminder          import reminder
from actions.computer_settings import computer_settings
from actions.screen_processor  import _capture_camera, _capture_screen_region, extract_ocr
from actions.screen_read       import vision_answer, _facts as _screen_facts
from actions.youtube_video     import youtube_video
from actions.desktop           import desktop_control
from actions.browser_control   import browser_control
from actions.code_helper       import code_helper
from actions.dev_agent         import dev_agent
from actions.web_search        import web_search as web_search_action
from actions.computer_control  import computer_control
from actions.game_updater      import game_updater
from actions.system_monitor    import SystemMonitor, get_system_status
from actions.proactive         import ProactiveEngine
from actions.background_monitor import (
    add_monitor, remove_monitor, list_monitors, check_all as monitor_check_all,
)
from memory.config_manager     import get_brief_enabled
from core.result import coerce_side_effect, failed as _failed
from core.tools import NEW_DECLARATIONS, NATIVE, NEW_NAMES
from core.plugins import PluginManager, PluginContext
from core import brain as _brain
from core.task_manager import TASKS
from actions.notifications import WATCHER as NOTIF_WATCHER, describe as _describe_note


def get_base_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent


BASE_DIR        = get_base_dir()
API_CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"
PROMPT_PATH     = BASE_DIR / "core" / "prompt.txt"
# Gemini Live API model ID. Native audio keeps transcription and tool turns
# compatible with the current Live SDK configuration.
from core import llm as _llm_config
def _live_model():
    return _llm_config.load_config().get("gemini", {}).get("live_model", "")
CHANNELS            = 1
SEND_SAMPLE_RATE    = 16000
RECEIVE_SAMPLE_RATE = 24000
CHUNK_SIZE          = 1024

def _get_api_key() -> str:
    """Gemini key (optional now: only the Live voice uses it; all thinking is local)."""
    try:
        from core.secrets import get_secret
        return get_secret("gemini")
    except Exception:
        return ""


def _is_quota_error(text: str) -> bool:
    t = (text or "").lower()
    return any(k in t for k in ("resource_exhausted", "quota", "429", "rate limit", "rate_limit"))


def _is_invalid_api_key_error(error: BaseException) -> bool:
    """Recognize Gemini authentication failures without hiding other errors."""
    message = _exception_details(error).lower()
    return any(marker in message for marker in (
        "api key not valid",
        "api_key_invalid",
        "invalid api key",
        "unauthenticated",
        "status code: 401",
        "invalid authentication credentials",
        "expected oauth 2 access token",
    ))


def _exception_details(error: BaseException) -> str:
    """Return useful messages from normal and ExceptionGroup failures."""
    messages = [str(error).strip()]
    nested = getattr(error, "exceptions", ())
    for child in nested:
        detail = _exception_details(child)
        if detail:
            messages.append(detail)
    return " | ".join(dict.fromkeys(message for message in messages if message))


def _is_live_inactivity_error(error: BaseException) -> bool:
    """Distinguish a normal idle socket close from authentication failures."""
    detail = _exception_details(error).lower()
    return "inactivity" in detail and ("1008" in detail or "session closed" in detail)


# LiveKit configuration and token generation
def _load_livekit_config() -> dict:
    """Load LiveKit configuration from config file or environment."""
    import configparser
    from dotenv import load_dotenv

    load_dotenv(BASE_DIR / "config" / "runtime.env", override=False)
    load_dotenv(BASE_DIR / ".env", override=False)
    config_path = BASE_DIR / "config" / "livekit.ini"

    cfg = configparser.ConfigParser()
    cfg.read(config_path)

    livekit_cfg = {}
    if "livekit" in cfg:
        livekit_cfg["url"] = cfg.get("livekit", "url", fallback="")
        livekit_cfg["api_key"] = cfg.get("livekit", "api_key", fallback="")
        livekit_cfg["api_secret"] = cfg.get("livekit", "api_secret", fallback="")
        livekit_cfg["room_name"] = cfg.get("livekit", "room_name", fallback="")
        livekit_cfg["participant_name"] = cfg.get("livekit", "participant_name", fallback="")

    # Also check environment variables
    if not livekit_cfg.get("url"):
        livekit_cfg["url"] = os.environ.get("LIVEKIT_URL", "http://localhost:7880")
    if not livekit_cfg.get("api_key"):
        livekit_cfg["api_key"] = os.environ.get("LIVEKIT_API_KEY", "")
    if not livekit_cfg.get("api_secret"):
        livekit_cfg["api_secret"] = os.environ.get("LIVEKIT_API_SECRET", "")
    if not livekit_cfg.get("room_name"):
        livekit_cfg["room_name"] = os.environ.get("LIVEKIT_ROOM", "mark-room")
    if not livekit_cfg.get("participant_name"):
        livekit_cfg["participant_name"] = os.environ.get("LIVEKIT_PARTICIPANT", "mark-user")

    return livekit_cfg


def _generate_livekit_token(room_name: str, participant_name: str) -> str:
    """Generate an authenticated LiveKit access token."""
    livekit_cfg = _load_livekit_config()

    from livekit import api

    if not livekit_cfg["api_key"] or not livekit_cfg["api_secret"]:
        raise ValueError("LiveKit API key and secret are required")
    return (api.AccessToken(livekit_cfg["api_key"], livekit_cfg["api_secret"])
            .with_identity(participant_name)
            .with_grants(api.VideoGrants(room_join=True, room=room_name))
            .to_jwt())


async def _wait_for_microphone(ui) -> None:
    """Avoid reconnecting an idle session repeatedly while the microphone is muted."""
    while ui.muted:
        await asyncio.sleep(0.25)


def _is_live_audio_configuration_error(error: BaseException) -> bool:
    """Return whether Live rejected the model/audio configuration permanently."""
    details = _exception_details(error).lower()
    return (
        "1007" in details
        or "content_type_audio" in details
        or "content type audio" in details
        or "not supported for this model configuration" in details
    )


def _load_system_prompt() -> str:
    try:
        return PROMPT_PATH.read_text(encoding="utf-8")
    except Exception:
        return (
            "You are MARK, the assistant running inside this existing multi-agent runtime. "
            "Be concise, direct, and always use the provided tools to complete tasks. "
            "Never simulate or guess results — always call the appropriate tool."
        )

_CTRL_RE = re.compile(r"<ctrl\d+>", re.IGNORECASE)

def _clean_transcript(text: str) -> str:    
    text = _CTRL_RE.sub("", text)
    text = re.sub(r"[\x00-\x08\x0b-\x1f]", "", text)
    return text.strip()

TOOL_DECLARATIONS = [
    {"name": "gesture_mouse", "description": "Control webcam gesture mouse, enabled at startup when available. Actions: enable, disable, sensitivity_up, sensitivity_down, drag_on, drag_off, status.",
     "parameters": {"type": "OBJECT", "properties": {"action": {"type": "STRING"}}, "required": ["action"]}},
    {
        "name": "open_app",
        "description": (
            "Opens an application or website. SMART: if the app is already running (on the taskbar) it is NOT searched or launched again - "
            "it is brought to the front and maximized (browsers get a fresh tab, or the given url as a new tab). Only apps that are not running "
            "are started (taskbar pin / Start menu / Store). The result is VERIFIED against the real front window. "
            "Use this whenever the user asks to open, launch, start, switch to or show any app, website or program."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "app_name": {
                    "type": "STRING",
                    "description": "Name of the application or site (e.g. 'WhatsApp', 'Chrome', 'Spotify', 'youtube')"
                },
                "url": {"type": "STRING", "description": "Browsers only: page to open in a new tab"},
                "new_tab": {"type": "BOOLEAN", "description": "Browsers: open a fresh tab (default true)"},
                "maximize": {"type": "BOOLEAN", "description": "Maximize the window (default true)"},
            },
            "required": ["app_name"]
        }
    },
    {
        "name": "web_search",
        "description": (
            "Searches the web. Use for ANY question about current facts, events, prices, "
            "or topics — always prefer this over guessing. "
            "Modes: 'search' (default), 'news' (latest headlines on a topic), "
            "'research' (deep comprehensive answer), 'price' (product cost lookup), "
            "'compare' (side-by-side comparison of items)."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query":  {"type": "STRING", "description": "Search query or topic"},
                "mode":   {"type": "STRING", "description": "search | news | research | price | compare"},
                "items":  {"type": "ARRAY",  "items": {"type": "STRING"}, "description": "Items to compare (compare mode)"},
                "aspect": {"type": "STRING", "description": "Comparison aspect: price | specs | reviews | features"},
            },
            "required": ["query"]
        }
    },
    {
        "name": "system_status",
        "description": (
            "Returns real-time system metrics: CPU usage, RAM, GPU load, CPU temperature, "
            "uptime, and process count. Use when the user asks about computer performance, "
            "temperature, memory, or resource usage."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {},
        }
    },
    {
        "name": "weather_report",
        "description": "Gives the weather report to user",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "city": {"type": "STRING", "description": "City name"}
            },
            "required": ["city"]
        }
    },
    {
        "name": "send_message",
        "description": "Sends a text message. For WhatsApp prefer the whatsapp tool; this one also drives Telegram, Signal, Discord, Instagram, Messenger by keystrokes and CANNOT verify delivery.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "receiver":     {"type": "STRING", "description": "Recipient contact name"},
                "message_text": {"type": "STRING", "description": "The message to send"},
                "platform":     {"type": "STRING", "description": "Platform: WhatsApp, Telegram, etc."}
            },
            "required": ["receiver", "message_text", "platform"]
        }
    },
    {
        "name": "reminder",
        "description": "Sets a timed reminder using Task Scheduler.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "date":    {"type": "STRING", "description": "Date in YYYY-MM-DD format"},
                "time":    {"type": "STRING", "description": "Time in HH:MM format (24h)"},
                "message": {"type": "STRING", "description": "Reminder message text"}
            },
            "required": ["date", "time", "message"]
        }
    },
    {
        "name": "youtube_video",
        "description": (
            "Controls YouTube. Use for: playing videos, summarizing a video's content, "
            "getting video info, or showing trending videos."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "play | summarize | get_info | trending (default: play)"},
                "query":  {"type": "STRING", "description": "Search query for play action"},
                "save":   {"type": "BOOLEAN", "description": "Save summary to Notepad (summarize only)"},
                "region": {"type": "STRING", "description": "Country code for trending e.g. TR, US"},
                "url":    {"type": "STRING", "description": "Video URL for get_info action"},
            },
            "required": []
        }
    },
    {
        "name": "screen_process",
        "description": (
            "Captures the screen or webcam image and lets you analyze it. "
            "MUST be called when user asks what is on screen, what you see, "
            "look at camera, analyze my screen, etc. "
            "You have NO visual ability without this tool. "
            "After the image is captured it is sent directly to you — describe what you see and answer the user's question. "
            "The camera runs in the background without a preview panel."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "angle": {"type": "STRING", "description": "'screen' to capture display, 'camera' for webcam. Default: 'screen'"},
                "text":  {"type": "STRING", "description": "The question or instruction about the captured image"},
                "monitor": {"type": "INTEGER", "description": "Monitor index: 0 is the combined desktop, 1+ are individual monitors"},
                "region": {"type": "OBJECT", "description": "Optional pixel crop with left, top, width, height"},
                "ocr": {"type": "BOOLEAN", "description": "Run local OCR and include detected text in the vision prompt"},
            },
            "required": ["text"]
        }
    },
    {
        "name": "close_camera",
        "description": (
            "Closes the live camera view shown on screen. "
            "Call when user says: close camera, stop camera, turn off camera, "
            "kamerayı kapat, kapat, creepy, etc."
        ),
        "parameters": {"type": "OBJECT", "properties": {}, "required": []}
    },
    {
        "name": "computer_settings",
        "description": (
            "Full OS / desktop control via voice. Use for ANY single system command including: "
            "volume_up/down/mute, volume_set (value 0-100), brightness_up/down, brightness_set (0-100), "
            "media_play_pause/next/prev/stop, lock_screen, sleep, hibernate, log_off, restart, shutdown, "
            "cancel_shutdown, dark_mode, night_light, wifi_on/off, toggle_wifi, toggle_bluetooth, "
            "empty_recycle_bin, notification_center, clipboard_history, start_menu, emoji_panel, "
            "open_settings, file_explorer, open_run, open_terminal, open_calculator, task_manager, "
            "minimize/maximize/fullscreen/snap_left/snap_right/switch_window/show_desktop/close_app, "
            "screenshot, scroll_*, zoom_*, tab controls, copy/paste/cut/undo/redo/select_all/save, "
            "type_text, press_key, kill_app (value=app name). "
            "Dangerous actions (shutdown/restart/sleep/hibernate/log_off) require confirmed=yes. "
            "Prefer this tool for OS-level commands; use computer_control for precise mouse/click/coords."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "Action name e.g. volume_up, brightness_set, lock_screen, wifi_off, kill_app, shutdown"},
                "description": {"type": "STRING", "description": "Natural language if action is unclear — intent will be auto-detected"},
                "value":       {"type": "STRING", "description": "Optional: volume/brightness 0-100, text to type, key name, or app name for kill_app"},
                "confirmed":   {"type": "STRING", "description": "Must be yes for shutdown/restart/sleep/hibernate/log_off"},
                "text":        {"type": "STRING", "description": "Text to type (alias for value on type_text)"},
                "press_enter": {"type": "STRING", "description": "true to press Enter after typing"}
            },
            "required": []
        }
    },
    {
        "name": "browser_control",
        "description": (
            "Controls any web browser. Use for: opening websites, searching the web, "
            "clicking elements, filling forms, scrolling, screenshots, navigation, any web-based task. "
            "Simple open/search requests launch the user's own browser normally. Interactive actions "
            "first attach to an existing Chromium browser started with remote debugging (preserving "
            "the user's tabs and logged-in profile), then fall back to the persistent MARK profile "
            "when attachment is unavailable. "
            "Always pass the 'browser' parameter when the user specifies a browser (e.g. 'open in Edge', "
            "'use Firefox', 'open Chrome'). Multiple browsers can run simultaneously."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "go_to | search | click | type | scroll | fill_form | smart_click | smart_type | get_text | get_url | press | new_tab | close_tab | screenshot | back | forward | reload | switch | list_browsers | close | close_all"},
                "browser":     {"type": "STRING", "description": "Target browser: chrome | edge | firefox | opera | operagx | brave | vivaldi | safari. Omit to use the currently active browser."},
                "url":         {"type": "STRING", "description": "URL for go_to / new_tab action"},
                "query":       {"type": "STRING", "description": "Search query for search action"},
                "engine":      {"type": "STRING", "description": "Search engine: google | bing | duckduckgo | yandex (default: google)"},
                "selector":    {"type": "STRING", "description": "CSS selector for click/type"},
                "text":        {"type": "STRING", "description": "Text to click or type"},
                "description": {"type": "STRING", "description": "Element description for smart_click/smart_type"},
                "direction":   {"type": "STRING", "description": "up | down for scroll"},
                "amount":      {"type": "INTEGER", "description": "Scroll amount in pixels (default: 500)"},
                "key":         {"type": "STRING", "description": "Key name for press action (e.g. Enter, Escape, F5)"},
                "path":        {"type": "STRING", "description": "Save path for screenshot"},
                "incognito":   {"type": "BOOLEAN", "description": "Open in private/incognito mode"},
                "clear_first": {"type": "BOOLEAN", "description": "Clear field before typing (default: true)"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "desktop_control",
        "description": "Controls the desktop: wallpaper, organize, clean, list, stats.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "wallpaper | wallpaper_url | organize | clean | list | stats | current_wallpaper"},
                "path":   {"type": "STRING", "description": "Image path for wallpaper"},
                "url":    {"type": "STRING", "description": "Image URL for wallpaper_url"},
                "mode":   {"type": "STRING", "description": "by_type or by_date for organize"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "code_helper",
        "description": "Writes, edits, explains, or optimizes source for review.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "write | edit | explain | optimize | auto"},
                "description": {"type": "STRING", "description": "What the code should do or what change to make"},
                "language":    {"type": "STRING", "description": "Programming language (default: python)"},
                "output_path": {"type": "STRING", "description": "Where to save the file"},
                "file_path":   {"type": "STRING", "description": "Path to existing file for edit/explain/run/build"},
                "code":        {"type": "STRING", "description": "Raw code string for explain"},
                "args":        {"type": "STRING", "description": "CLI arguments for run/build"},
                "timeout":     {"type": "INTEGER", "description": "Execution timeout in seconds (default: 30)"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "dev_agent",
        "description": "Plans and writes multi-file project source for review.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "description":  {"type": "STRING", "description": "What the project should do"},
                "language":     {"type": "STRING", "description": "Programming language (default: python)"},
                "project_name": {"type": "STRING", "description": "Optional project folder name"},
                "timeout":      {"type": "INTEGER", "description": "Run timeout in seconds (default: 30)"},
            },
            "required": ["description"]
        }
    },
    {
        "name": "computer_control",
        "description": (
            "Precise mouse/keyboard automation. Use when computer_settings is not enough: "
            "click at x,y, double_click, right_click, move, drag (x1,y1,x2,y2), "
            "screen_find / screen_click by natural-language element description, "
            "type/smart_type, hotkey, press, scroll, screenshot, focus_window, wait, clear_field."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "type | smart_type | click | double_click | right_click | hotkey | press | scroll | move | drag | copy | paste | screenshot | wait | clear_field | focus_window | screen_find | screen_click | random_data | user_data"},
                "text":        {"type": "STRING", "description": "Text to type or paste"},
                "x":           {"type": "INTEGER", "description": "X coordinate"},
                "y":           {"type": "INTEGER", "description": "Y coordinate"},
                "x1":          {"type": "INTEGER", "description": "Drag start X"},
                "y1":          {"type": "INTEGER", "description": "Drag start Y"},
                "x2":          {"type": "INTEGER", "description": "Drag end X"},
                "y2":          {"type": "INTEGER", "description": "Drag end Y"},
                "keys":        {"type": "STRING", "description": "Key combination e.g. 'ctrl+c'"},
                "key":         {"type": "STRING", "description": "Single key e.g. 'enter'"},
                "direction":   {"type": "STRING", "description": "up | down | left | right"},
                "amount":      {"type": "INTEGER", "description": "Scroll amount (default: 3)"},
                "seconds":     {"type": "NUMBER",  "description": "Seconds to wait"},
                "title":       {"type": "STRING",  "description": "Window title for focus_window"},
                "description": {"type": "STRING",  "description": "Element description for screen_find/screen_click"},
                "type":        {"type": "STRING",  "description": "Data type for random_data"},
                "field":       {"type": "STRING",  "description": "Field for user_data: name|email|city"},
                "clear_first": {"type": "BOOLEAN", "description": "Clear field before typing (default: true)"},
                "path":        {"type": "STRING",  "description": "Save path for screenshot"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "game_updater",
        "description": (
            "THE ONLY tool for ANY Steam or Epic Games request. "
            "Use for: installing, downloading, updating games, listing installed games, "
            "checking download status, scheduling updates. "
            "ALWAYS call directly for any Steam/Epic/game request. "
            "NEVER use browser_control or web_search for Steam/Epic."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":    {"type": "STRING",  "description": "update | install | list | download_status | schedule | cancel_schedule | schedule_status (default: update)"},
                "platform":  {"type": "STRING",  "description": "steam | epic | both (default: both)"},
                "game_name": {"type": "STRING",  "description": "Game name (partial match supported)"},
                "app_id":    {"type": "STRING",  "description": "Steam AppID for install (optional)"},
                "hour":      {"type": "INTEGER", "description": "Hour for scheduled update 0-23 (default: 3)"},
                "minute":    {"type": "INTEGER", "description": "Minute for scheduled update 0-59 (default: 0)"},
                "shutdown_when_done": {"type": "BOOLEAN", "description": "Shut down PC when download finishes"},
            },
            "required": []
        }
    },
    {
        "name": "flight_finder",
        "description": "Searches Google Flights and speaks the best options.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "origin":      {"type": "STRING",  "description": "Departure city or airport code"},
                "destination": {"type": "STRING",  "description": "Arrival city or airport code"},
                "date":        {"type": "STRING",  "description": "Departure date (any format)"},
                "return_date": {"type": "STRING",  "description": "Return date for round trips"},
                "passengers":  {"type": "INTEGER", "description": "Number of passengers (default: 1)"},
                "cabin":       {"type": "STRING",  "description": "economy | premium | business | first"},
                "save":        {"type": "BOOLEAN", "description": "Save results to Notepad"},
            },
            "required": ["origin", "destination", "date"]
        }
    },
    {
        "name": "manage_monitor",
        "description": (
            "Add, remove, or list background monitoring topics. "
            "MARK checks these topics once a day and alerts the user when there is a new development. "
            "Use 'add' when the user says 'monitor X', 'track X', 'follow X'. "
            "Use 'remove' when the user says 'stop monitoring X'. "
            "Use 'list' when the user asks what is being monitored. "
            "Do NOT add crypto, financial, or trading topics."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type":        "STRING",
                    "description": "add | remove | list",
                },
                "topic": {
                    "type":        "STRING",
                    "description": "Topic to monitor or stop monitoring (e.g. 'space exploration', 'AI news')",
                },
            },
            "required": ["action"],
        },
    },
    {
        "name": "shutdown_mark",
        "description": (
            "Shuts down the assistant completely. "
            "Call this when the user expresses intent to end the conversation, "
            "close the assistant, say goodbye, or stop MARK. "
            "The user can say this in ANY language."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {},
        }
    },
    {
    "name": "file_processor",
    "description": (
        "Processes any file that the user has uploaded or dropped onto the interface. "
        "Use this when the user refers to an uploaded file and wants an action on it. "
        "Supports: images (describe/ocr/resize/compress/convert), "
        "PDFs (summarize/extract_text/to_word), "
        "Word docs & text files (summarize/fix/reformat/translate), "
        "CSV (analyze/stats/filter/sort/convert); for Excel workbooks use the dedicated excel tool, "
        "JSON/XML (validate/format/analyze), "
        "code files (explain/review/fix/optimize/run/document/test), "
        "audio (transcribe/trim/convert/info), "
        "video (trim/extract_audio/extract_frame/compress/transcribe/info), "
        "archives (list/extract), "
        "presentations (summarize/extract_text). "
        "For workbooks use the dedicated excel tool instead. ALWAYS call this tool for other uploaded files. "
        "If the user's command is ambiguous, pick the most logical action for that file type."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "file_path": {
                "type": "STRING",
                "description": "Full path to the uploaded file. Leave empty to use the currently uploaded file."
            },
            "action": {
                "type": "STRING",
                "description": (
                    "What to do with the file. Examples by type:\n"
                    "image: describe | ocr | resize | compress | convert | info\n"
                    "pdf: summarize | extract_text | to_word | info\n"
                    "docx/txt: summarize | fix | reformat | translate_hint | word_count | to_bullet\n"
                    "csv: analyze | stats | filter | sort | convert | info\n"
                    "json: validate | format | analyze | to_csv\n"
                    "code: explain | review | fix | optimize | run | document | test\n"
                    "audio: transcribe | trim | convert | info\n"
                    "video: trim | extract_audio | extract_frame | compress | transcribe | info | convert\n"
                    "archive: list | extract\n"
                    "pptx: summarize | extract_text | analyze"
                )
            },
            "instruction": {
                "type": "STRING",
                "description": "Free-form instruction if action doesn't cover it. E.g. 'translate this to Turkish', 'find all email addresses'"
            },
            "format": {
                "type": "STRING",
                "description": "Target format for conversion. E.g. 'mp3', 'pdf', 'csv', 'png'"
            },
            "width":     {"type": "INTEGER", "description": "Target width for image resize"},
            "height":    {"type": "INTEGER", "description": "Target height for image resize"},
            "scale":     {"type": "NUMBER",  "description": "Scale factor for image resize (e.g. 0.5)"},
            "quality":   {"type": "INTEGER", "description": "Quality 1-100 for image/video compress"},
            "start":     {"type": "STRING",  "description": "Start time for trim: seconds or HH:MM:SS"},
            "end":       {"type": "STRING",  "description": "End time for trim: seconds or HH:MM:SS"},
            "timestamp": {"type": "STRING",  "description": "Timestamp for video frame extraction HH:MM:SS"},
            "column":    {"type": "STRING",  "description": "Column name for CSV filter/sort"},
            "value":     {"type": "STRING",  "description": "Filter value for CSV filter"},
            "condition": {"type": "STRING",  "description": "Filter condition: equals|contains|gt|lt"},
            "ascending": {"type": "BOOLEAN", "description": "Sort order for CSV sort (default: true)"},
            "save":      {"type": "BOOLEAN", "description": "Save result to file (default: true)"},
            "destination": {"type": "STRING", "description": "Output folder for archive extract"},
        },
        "required": []
    }
},
    {
        "name": "save_memory",
        "description": (
            "Save an important personal fact about the user to long-term memory. "
            "Call this silently whenever the user reveals something worth remembering: "
            "name, age, city, job, preferences, hobbies, relationships, projects, or future plans. "
            "Do NOT call for: weather, reminders, searches, or one-time commands. "
            "Do NOT announce that you are saving — just call it silently. "
            "Values must be in English regardless of the conversation language."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "category": {
                    "type": "STRING",
                    "description": (
                        "identity — name, age, birthday, city, job, language, nationality | "
                        "preferences — favorite food/color/music/film/game/sport, hobbies | "
                        "projects — active projects, goals, things being built | "
                        "relationships — friends, family, partner, colleagues | "
                        "wishes — future plans, things to buy, travel dreams | "
                        "notes — habits, schedule, anything else worth remembering"
                    )
                },
                "key":   {"type": "STRING", "description": "Short snake_case key (e.g. name, favorite_food, sister_name)"},
                "value": {"type": "STRING", "description": "Concise value in English (e.g. Fatih, pizza, older sister)"},
            },
            "required": ["category", "key", "value"]
        }
    },
]

BUILTIN_NAMES = {d['name'] for d in TOOL_DECLARATIONS} | NEW_NAMES | {'plugins', 'run_goal'}


class _PluginsChanged(Exception):
    """Raised to restart the Live session so new plugin tools are advertised."""


class MarkLive:

    def __init__(self, ui: MarkUI):
        self.ui             = ui
        from actions.gesture_mouse import GestureMouse
        from actions.camera_source import CAMERA
        self._gesture_source = CAMERA
        def gesture_status(status):
            self.ui.show_gesture_status(status)
            if str(status).startswith("OFF"):
                self._gesture_source.unsubscribe(self.gesture_mouse)
        self.gesture_mouse = GestureMouse(status=gesture_status)
        self._asst_name     = "MARK"
        self.session              = None
        self.audio_in_queue       = None
        self.out_queue            = None
        self._loop                = None
        self._is_speaking         = False
        self._speaking_lock       = threading.Lock()
        self._pending_vision       = None    # (img_bytes, mime_type, question, angle) to inject after tool response
        self._vision_cam_active    = False   # True if camera was opened for vision → auto-close after response
        self._vision_close_pending = False   # True after vision injected; next turn_complete closes camera
        self._vision_last_time     = 0.0     # monotonic time of last screen_process call (cooldown guard)
        self._vision_busy          = False   # True while a vision capture/inject cycle is in flight
        self._vision_last_digest   = None    # Digest of the last captured image for change detection
        self._interrupted          = False   # True while draining audio after user interrupt
        self._ask_after_interrupt  = False  # ask what happened after the cancelled turn ends
        self._cancel_requested     = False   # True when user wants current tool/thinking cancelled
        self._active_task_id       = None
        self._local_cancel_event   = threading.Event()
        self._local_command_lock   = threading.Lock()
        self._current_tool_task   = None    # asyncio.Task of the in-flight tool (if any)
        from core.screen_awareness import ScreenMonitor
        self.screen_monitor = ScreenMonitor(interval=float(os.environ.get("MARK_SCREEN_INTERVAL", "2")))
        self.ui.on_screen_monitor_toggle = self.screen_monitor.set_enabled
        self.ui.on_runtime_stop = self.stop
        self.ui.on_gesture_action = self._ui_gesture_action
        self.ui.on_text_command   = self._on_text_command
        self.ui.on_interrupt      = self.interrupt
        self.ui.on_call_action    = self._ui_call_action
        self.ui.announce_vision  = self.speak
        self._turn_done_event: asyncio.Event | None = None
        self._briefing_sent    = False          # morning briefing fires once per process
        self._sys_monitor      = SystemMonitor()  # persistent cooldown state
        self._proactive        = ProactiveEngine()
        self._last_user_speech = time.monotonic()  # updated on every user utterance
        self._session_log: list[str] = []          # conversation turns for end-of-session summary
        self._resume_handle = None
        self._live_key_id = None
        self._live_in = []
        self._live_out = []
        self._completed_calls = {}
        self._plugins_changed  = threading.Event()
        self._note_queue: asyncio.Queue | None = None
        self._live_input_ready = False
        self._live_disabled = False
        self._livekit_connected = False
        self._livekit_worker = None
        self.plugins = PluginManager(PluginContext(
            ui=ui, speak=lambda t: self.speak(t), call_tool=lambda n, a: self._dispatch_sync(n, a)))
        self.ui.plugin_manager = self.plugins
        self.plugins.on_change = self._plugins_changed.set

    async def connect_livekit(self) -> bool:
        """Connect to LiveKit server and start the agent worker.

        Returns:
            True if connection was successful
        """
        if self._livekit_connected and self._livekit_worker and self._livekit_worker._is_running:
            logger.info("Already connected to LiveKit")
            return True
        if self._livekit_worker is not None:
            await self.disconnect_livekit()

        if not LIVEKIT_AVAILABLE:
            logger.warning("LiveKit SDK not available")
            return False

        try:
            # Load LiveKit configuration
            livekit_cfg = _load_livekit_config()
            room_name = livekit_cfg.get("room_name", "mark-room")
            participant_name = livekit_cfg.get("participant_name", "mark-user")
            # Generate access token
            self._livekit_token = _generate_livekit_token(room_name, participant_name)

            # Create the LiveKit agent worker
            from core._llm_gateway import CloudLLMGateway

            # Initialize LLM gateway
            llm_gateway = CloudLLMGateway(config_path="core/prompt.txt")

            # Initialize STT engine
            from core._speech_stt import STTEngine
            stt_engine = getattr(self, "_livekit_stt_engine", None)
            if stt_engine is None:
                stt_engine = STTEngine(model_size="base", device="cpu", compute_type="int8", language=None)
                self._livekit_stt_engine = stt_engine

            # Initialize TTS engine
            from core._tts import PiperTTSEngine
            tts_engine = getattr(self, "_livekit_tts_engine", None)
            if tts_engine is None:
                tts_engine = PiperTTSEngine()
                self._livekit_tts_engine = tts_engine

            # Create the LiveKit agent worker
            from core._livekit_worker import LiveKitAgentWorker
            self._livekit_worker = LiveKitAgentWorker(
                markdown_adapter=self,
                llm_gateway=llm_gateway,
                stt_engine=stt_engine,
                tts_engine=tts_engine,
            )

            from livekit import rtc
            allowed = os.environ.get("LIVEKIT_REMOTE_IDENTITY", "").strip()
            if not allowed or allowed == participant_name:
                raise ValueError("Set LIVEKIT_REMOTE_IDENTITY to the authorized remote participant")
            self._livekit_worker.allowed_identity = allowed
            self._livekit_worker.desktop_preview_allowed = os.environ.get("MARK_LIVEKIT_DESKTOP_PREVIEW", "0") == "1"
            room = rtc.Room()
            try:
                await room.connect(livekit_cfg["url"], self._livekit_token)
                await self._livekit_worker.start(room, room.local_participant)
            except BaseException:
                await room.disconnect()
                raise
            self._livekit_connected = True
            return True

        except Exception as e:
            logger.error(f"Failed to connect to LiveKit: {e}", exc_info=True)
            self._livekit_connected = False
            return False

    async def disconnect_livekit(self) -> None:
        """Stop the worker and clear its connection state."""
        try:
            if self._livekit_worker is not None:
                await self._livekit_worker.stop()
        finally:
            self._livekit_worker = None
            self._livekit_connected = False

    def _ui_gesture_action(self, action: str) -> None:
        if not self._loop or not self._loop.is_running():
            self.ui.write_log("Gesture control is still starting.")
            return
        future = asyncio.run_coroutine_threadsafe(self._dispatch("gesture_mouse", {"action": action}), self._loop)
        def completed(result):
            try:
                self.ui.write_log(str(result.result()).splitlines()[0])
            except Exception as exc:
                self.ui.write_log(f"Gesture control error: {type(exc).__name__}")
        future.add_done_callback(completed)

    def _ui_call_action(self, action: str) -> None:
        r = NATIVE["calls"]({"action": action}, self.ui)
        self.ui.write_log(f"SYS: call {action} -> {r.splitlines()[0]}")
        if not r.startswith("[DONE]"):
            self.speak(f"The call {action} did not work. {r.splitlines()[1] if len(r.splitlines()) > 1 else ''}")

    def _on_text_command(self, text: str):
        # Typed commands use the configured text provider.
        if not self._loop or not self._loop.is_running():
            self.ui.write_log("ERR: MARK is still starting; send the command again in a moment.")
            return
        if not self._local_command_lock.acquire(blocking=False):
            self.ui.write_log("SYS: A typed command is running. Wait for the result or press Stop.")
            return
        threading.Thread(target=self._local_command, args=(text,), daemon=True).start()

    def _local_command(self, text: str) -> None:
        try:
            self._run_local_command(text)
        finally:
            self._local_command_lock.release()

    def _run_local_command(self, text: str) -> None:
        """Handle a typed command through the configured text provider."""
        from core import llm as _llm
        name = getattr(self.ui, "name", "MARK")
        tid = self.ui.task_start("run_goal", {"goal": text})
        self._active_task_id = tid
        self._local_cancel_event.clear()
        self._cancel_requested = False
        TASKS.start(tid, "run_goal", {"goal": text})
        def log_progress(message: str) -> None:
            self.ui.write_log(message)
            if message.startswith("[brain") and "->" in message:
                TASKS.checkpoint(tid, message[:140])

        conversation_id = None
        try:
            from core.agents.runtime import execute_turn
            from memory import db
            active = db.get_active_conversation()
            conversation_id = active["id"] if active else None
            report = execute_turn(text, dispatch=self._dispatch_sync,
                                  declarations=self._all_declarations,
                                  conversation_id=conversation_id,
                                  cancelled=self._local_cancel_event.is_set,
                                  log=log_progress,
                                  on_plan=lambda: TASKS.stage(tid, "PLANNING"))
        except Exception as e:
            report = f"RESULT: FAILED\nDONE: none\nNOT DONE: local brain crashed: {type(e).__name__}: {e}"
            self.ui.write_log(f"ERR: local brain crashed: {type(e).__name__}: {e}")
        TASKS.finish(tid, report, verification="PASS" if report.startswith("RESULT: SUCCESS") else "UNVERIFIED")
        self.ui.task_end(tid, report)
        self._active_task_id = None
        head = report.split("\nEXECUTION TRACE:")[0].strip()
        self.ui.write_log(f"{name}: {head[:900]}")
        from memory import db
        db.append_conversation_turn(text, head, channel="typed", conversation_id=conversation_id)
        cfg = _llm.load_config()
        if cfg["profile"] in {"offline", "low_resource"} and cfg.get("speak_local_replies"):
            try:
                from actions.local_voice import speak_local
                orb = getattr(self.ui, "orb", None)
                if orb:
                    orb.set_activity("local-speech", "speaking")
                try:
                    speak_local(head, self._local_cancel_event)
                finally:
                    if orb:
                        orb.set_activity("local-speech", None)
            except Exception as exc:
                self.ui.write_log(f"SYS: Speech output unavailable ({type(exc).__name__}).")

    def set_speaking(self, value: bool):
        with self._speaking_lock:
            self._is_speaking = value
        if getattr(self.ui, "orb", None):
            self.ui.orb.set_activity("live-speech", "speaking" if value else None)
        if value:
            self.ui.set_state("SPEAKING")
        elif not self.ui.muted:
            self.ui.set_state("LISTENING")

    def interrupt(self) -> None:
        """Stop MARK mid-speech: drain queued audio and open mic immediately."""
        self._interrupted = True
        self._cancel_requested = True
        self._local_cancel_event.set()
        worker = getattr(self, "_livekit_worker", None)
        if worker is not None and self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(worker.interrupt(), self._loop)
        if self._active_task_id is not None:
            TASKS.request_cancel(self._active_task_id)
        self._ask_after_interrupt = bool(self.session)
        # Cancel in-flight tool/thinking task if any
        t = self._current_tool_task
        if t is not None and not t.done():
            try:
                self._loop.call_soon_threadsafe(t.cancel)
            except Exception:
                pass
        self._current_tool_task = None
        # Clear vision busy flags so assistant isn't stuck "blind"
        self._vision_busy = False
        self._pending_vision = None
        q = self.audio_in_queue
        if q:
            drained = 0
            while True:
                try:
                    q.get_nowait()
                    drained += 1
                except Exception:
                    break
            if drained:
                print(f"[MARK] ✋ Interrupted — {drained} audio chunks discarded")
        self.set_speaking(False)
        if self._turn_done_event:
            self._turn_done_event.clear()
        self.ui.write_log("SYS: Interrupted — listening...")
        try:
            self.ui.set_state("LISTENING")
        except Exception:
            pass

    async def _ask_after_interrupt_prompt(self) -> None:
        """Ask the user why they interrupted, after the cancelled response is drained."""
        session = self.session
        if session is None:
            return
        try:
            await session.send_client_content(
                turns={"parts": [{
                    "text": "The user interrupted you. Ask exactly: 'Kya hua sir?'"
                }]},
                turn_complete=True,
            )
        except Exception as exc:
            print(f"[MARK] Could not ask after interrupt: {type(exc).__name__}: {exc}")

    def speak(self, text: str):
        if not self._loop or not self.session:
            return
        asyncio.run_coroutine_threadsafe(
            self.session.send_client_content(
                turns={"parts": [{"text": text}]},
                turn_complete=True
            ),
            self._loop
        )

    def speak_error(self, tool_name: str, error: Exception | str) -> None:
        short = str(error)[:120]
        self.ui.write_log(f"ERR: {tool_name} — {short}")
        self.speak(f"Sir, {tool_name} encountered an error. {short}")

    # ── vision ─────────────────────────────────────────────────────────────────
    async def _screen_process(self, args: dict, loop) -> str:
        """
        Look at the screen / camera.  Primary path answers INSIDE the tool call (Gemini vision, synchronous),
        so there is no fragile "image arrives on the next turn" step.  If that call fails (offline / quota) it
        falls back to injecting the picture into the live session.
        """
        angle     = str(args.get("angle", "screen")).lower().strip()
        user_text = str(args.get("text", "What do you see?")).strip()
        if angle not in {"screen", "camera"}:
            return "[FAILED] screen_process\nREASON: angle must be 'screen' or 'camera'"
        if not user_text:
            return "[FAILED] screen_process\nREASON: text must not be empty"
        try:
            if angle == "camera":
                img_b, mime_t = await loop.run_in_executor(None, _capture_camera)
                self.ui.show_camera_frame(img_b)
            else:
                img_b, mime_t = await loop.run_in_executor(
                    None,
                    lambda: _capture_screen_region(monitor=int(args.get("monitor", 1)), region=args.get("region")),
                )
            if not img_b:
                raise RuntimeError("capture returned an empty image")
        except Exception as e:
            return f"[FAILED] Could not capture the {angle}\nREASON: {type(e).__name__}: {e}"

        extra = ""
        if args.get("ocr"):
            extra += "LOCAL OCR:\n" + extract_ocr(img_b) + "\n"
        if angle == "screen":
            try:
                extra += await loop.run_in_executor(None, _screen_facts)
            except Exception:
                pass
        try:
            ans = await loop.run_in_executor(None, lambda: vision_answer(img_b, user_text, mime_t, extra))
            if ans:
                return (f"[DONE] {angle.capitalize()} analysed ({len(img_b):,} bytes).\n"
                        f"ANSWER: {ans}\n"
                        "RULE: Tell the user this answer in your own words, briefly. Do not call screen_process again for the same question.")
            print("[Vision] empty answer from vision model - using live injection")
        except Exception as e:
            print(f"[Vision] direct vision failed ({type(e).__name__}: {e}) - using live injection")

        # ---- fallback: hand the picture to the live session after this turn --------------------
        _now = time.monotonic()
        if self._vision_busy and (_now - self._vision_last_time) < 30.0:
            return "Vision is still processing the previous request. I will not call this again."
        self._vision_busy      = True
        self._vision_last_time = _now
        self._vision_cam_active = angle == "camera"
        if args.get("ocr"):
            user_text += f"\n\nLOCAL OCR:\n{extract_ocr(img_b)}"
        self._vision_last_digest = hashlib.sha256(img_b).hexdigest()[:12]
        self._pending_vision = (img_b, mime_t, user_text, angle)
        return (
            f"[VISION_ACTIVE] {angle.capitalize()} captured ({len(img_b):,} bytes). "
            f"Immediately say ONE short natural sentence in the user's own language, "
            f"telling them you are looking at their {angle} right now. "
            f"Do NOT describe or guess content - the actual image arrives in the NEXT message."
        )

    def _build_config(self) -> gtypes.LiveConnectConfig:
        from datetime import datetime

        # Load customization from config
        try:
            _cfg = json.loads(open(API_CONFIG_PATH, encoding="utf-8").read())
            self._asst_name = "MARK"
            _user_name = (_cfg.get("user_name") or "").strip()
        except Exception:
            self._asst_name = "MARK"
            _user_name = ""

        memory     = load_memory()
        _pl = self.plugins.describe()
        sys_extra  = f"\n\n[INSTALLED PLUGINS]\n{_pl}\n"
        mem_str    = format_memory_for_prompt(memory)
        sys_prompt = _load_system_prompt()

        now      = datetime.now()
        time_str = now.strftime("%A, %B %d, %Y — %I:%M %p")
        time_ctx = (
            f"[CURRENT DATE & TIME]\n"
            f"Right now it is: {time_str}\n"
            f"Use this to calculate exact times for reminders.\n\n"
        )

        # Identity injection — overrides any hardcoded name in prompt.txt
        _addr = (f"ADDRESS: Always call the user '{_user_name}'."
                 if _user_name
                 else "ADDRESS: When speaking Turkish → always say \"efendim\". "
                      "When speaking English → say \"sir\". Never mix languages.")
        identity_ctx = (
            f"[IDENTITY]\n"
            f"Your name is {self._asst_name}. "
            f"Always refer to yourself as {self._asst_name}.\n"
            f"{_addr}\n\n"
        )

        # Be transparent about the model that handles this live voice session.
        # The local text/code models are separate and may be used by tools/tasks.
        try:
            from core import llm as _llm_info
            _llm_cfg = _llm_info.load_config()
            _backend = str(_llm_cfg.get("backend", "multi_provider")).lower()
            if _backend == "gemini":
                _llm_route = f"Text/code tasks use Gemini ({_llm_cfg.get('gemini', {}).get('text_model', 'configured model')})."
            else:
                _llm_route = f"Configured text backend: {_backend}."
        except Exception:
            _llm_route = "The configured text model route could not be read."
        model_ctx = (
            "[MODEL TRANSPARENCY]\n"
            f"This live voice conversation is handled by Google Gemini ({_live_model()}).\n"
            f"{_llm_route}\n"
            "If asked which LLM you are running on, answer accurately and briefly. "
            "Distinguish this live voice model from the separate text/code task route; "
            "do not claim a fallback model handled this turn unless you know it did.\n\n"
        )

        parts = [time_ctx, identity_ctx, model_ctx]
        if mem_str and _llm_config.load_config().get("share_history_with_cloud", False):
            parts.append(mem_str)
        if not self._resume_handle and _llm_config.load_config().get("share_history_with_cloud", False):
            history = recent_messages(channel=None)
            if history:
                parts.append("Prior Gemini voice dialogue for reference only. "
                             "Continue from the conversation. Never repeat previously requested actions.\n"
                             + json.dumps(history, ensure_ascii=False))
        parts.append(sys_prompt + sys_extra)

        return gtypes.LiveConnectConfig(
            response_modalities=["AUDIO"],
            output_audio_transcription=gtypes.AudioTranscriptionConfig(),
            input_audio_transcription=gtypes.AudioTranscriptionConfig(),
            system_instruction="\n".join(parts),
            tools=[{"function_declarations": self._all_declarations()}],
            session_resumption=gtypes.SessionResumptionConfig(handle=self._resume_handle),
            context_window_compression=gtypes.ContextWindowCompressionConfig(sliding_window=gtypes.SlidingWindow()),
            speech_config=gtypes.SpeechConfig(
                voice_config=gtypes.VoiceConfig(
                    prebuilt_voice_config=gtypes.PrebuiltVoiceConfig(
                        voice_name="Charon"
                    )
                )
            ),
        )

    async def _execute_tool(self, fc) -> gtypes.FunctionResponse:
        name = fc.name
        args = dict(fc.args or {})

        declaration = next((d for d in self._all_declarations() if d["name"] == name), None)
        error = risk.validate_arguments(declaration, args)
        if error:
            return gtypes.FunctionResponse(id=fc.id, name=name, response={"result": "[FAILED] " + error})

        # If user already requested cancel, abort early.
        if self._cancel_requested:
            self._cancel_requested = False
            self._interrupted = False
            try:
                self.ui.task_end(name if name != "save_memory" else None, False)
            except Exception:
                pass
            self.ui.set_state("LISTENING")
            return gtypes.FunctionResponse(
                id=fc.id, name=name,
                response={"result": "[CANCELLED] User interrupted before execution."}
            )

        self._current_tool_task = asyncio.current_task()
        self.ui.set_state("THINKING")
        parent_id = self._active_task_id
        _tid = None if name == "save_memory" else self.ui.task_start(name, args)
        if _tid is not None:
            self._active_task_id = _tid
            TASKS.start(_tid, name, args)

        if name == "save_memory":
            category = args.get("category", "notes")
            key      = args.get("key", "")
            value    = args.get("value", "")
            from memory.db import validate_fact
            error = validate_fact(key, value)
            if not error:
                update_memory({category: {key: {"value": value}}})
            if not self.ui.muted:
                self.ui.set_state("LISTENING")
            return gtypes.FunctionResponse(
                id=fc.id, name=name,
                response={"result": f"[FAILED] {error}" if error else "ok",
                          "silent": not bool(error)}
            )

        loop   = asyncio.get_event_loop()
        result = _failed(f"{name} returned nothing", "no result was produced").to_llm()

        try:
            if (_llm_config.load_config().get("profile") == "offline" and name in
                {"web_search", "flight_finder", "weather_report", "weather_plugin", "news_plugin", "youtube_video",
                 "send_email", "send_message", "message_delivery", "whatsapp", "software_manager", "game_updater"}):
                raise ValueError("This tool needs network access. Offline profile is active.")
            needs_approval = risk.requires_confirmation(name, args)
            if self.plugins.has_tool(name) and name not in risk.RISK_MAP and name not in risk.READ_ONLY_PLUGINS:
                needs_approval = True
            if _tid is not None:
                TASKS.set_approval_required(_tid, needs_approval)
            if needs_approval:
                if _tid is not None:
                    TASKS.stage(_tid, "WAITING_FOR_APPROVAL")
                approved = await self.ui.confirm_action(name, args)
                if not approved or self._cancel_requested:
                    raise asyncio.CancelledError()
                args["confirmed"] = True
                if _tid is not None:
                    TASKS.stage(_tid, "RUNNING")

            from core import interaction_verify as _interaction_verify
            _typing_action = name == "computer_settings" and str(args.get("action", "")).lower() in {"type_text", "write_on_screen", "type", "write"}
            _before_interaction = await asyncio.to_thread(_interaction_verify.text_snapshot if _typing_action else _interaction_verify.snapshot) if _interaction_verify.supports(name, args) else None

            if name == "gesture_mouse":
                action = str(args.get("action", "")).lower()
                if action == "enable":
                    self._enable_gesture_mouse()
                    result = "[UNVERIFIED] Gesture mouse starting; waiting for camera frames."
                elif action == "disable":
                    self.gesture_mouse.disable()
                    result = "[DONE] Gesture mouse disabled."
                elif action in ("drag_on", "drag_off"):
                    with self.gesture_mouse._lock:
                        self.gesture_mouse._release()
                        self.gesture_mouse._pinch = None
                        self.gesture_mouse.drag_mode = action == "drag_on"
                    result = "[DONE] Gesture drag mode " + ("enabled." if action == "drag_on" else "disabled.")
                elif action in ("sensitivity_up", "sensitivity_down"):
                    with self.gesture_mouse._lock:
                        cfg = self.gesture_mouse.config
                        cfg.sensitivity = max(0.2, min(3.0, cfg.sensitivity + (0.2 if action == "sensitivity_up" else -0.2)))
                        value = cfg.sensitivity
                    result = f"[DONE] Gesture sensitivity set to {value:.1f}."
                elif action == "status":
                    result = "[DONE] Gesture mouse " + ("enabled." if self.gesture_mouse._enabled else "disabled.")
                else:
                    result = "[FAILED] Unknown gesture mouse action."
            elif name in NATIVE:
                result = await loop.run_in_executor(None, lambda: NATIVE[name](args, self.ui))

            elif name == "run_goal":
                _goal = str(args.get("goal", "")).strip()
                if not _goal:
                    result = "[FAILED] Goal was empty\nREASON: provide a concrete objective"
                else:
                    _max_steps = max(1, min(int(args.get("max_steps") or _brain.MAX_STEPS_DEFAULT), 30))
                    _recent = "\n".join(self._session_log[-6:])
                    _goal_context = format_memory_for_prompt(load_memory())[:1500] if _llm_config.load_config().get("share_history_with_cloud") else ""
                    if _recent:
                        _goal_context += f"\n\nRECENT SESSION:\n{_recent[:1200]}"
                    self.ui.write_log(f"BRAIN: goal = {_goal[:100]} (max_steps={_max_steps})")
                    def _goal_checkpoint(tool_name: str) -> str:
                        try:
                            image, _ = _capture_screen_region()
                            ocr = extract_ocr(image)
                            if ocr.startswith("[OCR_"):
                                return f"{tool_name}: screen captured; OCR unavailable"
                            return f"{tool_name}: screen captured; OCR={ocr[:120]}"
                        except Exception as exc:
                            return f"{tool_name}: capture failed ({type(exc).__name__}: {exc})"
                    result = await loop.run_in_executor(None, lambda: _brain.run_goal(
                        goal=_goal,
                        api_key=_get_api_key(),
                        declarations=self._all_declarations(),
                        dispatch=self._dispatch_sync,
                        log=self.ui.write_log,
                        max_steps=_max_steps,
                        context=_goal_context,
                        cancelled=self._local_cancel_event.is_set,
                        visual_checkpoints=bool(args.get("visual_checkpoints", False)),
                        checkpoint=_goal_checkpoint,
                    ))

            elif name == "plugins":
                act = str(args.get("action", "list")).lower()
                if act == "list":
                    result = "[DONE] Plugins:\n" + self.plugins.describe()
                elif act == "discover":
                    query = str(args.get("query", "")).strip().lower()
                    matches = [d["name"] for d in self._all_declarations()
                               if not query or query in (d["name"] + " " + d.get("description", "")).lower()]
                    result = "[DONE] Matching tool names: " + ", ".join(matches)
                elif act == "reload":
                    await asyncio.to_thread(self.plugins.load_all, BUILTIN_NAMES)
                    self._plugins_changed.set()
                    result = "[DONE] Plugins reloaded:\n" + self.plugins.describe()
                elif act == "create":
                    result = "[FAILED] Install reviewed Python extensions in the plugins directory."
                elif act == "remove":
                    ok, msg = await asyncio.to_thread(self.plugins.remove, str(args.get("name", "")))
                    result = ("[DONE] " if ok else "[FAILED] Plugin not removed\nREASON: ") + msg
                else:
                    result = f"[FAILED] Unknown plugins action '{act}'\nREASON: valid: list discover reload create remove"

            elif name == "excel" and self.plugins.has_tool(name):
                excel_args = dict(args)
                if not excel_args.get("path") and self.ui.current_file:
                    excel_args["path"] = self.ui.current_file
                result = await loop.run_in_executor(None, lambda: self.plugins.run(name, excel_args))

            elif self.plugins.has_tool(name):
                result = await loop.run_in_executor(None, lambda: self.plugins.run(name, args))

            elif name == "open_app":
                r = await loop.run_in_executor(None, lambda: open_app(parameters=args, response=None, player=self.ui))
                result = coerce_side_effect("open_app", r)

            elif name == "weather_report":
                r = await loop.run_in_executor(None, lambda: weather_action(parameters=args, player=self.ui))
                result = r or "[FAILED] weather returned nothing"

            elif name == "browser_control":
                r = await loop.run_in_executor(None, lambda: browser_control(parameters=args, player=self.ui))
                result = coerce_side_effect("browser_control", r)

            elif name == "send_message":
                _plat = str(args.get("platform", "whatsapp")).lower()
                if "whatsapp" in _plat or _plat in ("wp", "wapp"):
                    result = await loop.run_in_executor(None, lambda: NATIVE["whatsapp"](
                        {"action": "send", "contact": args.get("receiver", ""), "message": args.get("message_text", "")}, self.ui))
                else:
                    r = await loop.run_in_executor(None, lambda: send_message(parameters=args, response=None, player=self.ui, session_memory=None))
                    result = coerce_side_effect(name, r)

            elif name == "reminder":
                r = await loop.run_in_executor(None, lambda: reminder(parameters=args, response=None, player=self.ui))
                result = coerce_side_effect("reminder", r)

            elif name == "youtube_video":
                r = await loop.run_in_executor(None, lambda: youtube_video(parameters=args, response=None, player=self.ui))
                result = coerce_side_effect("youtube_video", r)

            elif name == "screen_process":
                result = await self._screen_process(args, loop)

            elif name == "close_camera":
                self.ui.stop_camera_stream()
                result = "Camera closed."

            elif name == "computer_settings":
                r = await loop.run_in_executor(None, lambda: computer_settings(parameters=args, response=None, player=self.ui))
                result = coerce_side_effect("computer_settings", r)

            elif name == "desktop_control":
                r = await loop.run_in_executor(None, lambda: desktop_control(parameters=args, player=self.ui))
                result = coerce_side_effect("desktop_control", r)

            elif name == "code_helper":
                r = await loop.run_in_executor(None, lambda: code_helper(parameters=args, player=self.ui, speak=self.speak))
                result = r or "[UNVERIFIED] The tool returned no output; effect unknown."

            elif name == "dev_agent":
                r = await loop.run_in_executor(None, lambda: dev_agent(parameters=args, player=self.ui, speak=self.speak))
                result = r or "[UNVERIFIED] The tool returned no output; effect unknown."

            elif name == "web_search":
                r = await loop.run_in_executor(None, lambda: web_search_action(parameters=args, player=self.ui))
                result = r or "[UNVERIFIED] The tool returned no output; effect unknown."
                # Mirror results to the on-screen content panel
                _mode = args.get("mode", "search")
                if r and not r.startswith("No results") and not r.startswith("Search failed"):
                    _query = args.get("query") or ", ".join(args.get("items", []))
                    _label = f"{_mode.upper()} — {_query[:38]}" if _query else _mode.upper()
                    self.ui.show_content(_label, r)
            elif name == "file_processor":
                if not args.get("file_path") and self.ui.current_file:
                    args["file_path"] = self.ui.current_file
                ext = Path(str(args.get("file_path") or "")).suffix.lower()
                if ext in {".xlsx", ".xlsm", ".xls", ".ods"}:
                    result = "[FAILED] Use the dedicated excel tool for workbook inspection, edits, formulas, summaries, and charts."
                else:
                    r = await loop.run_in_executor(
                        None,
                        lambda: file_processor(parameters=args, player=self.ui, speak=self.speak)
                    )
                    result = r or "[UNVERIFIED] The tool returned no output; effect unknown."

            elif name == "computer_control":
                r = await loop.run_in_executor(None, lambda: computer_control(parameters=args, player=self.ui))
                result = coerce_side_effect("computer_control", r)

            elif name == "game_updater":
                r = await loop.run_in_executor(None, lambda: game_updater(parameters=args, player=self.ui, speak=self.speak))
                result = coerce_side_effect("game_updater", r)

            elif name == "flight_finder":
                r = await loop.run_in_executor(None, lambda: flight_finder(parameters=args, player=self.ui))
                result = r or "[UNVERIFIED] The tool returned no output; effect unknown."

            elif name == "system_status":
                r = await loop.run_in_executor(None, get_system_status)
                result = str(r)

            elif name == "manage_monitor":
                action = args.get("action", "").lower().strip()
                topic  = args.get("topic", "").strip()
                if action == "add" and topic:
                    result = await asyncio.to_thread(add_monitor, topic)
                elif action == "remove" and topic:
                    result = await asyncio.to_thread(remove_monitor, topic)
                elif action == "list":
                    topics = await asyncio.to_thread(list_monitors)
                    result = ("Monitoring: " + ", ".join(topics)) if topics else "No topics are being monitored."
                else:
                    result = "Specify action (add/remove/list) and a topic."

            elif name == "shutdown_mark":
                self.ui.write_log("SYS: Shutdown requested.")
                async def _do_shutdown():
                    await self._save_session_summary()
                    if self.session:
                        try:
                            await self.session.send_client_content(
                                turns={"parts": [{"text": "Say a brief natural goodbye to the user."}]},
                                turn_complete=True,
                            )
                        except Exception:
                            pass
                    await asyncio.sleep(1.5)
                    self.ui._post(self.ui.close)
                asyncio.create_task(_do_shutdown())

            else:
                result = _failed(f"Unknown tool {name}", "no such tool is installed (check plugins/ or the tool name)").to_llm()

        except asyncio.CancelledError:
            result = "[CANCELLED] User interrupted during execution."
            self._cancel_requested = False
            self._current_tool_task = None

        except Exception as e:
            result = _failed(f"{name} crashed", f"{type(e).__name__}: {e}").to_llm()
            traceback.print_exc()
            self.speak_error(name, e)

        if not self.ui.muted:
            self.ui.set_state("LISTENING")

        if "_before_interaction" in locals() and _interaction_verify.supports(name, args):
            verifier = _interaction_verify.verify_text if _typing_action else _interaction_verify.verify
            result = await asyncio.to_thread(verifier, _before_interaction, args, str(result))

        if _tid is not None:
            TASKS.stage(_tid, "VERIFYING")
        if name in {"open_app", "file_controller"} and str(result).startswith(("[DONE]", "[UNVERIFIED]")):
            from core.action_confirmation import confirm_result
            result = await asyncio.to_thread(confirm_result, name, args, str(result), self.screen_monitor, _tid)
        verification = await self._report_verification(name, str(result))
        if _tid is not None:
            TASKS.finish(_tid, str(result), verification=verification)
            self.ui.task_end(_tid, str(result))
            self._active_task_id = parent_id
        return gtypes.FunctionResponse(
            id=fc.id, name=name,
            response={"result": result}
        )

    async def _report_verification(self, name: str, result: str) -> str:
        """Turn a tool's honest status into a visible PASS / FAIL / UNVERIFIED card.

        DONE        -> PASS    (the effect was checked)
        FAILED      -> FAIL    (checked and did NOT happen)
        UNVERIFIED  -> UNVERIFIED (ran, effect could not be confirmed)
        """
        status, detail, evidence = "UNVERIFIED", "", ""
        lines = [l for l in str(result).splitlines() if l.strip()]
        for st in ("DONE", "FAILED", "UNVERIFIED"):
            if str(result).lstrip().startswith(f"[{st}]"):
                status = {"DONE": "PASS", "FAILED": "FAIL", "UNVERIFIED": "UNVERIFIED"}[st]
                first = lines[0] if lines else ""
                detail = (first[first.find(f"[{st}]") + len(st) + 2:].strip() if st in first else first).strip()
                break
        for l in lines:
            if l.startswith("EVIDENCE:"):
                evidence = l[len("EVIDENCE:"):].strip()
                break
            if l.startswith("REASON:") and not detail:
                detail = l[len("REASON:"):].strip()
        if not detail and lines:
            detail = lines[0][:200]
        image = None  # Do not capture private screen contents for routine verification.
        try:
            self.ui.show_verification(name, status, detail[:300], evidence[:500], image)
        except Exception as e:
            print(f"[Verify] {e}")
        return status

    async def _send_realtime(self):
        """Forward microphone PCM chunks to Gemini Live as an audio Blob."""
        while True:
            assert self.out_queue is not None
            msg = await self.out_queue.get()

            # Ignore stale queued audio if the Live session is reconnecting.
            session = self.session
            if session is None:
                continue

            try:
                audio_blob = gtypes.Blob(
                    data=msg["data"],
                    mime_type=msg["mime_type"],
                )
                await session.send_realtime_input(audio=audio_blob)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(f"[MARK] Audio send failed: {type(exc).__name__}: {exc}")
                raise

    async def _listen_audio(self):
        loop = asyncio.get_event_loop()

        def enqueue_audio(data: bytes) -> None:
            if self.out_queue is None:
                return
            try:
                self.out_queue.put_nowait(
                    {"data": data, "mime_type": f"audio/pcm;rate={SEND_SAMPLE_RATE}"}
                )
            except asyncio.QueueFull:
                print("[MARK] Audio queue full; dropping microphone chunk.")

        def callback(indata, frames, time_info, status):
            del frames, time_info, status
            with self._speaking_lock:
                mark_speaking = self._is_speaking
            if not mark_speaking:
                try:
                    if self.ui.muted:
                        self.ui.set_level(0.0)
                    else:
                        rms = float(np.sqrt(np.mean(np.square(indata.astype(np.float32))))) / 32768.0
                        self.ui.set_level(min(1.0, rms * 9.0))
                except Exception:
                    pass
            if not mark_speaking and not self.ui.muted:
                data = indata.tobytes()
                loop.call_soon_threadsafe(
                    enqueue_audio,
                    data,
                )

        try:
            while True:
                while self.ui.muted:
                    await asyncio.sleep(0.1)
                with sd.InputStream(
                    samplerate=SEND_SAMPLE_RATE,
                    channels=CHANNELS,
                    dtype="int16",
                    blocksize=CHUNK_SIZE,
                    callback=callback,
                ):
                    if getattr(self.ui, "orb", None):
                        self.ui.orb.set_activity("live-mic", "listening")
                    try:
                        while not self.ui.muted:
                            await asyncio.sleep(0.1)
                    finally:
                        if getattr(self.ui, "orb", None):
                            self.ui.orb.set_activity("live-mic", None)
        except Exception as e:
            print(f"[MARK] ❌ Mic: {e}")
            raise

    async def _receive_audio(self):
        out_buf, in_buf = self._live_out, self._live_in

        try:
            while True:
                assert self.session is not None
                async for response in self.session.receive():

                    update = getattr(response, "session_resumption_update", None)
                    if update is not None:
                        self._resume_handle = update.new_handle if update.resumable else None
                    if response.data:
                        if self._interrupted:
                            pass  # discard: interrupted
                        else:
                            if self._turn_done_event and self._turn_done_event.is_set():
                                self._turn_done_event.clear()
                            # Split into ~50 ms chunks so interrupt() stops audio within 50 ms
                            # (24000 Hz × 2 bytes/sample × 0.05 s = 2400 bytes per slice)
                            _audio_data = response.data
                            _SLICE = 2400
                            for _i in range(0, len(_audio_data), _SLICE):
                                await self.audio_in_queue.put(_audio_data[_i : _i + _SLICE])  # type: ignore

                    if response.server_content:
                        sc = response.server_content

                        if sc.output_transcription and sc.output_transcription.text:
                            txt = _clean_transcript(sc.output_transcription.text)
                            if txt and txt != (out_buf[-1] if out_buf else ""):
                                out_buf.append(txt)

                        if sc.input_transcription and sc.input_transcription.text:
                            txt = _clean_transcript(sc.input_transcription.text)
                            if txt:
                                in_buf.append(txt)
                                self._last_user_speech = time.monotonic()

                        if sc.turn_complete:
                            if self._turn_done_event:
                                self._turn_done_event.set()

                            # If this turn_complete ends an interrupted response, clear the
                            # flag and skip all further processing for that turn.
                            if self._interrupted:
                                self._interrupted = False
                                self._cancel_requested = False
                                self._current_tool_task = None
                                self._vision_busy = False
                                self._pending_vision = None
                                in_buf.clear()
                                out_buf.clear()
                                if self._ask_after_interrupt:
                                    self._ask_after_interrupt = False
                                    asyncio.create_task(self._ask_after_interrupt_prompt())
                                continue

                            full_in = " ".join(in_buf).strip()
                            if full_in:
                                self.ui.write_log(f"You: {full_in}")
                                self._session_log.append(f"User: {full_in}")
                            in_buf.clear()

                            full_out = " ".join(out_buf).strip()
                            if full_out:
                                self.ui.write_log(f"{self._asst_name}: {full_out}")
                                self._session_log.append(f"{self._asst_name}: {full_out}")
                            del self._session_log[:-40]
                            if full_in:
                                assistant_text = full_out or "[No response transcript]"
                                await asyncio.to_thread(
                                    append_conversation_turn, full_in, assistant_text
                                )
                            out_buf.clear()

                            # Vision injection: model finished tool-response turn → now send the image
                            if self._pending_vision and self.session:
                                import base64 as _b64
                                img_b, mime_t, question = self._pending_vision[:3]
                                self._pending_vision = None
                                b64 = _b64.b64encode(img_b).decode("ascii")
                                await self.session.send_client_content(  # type: ignore
                                    turns={"parts": [  # type: ignore
                                        {"inline_data": {"mime_type": mime_t, "data": b64}},
                                        {"text": question},
                                    ]},
                                    turn_complete=True,
                                )
                                # Mark next turn_complete behaviour depending on angle
                                if self._vision_cam_active:
                                    # Camera: keep busy until MARK finishes speaking the answer
                                    self._vision_cam_active    = False
                                    self._vision_close_pending = True
                                else:
                                    # Screen-only: no camera to close; release busy flag now
                                    self._vision_busy = False
                            elif self._vision_close_pending:
                                # This turn_complete IS the vision answer — close camera + release busy flag
                                self._vision_close_pending = False
                                self._vision_busy = False

                    if response.tool_call:
                        fn_responses = []
                        for fc in (response.tool_call.function_calls or []):
                            call_id = getattr(fc, "id", None)
                            if call_id and call_id in self._completed_calls:
                                fr = self._completed_calls[call_id]
                            else:
                                if call_id:
                                    self._completed_calls[call_id] = gtypes.FunctionResponse(
                                        id=call_id, name=fc.name, response={"result": "[UNVERIFIED] Prior execution interrupted. Check the result."})
                                fr = await self._execute_tool(fc)
                                if call_id:
                                    self._completed_calls[call_id] = fr
                                    if len(self._completed_calls) > 256:
                                        del self._completed_calls[next(iter(self._completed_calls))]
                            fn_responses.append(fr)
                        # Reset cancel/interrupt bookkeeping after tool completion
                        self._current_tool_task = None
                        self._cancel_requested = False
                        if self.session:
                            await self.session.send_tool_response(
                                function_responses=fn_responses
                            )
        except Exception as e:
            if not _is_live_inactivity_error(e):
                print(f"[MARK] Receive error: {type(e).__name__}: {e}")
                traceback.print_exc()
            raise

    async def _play_audio(self):

        stream = sd.RawOutputStream(
            samplerate=RECEIVE_SAMPLE_RATE,
            channels=CHANNELS,
            dtype="int16",
            blocksize=CHUNK_SIZE,
        )
        stream.start()

        try:
            while True:
                try:
                    chunk = await asyncio.wait_for(
                        self.audio_in_queue.get(),  # type: ignore
                        timeout=0.1
                    )
                except asyncio.TimeoutError:
                    if (
                        self._turn_done_event
                        and self._turn_done_event.is_set()
                        and self.audio_in_queue.empty()  # type: ignore
                    ):
                        self.set_speaking(False)
                        self._turn_done_event.clear()
                    continue

                self.set_speaking(True)

                # Batch all immediately-available chunks into one write to reduce
                # thread-pool round-trips (was one asyncio.to_thread per 50ms slice).
                # Cap at ~200 ms so interrupt() still stops audio within ~200 ms.
                batch = bytearray(chunk)
                while len(batch) < 9600:   # 9600 bytes ≈ 200 ms at 24 kHz / 16-bit mono
                    try:
                        batch.extend(self.audio_in_queue.get_nowait())  # type: ignore
                    except asyncio.QueueEmpty:
                        break

                try:
                    pcm = np.frombuffer(bytes(batch[: len(batch) // 2 * 2]), dtype=np.int16).astype(np.float32)
                    if pcm.size:
                        self.ui.set_level(min(1.0, float(np.sqrt(np.mean(pcm * pcm))) / 32768.0 * 6.0))
                except Exception:
                    pass
                try:
                    await asyncio.to_thread(stream.write, bytes(batch))
                except (RuntimeError, asyncio.CancelledError):
                    break   # executor shutting down — exit cleanly
        except Exception as e:
            print(f"[MARK] ❌ Play: {e}")
            raise
        finally:
            self.set_speaking(False)
            stream.stop()
            stream.close()

    # ── Morning briefing ────────────────────────────────────────────────────────

    async def _send_startup_briefing(self) -> None:
        """
        Startup greeting only (no news).
        Warm welcome + time + optional last-session mention; no tools, no headlines.
        """
        memory = load_memory() if _llm_config.load_config().get("share_history_with_cloud") else {}
        identity = memory.get("identity", {})

        def _val(k: str) -> str:
            e = identity.get(k, {})
            return (e.get("value", "") if isinstance(e, dict) else str(e)).strip()

        lang = _val("language")
        name = _val("name")
        time_str = datetime.now().strftime("%H:%M")

        await asyncio.sleep(0.3)
        if not self.session:
            return

        lang_clause = f" Respond in {lang}." if lang else ""
        name_clause = f" Address the user as {name}." if name else ""

        last = await asyncio.to_thread(pop_last_session) if _llm_config.load_config().get("share_history_with_cloud") else None
        session_clause = ""
        if last:
            try:
                _delta = (datetime.now() - datetime.strptime(last["date"], "%Y-%m-%d")).days
                _when  = "earlier today" if _delta == 0 else ("yesterday" if _delta == 1 else f"{_delta} days ago")
            except Exception:
                _when = "last time"
            session_clause = (
                f" Also briefly and naturally mention that {_when}: {last['summary']}"
            )

        p1 = (
            f"Greet the user warmly and mention it is {time_str}.{session_clause} "
            f"Keep it to 2 short sentences max. Do not mention news, headlines, or that you are fetching anything. "
            f"Do not call any tools.{lang_clause}{name_clause}"
        )

        if self._turn_done_event:
            self._turn_done_event.clear()

        await self.session.send_client_content(
            turns={"parts": [{"text": p1}]},
            turn_complete=True,
        )
        self.ui.write_log("SYS: Startup greeting sent.")

    async def _save_session_summary(self) -> None:
        """Summarise the current session in 1-2 sentences and save to long_term.json."""
        log = self._session_log
        if len(log) < 3:          # need at least one exchange to be worth saving
            return
        self._session_log = []    # reset immediately so the next session starts clean

        memory = load_memory()
        lang_entry = memory.get("identity", {}).get("language", {})
        lang = (lang_entry.get("value", "") if isinstance(lang_entry, dict) else str(lang_entry)).strip()
        lang = lang or "English"

        convo = "\n".join(log[-40:])   # cap at last 40 turns to stay within token budget
        prompt = (
            f"Summarize this conversation in 1-2 sentences in {lang}. "
            "Focus on what the user accomplished or discussed. "
            "Output ONLY the summary text, nothing else:\n\n" + convo
        )
        try:
            from core import llm as _llm
            cfg = _llm.load_config()
            memory_model = cfg.get("gemini", {}).get("memory_model", "")
            if cfg.get("backend") == "gemini" and memory_model:
                try:
                    resp = await asyncio.to_thread(_llm._run_provider, "gemini", _llm._gemini_generate_model, memory_model, prompt, cfg.get("gemini", {}))
                except Exception:
                    print("[Memory] Gemini summary failed; trying configured local/cloud fallback.")
                    self.ui.write_log("SYS: Gemini memory summary failed.")
                    return
            else:
                resp = await asyncio.to_thread(_llm.Client().models.generate_content,
                                               model="role:fast", contents=prompt)
            summary = (resp.text or "").strip()
            if summary:
                save_session_summary(summary, lang)
        except Exception as e:
            print(f"[Memory] ⚠️ Session summary failed: {e}")

    # ── System monitor ──────────────────────────────────────────────────────────

    async def _run_system_monitor(self) -> None:
        """Background task: voice alerts when metrics exceed thresholds."""
        while True:
            await asyncio.sleep(10)
            alert = await asyncio.to_thread(self._sys_monitor.check)
            if not alert or not self.session:
                continue
            # Don't interrupt an active conversation
            with self._speaking_lock:
                speaking = self._is_speaking
            if speaking or (time.monotonic() - self._last_user_speech) < 10:
                continue
            try:
                await self.session.send_client_content(
                    turns={"parts": [{"text": alert}]},
                    turn_complete=True,
                )
            except Exception as e:
                print(f"[Monitor] ⚠️ Could not send alert: {e}")

    # ── Background monitor ──────────────────────────────────────────────────────

    async def _run_background_monitor(self) -> None:
        """Check user-configured topics once per day; speak alerts when new headlines appear."""
        await asyncio.sleep(300)          # wait 5 min after startup before first check
        while True:
            if self.session:
                # Don't interrupt if user spoke recently or MARK is mid-sentence
                with self._speaking_lock:
                    speaking = self._is_speaking
                recent_speech = (time.monotonic() - self._last_user_speech) < 30
                if not speaking and not recent_speech:
                    try:
                        alerts = await asyncio.to_thread(monitor_check_all)
                        memory = load_memory()
                        lang_e = memory.get("identity", {}).get("language", {})
                        lang   = (lang_e.get("value", "") if isinstance(lang_e, dict) else str(lang_e)).strip() or "English"
                        for alert in alerts:
                            msg = (
                                f"{alert}\n\n"
                                f"Inform the user about this development naturally in {lang}. "
                                "One brief sentence only."
                            )
                            await self.session.send_client_content(
                                turns={"parts": [{"text": msg}]},
                                turn_complete=True,
                            )
                            self.ui.write_log(f"SYS: Monitor alert sent.")
                            await asyncio.sleep(6)   # gap between consecutive alerts
                    except Exception as e:
                        print(f"[Monitor] ⚠️ Background check error: {e}")
            await asyncio.sleep(1800)     # check every 30 minutes

    # ── Proactive mode ──────────────────────────────────────────────────────────

    async def _run_proactive_mode(self) -> None:
        """
        Background task: periodically checks if the user has been silent long enough,
        then hands time + memory context to Gemini so it can decide what (if anything)
        to say proactively. No hardcoded rules — Gemini makes the call.
        """
        while True:
            await asyncio.sleep(60)   # evaluate once per minute

            if not self.session:
                continue

            with self._speaking_lock:
                speaking = self._is_speaking
            if speaking:
                continue

            if not self._proactive.should_trigger(self._last_user_speech):
                continue

            self._proactive.mark_triggered()

            try:
                memory       = await asyncio.to_thread(load_memory)
                monitors     = await asyncio.to_thread(list_monitors)
                recent_turns = self._session_log[-8:] if self._session_log else []
                prompt = self._proactive.build_prompt(
                    memory       = memory,
                    monitors     = monitors or None,
                    recent_turns = recent_turns or None,
                )
                await self.session.send_client_content(
                    turns={"parts": [{"text": prompt}]},
                    turn_complete=True,
                )
                self.ui.write_log("SYS: Proactive check-in.")
            except Exception as e:
                print(f"[Proactive] ⚠️ {e}")

    # ── plugin hot-reload + notification relay ─────────────────────────────────

    def _all_declarations(self) -> list[dict]:
        declarations = TOOL_DECLARATIONS + NEW_DECLARATIONS + self.plugins.declarations()
        if os.environ.get("MARK_DEBUG_DECLARATIONS") == "1":
            for index, declaration in enumerate(declarations):
                print(f"\nFunction #{index}:")
                print(json.dumps(declaration, indent=2, ensure_ascii=False))
        return declarations

    def _push_hud_counts(self) -> None:
        """Publish real tool / plugin counters so the HUD never shows a guess."""
        try:
            decls = self._all_declarations()
            self.ui.set_state_info(
                tools_total=len(decls),
                plugins_ok=len(self.plugins.loaded),
                plugins_bad=len(self.plugins.errors),
                session="live" if self.session else "offline",
            )
        except Exception as e:
            print(f"[HUD] counts: {e}")

    def _dispatch_sync(self, name: str, args: dict) -> str:
        """Run any tool from a worker thread (used by the brain and by plugins)."""
        if not self._loop or not self._loop.is_running():
            return "[FAILED] MARK loop is not running yet"
        fut = asyncio.run_coroutine_threadsafe(self._dispatch(name, args), self._loop)
        return fut.result(timeout=900)

    async def _dispatch(self, name: str, args: dict) -> str:
        from types import SimpleNamespace
        resp = await self._execute_tool(SimpleNamespace(name=name, args=args, id=f"int-{time.time_ns()}"))
        self._current_tool_task = None
        self._cancel_requested = False
        return str((resp.response or {}).get("result", ""))

    async def _watch_plugins(self) -> None:
        while True:
            await asyncio.sleep(1.0)
            if self._plugins_changed.is_set():
                self._plugins_changed.clear()
                self.ui.write_log("SYS: Plugins changed - reloading tools...")
                raise _PluginsChanged()

    def _on_notification(self, note: dict) -> None:
        """Called from the watcher thread."""
        policy = self.plugins.notification_policy(note)
        if policy == "dnd":
            return
        try:                                       # armed call answering (off unless the user armed it)
            from actions.call_agent import AGENT as _CALL_AGENT
            _CALL_AGENT.on_notification(note, self.ui)
        except Exception as e:
            print(f"[call_agent] {type(e).__name__}: {e}")
        note = dict(note)
        note["_mark_notification_policy"] = policy
        if self._loop and self._note_queue is not None:
            self._loop.call_soon_threadsafe(self._note_queue.put_nowait, note)

    async def _notification_pump(self) -> None:
        assert self._note_queue is not None
        while True:
            first = await self._note_queue.get()
            batch = [first]
            await asyncio.sleep(1.2)
            while not self._note_queue.empty():
                batch.append(self._note_queue.get_nowait())
            for n in batch:
                self.ui.write_log(f"NOTIF: {_describe_note(n)}")
                if hasattr(self.ui, "add_notification"):
                    self.ui.add_notification(n)
                await asyncio.to_thread(self.plugins.dispatch_notification, n)
            if (
                not NOTIF_WATCHER.announce
                or not self.session
                or not self._live_input_ready
            ):
                continue
            spoken = [n for n in batch if n.get("_mark_notification_policy") != "silent"]
            if not spoken:
                continue
            lines = "\n".join(_describe_note(n) for n in spoken[:5])
            calls_in = any(n["is_call"] for n in spoken)
            ask = (" One of these is an incoming call: ask whether to answer or decline it." if calls_in else "")
            try:
                await self.session.send_client_content(
                    turns={"parts": [{"text": (
                        "[NOTIFICATION] New Windows notification(s):\n" + lines +
                        "\nTell the user in ONE short sentence which app and who it is from and what it says."
                        " Do not call any tool unless the user asks." + ask)}]},
                    turn_complete=True)
            except Exception as e:
                print(f"[Notify] could not announce: {e}")

    # ── main loop ───────────────────────────────────────────────────────────

    def _enable_gesture_mouse(self):
        self.gesture_mouse.enable()

        def on_gesture_error(error):
            del error
            self.gesture_mouse.disable("OFF: CAMERA UNAVAILABLE")

        self._gesture_source.subscribe(self.gesture_mouse, self.gesture_mouse.submit,
            on_gesture_error)

    def _start_default_camera_features(self):
        self.ui._post(self.ui.start_camera_stream)
        try:
            self._enable_gesture_mouse()
        except Exception as exc:
            self.ui.show_gesture_status("OFF: DEPENDENCY UNAVAILABLE")
            self.ui.write_log(f"Gesture mouse unavailable: {exc}")

    async def run(self):
        self._run_task = asyncio.current_task()
        startup = asyncio.create_task(asyncio.to_thread(self._start_default_camera_features))
        try:
            await self._serve()
        finally:
            await startup
            self._local_cancel_event.set()
            self.gesture_mouse.disable()
            self.screen_monitor.stop()
            self.plugins.stop()
            NOTIF_WATCHER.stop()
            if self._on_notification in NOTIF_WATCHER.listeners:
                NOTIF_WATCHER.listeners.remove(self._on_notification)
            _llm_config.providers.close()

    def stop(self):
        self.gesture_mouse.disable()
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._run_task.cancel)

    async def _serve(self):
        self._loop = asyncio.get_event_loop()
        self.screen_monitor.start()
        self.ui.write_log("SYS: Local screen monitoring ON; Gemini Live receives audio only.")

        self._note_queue = asyncio.Queue()
        self.plugins.load_all(BUILTIN_NAMES)
        self.plugins.start_watching()
        NOTIF_WATCHER.listeners.append(self._on_notification)
        async def start_notifications():
            status = await asyncio.to_thread(NOTIF_WATCHER.start)
            self.ui.write_log("SYS: Notification reader ON" if status.ok else f"SYS: Notification reader OFF - {status.reason}")
        asyncio.create_task(start_notifications())
        asyncio.create_task(self._notification_pump())

        self._push_hud_counts()
        while True:
            cfg = _llm_config.load_config()
            if cfg.get("voice_transport") == "livekit" and cfg["profile"] != "offline":
                if not self._livekit_worker or not self._livekit_worker._is_running:
                    await self.disconnect_livekit()
                    if not await self.connect_livekit():
                        await asyncio.sleep(5)
                        continue
                await asyncio.sleep(1)
                continue
            if getattr(self, "_livekit_connected", False):
                await self.disconnect_livekit()
            if self._live_disabled or cfg["profile"] in {"offline", "low_resource"}:
                await asyncio.sleep(3)
                continue
            if len(_get_api_key()) < 20:
                if not getattr(self, "_told_no_key", False):
                    self._told_no_key = True
                    self.ui.write_log("SYS: Voice is off (no Gemini key). Typed commands use the configured AI router. Add a key from API KEYS if you want voice.")
                self.ui.set_state("SLEEPING")
                await asyncio.sleep(3)
                continue
            self._told_no_key = False
            attempted_handle, connected, client = None, False, None
            wait_for_unmute = False
            try:
                if hasattr(self.ui, "set_gemini_live_state"):
                    self.ui.set_gemini_live_state("TESTING")
                self.ui.set_state("THINKING")
                key = _get_api_key()
                key_id = hashlib.sha256(key.encode()).digest()
                if key_id != self._live_key_id:
                    self._resume_handle = None
                    self._live_key_id = key_id
                attempted_handle = self._resume_handle
                connected = False
                if not attempted_handle:
                    if self._live_in:
                        await asyncio.to_thread(append_conversation_turn, " ".join(self._live_in),
                                                "[Interrupted] " + " ".join(self._live_out))
                    self._live_in.clear()
                    self._live_out.clear()
                    self._completed_calls.clear()
                config = await asyncio.to_thread(self._build_config)

                # Fresh client on every reconnect — avoids stale HTTP session state
                client = genai.Client(
                    api_key=key,
                    http_options={"api_version": "v1beta"}
                )

                async with (
                    client.aio.live.connect(model=_live_model(), config=config) as session,
                    asyncio.TaskGroup() as tg,
                ):
                    connected = True
                    self._conn_backoff = 3
                    self.session          = session
                    self._live_input_ready = False
                    self.audio_in_queue   = asyncio.Queue(maxsize=200)
                    self.out_queue        = asyncio.Queue(maxsize=200)
                    self._turn_done_event = asyncio.Event()

                    # Reset transient state that must not carry over from a previous session
                    self._pending_vision       = None
                    self._vision_cam_active    = False
                    self._vision_close_pending = False
                    self._vision_busy          = False
                    self._vision_last_time     = 0.0
                    self._interrupted          = False

                    self.ui.set_state("LISTENING")
                    self.ui.write_log("SYS: MARK online.")
                    self._push_hud_counts()

                    # Receive the optional startup response before opening the
                    # microphone; Live API does not accept overlapping turns.
                    tg.create_task(self._receive_audio())
                    tg.create_task(self._play_audio())

                    if not self._briefing_sent and get_brief_enabled():
                        self._briefing_sent = True
                        await self._send_startup_briefing()
                        try:
                            await asyncio.wait_for(self._turn_done_event.wait(), timeout=15)
                        except asyncio.TimeoutError:
                            print("[MARK] Startup briefing did not finish within 15s.")

                    tg.create_task(self._send_realtime())
                    tg.create_task(self._listen_audio())
                    tg.create_task(self._run_system_monitor())
                    tg.create_task(self._run_background_monitor())
                    tg.create_task(self._run_proactive_mode())
                    tg.create_task(self._watch_plugins())
                    self._live_input_ready = True
                    if hasattr(self.ui, "set_gemini_live_state"):
                        self.ui.set_gemini_live_state("READY")

            except (KeyboardInterrupt, asyncio.CancelledError):
                raise
            except SystemExit:
                raise
            except BaseException as e:
                # Catches both Exception and BaseExceptionGroup (Python 3.11+
                # TaskGroup raises BaseExceptionGroup when tasks are cancelled
                # externally, which `except Exception` would miss, letting the
                # exception escape the while-loop and causing asyncio.run() to
                # start shutdown — resulting in "executor after shutdown" errors).
                if _is_live_inactivity_error(e):
                    # Expected when no audio is sent, especially while muted.
                    self._conn_backoff = 3
                    wait_for_unmute = bool(self.ui.muted)
                    self.ui.write_log("SYS: Voice session idle; " +
                                      ("it will reconnect when the microphone is enabled." if wait_for_unmute
                                       else "reconnecting."))
                    if hasattr(self.ui, "set_gemini_live_state"):
                        self.ui.set_gemini_live_state("IDLE" if wait_for_unmute else "DEGRADED")
                elif "_PluginsChanged" in repr(e) or isinstance(e, _PluginsChanged):
                    self._resume_handle = None
                    self._conn_backoff = 0
                    self.ui.write_log("SYS: Reconnecting with updated plugins...")
                if _is_live_inactivity_error(e):
                    continue_idle = True
                else:
                    continue_idle = False
                    print(f"[MARK] Live connection error ({type(e).__name__}); details withheld.")

                # Invalid API key — stop hammering the API, prompt re-configuration
                if not continue_idle and _is_invalid_api_key_error(e):
                    self.ui.write_log(
                        "ERR: Gemini API key rejected. Create a new key at "
                        "https://aistudio.google.com/api-keys and paste it here."
                    )
                    self.ui.set_state("SLEEPING")
                    self.ui.prompt_reconfig()
                    while not self.ui._win._ready:
                        await asyncio.sleep(1)
                    print("[MARK] New API key saved — reconnecting...")
                    self._conn_backoff = 3
                    continue

                # A Live API/model/protocol error is not proof that the key is bad.
                # Keep the original SDK message visible so the user can fix the
                # model, API access, quota, or network configuration correctly.
                error_details = _exception_details(e)
                if not continue_idle and attempted_handle and not connected and any(marker in error_details.lower() for marker in
                        ("handle", "resum", "expired", "1007", "invalid argument", "not found")):
                    self._resume_handle = None
                    self.ui.write_log("SYS: Voice session expired. Restoring recent dialogue.")
                    continue
                if not continue_idle:
                    self.ui.write_log(f"ERR: Gemini connection failed ({type(e).__name__}).")
                if not continue_idle and _is_live_audio_configuration_error(e):
                    self._live_disabled = True
                    self._live_input_ready = False
                    self.ui.write_log(
                        "ERR: Gemini Live rejected the audio configuration "
                        f"(model={_live_model()}; input=PCM s16le mono "
                        f"{SEND_SAMPLE_RATE}Hz; mime=audio/pcm;rate={SEND_SAMPLE_RATE}). "
                        "Voice is disabled until MARK is restarted; typed commands remain available."
                    )
                    if hasattr(self.ui, "set_gemini_live_state"):
                        self.ui.set_gemini_live_state("UNAVAILABLE")
                    continue
                if not continue_idle and hasattr(self.ui, "set_gemini_live_state"):
                    self.ui.set_gemini_live_state("DEGRADED")

                # Network / timeout errors — log clearly and back off
                err_str = error_details
                is_net_err = any(k in err_str for k in (
                    "TimeoutError", "timed out", "getaddrinfo", "CancelledError",
                    "ConnectionRefusedError", "OSError", "Cannot connect",
                ))
                if continue_idle:
                    pass
                elif _is_quota_error(err_str):
                    self._conn_backoff = 300
                    self.ui.write_log("SYS: Gemini voice quota reached. The configured text provider keeps handling typed commands; "
                                      "voice retries in 5 minutes.")
                elif is_net_err:
                    _conn_backoff = min(getattr(self, "_conn_backoff", 3) * 2, 60)
                    self._conn_backoff = _conn_backoff
                    self.ui.write_log(
                        f"NET: Connection failed - retrying in {_conn_backoff}s. Check internet / VPN."
                    )
                else:
                    self._conn_backoff = 3

            finally:
                if client is not None:
                    await client.aio.aclose()
                    client.close()
                self.session = None
                self._live_input_ready = False
                if getattr(self.ui, "_gemini_live_state", None) == "READY":
                    self.ui.set_gemini_live_state("IDLE")

            self.set_speaking(False)
            self.ui.set_state("SLEEPING")

            if wait_for_unmute:
                await _wait_for_microphone(self.ui)
            delay = getattr(self, "_conn_backoff", 3)
            await asyncio.sleep(delay)

def main():
    ui = MarkUI("assets/face.png")
    from mark_orb import start_orb
    ui.orb = start_orb(on_restore=ui.restore_from_orb, visible=False)
    from core import llm as _llm_boot
    _llm_boot.set_provider_listener(ui.set_llm_provider)
    ui.set_llm_provider("sleep")

    def runner():
        mark = MarkLive(ui)
        try:
            asyncio.run(mark.run())
        except (KeyboardInterrupt, asyncio.CancelledError):
            print("\n🔴 Shutting down...")

    worker = threading.Thread(target=runner, daemon=True, name="mark-runtime")
    worker.start()
    try:
        ui.root.mainloop()
    except KeyboardInterrupt:
        print("\n[MARK] Shutting down...")
    finally:
        ui.orb.close()
        if getattr(ui, "on_runtime_stop", None):
            ui.on_runtime_stop()
        worker.join(timeout=5)
        _llm_boot.providers.close()

if __name__ == "__main__":
    main()

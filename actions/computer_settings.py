#computer_settings.py
import json
import re
import sys
import time
import subprocess
import platform
from pathlib import Path

from typing import Any

pyautogui: Any = None
pyperclip: Any = None

try:
    import pyautogui
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE    = 0.05
    _PYAUTOGUI = True
except ImportError:
    _PYAUTOGUI = False

try:
    import pyperclip
    _PYPERCLIP = True
except ImportError:
    _PYPERCLIP = False

_OS = platform.system()  # "Windows" | "Darwin" | "Linux"

if _OS == "Windows":
    _WIN_HIDE: dict = {"creationflags": subprocess.CREATE_NO_WINDOW}
else:
    _WIN_HIDE: dict = {}


def _get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent

def _get_api_key() -> str:
    """Credentials are handled by the configured provider."""
    return ""


def _get_macos_wifi_interface() -> str:
    try:
        result = subprocess.run(
            ["networksetup", "-listallhardwareports"],
            capture_output=True, text=True, timeout=5
        )
        lines = result.stdout.splitlines()
        for i, line in enumerate(lines):
            if "Wi-Fi" in line or "AirPort" in line:
                for j in range(i, min(i + 4, len(lines))):
                    if lines[j].startswith("Device:"):
                        return lines[j].split(":", 1)[1].strip()
    except Exception:
        pass
    return "en0" 

def volume_up():
    if _OS == "Windows":
        for _ in range(5): pyautogui.press("volumeup")
    elif _OS == "Darwin":
        subprocess.run(["osascript", "-e",
            "set volume output volume (output volume of (get volume settings) + 10)"],
            capture_output=True)
    else:
        subprocess.run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", "+10%"],
            capture_output=True)

def volume_down():
    if _OS == "Windows":
        for _ in range(5): pyautogui.press("volumedown")
    elif _OS == "Darwin":
        subprocess.run(["osascript", "-e",
            "set volume output volume (output volume of (get volume settings) - 10)"],
            capture_output=True)
    else:
        subprocess.run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", "-10%"],
            capture_output=True)

def volume_mute():
    if _OS == "Windows":
        pyautogui.press("volumemute")
    elif _OS == "Darwin":
        subprocess.run(["osascript", "-e", "set volume with output muted"],
            capture_output=True)
    else:
        subprocess.run(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "toggle"],
            capture_output=True)

def volume_set(value: int):
    value = max(0, min(100, int(value)))
    if _OS == "Windows":
        try:
            import math
            from ctypes import cast, POINTER
            from comtypes import CLSCTX_ALL
            from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
            devices   = AudioUtilities.GetSpeakers()
            interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)  # type: ignore
            vol       = cast(interface, POINTER(IAudioEndpointVolume))
            vol_db    = -65.25 if value == 0 else max(-65.25, 20 * math.log10(value / 100))
            vol.SetMasterVolumeLevel(vol_db, None)  # type: ignore
            return
        except Exception as e:
            print(f"[Settings] pycaw failed, using keypress fallback: {e}")
            pyautogui.press("volumemute")
            pyautogui.press("volumemute")
    elif _OS == "Darwin":
        subprocess.run(["osascript", "-e", f"set volume output volume {value}"],
            capture_output=True)
        return
    else:
        subprocess.run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{value}%"],
            capture_output=True)
        return

def brightness_up():
    if _OS == "Darwin":
        subprocess.run(["osascript", "-e",
            'tell application "System Events" to key code 144'],
            capture_output=True)
    elif _OS == "Linux":
        if subprocess.run(["which", "brightnessctl"],
                capture_output=True).returncode == 0:
            subprocess.run(["brightnessctl", "set", "+10%"], capture_output=True)
        else:
            subprocess.run(
                'xrandr --output $(xrandr | grep " connected" | head -1 | cut -d " " -f1)'
                ' --brightness $(python3 -c "import subprocess; '
                'b=float(subprocess.check_output([\"xrandr\",\"--verbose\"]).decode()'
                '.split(\"Brightness:\")[1].split()[0]); print(min(1.0,b+0.1))")',
                shell=True, capture_output=True
            )
    else:
        try:
            subprocess.run(
                ["powershell", "-Command",
                 "(Get-WmiObject -Namespace root/wmi -Class WmiMonitorBrightnessMethods)"
                 ".WmiSetBrightness(1, [math]::Min(100, "
                 "(Get-WmiObject -Namespace root/wmi -Class WmiMonitorBrightness).CurrentBrightness + 10))"],
                capture_output=True, timeout=5, **_WIN_HIDE
            )
        except Exception as e:
            print(f"[Settings] Brightness up failed on Windows: {e}")

def brightness_down():
    if _OS == "Darwin":
        subprocess.run(["osascript", "-e",
            'tell application "System Events" to key code 145'],
            capture_output=True)
    elif _OS == "Linux":
        if subprocess.run(["which", "brightnessctl"],
                capture_output=True).returncode == 0:
            subprocess.run(["brightnessctl", "set", "10%-"], capture_output=True)
        else:
            subprocess.run(
                'xrandr --output $(xrandr | grep " connected" | head -1 | cut -d " " -f1)'
                ' --brightness $(python3 -c "import subprocess; '
                'b=float(subprocess.check_output([\"xrandr\",\"--verbose\"]).decode()'
                '.split(\"Brightness:\")[1].split()[0]); print(max(0.1,b-0.1))")',
                shell=True, capture_output=True
            )
    else:
        try:
            subprocess.run(
                ["powershell", "-Command",
                 "(Get-WmiObject -Namespace root/wmi -Class WmiMonitorBrightnessMethods)"
                 ".WmiSetBrightness(1, [math]::Max(0, "
                 "(Get-WmiObject -Namespace root/wmi -Class WmiMonitorBrightness).CurrentBrightness - 10))"],
                capture_output=True, timeout=5, **_WIN_HIDE
            )
        except Exception as e:
            print(f"[Settings] Brightness down failed on Windows: {e}")

def close_app():
    if _OS == "Darwin": pyautogui.hotkey("command", "q")
    else:               pyautogui.hotkey("alt", "f4")

def close_window():
    if _OS == "Darwin": pyautogui.hotkey("command", "w")
    else:               pyautogui.hotkey("ctrl", "w")

def full_screen():
    if _OS == "Darwin": pyautogui.hotkey("ctrl", "command", "f")
    else:               pyautogui.press("f11")

def minimize_window():
    if _OS == "Darwin": pyautogui.hotkey("command", "m")
    else:               pyautogui.hotkey("win", "down")

def maximize_window():
    if _OS == "Darwin":
        subprocess.run(["osascript", "-e",
            'tell application "System Events" to keystroke "f" '
            'using {control down, command down}'],
            capture_output=True)
    elif _OS == "Windows":
        pyautogui.hotkey("win", "up")
    else:
        try:
            subprocess.run(["wmctrl", "-r", ":ACTIVE:", "-b", "add,maximized_vert,maximized_horz"],
                capture_output=True)
        except Exception:
            pyautogui.hotkey("super", "up")

def snap_left():
    if _OS == "Windows":
        pyautogui.hotkey("win", "left")
    elif _OS == "Darwin":
        # macOS has no built-in snap; try Rectangle app shortcut if installed
        try:
            subprocess.run(["open", "-a", "Rectangle"], capture_output=True, timeout=1)
        except Exception:
            pass
        pyautogui.hotkey("ctrl", "option", "left")
    else:  # Linux
        try:
            subprocess.run(["wmctrl", "-r", ":ACTIVE:", "-e", "0,0,0,960,1080"],
                capture_output=True)
        except Exception:
            pass

def snap_right():
    if _OS == "Windows":
        pyautogui.hotkey("win", "right")
    elif _OS == "Darwin":
        try:
            subprocess.run(["open", "-a", "Rectangle"], capture_output=True, timeout=1)
        except Exception:
            pass
        pyautogui.hotkey("ctrl", "option", "right")
    else:  # Linux
        try:
            subprocess.run(["wmctrl", "-r", ":ACTIVE:", "-e", "0,960,0,960,1080"],
                capture_output=True)
        except Exception:
            pass

def switch_window():
    if _OS == "Darwin": pyautogui.hotkey("command", "tab")
    else:               pyautogui.hotkey("alt", "tab")

def show_desktop():
    if _OS == "Darwin":   pyautogui.hotkey("fn", "f11")
    elif _OS == "Windows": pyautogui.hotkey("win", "d")
    else:                  pyautogui.hotkey("super", "d")

def open_task_manager():
    if _OS == "Windows":
        pyautogui.hotkey("ctrl", "shift", "esc")
    elif _OS == "Darwin":
        subprocess.Popen(["open", "-a", "Activity Monitor"])
    else:
        for cmd in [["gnome-system-monitor"], ["xfce4-taskmanager"], ["htop"]]:
            if subprocess.run(["which", cmd[0]], capture_output=True).returncode == 0:
                subprocess.Popen(cmd)
                break


def focus_search():
    if _OS == "Darwin": pyautogui.hotkey("command", "l")
    else:               pyautogui.hotkey("ctrl", "l")

def pause_video():      pyautogui.press("space")

def refresh_page():
    if _OS == "Darwin": pyautogui.hotkey("command", "r")
    else:               pyautogui.press("f5")

def close_tab():
    if _OS == "Darwin": pyautogui.hotkey("command", "w")
    else:               pyautogui.hotkey("ctrl", "w")

def new_tab():
    if _OS == "Darwin": pyautogui.hotkey("command", "t")
    else:               pyautogui.hotkey("ctrl", "t")

def next_tab():
    if _OS == "Darwin": pyautogui.hotkey("command", "shift", "bracketright")
    else:               pyautogui.hotkey("ctrl", "tab")

def prev_tab():
    if _OS == "Darwin": pyautogui.hotkey("command", "shift", "bracketleft")
    else:               pyautogui.hotkey("ctrl", "shift", "tab")

def go_back():
    if _OS == "Darwin": pyautogui.hotkey("command", "left")
    else:               pyautogui.hotkey("alt", "left")

def go_forward():
    if _OS == "Darwin": pyautogui.hotkey("command", "right")
    else:               pyautogui.hotkey("alt", "right")

def zoom_in():
    if _OS == "Darwin": pyautogui.hotkey("command", "equal")
    else:               pyautogui.hotkey("ctrl", "equal")

def zoom_out():
    if _OS == "Darwin": pyautogui.hotkey("command", "minus")
    else:               pyautogui.hotkey("ctrl", "minus")

def zoom_reset():
    if _OS == "Darwin": pyautogui.hotkey("command", "0")
    else:               pyautogui.hotkey("ctrl", "0")

def find_on_page():
    if _OS == "Darwin": pyautogui.hotkey("command", "f")
    else:               pyautogui.hotkey("ctrl", "f")

def reload_page_n(n: int):
    for _ in range(max(1, n)):
        refresh_page()
        time.sleep(0.8)


def scroll_up(amount: int = 500):    pyautogui.scroll(amount)
def scroll_down(amount: int = 500):  pyautogui.scroll(-amount)

def scroll_top():
    if _OS == "Darwin": pyautogui.hotkey("command", "up")
    else:               pyautogui.hotkey("ctrl", "home")

def scroll_bottom():
    if _OS == "Darwin": pyautogui.hotkey("command", "down")
    else:               pyautogui.hotkey("ctrl", "end")

def page_up():   pyautogui.press("pageup")
def page_down(): pyautogui.press("pagedown")


def copy():
    if _OS == "Darwin": pyautogui.hotkey("command", "c")
    else:               pyautogui.hotkey("ctrl", "c")

def paste():
    if _OS == "Darwin": pyautogui.hotkey("command", "v")
    else:               pyautogui.hotkey("ctrl", "v")

def cut():
    if _OS == "Darwin": pyautogui.hotkey("command", "x")
    else:               pyautogui.hotkey("ctrl", "x")

def undo():
    if _OS == "Darwin": pyautogui.hotkey("command", "z")
    else:               pyautogui.hotkey("ctrl", "z")

def redo():
    if _OS == "Darwin": pyautogui.hotkey("command", "shift", "z")
    else:               pyautogui.hotkey("ctrl", "y")

def select_all():
    if _OS == "Darwin": pyautogui.hotkey("command", "a")
    else:               pyautogui.hotkey("ctrl", "a")

def save_file():
    if _OS == "Darwin": pyautogui.hotkey("command", "s")
    else:               pyautogui.hotkey("ctrl", "s")

def press_enter():   pyautogui.press("enter")
def press_escape():  pyautogui.press("escape")
def press_key(key: str): pyautogui.press(key)

def type_text(text: str, press_enter_after: bool = False):
    if not text:
        return
    if _PYPERCLIP:
        pyperclip.copy(str(text))
        time.sleep(0.15)
        paste()
    else:
        pyautogui.write(str(text), interval=0.03)
    if press_enter_after:
        time.sleep(0.1)
        pyautogui.press("enter")

def take_screenshot():
    if _OS == "Windows":
        pyautogui.hotkey("win", "shift", "s")
    elif _OS == "Darwin":
        pyautogui.hotkey("command", "shift", "3")
    else:
        for cmd in [["scrot"], ["gnome-screenshot"], ["import", "-window", "root", "screenshot.png"]]:
            if subprocess.run(["which", cmd[0]], capture_output=True).returncode == 0:
                subprocess.Popen(cmd)
                return
        pyautogui.hotkey("ctrl", "print_screen")

def lock_screen():
    if _OS == "Windows":
        pyautogui.hotkey("win", "l")
    elif _OS == "Darwin":
        subprocess.run(["pmset", "displaysleepnow"], capture_output=True)
    else:
        for cmd in [
            ["gnome-screensaver-command", "-l"],
            ["xdg-screensaver", "lock"],
            ["loginctl", "lock-session"],
        ]:
            if subprocess.run(["which", cmd[0]], capture_output=True).returncode == 0:
                subprocess.run(cmd, capture_output=True)
                return

def open_system_settings():
    if _OS == "Windows":
        pyautogui.hotkey("win", "i")
    elif _OS == "Darwin":
        subprocess.Popen(["open", "-a", "System Preferences"])
    else:
        for cmd in [["gnome-control-center"], ["xfce4-settings-manager"], ["kcmshell5"]]:
            if subprocess.run(["which", cmd[0]], capture_output=True).returncode == 0:
                subprocess.Popen(cmd)
                return

def open_file_explorer():
    if _OS == "Windows":
        pyautogui.hotkey("win", "e")
    elif _OS == "Darwin":
        subprocess.Popen(["open", str(Path.home())])
    else:
        for cmd in [["nautilus"], ["thunar"], ["dolphin"], ["nemo"]]:
            if subprocess.run(["which", cmd[0]], capture_output=True).returncode == 0:
                subprocess.Popen(cmd)
                return
        subprocess.Popen(["xdg-open", str(Path.home())])

def sleep_display():
    if _OS == "Windows":
        try:
            import ctypes
            ctypes.windll.user32.SendMessageW(0xFFFF, 0x0112, 0xF170, 2)
        except Exception as e:
            print(f"[Settings] sleep_display failed: {e}")
    elif _OS == "Darwin":
        subprocess.run(["pmset", "displaysleepnow"], capture_output=True)
    else:
        subprocess.run(["xset", "dpms", "force", "off"], capture_output=True)

def open_run():
    if _OS == "Windows":
        pyautogui.hotkey("win", "r")

def dark_mode():
    if _OS == "Darwin":
        subprocess.run(["osascript", "-e",
            'tell app "System Events" to tell appearance preferences '
            'to set dark mode to not dark mode'],
            capture_output=True)
    elif _OS == "Windows":
        try:
            import winreg
            key_path = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Themes\Personalize"
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_ALL_ACCESS)
            current, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            winreg.SetValueEx(key, "AppsUseLightTheme", 0, winreg.REG_DWORD, 1 - current)
            winreg.SetValueEx(key, "SystemUsesLightTheme", 0, winreg.REG_DWORD, 1 - current)
            winreg.CloseKey(key)
        except Exception as e:
            print(f"[Settings] dark_mode registry failed: {e}")
    else:
        try:
            result = subprocess.run(
                ["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"],
                capture_output=True, text=True
            )
            current = result.stdout.strip()
            new_scheme = "'default'" if "dark" in current else "'prefer-dark'"
            subprocess.run(
                ["gsettings", "set", "org.gnome.desktop.interface", "color-scheme", new_scheme],
                capture_output=True
            )
        except Exception as e:
            print(f"[Settings] dark_mode Linux failed: {e}")

def toggle_wifi():
    if _OS == "Darwin":
        iface = _get_macos_wifi_interface()
        result = subprocess.run(
            ["networksetup", "-getairportpower", iface],
            capture_output=True, text=True
        )
        state = "off" if "On" in result.stdout else "on"
        subprocess.run(["networksetup", "-setairportpower", iface, state],
            capture_output=True)
    elif _OS == "Windows":
        try:
            subprocess.run(
                ["powershell", "-Command",
                 "$adapter = Get-NetAdapter | Where-Object {$_.PhysicalMediaType -eq 'Native 802.11'};"
                 "if ($adapter.Status -eq 'Up') { Disable-NetAdapter -Name $adapter.Name -Confirm:$false }"
                 "else { Enable-NetAdapter -Name $adapter.Name -Confirm:$false }"],
                capture_output=True, timeout=10, **_WIN_HIDE
            )
        except Exception as e:
            print(f"[Settings] toggle_wifi Windows failed: {e}")
    else:
        try:
            result = subprocess.run(["nmcli", "radio", "wifi"], capture_output=True, text=True)
            state  = "off" if "enabled" in result.stdout else "on"
            subprocess.run(["nmcli", "radio", "wifi", state], capture_output=True)
        except Exception as e:
            print(f"[Settings] toggle_wifi Linux failed: {e}")

def restart_computer():
    if _OS == "Windows":
        subprocess.run(["shutdown", "/r", "/t", "10"], capture_output=True, **_WIN_HIDE)
    elif _OS == "Darwin":
        subprocess.run(["osascript", "-e",
            'tell application "System Events" to restart'],
            capture_output=True)
    else:
        subprocess.run(["systemctl", "reboot"], capture_output=True)

def shutdown_computer():
    if _OS == "Windows":
        subprocess.run(["shutdown", "/s", "/t", "10"], capture_output=True)
    elif _OS == "Darwin":
        subprocess.run(["osascript", "-e",
            'tell application "System Events" to shut down'],
            capture_output=True)
    else:
        subprocess.run(["systemctl", "poweroff"], capture_output=True)


def brightness_set(value: int):
    """Set brightness 0-100 where supported."""
    value = max(0, min(100, int(value)))
    if _OS == "Darwin":
        # macOS has no reliable CLI brightness without brightness CLI tool
        try:
            subprocess.run(["brightness", str(value / 100.0)], capture_output=True, timeout=3)
            return
        except Exception:
            # Approximate with key presses from mid point — best effort
            for _ in range(16):
                subprocess.run(["osascript", "-e",
                    'tell application "System Events" to key code 145'], capture_output=True)
            n = max(0, min(16, round(value / 6.25)))
            for _ in range(n):
                subprocess.run(["osascript", "-e",
                    'tell application "System Events" to key code 144'], capture_output=True)
            return
    if _OS == "Linux":
        if subprocess.run(["which", "brightnessctl"], capture_output=True).returncode == 0:
            subprocess.run(["brightnessctl", "set", f"{value}%"], capture_output=True)
            return
        # xrandr fallback 0.1–1.0
        frac = max(0.1, min(1.0, value / 100.0))
        subprocess.run(
            f'xrandr --output $(xrandr | grep " connected" | head -1 | cut -d " " -f1) --brightness {frac}',
            shell=True, capture_output=True
        )
        return
    # Windows
    try:
        subprocess.run(
            ["powershell", "-Command",
             f"(Get-WmiObject -Namespace root/wmi -Class WmiMonitorBrightnessMethods)"
             f".WmiSetBrightness(1, {value})"],
            capture_output=True, timeout=5, **_WIN_HIDE
        )
    except Exception as e:
        print(f"[Settings] brightness_set failed: {e}")


def media_play_pause():
    if _PYAUTOGUI:
        pyautogui.press("playpause")


def media_next():
    if _PYAUTOGUI:
        pyautogui.press("nexttrack")


def media_prev():
    if _PYAUTOGUI:
        pyautogui.press("prevtrack")


def media_stop():
    if _PYAUTOGUI:
        pyautogui.press("stop")


def sleep_computer():
    if _OS == "Windows":
        subprocess.run(
            ["powershell", "-Command", "Add-Type -AssemblyName System.Windows.Forms; "
             "[System.Windows.Forms.Application]::SetSuspendState('Suspend',$false,$false)"],
            capture_output=True, timeout=10, **_WIN_HIDE
        )
    elif _OS == "Darwin":
        subprocess.run(["pmset", "sleepnow"], capture_output=True)
    else:
        subprocess.run(["systemctl", "suspend"], capture_output=True)


def hibernate_computer():
    if _OS == "Windows":
        subprocess.run(["shutdown", "/h"], capture_output=True, **_WIN_HIDE)
    elif _OS == "Darwin":
        # macOS has no true hibernate for users; sleep is closest
        subprocess.run(["pmset", "sleepnow"], capture_output=True)
    else:
        subprocess.run(["systemctl", "hibernate"], capture_output=True)


def log_off():
    if _OS == "Windows":
        subprocess.run(["shutdown", "/l"], capture_output=True, **_WIN_HIDE)
    elif _OS == "Darwin":
        subprocess.run(["osascript", "-e",
            'tell application "System Events" to log out'], capture_output=True)
    else:
        subprocess.run(["loginctl", "terminate-user", str(Path.home().name)],
                       capture_output=True)


def empty_recycle_bin():
    if _OS == "Windows":
        try:
            subprocess.run(
                ["powershell", "-Command",
                 "Clear-RecycleBin -Force -ErrorAction SilentlyContinue"],
                capture_output=True, timeout=30, **_WIN_HIDE
            )
        except Exception as e:
            print(f"[Settings] empty recycle failed: {e}")
    elif _OS == "Darwin":
        trash = Path.home() / ".Trash"
        if trash.exists():
            for item in trash.iterdir():
                try:
                    if item.is_dir():
                        import shutil
                        shutil.rmtree(item, ignore_errors=True)
                    else:
                        item.unlink(missing_ok=True)
                except Exception:
                    pass
    else:
        trash = Path.home() / ".local" / "share" / "Trash" / "files"
        if trash.exists():
            import shutil
            for item in trash.iterdir():
                try:
                    if item.is_dir():
                        shutil.rmtree(item, ignore_errors=True)
                    else:
                        item.unlink(missing_ok=True)
                except Exception:
                    pass


def toggle_bluetooth():
    if _OS == "Darwin":
        # Requires blueutil if installed
        try:
            r = subprocess.run(["blueutil", "-p"], capture_output=True, text=True, timeout=5)
            on = (r.stdout or "").strip() == "1"
            subprocess.run(["blueutil", "-p", "0" if on else "1"], capture_output=True, timeout=5)
            return
        except Exception:
            # Open Bluetooth prefs as fallback
            subprocess.Popen(["open", "x-apple.systempreferences:com.apple.preference.bluetooth"])
            return
    if _OS == "Windows":
        try:
            subprocess.run(
                ["powershell", "-Command",
                 "$b = Get-PnpDevice -Class Bluetooth -ErrorAction SilentlyContinue | "
                 "Where-Object { $_.FriendlyName -match 'Adapter|Radio' } | Select-Object -First 1; "
                 "if ($b -and $b.Status -eq 'OK') { Disable-PnpDevice -InstanceId $b.InstanceId -Confirm:$false } "
                 "elseif ($b) { Enable-PnpDevice -InstanceId $b.InstanceId -Confirm:$false }"],
                capture_output=True, timeout=15, **_WIN_HIDE
            )
        except Exception as e:
            print(f"[Settings] bluetooth toggle failed: {e}")
        return
    # Linux
    try:
        r = subprocess.run(["bluetoothctl", "show"], capture_output=True, text=True, timeout=5)
        powered = "Powered: yes" in (r.stdout or "")
        subprocess.run(["bluetoothctl", "power", "off" if powered else "on"],
                       capture_output=True, timeout=5)
    except Exception as e:
        print(f"[Settings] bluetooth Linux failed: {e}")


def night_light_toggle():
    if _OS == "Windows":
        # Toggle night light via registry + settings broadcast is flaky; open page
        try:
            subprocess.run(
                ["powershell", "-Command",
                 "Start-Process ms-settings:nightlight"],
                capture_output=True, timeout=5, **_WIN_HIDE
            )
        except Exception as e:
            print(f"[Settings] night light: {e}")
    elif _OS == "Darwin":
        # Night Shift — open Displays prefs
        subprocess.Popen(["open", "x-apple.systempreferences:com.apple.preference.displays"])
    else:
        try:
            r = subprocess.run(
                ["gsettings", "get", "org.gnome.settings-daemon.plugins.color", "night-light-enabled"],
                capture_output=True, text=True, timeout=5
            )
            cur = (r.stdout or "").strip().lower()
            new = "false" if "true" in cur else "true"
            subprocess.run(
                ["gsettings", "set", "org.gnome.settings-daemon.plugins.color",
                 "night-light-enabled", new],
                capture_output=True, timeout=5
            )
        except Exception as e:
            print(f"[Settings] night light Linux: {e}")


def open_notification_center():
    if _OS == "Windows":
        if _PYAUTOGUI:
            pyautogui.hotkey("win", "a")
    elif _OS == "Darwin":
        if _PYAUTOGUI:
            pyautogui.hotkey("fn", "n")  # Control Center / Notification varies by macOS
            # Alternate: Notification Center
            try:
                subprocess.run(["osascript", "-e",
                    'tell application "System Events" to key code 160 using {control down}'],
                    capture_output=True)
            except Exception:
                pass
    else:
        if _PYAUTOGUI:
            pyautogui.hotkey("super", "v")  # GNOME notification list common binding


def open_clipboard_history():
    if _OS == "Windows" and _PYAUTOGUI:
        pyautogui.hotkey("win", "v")
    elif _OS == "Darwin" and _PYAUTOGUI:
        # No native clipboard history; Cmd+Shift+V often used by apps
        pyautogui.hotkey("command", "shift", "v")
    else:
        if _PYAUTOGUI:
            pyautogui.hotkey("ctrl", "shift", "v")


def open_start_menu():
    if _OS == "Windows" and _PYAUTOGUI:
        pyautogui.press("win")
    elif _OS == "Darwin" and _PYAUTOGUI:
        pyautogui.hotkey("command", "space")  # Spotlight
    else:
        if _PYAUTOGUI:
            pyautogui.press("super")


def open_emoji_panel():
    if _OS == "Windows" and _PYAUTOGUI:
        pyautogui.hotkey("win", ".")
    elif _OS == "Darwin" and _PYAUTOGUI:
        pyautogui.hotkey("command", "control", "space")
    else:
        if _PYAUTOGUI:
            pyautogui.hotkey("ctrl", ".")


def wifi_on():
    if _OS == "Darwin":
        iface = _get_macos_wifi_interface()
        subprocess.run(["networksetup", "-setairportpower", iface, "on"], capture_output=True)
    elif _OS == "Windows":
        subprocess.run(
            ["powershell", "-Command",
             "Get-NetAdapter | Where-Object {$_.PhysicalMediaType -eq 'Native 802.11'} | "
             "Enable-NetAdapter -Confirm:$false"],
            capture_output=True, timeout=10, **_WIN_HIDE
        )
    else:
        subprocess.run(["nmcli", "radio", "wifi", "on"], capture_output=True)


def wifi_off():
    if _OS == "Darwin":
        iface = _get_macos_wifi_interface()
        subprocess.run(["networksetup", "-setairportpower", iface, "off"], capture_output=True)
    elif _OS == "Windows":
        subprocess.run(
            ["powershell", "-Command",
             "Get-NetAdapter | Where-Object {$_.PhysicalMediaType -eq 'Native 802.11'} | "
             "Disable-NetAdapter -Confirm:$false"],
            capture_output=True, timeout=10, **_WIN_HIDE
        )
    else:
        subprocess.run(["nmcli", "radio", "wifi", "off"], capture_output=True)


def kill_app_by_name(name: str) -> str:
    name = (name or "").strip()
    if not name:
        return "No app name provided."
    try:
        import psutil
    except ImportError:
        return "psutil not installed."
    killed = []
    needle = name.lower()
    targets = []
    for p in psutil.process_iter(["pid", "name"]):
        try:
            pname = (p.info.get("name") or "").lower()
            if needle in pname or pname.startswith(needle):
                targets.append(p)
                killed.append(f"{p.info.get('name')}({p.info.get('pid')})")
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    if not killed:
        return f"No running process matched: {name}"
    for p in targets:
        try:
            p.terminate()
        except Exception:
            pass
    try:
        psutil.wait_procs(targets, timeout=2)
    except Exception:
        pass
    for p in targets:
        try:
            if p.is_running():
                p.kill()
        except Exception:
            pass
    return f"Closed: {', '.join(killed[:8])}" + ("…" if len(killed) > 8 else "")


def cancel_shutdown():
    if _OS == "Windows":
        subprocess.run(["shutdown", "/a"], capture_output=True, **_WIN_HIDE)
    elif _OS == "Darwin":
        subprocess.run(["killall", "shutdown"], capture_output=True)
    else:
        subprocess.run(["shutdown", "-c"], capture_output=True)


def open_terminal():
    if _OS == "Windows":
        for cmd in (["wt.exe"], ["cmd.exe", "/c", "start", "cmd"]):
            try:
                subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return
            except Exception:
                continue
    elif _OS == "Darwin":
        subprocess.Popen(["open", "-a", "Terminal"])
    else:
        for cmd in (["gnome-terminal"], ["konsole"], ["xfce4-terminal"], ["xterm"]):
            if subprocess.run(["which", cmd[0]], capture_output=True).returncode == 0:
                subprocess.Popen(cmd)
                return


def open_calculator():
    if _OS == "Windows":
        subprocess.Popen(["calc.exe"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    elif _OS == "Darwin":
        subprocess.Popen(["open", "-a", "Calculator"])
    else:
        for cmd in (["gnome-calculator"], ["kcalc"], ["galculator"], ["xcalc"]):
            if subprocess.run(["which", cmd[0]], capture_output=True).returncode == 0:
                subprocess.Popen(cmd)
                return


ACTION_MAP: dict[str, Any] = {
    "volume_up":           volume_up,
    "volume_down":         volume_down,
    "mute":                volume_mute,
    "unmute":              volume_mute,
    "toggle_mute":         volume_mute,
    "brightness_up":       brightness_up,
    "brightness_down":     brightness_down,
    "sleep_display":       sleep_display,
    "screen_off":          sleep_display,
    "pause_video":         pause_video,
    "play_pause":          pause_video,
    "media_play_pause":    media_play_pause,
    "media_next":          media_next,
    "next_track":          media_next,
    "media_prev":          media_prev,
    "previous_track":      media_prev,
    "media_stop":          media_stop,
    "close_app":           close_app,
    "close_window":        close_window,
    "full_screen":         full_screen,
    "fullscreen":          full_screen,
    "minimize":            minimize_window,
    "maximize":            maximize_window,
    "snap_left":           snap_left,
    "snap_right":          snap_right,
    "switch_window":       switch_window,
    "show_desktop":        show_desktop,
    "task_manager":        open_task_manager,
    "focus_search":        focus_search,
    "refresh_page":        refresh_page,
    "reload":              refresh_page,
    "close_tab":           close_tab,
    "new_tab":             new_tab,
    "next_tab":            next_tab,
    "prev_tab":            prev_tab,
    "go_back":             go_back,
    "go_forward":          go_forward,
    "zoom_in":             zoom_in,
    "zoom_out":            zoom_out,
    "zoom_reset":          zoom_reset,
    "find_on_page":        find_on_page,
    "scroll_up":           scroll_up,
    "scroll_down":         scroll_down,
    "scroll_top":          scroll_top,
    "scroll_bottom":       scroll_bottom,
    "page_up":             page_up,
    "page_down":           page_down,
    "copy":                copy,
    "paste":               paste,
    "cut":                 cut,
    "undo":                undo,
    "redo":                redo,
    "select_all":          select_all,
    "save":                save_file,
    "enter":               press_enter,
    "escape":              press_escape,
    "screenshot":          take_screenshot,
    "lock_screen":         lock_screen,
    "open_settings":       open_system_settings,
    "file_explorer":       open_file_explorer,
    "open_run":            open_run,
    "dark_mode":           dark_mode,
    "toggle_wifi":         toggle_wifi,
    "wifi_on":             wifi_on,
    "wifi_off":            wifi_off,
    "toggle_bluetooth":    toggle_bluetooth,
    "bluetooth":           toggle_bluetooth,
    "night_light":         night_light_toggle,
    "night_light_toggle":  night_light_toggle,
    "notification_center": open_notification_center,
    "action_center":       open_notification_center,
    "clipboard_history":   open_clipboard_history,
    "start_menu":          open_start_menu,
    "spotlight":           open_start_menu,
    "emoji_panel":         open_emoji_panel,
    "empty_recycle_bin":   empty_recycle_bin,
    "empty_trash":         empty_recycle_bin,
    "sleep":               sleep_computer,
    "sleep_computer":      sleep_computer,
    "hibernate":           hibernate_computer,
    "log_off":             log_off,
    "logout":              log_off,
    "sign_out":            log_off,
    "cancel_shutdown":     cancel_shutdown,
    "open_terminal":       open_terminal,
    "terminal":            open_terminal,
    "open_calculator":     open_calculator,
    "calculator":          open_calculator,
    "restart":             restart_computer,
    "shutdown":            shutdown_computer,
}

_DANGEROUS_ACTIONS = {"restart", "shutdown", "sleep", "sleep_computer", "hibernate", "log_off", "logout", "sign_out"}



def _detect_action(description: str) -> dict:

    from core import llm as genai  # configured LLM client (drop-in)
    _client = genai.Client(api_key=_get_api_key())

    available = ", ".join(sorted(ACTION_MAP.keys())) + \
                ", volume_set, brightness_set, type_text, press_key, reload_n, kill_app"

    prompt = f"""You are an intent detector for a computer control assistant.

The user issued a command (possibly in any language): "{description}"

Available actions: {available}

Return ONLY a valid JSON object:
{{"action": "action_name", "value": null_or_value}}

Rules:
- Pick the single best matching action from the available list.
- For volume_set: value is an integer 0-100.
- For type_text: value is the exact text to type.
- For press_key: value is the key name (e.g. "f5", "tab", "enter").
- For reload_n: value is an integer (number of times to reload).
- If no clear match, pick the closest action.
- Return ONLY the JSON, no explanation, no markdown."""

    try:
        resp = _client.models.generate_content(model="role:fast", contents=prompt)
        text = re.sub(r"```(?:json)?", "", (resp.text or "")).strip().rstrip("`").strip()
        return json.loads(text)
    except Exception as e:
        print(f"[Settings] Intent detection failed: {e}")
        return {"action": description.lower().replace(" ", "_"), "value": None}

def computer_settings(
    parameters: dict | None = None,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    if not _PYAUTOGUI:
        return "pyautogui is not installed. Run: pip install pyautogui"

    params      = parameters or {}
    raw_action  = params.get("action", "").strip()
    description = params.get("description", "").strip()
    value       = params.get("value", None)

    if not raw_action and description:
        detected   = _detect_action(description)
        raw_action = detected.get("action", "")
        if value is None:
            value = detected.get("value")

    action = raw_action.lower().strip().replace(" ", "_").replace("-", "_")

    if not action:
        return "No action could be determined."

    print(f"[Settings] Action: {action}  Value: {value}  OS: {_OS}")
    if player:
        player.write_log(f"[Settings] {action}")

    if action in _DANGEROUS_ACTIONS:
        confirmed = str(params.get("confirmed", "")).lower()
        if confirmed not in ("yes", "true", "1", "confirm"):
            return (
                f"This will {action} the computer. "
                f"Please confirm by calling again with confirmed=yes."
            )

    if action == "volume_set":
        try:
            volume_set(int(value or 50))
            return f"Volume set to {value}%."
        except Exception as e:
            return f"Could not set volume: {e}"

    if action in ("brightness_set", "set_brightness"):
        try:
            brightness_set(int(value or 50))
            return f"Brightness set to {value}%."
        except Exception as e:
            return f"Could not set brightness: {e}"

    if action in ("kill_app", "close_process", "force_close", "kill_process"):
        target = str(value or params.get("text", "") or params.get("app", "")).strip()
        return kill_app_by_name(target)

    if action in ("type_text", "write_on_screen", "type", "write"):
        text = str(value or params.get("text", "")).strip()
        if not text:
            return "No text provided to type."
        enter_after = str(params.get("press_enter", "false")).lower() in ("true", "1", "yes")
        type_text(text, press_enter_after=enter_after)
        return f"Typed: {text[:80]}"

    if action == "press_key":
        key = str(value or params.get("key", "")).strip()
        if not key:
            return "No key specified."
        press_key(key)
        return f"Pressed: {key}"

    if action in ("reload_n", "refresh_n", "reload_page_n"):
        try:
            reload_page_n(int(value or 1))
            return f"Reloaded {value or 1} time(s)."
        except Exception as e:
            return f"Reload failed: {e}"

    if action == "scroll_up":
        scroll_up(int(value or 500))
        return "Scrolled up."

    if action == "scroll_down":
        scroll_down(int(value or 500))
        return "Scrolled down."

    func = ACTION_MAP.get(action)
    if not func:
        return f"Unknown action: '{raw_action}'."

    try:
        func()
        return f"Done: {action}."
    except Exception as e:
        print(f"[Settings] Action failed ({action}): {e}")
        return f"Action failed ({action}): {e}"
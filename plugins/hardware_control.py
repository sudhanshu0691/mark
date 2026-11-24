"""
Hardware Control - deep system-level monitoring and control on Windows.

Reads real hardware state (battery, brightness, volume, GPU, drives, fans, USB).
Every SET operation reads the value BACK before reporting DONE, so you never get
"aapke brightness set ho gaye" when they did not actually change.
"""
from __future__ import annotations

import ctypes
import platform
import subprocess
import sys

from core.result import done, failed, unverified

_OS = platform.system()
NAME = "hardware_control"
DESCRIPTION = ("Read and control hardware: battery, screen brightness, volume, mute, power plan, "
               "GPU, disks, temperatures, USB devices. Sets are confirmed by reading back.")
METADATA = {"version": "1.0.0",
            "capabilities": ["hardware", "battery", "brightness", "volume", "gpu", "disk", "sensor", "usb"],
            "permissions": [], "timeout": 40}

DECLARATION = {
    "name": "hardware_control",
    "description": ("Deeper hardware control and monitoring. "
                    "action: battery | brightness | volume | mute | power | gpu | disk | "
                    "temp | usb | info | fans."),
    "parameters": {"type": "OBJECT", "properties": {
        "action": {"type": "STRING", "description": "battery | brightness | volume | mute | power | gpu | disk | temp | usb | info | fans"},
        "value": {"type": "NUMBER", "description": "0-100 brightness/volume, or gain"},
        "set": {"type": "BOOLEAN", "description": "true = set the value (otherwise just read)"},
        "drive": {"type": "STRING", "description": "Drive letter for disk, e.g. C"},
        "mode": {"type": "STRING", "description": "For mute: on | off | toggle"},
        "plan": {"type": "STRING", "description": "For power: balanced | powersaver | highperformance"},
    }, "required": ["action"]},
}

_HIDE = {"creationflags": subprocess.CREATE_NO_WINDOW} if _OS == "Windows" else {}


def _run(cmd, timeout=15) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, **_HIDE)
        return (r.stdout or r.stderr or "").strip()
    except Exception as e:
        return f"[error] {type(e).__name__}: {e}"


# ── volume (pycaw) ────────────────────────────────────────────────────────────
def _vol():
    """Return the IAudioEndpointVolume interface.

    pycaw 2024+ exposes a high-level AudioDevice with .EndpointVolume (already the
    cast interface).  Older versions need the manual Activate+cast path, which we
    still support below.
    """
    from ctypes import POINTER, cast
    from comtypes import CLSCTX_ALL
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

    dev = AudioUtilities.GetSpeakers()
    if dev is None:
        raise RuntimeError("no default speaker device")

    # path 1 (pycaw 2024+): ready-made endpoint interface
    ep = getattr(dev, "EndpointVolume", None)
    if ep is not None:
        try:
            if isinstance(ep, IAudioEndpointVolume) or hasattr(ep, "GetMasterVolumeLevelScalar"):
                return ep
        except Exception:
            pass
        # comtypes POINTER still needs .contents? no - it is already usable
        try:
            ep.GetMasterVolumeLevelScalar()
            return ep
        except Exception:
            pass

    # path 2 (classic): Activate the raw IMMDevice, whatever wrapper hides it
    inner = dev
    for attr in ("Device", "device", "_device", "IDevice"):
        if hasattr(inner, attr):
            inner = getattr(inner, attr)
            break
    act = getattr(inner, "Activate", None)
    if act is not None:
        try:
            iface = act(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
            return cast(iface, POINTER(IAudioEndpointVolume))
        except TypeError:
            guid = getattr(IAudioEndpointVolume, "_iid_", None)
            if guid is not None:
                return cast(act(guid, CLSCTX_ALL, None), POINTER(IAudioEndpointVolume))
    raise RuntimeError("could not obtain IAudioEndpointVolume from the audio device "
                       f"({type(dev).__name__})")


def _volume_read() -> tuple[float, bool]:
    v = _vol()
    cur = v.GetMasterVolumeLevelScalar()
    muted = bool(v.GetMute())
    return round(cur * 100, 1), muted


def _volume_write(pct: float) -> None:
    v = _vol()
    v.SetMasterVolumeLevelScalar(max(0.0, min(1.0, pct / 100.0)), None)


# ── brightness (WMI) ──────────────────────────────────────────────────────────
def _brightness_read():
    out = _run(["powershell", "-NoProfile", "-Command",
                "(Get-WmiObject -Namespace root\\wmi -Class WmiMonitorBrightness).Brightness"])
    try:
        return float(out.split()[0])
    except Exception:
        return None


def _brightness_write(pct: int) -> bool:
    out = _run(["powershell", "-NoProfile", "-Command",
                f"(Get-WmiObject -Namespace root\\wmi -Class WmiMonitorBrightnessMethods)"
                f".WmiSetBrightness(1,{int(pct)})"])
    return "true" in out.lower() or out.strip() == ""



def run(args: dict, ctx):
    import psutil
    act = str(args.get("action", "info")).lower().strip()
    want_set = bool(args.get("set"))
    val = args.get("value")

    # ── battery ───────────────────────────────────────────────────────────────
    if act == "battery":
        b = psutil.sensors_battery()
        if b is None:
            return unverified("No battery detected",
                              reason="psutil reports no battery - this looks like a desktop PC")
        state = "charging" if b.power_plugged else "discharging"
        left = ""
        if not b.power_plugged and b.secsleft and b.secsleft > 0:
            left = f", about {b.secsleft // 3600}h {(b.secsleft % 3600) // 60}m remaining"
        return done(f"Battery {b.percent:.0f}% ({state})",
                    evidence=f"percent={b.percent}, plugged={b.power_plugged}{left}")

    # ── brightness ────────────────────────────────────────────────────────────
    if act == "brightness":
        if not want_set and val is None:
            cur = _brightness_read()
            if cur is None:
                return unverified("Could not read brightness",
                                  reason="WMI WmiMonitorBrightness unavailable "
                                         "(common on external monitors / some laptops)")
            return done(f"Brightness is {cur:.0f}%", evidence=f"WMI reports {cur}")
        if val is None:
            return failed("No value given", "value 0-100 is required to set brightness")
        pct = max(0, min(100, int(float(val))))
        before = _brightness_read()
        if not _brightness_write(pct):
            return failed("Brightness command was rejected",
                          "WmiSetBrightness returned false - device may not support it")
        after = _brightness_read()
        if after is None:
            return unverified(f"Brightness set command sent (asked for {pct}%)",
                              reason="could not read the value back to confirm")
        if abs(after - pct) > 3:
            return unverified(f"Brightness reads {after:.0f}% (asked for {pct}%)",
                              reason=f"value did not reach the target; before={before}, after={after}")
        return done(f"Brightness set to {after:.0f}%",
                    evidence=f"read back = {after}% (was {before}%)")

    # ── volume / mute ─────────────────────────────────────────────────────────
    if act in ("volume", "mute"):
        try:
            before, muted = _volume_read()
        except Exception as e:
            return failed("Could not access the audio endpoint",
                          f"{type(e).__name__}: {e}. Install with: pip install pycaw comtypes")
        if act == "mute":
            mode = str(args.get("mode") or ("on" if want_set else "read")).lower()
            if mode == "read":
                return done(f"Muted: {muted}", evidence=f"master volume {before}%, mute={muted}")
            try:
                v = _vol()
                if mode in ("on", "mute", "true", "1"):
                    v.SetMute(1, None)
                    expect = True
                elif mode in ("off", "unmute", "false", "0"):
                    v.SetMute(0, None)
                    expect = False
                else:
                    v.SetMute(0 if muted else 1, None)
                    expect = not muted
            except Exception as e:
                return failed("Could not change mute", f"{type(e).__name__}: {e}")
            after, now_muted = _volume_read()
            if now_muted == expect:
                return done(f"Mute is now {'ON' if now_muted else 'OFF'}",
                            evidence=f"read back mute={now_muted} (was {muted})")
            return unverified("Mute command sent but state did not change",
                              reason=f"read back mute={now_muted}, was {muted}")

        if val is None:
            return done(f"Volume is {before}% (mute {'ON' if muted else 'OFF'})",
                        evidence=f"master volume={before}%, mute={muted}")
        pct = max(0.0, min(100.0, float(val)))
        try:
            _volume_write(pct)
        except Exception as e:
            return failed("Could not set volume", f"{type(e).__name__}: {e}")
        after, _m = _volume_read()
        if abs(after - pct) > 2:
            return unverified(f"Volume reads {after}% (asked for {pct:.0f}%)",
                              reason=f"before={before}%, after={after}%")
        return done(f"Volume set to {after}%", evidence=f"read back = {after}% (was {before}%)")

    # ── power plan ────────────────────────────────────────────────────────────
    if act == "power":
        want = str(args.get("plan") or "balanced").lower().replace(" ", "")
        listing = _run(["powercfg", "/list"])
        if not listing or listing.startswith("[error]"):
            return failed("Cannot read power plans", listing[:200])
        import re
        plans = re.findall(r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\s*\((.+?)\)", listing)
        if not plans:
            return unverified("No power plans parsed", reason="unexpected powercfg /list output",
                              evidence=listing[:400])
        want_key = {"powersaver": "power saver", "powersaving": "power saver",
                    "highperformance": "high performance", "high": "high performance",
                    "balanced": "balanced"}.get(want, want)
        match = next((g for g, n in plans if want_key in n.lower()), None)
        matched_name = next((n for g, n in plans if want_key in n.lower()), "")
        if not match:
            return failed(f"No power plan matching '{want}'",
                          "available: " + ", ".join(sorted({n for _g, n in plans})))
        before = _run(["powercfg", "/getactivescheme"])
        _run(["powercfg", "/setactive", match])
        after = _run(["powercfg", "/getactivescheme"])
        if match.lower() in after.lower():
            return done(f"Power plan switched to '{matched_name}'",
                        evidence=f"before: {before}\nafter:  {after}")
        return unverified(f"Requested power plan '{matched_name}'",
                          reason="powercfg did not confirm the change",
                          evidence=f"before: {before}\nafter:  {after}\nplans: {listing[:300]}")

    # ── gpu ───────────────────────────────────────────────────────────────────
    if act == "gpu":
        try:
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                import pynvml as nvml          # nvidia-ml-py provides this module
            nvml.nvmlInit()
            h = nvml.nvmlDeviceGetHandleByIndex(0)
            util = nvml.nvmlDeviceGetUtilizationRates(h)
            mem = nvml.nvmlDeviceGetMemoryInfo(h)
            name = "NVIDIA GPU"
            try:                              # GetName works on every NVML build
                import ctypes
                buf = ctypes.create_string_buffer(96)
                nvml.nvmlDeviceGetName(h, buf, 96)
                name = buf.value.decode(errors="ignore") or name
            except Exception:
                try:
                    props = nvml.nvmlDeviceGetProperties(h)
                    n = props.name
                    name = n.decode(errors="ignore") if isinstance(n, bytes) else str(n or name)
                except Exception:
                    pass
            return done(f"{name}: GPU {util.gpu}%, memory {util.memory}%",
                        evidence=f"util.gpu={util.gpu}%, util.memory={util.memory}%, "
                                 f"mem used={mem.used / 1e9:.2f}/{mem.total / 1e9:.2f} GB")
        except Exception as e:
            return unverified("No GPU telemetry available",
                              reason=f"{type(e).__name__}: {e}. NVIDIA GPU + nvidia-ml-py required")

    # ── disks ─────────────────────────────────────────────────────────────────
    if act == "disk":
        import os
        drive = str(args.get("drive") or "").upper().rstrip(":\\/")
        out = []
        got = False
        for letter in ([f"{drive}:\\"] if drive else [f"{d}:\\" for d in "CDEFGH"
                       if os.path.exists(f"{d}:\\")]):
            try:
                u = psutil.disk_usage(letter)
                out.append(f"{letter} {u.used / 1e9:.1f}/{u.total / 1e9:.1f} GB "
                           f"({u.percent}%) free {u.free / 1e9:.1f} GB")
                got = True
            except Exception as e:
                out.append(f"{letter} error: {e}")
        if not got:
            return unverified("No drive could be read", reason="no mount points available")
        return done(f"{len(out)} drive(s) reported",
                    evidence="\n".join(out) + (f"\nio: {psutil.disk_io_counters()}" if psutil.disk_io_counters() else ""))

    # ── temperature ───────────────────────────────────────────────────────────
    if act in ("temp", "temps", "fans"):
        found = {}
        try:
            for chip, entries in (psutil.sensors_temperatures() or {}).items():
                for e in entries:
                    if e.current is not None:
                        found[f"{chip}:{e.label or 'sensor'}"] = (
                            f"{e.current}°C" + (f" (crit {e.critical}°C)" if e.critical else ""))
        except AttributeError:
            pass          # Windows psutil has no sensors_temperatures -> try WMI below
        except Exception:
            pass
        if not found and _OS == "Windows":
            try:
                import wmi
                for tz in wmi.WMI(namespace="root/wmi").MSAcpi_ThermalZoneTemperature():
                    found["ACPI thermal zone"] = f"{(tz.CurrentTemperature / 10.0) - 273.15:.1f}°C"
            except Exception as e:
                return unverified("Temperature sensors are not available",
                                  reason=f"{type(e).__name__}: {e}. Windows only exposes the ACPI "
                                         f"thermal zone via WMI, and it may be absent on this machine.")
        if act in ("fans", "fan"):
            try:
                for chip, entries in (psutil.sensors_fans() or {}).items():
                    for e in entries:
                        found[f"{chip}:{e.label or 'fan'}"] = f"{e.current} RPM"
            except AttributeError:
                pass
        if not found:
            return unverified("No readable temperature/fan sensors",
                              reason="no sensor exposed by psutil/WMI on this machine")
        return done(f"{len(found)} sensor(s) read",
                    evidence="\n".join(f"{k} = {v}" for k, v in list(found.items())[:14]))

    # ── usb ───────────────────────────────────────────────────────────────────
    if act == "usb":
        if _OS != "Windows":
            return failed("USB listing is Windows-only", _OS)
        out = _run(["powershell", "-NoProfile", "-Command",
                    "Get-PnpDevice -Class USB | Where-Object {$_.Status -eq 'OK'} | "
                    "Select-Object -First 20 FriendlyName | Format-Table -HideTableHeaders"])
        lines = [l.strip() for l in out.splitlines() if l.strip()]
        if not lines:
            return unverified("No USB devices reported",
                              reason=f"PowerShell returned nothing: {out[:160]}")
        return done(f"{len(lines)} USB device(s) present", evidence="\n".join(lines[:20]))

    # ── full info ─────────────────────────────────────────────────────────────
    if act == "info":
        import os
        import psutil as P
        parts = []
        parts.append(f"CPU: {platform.processor() or 'unknown'} x{P.cpu_count(logical=True)} "
                     f"@ {P.cpu_freq().current if P.cpu_freq() else '?'} MHz, load {P.cpu_percent()}%")
        vm = P.virtual_memory()
        parts.append(f"RAM: {vm.used / 1e9:.1f}/{vm.total / 1e9:.1f} GB ({vm.percent}%)")
        b = P.sensors_battery()
        parts.append(f"Battery: {b.percent:.0f}% {'charging' if b.power_plugged else 'on battery'}"
                     if b else "Battery: none (desktop)")
        br = _brightness_read()
        parts.append(f"Brightness: {br:.0f}%" if br is not None else "Brightness: unavailable")
        try:
            v, m = _volume_read()
            parts.append(f"Volume: {v}% (mute {'ON' if m else 'OFF'})")
        except Exception:
            parts.append("Volume: unavailable")
        u = P.disk_usage(os.path.expandvars("%SystemDrive%") or "C:\\")
        parts.append(f"System drive: {u.used / 1e9:.1f}/{u.total / 1e9:.1f} GB ({u.percent}%)")
        import time as _t
        parts.append(f"Uptime: {int((_t.time() - P.boot_time()) // 3600)}h")
        return done("Hardware snapshot", evidence="\n".join(parts))

    return failed(f"Unknown hardware_control action '{act}'",
                  "valid: battery brightness volume mute power gpu disk temp usb fans info")


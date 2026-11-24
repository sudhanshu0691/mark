"""
Live telemetry for the MARK UI.

EVERY number here is MEASURED from the running machine (psutil / NVML / Windows),
never a placeholder.  A sample thread updates the snapshot once a second so the UI
render loop only ever does a cheap dict read.

Consumers:
    ui.py   -> TELEMETRY.snapshot / TELEMETRY.history  (what you see on screen)
    main.py -> TELEMETRY.assistant(...)                (voice / task / plugin state)
"""
from __future__ import annotations

import sys
import threading
import time
from collections import deque

try:
    import psutil
except Exception:                                   # pragma: no cover
    psutil = None

SAMPLE_INTERVAL = 1.0
HIST = 60                                           # ~1 minute of sparkline points


def _pct(part, total) -> float:
    try:
        return round(100.0 * part / total, 1) if total else 0.0
    except Exception:
        return 0.0


def fmt_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024.0
    return f"{n:.1f}TB"


def _u_percent(u) -> float:
    """used% of a psutil disk_usage / memory object."""
    return _pct(getattr(u, "used", 0), getattr(u, "total", 0))


def _nvml():
    """nvidia-ml-py ships the `pynvml` module; the legacy `pynvml` package is also OK."""
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            import pynvml as m                # noqa: PLC0415
            if hasattr(m, "nvmlDeviceGetUtilizationRates"):
                return m
        except Exception:
            pass
    return None


class Telemetry:
    """One background sampler + a thread-safe snapshot the UI can read every frame."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

        # measured history (drives the live sparklines)
        self.hist = {
            "cpu":  deque([0.0] * HIST, maxlen=HIST),
            "ram":  deque([0.0] * HIST, maxlen=HIST),
            "gpu":  deque([0.0] * HIST, maxlen=HIST),
            "net":  deque([0.0] * HIST, maxlen=HIST),
            "disk": deque([0.0] * HIST, maxlen=HIST),
        }
        # assistant-side state - pushed by main.py (not measurable by psutil)
        self.assistant_data: dict = {
            "state": "STARTING", "mic_level": 0.0, "speaker_level": 0.0,
            "mic_muted": False, "session": "connecting", "tools_total": 0,
            "plugins_ok": 0, "plugins_bad": 0, "tasks_running": 0, "active_task": "",
        }
        self.verifications: deque = deque(maxlen=80)      # newest last
        self.data: dict = {
            "ok": False, "why": "not sampled yet",
            "cpu_percent": 0.0, "cpu_cores": 0, "cpu_freq_mhz": None,
            "ram_percent": 0.0, "ram_used": "0B", "ram_total": "0B",
            "gpu_percent": None, "gpu_name": "",
            "net_up_bps": 0.0, "net_down_bps": 0.0,
            "disk_percent": 0.0, "disk_used": "0B", "disk_total": "0B", "disk_free": "0B",
            "disk_read_bps": 0.0, "disk_write_bps": 0.0,
            "battery_percent": None, "battery_plugged": None, "battery_time": "",
            "cpu_temp_c": None, "uptime": "0h 0m", "processes": 0,
            "top": [], "hostname": "", "os": "", "python": "",
        }
        self._prev_net = None
        self._prev_disk = None
        self._gpu_handle = None
        self._gpu_tried = False

    # ── optional probes (None = genuinely unavailable, never faked) ───────────
    def _gpu(self):
        if self._gpu_tried:
            return self._gpu_handle
        self._gpu_tried = True
        m = _nvml()
        if m is not None:
            try:
                m.nvmlInit()
                self._gpu_handle = m.nvmlDeviceGetHandleByIndex(0)
                try:
                    p = m.nvmlDeviceGetProperties(self._gpu_handle)
                    n = p.name
                    self.data["gpu_name"] = n.decode(errors="ignore") if isinstance(n, bytes) else str(n or "")
                except Exception:
                    self.data["gpu_name"] = "NVIDIA GPU"
            except Exception:
                self._gpu_handle = None
        return self._gpu_handle

    def _battery(self) -> tuple:
        if not psutil:
            return None, None, ""
        try:
            b = psutil.sensors_battery()
            if b is None:
                return None, None, ""
            left = ""
            if b.power_plugged:
                left = "charging"
            elif b.secsleft and b.secsleft > 0:
                left = f"{int(b.secsleft // 60)}m left"
            return round(b.percent, 1), bool(b.power_plugged), left
        except Exception:
            return None, None, ""

    def _temp(self):
        """CPU temperature: psutil on POSIX, WMI on Windows. None = genuinely unknown."""
        if psutil:
            try:
                for entries in (psutil.sensors_temperatures() or {}).values():
                    for e in entries:
                        if e.current and e.current > 0:
                            return round(float(e.current), 1)
            except AttributeError:
                pass                              # Windows build has no such API
            except Exception:
                pass
        if sys.platform == "win32":
            try:
                import wmi
                tz = wmi.WMI(namespace="root/wmi").MSAcpi_ThermalZoneTemperature()
                if tz:
                    return round((tz[0].CurrentTemperature / 10.0) - 273.15, 1)
            except Exception:
                pass
        return None

    def _disk(self) -> dict:
        out = {"percent": 0.0, "used": 0, "total": 0, "label": ""}
        if not psutil:
            return out
        try:
            best = None
            for part in psutil.disk_partitions(all=False):
                try:
                    u = psutil.disk_usage(part.mountpoint)
                except Exception:
                    continue
                # report the biggest mounted drive (usually the working one)
                if best is None or u.total > best[1]:
                    best = (part.mountpoint, u.total, u)
            if best:
                out.update(percent=_u_percent(best[2]), used=best[2].used,
                           total=best[2].total, label=best[0])
        except Exception:
            pass
        return out

    # ── the sampler ──────────────────────────────────────────────────────────
    def sample(self) -> None:
        d = dict(self.data)
        if not psutil:
            d.update(ok=False, why="psutil is not installed")
            with self._lock:
                self.data = d
            return
        try:
            import platform as _pl
            d["cpu_percent"] = float(psutil.cpu_percent(interval=None))
            d["cpu_cores"] = psutil.cpu_count(logical=True) or 0
            try:
                f = psutil.cpu_freq()
                d["cpu_freq_mhz"] = int(round(f.current)) if f else None
            except Exception:
                d["cpu_freq_mhz"] = None

            vm = psutil.virtual_memory()
            d["ram_percent"] = round(vm.percent, 1)
            d["ram_used"] = fmt_bytes(vm.used)
            d["ram_total"] = fmt_bytes(vm.total)

            h = self._gpu()
            if h is not None:
                try:
                    m = _nvml()
                    d["gpu_percent"] = float(m.nvmlDeviceGetUtilizationRates(h).gpu)
                except Exception:
                    d["gpu_percent"] = None

            now = time.monotonic()
            net = psutil.net_io_counters()
            prev, self._prev_net = self._prev_net, (net.bytes_sent, net.bytes_recv, now)
            if prev and now > prev[2]:
                dt = now - prev[2]
                d["net_up_bps"] = max(0.0, (net.bytes_sent - prev[0]) / dt)
                d["net_down_bps"] = max(0.0, (net.bytes_recv - prev[1]) / dt)

            try:
                dio = psutil.disk_io_counters()
                if dio:
                    prevd, self._prev_disk = self._prev_disk, (dio.read_bytes, dio.write_bytes, now)
                    if prevd and now > prevd[2]:
                        dt = now - prevd[2]
                        d["disk_read_bps"] = max(0.0, (dio.read_bytes - prevd[0]) / dt)
                        d["disk_write_bps"] = max(0.0, (dio.write_bytes - prevd[1]) / dt)
            except Exception:
                pass

            dk = self._disk()
            d["disk_percent"] = dk["percent"]
            d["disk_used"] = fmt_bytes(dk["used"])
            d["disk_total"] = fmt_bytes(dk["total"])
            d["disk_free"] = fmt_bytes(max(0, dk["total"] - dk["used"]))

            pct, plugged, left = self._battery()
            d["battery_percent"], d["battery_plugged"], d["battery_time"] = pct, plugged, left
            d["cpu_temp_c"] = self._temp()

            up = time.time() - psutil.boot_time()
            d["uptime"] = f"{int(up // 3600)}h {int((up % 3600) // 60)}m"
            d["processes"] = len(psutil.pids())
            try:
                import socket
                d["hostname"] = socket.gethostname() or ""
            except Exception:
                d["hostname"] = ""
            d["os"] = f"{_pl.system()} {_pl.release()}"
            d["python"] = _pl.python_version()
            d["ok"] = True
            d["why"] = ""

            try:
                procs = []
                for p in psutil.process_iter(["name", "cpu_percent", "memory_info"]):
                    try:
                        i = p.info
                        procs.append((i.get("cpu_percent") or 0.0, i.get("name") or "?",
                                      i["memory_info"].rss if i.get("memory_info") else 0))
                    except Exception:
                        continue
                procs.sort(reverse=True)
                d["top"] = [{"name": n, "cpu": round(c, 1), "mem": fmt_bytes(m)} for c, n, m in procs[:3]]
            except Exception:
                d["top"] = []
        except Exception as e:
            d["ok"] = False
            d["why"] = f"{type(e).__name__}: {e}"

        with self._lock:
            self.data = d
            self.hist["cpu"].append(d["cpu_percent"])
            self.hist["ram"].append(d["ram_percent"])
            self.hist["gpu"].append(d["gpu_percent"] or 0.0)
            self.hist["net"].append((d["net_up_bps"] + d["net_down_bps"]) / 1e6)
            self.hist["disk"].append((d["disk_read_bps"] + d["disk_write_bps"]) / 1e6)

    def _loop(self) -> None:
        if psutil:
            try:
                psutil.cpu_percent(interval=None)          # prime the delta
            except Exception:
                pass
        while not self._stop.is_set():
            try:
                self.sample()
            except Exception as e:
                print(f"[telemetry] {type(e).__name__}: {e}")
            self._stop.wait(SAMPLE_INTERVAL)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="telemetry")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    # ── public API ───────────────────────────────────────────────────────────
    def snapshot(self) -> dict:
        with self._lock:
            return dict(self.data)

    def assistant(self) -> dict:
        with self._lock:
            return dict(self.assistant_data)

    def set_assistant(self, **kv) -> None:
        with self._lock:
            self.assistant_data.update(kv)

    def add_verification(self, v: dict) -> None:
        """Record that Mark checked whether its own work really happened."""
        v = dict(v)
        v.setdefault("t", time.strftime("%H:%M:%S"))
        with self._lock:
            self.verifications.append(v)

    def verifications_list(self) -> list:
        with self._lock:
            return list(self.verifications)


TELEMETRY = Telemetry()
"""Network helpers: public IP, ping, Wi-Fi name, quick download speed."""
import platform
import re
import subprocess
import time

from core.result import done, failed

NAME = "net_tools"
DESCRIPTION = "Public IP, ping a host, current Wi-Fi, quick speed test."
METADATA = {"version": "1.0.0", "capabilities": ["network", "ping", "wifi", "speed", "ip"], "permissions": [], "timeout": 60}

DECLARATION = {
    "name": "network",
    "description": "action=ip (public IP), ping (host), wifi (connected network), speed (rough download Mbps), status (internet up?).",
    "parameters": {"type": "OBJECT", "properties": {
        "action": {"type": "STRING", "description": "ip | ping | wifi | speed | status"},
        "host": {"type": "STRING", "description": "Host for ping, default 8.8.8.8"}}, "required": ["action"]},
}


def run(args, ctx):
    import requests
    act = str(args.get("action", "status")).lower()
    try:
        if act in ("ip", "status"):
            ip = requests.get("https://api.ipify.org", timeout=6).text.strip()
            return done(f"Internet is up. Public IP {ip}", evidence="api.ipify.org replied")
        if act == "ping":
            host = re.sub(r"[^\w.\-]", "", str(args.get("host") or "8.8.8.8"))
            flag = "-n" if platform.system() == "Windows" else "-c"
            r = subprocess.run(["ping", flag, "4", host], capture_output=True, text=True, timeout=25)
            m = re.findall(r"(?:time|zeit|tiempo)[=<]\s*([\d.]+)\s*ms", r.stdout, re.I)
            if r.returncode == 0 and m:
                ms = [float(x) for x in m]
                return done(f"{host} replies, avg {sum(ms) / len(ms):.0f} ms", evidence=f"{len(ms)}/4 replies")
            return failed(f"{host} did not reply", (r.stdout or r.stderr)[-200:])
        if act == "wifi":
            if platform.system() != "Windows":
                return failed("wifi info is Windows-only", platform.system())
            out = subprocess.run(["netsh", "wlan", "show", "interfaces"], capture_output=True, text=True, timeout=10).stdout
            ssid = re.search(r"^\s*SSID\s*:\s*(.+)$", out, re.M)
            sig = re.search(r"Signal\s*:\s*(\d+%)", out)
            return done(f"Connected to {ssid.group(1).strip()}" + (f", signal {sig.group(1)}" if sig else ""), evidence="netsh wlan") if ssid else failed("Not on Wi-Fi", "no wireless interface connected")
        if act == "speed":
            t = time.time()
            r = requests.get("https://speed.cloudflare.com/__down?bytes=15000000", timeout=40, stream=True)
            n = sum(len(c) for c in r.iter_content(65536))
            sec = max(time.time() - t, 0.01)
            return done(f"Download about {n * 8 / sec / 1e6:.0f} Mbps", evidence=f"{n / 1e6:.1f} MB in {sec:.1f}s (rough, single stream)")
    except Exception as e:
        return failed("Network check failed", f"{type(e).__name__}: {str(e)[:150]} - probably no internet")
    return failed("Unknown network action", "valid: ip ping wifi speed status")

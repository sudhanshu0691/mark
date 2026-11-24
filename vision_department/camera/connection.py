"""Community Qubo cloud-stream adapter (exact-device compatibility is runtime checked)."""
from __future__ import annotations


class QuboCameraConnection:
    """Uses qubopy's authenticated Qubo cloud RTSPS stream; does not guess LAN URLs."""

    def __init__(self, device_id: str, quality: str = "high"):
        self.device_id = device_id.strip()
        self.quality = quality
        self.client = None
        self.device = None
        self.active_quality = quality

    async def open(self) -> str:
        if not self.device_id:
            raise RuntimeError("Set camera.device_id after discovering the camera in Qubo Home")
        try:
            from qubopy import QuboAuth, QuboClient
        except ImportError as exc:
            raise RuntimeError("Optional Qubo connector missing. Install the vision extras from requirements.txt") from exc
        from .session import load_session
        auth = QuboAuth()
        if not load_session(auth):
            await auth.close()
            raise RuntimeError("No Qubo session found. Run MARK Qubo authentication first")
        self.client = QuboClient(auth)
        await self.client.__aenter__()
        try:
            devices = await self.client.get_all_devices()
            self.device = next((item for item in devices if str(getattr(item, "device_id", "")) == self.device_id), None)
            if self.device is None:
                raise RuntimeError("Configured Qubo device UUID was not found in the authenticated account")
            stream_url = await self.device.start_stream(quality=self.quality)
            if not isinstance(stream_url, str) or not stream_url.lower().startswith(("rtsps://", "rtsp://")):
                raise RuntimeError("Qubo client did not return a supported RTSPS/RTSP stream")
            return stream_url
        except Exception:
            await self.close()
            raise

    async def close(self) -> None:
        if self.device is not None:
            try:
                await self.device.stop_stream(quality=self.active_quality)
            except Exception:
                pass
        if self.client is not None:
            try:
                await self.client.__aexit__(None, None, None)
            except Exception:
                pass
        self.device = None
        self.client = None

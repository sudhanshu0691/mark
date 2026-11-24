"""Qubo device discovery and stream-connector lifecycle."""
from __future__ import annotations

import asyncio

from ..camera.connection import QuboCameraConnection
from ..camera.discovery import discover_cameras


class QuboCameraAgent:
    name = "qubo_camera_agent"

    def discover(self):
        return asyncio.run(discover_cameras())

    def resolve_device_id(self, configured_id: str) -> str:
        device_id = str(configured_id).strip()
        if device_id:
            return device_id
        cameras = self.discover()
        if not cameras:
            raise RuntimeError("No Qubo cameras were found in the authenticated account")
        if len(cameras) > 1:
            choices = ", ".join(
                f"{camera['name']} ({camera['device_id']})" for camera in cameras
            )
            raise RuntimeError(
                "Multiple Qubo cameras found. Set camera.device_id in "
                f"vision_department/config/model_config.yaml. Available: {choices}"
            )
        discovered_id = str(cameras[0].get("device_id", "")).strip()
        if not discovered_id:
            raise RuntimeError("Qubo discovery returned a camera without a device ID")
        return discovered_id

    def connector(self, settings):
        if settings.camera_provider != "qubo_cloud":
            raise ValueError(f"Unsupported Qubo camera provider: {settings.camera_provider}")
        device_id = self.resolve_device_id(settings.camera_device_id)
        return QuboCameraConnection(device_id, settings.stream_quality)

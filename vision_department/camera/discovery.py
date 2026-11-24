"""Opt-in discovery through the community Qubo client; no credentials are logged."""
from __future__ import annotations


async def discover_cameras() -> list[dict[str, str]]:
    try:
        from qubopy import QuboAuth, QuboClient
    except ImportError as exc:
        raise RuntimeError("Optional Qubo connector missing. Install the vision extras from requirements.txt") from exc
    from .session import load_session
    auth = QuboAuth()
    if not load_session(auth):
        await auth.close()
        raise RuntimeError("No Qubo session found. Run MARK Qubo authentication first")
    try:
        async with QuboClient(auth) as client:
            devices = await client.get_all_devices()
            results = []
            for device in devices:
                model = str(getattr(device, "model", ""))
                name = str(getattr(device, "name", ""))
                if any(word in f"{model} {name}".casefold() for word in ("camera", "cam", "hcp", "hco")):
                    results.append({"device_id": str(getattr(device, "device_id", "")), "name": name or "Qubo camera", "model": model})
            return results
    finally:
        await auth.close()

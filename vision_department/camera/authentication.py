"""Interactive local Qubo session setup without putting OTP in shell history."""
from __future__ import annotations

import asyncio
from getpass import getpass


async def authenticate_interactively() -> None:
    try:
        from qubopy import QuboAuth
    except ImportError as exc:
        raise RuntimeError("Install the optional vision dependencies before Qubo authentication") from exc
    if __package__:
        from .session import save_session
    else:
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
        from vision_department.camera.session import save_session
    phone = getpass("Qubo account phone number (digits only, hidden): ").strip()
    if not phone.isdigit():
        raise ValueError("Enter the phone number as digits without the country code")
    auth = QuboAuth()  # The library enforces HTTPS for non-local endpoints.
    try:
        print("Requesting a Qubo verification code…")
        pending = await auth.request_phone_otp(phone, isd_code="+91", device_attribute="MARK Vision")
        otp = getpass("Enter the SMS code (hidden): ").strip()
        if not otp:
            raise ValueError("The verification code cannot be empty")
        await auth.verify_phone_otp(phone, otp, isd_code="+91", session_uuid=pending.get("uuid"),
                                    device_attribute="MARK Vision")
        save_session(auth)
        print("Qubo session saved to the current Windows user profile. The code was not written to MARK files or shell history.")
    finally:
        await auth.close()


def main() -> int:
    try:
        asyncio.run(authenticate_interactively())
        return 0
    except KeyboardInterrupt:
        print("Authentication cancelled.")
        return 1
    except Exception as exc:
        # Third-party exception strings may include request details. Keep output generic.
        print(f"Qubo authentication failed ({type(exc).__name__}). Check the Qubo app/account and retry.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

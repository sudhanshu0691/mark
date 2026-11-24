"""Loopback-only FastAPI routes for the embedded MARK vision manager."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from .schemas import VisionStatus

router = APIRouter(prefix="/api/vision", tags=["vision"])
_department = None


def bind_department(department) -> None:
    global _department
    _department = department


def _require_local(request: Request) -> None:
    host = request.client.host if request.client else ""
    if host not in {"127.0.0.1", "::1", "localhost", "testclient"}:
        raise HTTPException(status_code=403, detail="Vision API is available only on this computer")


@router.get("/status", response_model=VisionStatus)
def status(request: Request):
    _require_local(request)
    if _department is None:
        raise HTTPException(status_code=503, detail="Vision Department is not initialized")
    return _department.status


@router.get("/events")
def events(request: Request, limit: int = 100):
    _require_local(request)
    if _department is None:
        raise HTTPException(status_code=503, detail="Vision Department is not initialized")
    return _department.history(limit)


@router.post("/start")
def start(request: Request):
    _require_local(request)
    if _department is None:
        raise HTTPException(status_code=503, detail="Vision Department is not initialized")
    try:
        _department.start()
        return _department.status
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"{type(exc).__name__}: {exc}") from exc


@router.post("/stop")
def stop(request: Request):
    _require_local(request)
    if _department is None:
        raise HTTPException(status_code=503, detail="Vision Department is not initialized")
    _department.stop()
    return _department.status

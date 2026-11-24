"""Optional local API host; bind only to loopback (127.0.0.1)."""
from fastapi import FastAPI

from ..manager import VisionDepartment
from .routes import bind_department, router

app = FastAPI(title="MARK Vision Department", docs_url=None, redoc_url=None)
department = VisionDepartment()
bind_department(department)
app.include_router(router)


@app.on_event("shutdown")
def stop_vision():
    department.stop()

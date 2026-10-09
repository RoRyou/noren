"""NOREN backend entrypoint.

One Cloud Run service: REST + WebSocket APIs under /api and /ws, and the built
frontend (Vite) served as static files for every other path.
"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import REPO_ROOT, settings

app = FastAPI(title="NOREN")

FRONTEND_DIST = REPO_ROOT / "frontend" / "dist"


@app.get("/api/healthz")
async def healthz() -> dict:
    return {"ok": True, "live_model": settings.live_model}


if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{path:path}")
    async def spa(path: str) -> FileResponse:
        # Client-side routing: /t/<token> (customer), /staff, /owner all load index.html
        candidate = FRONTEND_DIST / path
        if path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(Path(FRONTEND_DIST / "index.html"))

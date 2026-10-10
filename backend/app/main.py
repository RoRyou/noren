"""NOREN バックエンドのエントリポイント。

Cloud Run の 1 サービスで、/api と /ws に REST・WebSocket API を置き、
それ以外のパスではビルド済みのフロントエンド（Vite）を静的ファイルとして返す。
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
        # クライアント側ルーティング：/t/<token>（お客様）、/staff、/owner はすべて index.html を返す
        candidate = FRONTEND_DIST / path
        if path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(Path(FRONTEND_DIST / "index.html"))

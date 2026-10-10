"""NOREN バックエンドのエントリポイント。

Cloud Run の 1 サービスで、/api と /ws に REST・WebSocket API を置き、
それ以外のパスではビルド済みのフロントエンド（Vite）を静的ファイルとして返す。
"""

import asyncio
import contextlib
import html
import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from app.api.context import dispatcher
from app.api.routes import router as api_router
from app.api.ws import router as ws_router
from app.config import REPO_ROOT, settings

logging.basicConfig(level=logging.INFO)
FRONTEND_DIST = REPO_ROOT / "frontend" / "dist"
MEDIA_DIR = REPO_ROOT / "backend" / "media"
SWEEP_S = 30


@contextlib.asynccontextmanager
async def lifespan(_app: FastAPI):
    async def sweeper():
        # outbox の見回り（kick で処理しそこねたイベントを拾う）。本番は Cloud Scheduler からも呼べる
        while True:
            await asyncio.sleep(SWEEP_S)
            for repo in list(dispatcher._repos.values()):
                with contextlib.suppress(Exception):
                    await dispatcher.dispatch(repo)

    task = asyncio.create_task(sweeper())
    yield
    task.cancel()


app = FastAPI(title="NOREN", lifespan=lifespan)
app.include_router(api_router)
app.include_router(ws_router)


@app.get("/api/healthz")
async def healthz() -> dict:
    return {"ok": True, "live_model": settings.live_model}


@app.get("/media/menu/{name}")
async def menu_photo(name: str) -> Response:
    """料理の写真。生成画像がまだなければ、料理名入りの仮の画像（SVG）を返す。"""
    path = MEDIA_DIR / "menu" / name
    if path.is_file():
        return FileResponse(path)
    from app.seed.izakaya import build_menu

    item = build_menu().get(Path(name).stem)
    label = html.escape(item.name.ja if item else Path(name).stem)
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="400" height="300"><rect width="400" height="300" fill="#f3ead8"/>'
           f'<text x="200" y="160" font-size="28" text-anchor="middle" fill="#6b4f2a" font-family="sans-serif">{label}</text></svg>')
    return Response(svg, media_type="image/svg+xml")


if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{path:path}")
    async def spa(path: str) -> FileResponse:
        # クライアント側ルーティング：/table（お客様）、/staff、/owner はすべて index.html を返す
        candidate = FRONTEND_DIST / path
        if path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(Path(FRONTEND_DIST / "index.html"))

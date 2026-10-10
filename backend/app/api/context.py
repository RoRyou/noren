"""リクエストがどの店（デモ店）に属するかを決める。

デモでは訪問者ごとに店を 1 つ作り、署名付き cookie（HMAC）で訪問者と店を結び付ける。
署名が合わない cookie は受け付けないので、ほかの人の店を操作することはできない。
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
import uuid
from dataclasses import dataclass

from fastapi import HTTPException, Request, WebSocket
from google.cloud import firestore

from app.config import settings
from app.realtime.hub import hub
from app.realtime.scheduler import scheduler
from app.seed.izakaya import KNOWLEDGE, build_menu, build_store
from app.services.table import TableService
from app.store.dispatcher import Dispatcher
from app.store.repo import Repo

COOKIE = "noren_demo"
DEMO_TTL_S = 24 * 3600
_SECRET = (settings.demo_secret or secrets.token_hex(32)).encode()

_client: firestore.AsyncClient | None = None
dispatcher = Dispatcher(hub, scheduler)


def client() -> firestore.AsyncClient:
    global _client
    if _client is None:
        _client = firestore.AsyncClient(project=settings.firestore_project or settings.google_cloud_project)
    return _client


def sign(store_id: str, expires: int) -> str:
    mac = hmac.new(_SECRET, f"{store_id}.{expires}".encode(), hashlib.sha256).hexdigest()[:32]
    return f"{store_id}.{expires}.{mac}"


def verify(token: str | None) -> str | None:
    if not token:
        return None
    try:
        store_id, expires, mac = token.rsplit(".", 2)
    except ValueError:
        return None
    if int(expires) < time.time():
        return None
    good = hmac.new(_SECRET, f"{store_id}.{expires}".encode(), hashlib.sha256).hexdigest()[:32]
    return store_id if hmac.compare_digest(good, mac) else None


@dataclass
class Ctx:
    repo: Repo
    svc: TableService


def ctx_for(store_id: str) -> Ctx:
    repo = Repo(client(), store_id)
    if store_id.startswith("demo_") and settings.demo_clock:
        return Ctx(repo, TableService(repo, dispatcher, clock=lambda: settings.demo_clock))
    return Ctx(repo, TableService(repo, dispatcher))


async def create_demo_store() -> tuple[str, str]:
    store_id = f"demo_{uuid.uuid4().hex[:12]}"
    expires = int(time.time()) + DEMO_TTL_S
    repo = Repo(client(), store_id)
    await repo.seed(build_store(store_id), build_menu(), KNOWLEDGE, extra={"demo": True, "expires_at": expires})
    return store_id, sign(store_id, expires)


def _resolve(token: str | None) -> Ctx:
    store_id = verify(token)
    if store_id is None:
        raise HTTPException(status_code=401, detail="demo_session_required")
    return ctx_for(store_id)


def http_ctx(request: Request) -> Ctx:
    return _resolve(request.cookies.get(COOKIE) or request.headers.get("x-noren-demo"))


def ws_ctx(ws: WebSocket) -> Ctx:
    return _resolve(ws.cookies.get(COOKIE) or ws.query_params.get("demo"))

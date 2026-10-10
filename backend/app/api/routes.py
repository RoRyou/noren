"""REST API。お客様の画面（タッチ）・店員の端末・デモの入口。"""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.api.context import COOKIE, DEMO_TTL_S, Ctx, create_demo_store, http_ctx, verify
from app.api.limits import allow_demo_creation
from app.domain.models import LineStatus
from app.domain.orders import TransitionError
from app.realtime.hub import hub

router = APIRouter(prefix="/api")


def _dump(m) -> dict:
    return m.model_dump(by_alias=True, mode="json")


# --- デモの入口 -----------------------------------------------------------------
@router.post("/demo/start")
async def demo_start(request: Request, response: Response, reset: bool = False) -> dict:
    """訪問者ごとのデモ店を用意する（cookie があればそのまま使う。reset=true で作り直す）。"""
    current = verify(request.cookies.get(COOKIE))
    if current and not reset:
        return {"store_id": current, "created": False}
    ip = (request.headers.get("x-forwarded-for") or (request.client.host if request.client else "")).split(",")[0].strip()
    if not allow_demo_creation(ip):
        raise HTTPException(429, "too_many_demo_stores")
    store_id, token = await create_demo_store()
    response.set_cookie(COOKIE, token, max_age=DEMO_TTL_S, httponly=True, samesite="lax")
    return {"store_id": store_id, "created": True, "token": token}


# --- お客様 ---------------------------------------------------------------------
class SeatIn(BaseModel):
    table_no: str = "2"
    party_size: int = 2
    lang: str = "ja"


class DeclareIn(BaseModel):
    declarations: list[dict]


class AlcoholIn(BaseModel):
    guest_id: str
    adult: bool
    driving: bool


class CartIn(BaseModel):
    item_id: str
    qty: int = 1
    for_guest: str | None = None
    for_guests: list[str] | None = None  # お酒などを一人一つずつ（画面で複数選んだとき）
    options: dict[str, str] = {}


class QtyIn(BaseModel):
    qty: int


class SubmitIn(BaseModel):
    confirm_token: str
    idempotency_key: str


@router.post("/tables/seat")
async def seat(body: SeatIn, ctx: Ctx = Depends(http_ctx)) -> dict:
    if not 1 <= body.party_size <= 12:
        raise HTTPException(400, "party_size")
    return _dump(await ctx.svc.seat(body.table_no, body.party_size, body.lang))


@router.get("/tables/{sid}")
async def view(sid: str, ctx: Ctx = Depends(http_ctx)) -> dict:
    return await ctx.svc.view(sid)


@router.post("/tables/{sid}/declare")
async def declare(sid: str, body: DeclareIn, ctx: Ctx = Depends(http_ctx)) -> dict:
    return _dump(await ctx.svc.declare(sid, body.declarations))


@router.post("/tables/{sid}/alcohol")
async def alcohol(sid: str, body: AlcoholIn, ctx: Ctx = Depends(http_ctx)) -> dict:
    session = await ctx.svc.set_alcohol(sid, body.guest_id, body.adult, body.driving)
    ok = body.adult and not body.driving
    hub.publish(ctx.repo.store_id, f"table:{sid}", {
        "type": "ui", "event": "alcohol", "guest_id": body.guest_id, "allowed": ok, "source": "touch",
        "say_ja": f"{body.guest_id} はお酒の確認を{'済ませました（20 歳以上・運転しない）' if ok else '済ませましたが、お酒は出せません'}。"})
    return _dump(session)


@router.get("/tables/{sid}/menu")
async def menu(sid: str, category: str | None = None, q: str | None = None, ctx: Ctx = Depends(http_ctx)) -> list[dict]:
    return await ctx.svc.menu_for(sid, category, q)


@router.get("/tables/{sid}/recommend")
async def recommend(sid: str, taste: str | None = None, category: str | None = None, for_guest: str | None = None,
                    ctx: Ctx = Depends(http_ctx)) -> list[dict]:
    return await ctx.svc.recommend(sid, [taste] if taste else None, category, for_guest)


@router.post("/tables/{sid}/cart")
async def add_cart(sid: str, body: CartIn, ctx: Ctx = Depends(http_ctx)) -> dict:
    if body.for_guests:  # 一人一つずつ追加し、確認が必要な人を返す
        checks = []
        for g in body.for_guests:
            c, session = await ctx.svc.add_to_cart(sid, body.item_id, 1, g, body.options, actor="customer_touch")
            checks.append(c)
        check = next((c for c in checks if not c.ok), checks[-1])
        out = {"check": check.to_dict(), "checks": [c.to_dict() for c in checks], "cart": [l.model_dump() for l in session.cart]}
    else:
        check, session = await ctx.svc.add_to_cart(sid, body.item_id, body.qty, body.for_guest, body.options, actor="customer_touch")
        out = {"check": check.to_dict(), "cart": [l.model_dump() for l in session.cart]}
    if check.decision.value in ("rejected", "need_staff"):
        out["alternatives"] = await ctx.svc.alternatives_for(sid, body.item_id, check.for_guest)
    item = (await ctx.repo.menu()).get(body.item_id)
    name = item.name.ja if item else body.item_id
    who = f"{body.for_guest or '、'.join(body.for_guests or [])} の分として" if (body.for_guest or body.for_guests) else ""
    say = {"ok": f"{name}を{who}カートに追加しました。",
           "need_alcohol_check": f"{name}は{who}お酒の確認が必要です（画面に確認を出しています）。",
           "need_guest": f"{name}はどなたの分か選ぶ必要があります。",
           "rejected": f"{name}は追加できませんでした（{'、'.join(check.reasons)}）。",
           "need_staff": f"{name}は店員の確認が必要です（{'、'.join(check.reasons)}）。"}.get(check.decision.value, "")
    hub.publish(ctx.repo.store_id, f"table:{sid}", {"type": "ui", "event": "cart", "cart": out["cart"], "source": "touch",
                                                     "say_ja": say})
    return out


@router.patch("/tables/{sid}/cart/{line_id}")
async def update_cart(sid: str, line_id: str, body: QtyIn, ctx: Ctx = Depends(http_ctx)) -> dict:
    check, session = await ctx.svc.update_line(sid, line_id, body.qty, actor="customer_touch")
    return {"check": check.to_dict() if check else None, "cart": [l.model_dump() for l in session.cart]}


@router.post("/tables/{sid}/read_back")
async def read_back(sid: str, ctx: Ctx = Depends(http_ctx)) -> dict:
    return await ctx.svc.read_back(sid)


@router.post("/tables/{sid}/submit")
async def submit(sid: str, body: SubmitIn, ctx: Ctx = Depends(http_ctx)) -> dict:
    return await ctx.svc.submit(sid, body.confirm_token, body.idempotency_key, actor="customer_touch")


@router.post("/orders/{oid}/lines/{line_id}/cancel")
async def cancel(oid: str, line_id: str, ctx: Ctx = Depends(http_ctx)) -> dict:
    return await ctx.svc.cancel_line(oid, line_id)


@router.post("/tables/{sid}/bill")
async def bill(sid: str, ctx: Ctx = Depends(http_ctx)) -> dict:
    return await ctx.svc.request_bill(sid)


@router.post("/tables/{sid}/emergency")
async def emergency(sid: str, ctx: Ctx = Depends(http_ctx)) -> dict:
    """緊急ボタン：AI を通さずに店員へ。"""
    return await ctx.svc.emergency(sid)


class AskStaffIn(BaseModel):
    item_id: str
    for_guest: str | None = None


@router.post("/tables/{sid}/ask_staff")
async def ask_staff(sid: str, body: AskStaffIn, ctx: Ctx = Depends(http_ctx)) -> dict:
    """△ / ？ の料理を店員に確認してもらう（通常の引き継ぎ）。"""
    from app.domain.escalation import Reason

    item = (await ctx.repo.menu()).get(body.item_id)
    name = item.name.ja if item else body.item_id
    who = f"{body.for_guest} の" if body.for_guest else ""
    return await ctx.svc.escalate(sid, Reason.dietary_check, f"{who}{name}について、アレルギー・食事制限の確認をお願いしたい",
                                  actor="customer_touch", translated=False)


@router.get("/tables/{sid}/notifications")
async def table_notifications(sid: str, ctx: Ctx = Depends(http_ctx)) -> list[dict]:
    return await ctx.repo.unacked_notifications(f"table:{sid}")


@router.post("/notifications/{nid}/ack")
async def ack(nid: str, ctx: Ctx = Depends(http_ctx)) -> dict:
    await ctx.repo.ack_notification(nid)
    return {"ok": True}


# --- 店員 -----------------------------------------------------------------------
class LineStatusIn(BaseModel):
    status: LineStatus
    reason: str = ""


class EscalationIn(BaseModel):
    status: str  # 状態：acknowledged / in_progress / resolved
    handler: str = "店員"


class SoldOutIn(BaseModel):
    item_ids: list[str]
    sold_out: bool = True


@router.get("/staff/board")
async def board(ctx: Ctx = Depends(http_ctx)) -> dict:
    sessions = await ctx.repo.open_sessions()
    return {
        "orders": [_dump(o) for o in await ctx.repo.open_orders()],
        "escalations": await ctx.repo.open_escalations(),
        "tables": [{"id": s.id, "table_no": s.table_no, "party_size": s.party_size, "status": s.status.value,
                    "guests": [g.model_dump() for g in s.guests]} for s in sessions],
        "notifications": await ctx.repo.unacked_notifications("staff"),
    }


@router.post("/staff/orders/{oid}/lines/{line_id}")
async def set_line(oid: str, line_id: str, body: LineStatusIn, ctx: Ctx = Depends(http_ctx)) -> dict:
    try:
        order = await ctx.repo.set_line_status(oid, line_id, body.status, body.reason)
    except TransitionError as e:
        raise HTTPException(409, str(e)) from e
    await ctx.repo.audit("staff", "line_status", {"order_id": oid, "line_id": line_id, "status": body.status.value}, "ok")
    ctx.svc.dispatcher.kick(ctx.repo)
    return _dump(order)


@router.post("/staff/orders/{oid}/paid")
async def mark_paid(oid: str, ctx: Ctx = Depends(http_ctx)) -> dict:
    """先払いの支払いを確認した。"""
    order = await ctx.repo.mark_paid(oid)
    ctx.svc.dispatcher.kick(ctx.repo)
    return _dump(order)


@router.post("/staff/escalations/{eid}")
async def update_escalation(eid: str, body: EscalationIn, ctx: Ctx = Depends(http_ctx)) -> dict:
    fields = {"status": body.status}
    if body.status == "acknowledged":
        fields["handler"] = body.handler
    await ctx.repo.update_escalation(eid, fields)
    await ctx.repo.audit("staff", "escalation", {"id": eid, **fields}, "ok")
    hub.publish(ctx.repo.store_id, "staff", {"type": "escalation_updated", "id": eid, **fields})
    return await ctx.repo.escalation(eid)


@router.post("/staff/tables/{sid}/close")
async def close(sid: str, ctx: Ctx = Depends(http_ctx)) -> dict:
    await ctx.svc.close(sid)
    hub.publish(ctx.repo.store_id, f"table:{sid}", {"type": "ui", "event": "closed"})
    return {"ok": True}


@router.post("/staff/sold_out")
async def sold_out(body: SoldOutIn, ctx: Ctx = Depends(http_ctx)) -> dict:
    await ctx.repo.set_sold_out(body.item_ids, body.sold_out, actor="staff")
    ctx.svc.dispatcher.kick(ctx.repo)
    return {"ok": True}


class AgentIn(BaseModel):
    text: str


@router.post("/staff/agent")
async def store_agent(body: AgentIn, ctx: Ctx = Depends(http_ctx)) -> dict:
    """店舗エージェントに一言で伝える（「出汁が切れた」「今日のおすすめはまぐろ」など）。"""
    from app.services.store_agent import StoreAgentService

    result = await StoreAgentService(ctx.repo).handle(body.text)
    if result.get("applied"):
        ctx.svc.dispatcher.kick(ctx.repo)
    return result


@router.post("/staff/agent/{pending_id}/confirm")
async def store_agent_confirm(pending_id: str, ctx: Ctx = Depends(http_ctx)) -> dict:
    from app.services.store_agent import StoreAgentService

    result = await StoreAgentService(ctx.repo).confirm(pending_id)
    ctx.svc.dispatcher.kick(ctx.repo)
    return result


@router.get("/staff/audit")
async def audit(ctx: Ctx = Depends(http_ctx)) -> list[dict]:
    return await ctx.repo.audit_logs()


@router.get("/staff/stream")
async def staff_stream(ctx: Ctx = Depends(http_ctx)) -> StreamingResponse:
    """店員端末への配信（SSE）。接続したら、まず未確認の通知を送り直す。"""
    q = hub.subscribe(ctx.repo.store_id, "staff")

    async def gen():
        try:
            for n in await ctx.repo.unacked_notifications("staff"):
                yield f"data: {json.dumps({'type': 'notification', **n}, ensure_ascii=False)}\n\n"
            while True:
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=15)
                    yield f"data: {json.dumps(msg, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
        finally:
            hub.unsubscribe(ctx.repo.store_id, "staff", q)

    return StreamingResponse(gen(), media_type="text/event-stream")


# --- 時間指定の処理（Cloud Tasks / 定期実行から呼ばれる） ------------------------------
@router.post("/tasks/sweep")
async def sweep(ctx: Ctx = Depends(http_ctx)) -> dict:
    return {"dispatched": await ctx.svc.dispatcher.dispatch(ctx.repo)}


@router.post("/tasks/run/{name}")
async def run_task(name: str, request: Request) -> dict:
    """Cloud Tasks からの呼び出し（OIDC トークンを検証してから実行）。"""
    from app.api.context import dispatcher
    from app.realtime.cloud_tasks import verify_oidc

    if not verify_oidc(request.headers.get("authorization")):
        raise HTTPException(403, "forbidden")
    handler = dispatcher.scheduler.handlers.get(name)
    if handler is None:
        raise HTTPException(404, name)
    await handler(await request.json())
    return {"ok": True}


@router.post("/tasks/sweep_all")
async def sweep_all(request: Request) -> dict:
    """Cloud Scheduler から定期的に呼ぶ：送信待ちの outbox がある店をすべて処理する。"""
    from google.cloud.firestore_v1.base_query import FieldFilter

    from app.api.context import client, ctx_for, dispatcher
    from app.realtime.cloud_tasks import verify_oidc

    if not verify_oidc(request.headers.get("authorization")):
        raise HTTPException(403, "forbidden")
    stores = set()
    async for snap in client().collection_group("outbox").where(filter=FieldFilter("status", "==", "pending")).stream():
        stores.add(snap.reference.parent.parent.id)
    total = 0
    for store_id in stores:
        total += await dispatcher.dispatch(ctx_for(store_id).repo)
    return {"stores": len(stores), "dispatched": total}

"""Firestore のリポジトリ。店ごとに stores/{store_id} の下へまとめる（デモ店も同じ形）。

業務の書き込みは、同じトランザクションの中で outbox（送信待ちのイベント）も書く（Transactional Outbox）。
通知の送信は outbox を後から処理する側が行う（app/store/dispatcher.py）。
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from datetime import datetime, timedelta, timezone

from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from app.domain.cart import cart_token, recheck_cart
from app.domain.models import (
    Guest, LineStatus, Localized, MenuItem, Order, SessionStatus, StoreSettings, TableSession,
)
from app.domain.orders import (
    StockError, build_lines, otoshi_charge, release_stock, take_stock, transition,
)

JST = timezone(timedelta(hours=9))
TXN_ATTEMPTS = 10  # 同じ料理への同時注文では競合で中断されることがあるため、多めに再試行する

# 在庫が動く書き込み（注文・状態変更・品切れ）は、店ごとにプロセス内で 1 つずつ実行する。
# デモは Cloud Run を 1 インスタンスで動かすので、これで同時実行の競合（エミュレーターではロック待ちが
# 解けないことがある）を避けられる。正しさ自体は Firestore のトランザクションで守る。
_STORE_LOCKS: dict[str, asyncio.Lock] = {}


def store_lock(store_id: str) -> asyncio.Lock:
    return _STORE_LOCKS.setdefault(store_id, asyncio.Lock())


def now_iso() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class OrderError(ValueError):
    def __init__(self, reason: str, detail: dict | None = None):
        super().__init__(reason)
        self.reason = reason
        self.detail = detail or {}


def _dump(model) -> dict:
    return model.model_dump(by_alias=True, mode="json")


class Repo:
    def __init__(self, client: firestore.AsyncClient, store_id: str):
        self.db = client
        self.store_id = store_id
        self.root = client.collection("stores").document(store_id)

    # --- 店・メニュー -------------------------------------------------------
    def col(self, name: str):
        return self.root.collection(name)

    async def seed(self, store: StoreSettings, items: dict[str, MenuItem], knowledge: list[dict], extra: dict | None = None) -> None:
        batch = self.db.batch()
        batch.set(self.root, {**_dump(store), **(extra or {})})
        for item in items.values():
            batch.set(self.col("menu_items").document(item.id), _dump(item))
        for k in knowledge:
            batch.set(self.col("knowledge").document(k["id"]), {**k, "text": _dump(k["text"])})
        await batch.commit()

    async def store(self) -> StoreSettings:
        snap = await self.root.get()
        if not snap.exists:
            raise KeyError(self.store_id)
        return StoreSettings.model_validate(snap.to_dict())

    async def store_meta(self) -> dict:
        snap = await self.root.get()
        return snap.to_dict() or {}

    async def menu(self) -> dict[str, MenuItem]:
        out = {}
        async for snap in self.col("menu_items").stream():
            out[snap.id] = MenuItem.model_validate(snap.to_dict())
        return out

    async def knowledge(self) -> list[dict]:
        out = []
        async for snap in self.col("knowledge").stream():
            d = snap.to_dict()
            d["text"] = Localized.model_validate(d["text"])
            out.append(d)
        return out

    async def update_item(self, item_id: str, fields: dict) -> None:
        await self.col("menu_items").document(item_id).update(fields)

    # --- テーブル -----------------------------------------------------------
    async def create_session(self, table_no: str, party_size: int, lang: str) -> TableSession:
        store = await self.store()
        session = TableSession(
            id=new_id("ses"), table_no=table_no, party_size=party_size, lang=lang,
            guests=[Guest(id=f"g{i + 1}", label=f"ゲスト {i + 1}" if lang == "ja" else f"Guest {i + 1}") for i in range(party_size)],
            seated_at=now_iso(),
        )
        if store.otoshi_price and store.otoshi_label:
            session.charges.append(otoshi_charge(store.otoshi_price, party_size, store.otoshi_label))
        await self.col("sessions").document(session.id).set(_dump(session))
        return session

    async def session(self, session_id: str) -> TableSession:
        snap = await self.col("sessions").document(session_id).get()
        if not snap.exists:
            raise KeyError(session_id)
        return TableSession.model_validate(snap.to_dict())

    async def save_session(self, session: TableSession) -> None:
        await self.col("sessions").document(session.id).set(_dump(session))

    async def open_sessions(self) -> list[TableSession]:
        out = []
        async for snap in self.col("sessions").where(filter=FieldFilter("status", "in", ["dining", "awaiting_prepay", "billing"])).stream():
            out.append(TableSession.model_validate(snap.to_dict()))
        return out

    async def orders_for(self, session_id: str) -> list[Order]:
        out = []
        async for snap in self.col("orders").where(filter=FieldFilter("session_id", "==", session_id)).stream():
            out.append(Order.model_validate(snap.to_dict()))
        out.sort(key=lambda o: o.created_at)
        return out

    async def open_orders(self) -> list[Order]:
        out = []
        async for snap in self.col("orders").stream():
            o = Order.model_validate(snap.to_dict())
            if any(l.status in (LineStatus.accepted, LineStatus.confirmed, LineStatus.cooking) for l in o.lines):
                out.append(o)
        out.sort(key=lambda o: o.created_at)
        return out

    # --- 注文（トランザクション + outbox） -------------------------------------
    async def submit_order(self, session_id: str, token: str, idempotency_key: str, now_hhmm: str | None = None) -> Order:
        """復唱したカートを注文として受け付ける。在庫の引き当てと outbox を同じトランザクションで書く。"""
        order_id = "ord_" + hashlib.sha256(f"{session_id}:{idempotency_key}".encode()).hexdigest()[:16]
        session_ref = self.col("sessions").document(session_id)
        order_ref = self.col("orders").document(order_id)
        store = await self.store()

        @firestore.async_transactional
        async def txn(transaction):
            existing = await order_ref.get(transaction=transaction)
            if existing.exists:  # 同じ送信が二度来ても注文は 1 つ
                return Order.model_validate(existing.to_dict()), False
            snap = await session_ref.get(transaction=transaction)
            session = TableSession.model_validate(snap.to_dict())
            if not session.cart:
                raise OrderError("empty_cart")
            if not token or token != session.confirm_token or token != cart_token(session.cart):
                raise OrderError("not_confirmed")  # 復唱の後にカートが変わった
            item_refs = {l.item_id: self.col("menu_items").document(l.item_id) for l in session.cart}
            items = {}
            for item_id, ref in item_refs.items():
                s = await ref.get(transaction=transaction)
                items[item_id] = MenuItem.model_validate(s.to_dict())
            problems = recheck_cart(session, store, items, now_hhmm)
            if problems:
                line, check = problems[0]
                raise OrderError(check.reasons[0] if check.reasons else "cart_check_failed",
                                 {"line_id": line.line_id, **check.to_dict()})
            lines = build_lines(session.cart, items)
            try:
                changes = take_stock(items, lines)
            except StockError as e:
                raise OrderError(e.reason, {"item_id": e.item_id}) from e
            ts = now_iso()
            for l in lines:
                l.updated_at = ts
            order = Order(id=order_id, session_id=session_id, lines=lines, idempotency_key=idempotency_key, created_at=ts)
            transaction.set(order_ref, _dump(order))
            for c in changes:
                transaction.update(item_refs[c.item_id], {"remaining": c.remaining, "sold_out": c.sold_out,
                                                          "sold_out_reason": c.sold_out_reason})
                if c.became_sold_out:
                    self._outbox(transaction, "sold_out", {"item_id": c.item_id, "cause": "stock"})
            transaction.update(session_ref, {"cart": [], "confirm_token": ""})
            self._outbox(transaction, "order_accepted", {"order_id": order_id, "session_id": session_id})
            return order, True

        try:
            async with store_lock(self.store_id):
                order, _created = await txn(self.db.transaction(max_attempts=TXN_ATTEMPTS))
        except ValueError as e:  # 同じ料理を同時に注文して、再試行しても競合が解けなかった
            if "transaction" in str(e).lower():
                raise OrderError("busy_retry") from e
            raise
        return order

    async def set_line_status(self, order_id: str, line_id: str, to: LineStatus, reason: str = "", actor: str = "staff") -> Order:
        """厨房・店員・お客様による料理ごとの状態変更。断る・取り消すときは在庫を戻す。"""
        order_ref = self.col("orders").document(order_id)

        @firestore.async_transactional
        async def txn(transaction):
            snap = await order_ref.get(transaction=transaction)
            order = Order.model_validate(snap.to_dict())
            idx = next(i for i, l in enumerate(order.lines) if l.line_id == line_id)
            line = order.lines[idx]
            item_ref = self.col("menu_items").document(line.item_id)
            item_snap = await item_ref.get(transaction=transaction)
            item = MenuItem.model_validate(item_snap.to_dict())
            new_line = transition(line, to)  # できない遷移は TransitionError
            new_line.reject_reason = reason if to == LineStatus.rejected else line.reject_reason
            new_line.updated_at = now_iso()
            order.lines[idx] = new_line
            transaction.set(order_ref, _dump(order))
            if to in (LineStatus.rejected, LineStatus.cancelled):
                change = release_stock(item, line.qty)
                if change:
                    transaction.update(item_ref, {"remaining": change.remaining, "sold_out": change.sold_out,
                                                  "sold_out_reason": change.sold_out_reason})
            self._outbox(transaction, "line_status", {"order_id": order_id, "line_id": line_id, "session_id": order.session_id,
                                                      "item_id": line.item_id, "status": to.value, "reason": reason, "actor": actor})
            return order

        async with store_lock(self.store_id):
            return await txn(self.db.transaction(max_attempts=TXN_ATTEMPTS))

    async def set_sold_out(self, item_ids: list[str], sold_out: bool, actor: str) -> None:
        @firestore.async_transactional
        async def txn(transaction):
            for item_id in item_ids:
                transaction.update(self.col("menu_items").document(item_id),
                                   {"sold_out": sold_out, "sold_out_reason": "manual" if sold_out else ""})
                if sold_out:
                    self._outbox(transaction, "sold_out", {"item_id": item_id, "cause": "manual", "actor": actor})

        async with store_lock(self.store_id):
            await txn(self.db.transaction(max_attempts=TXN_ATTEMPTS))

    # --- outbox・通知・引き継ぎ・監査 ---------------------------------------------
    def _outbox(self, transaction, kind: str, payload: dict) -> str:
        event_id = new_id("ev")
        transaction.set(self.col("outbox").document(event_id),
                        {"id": event_id, "kind": kind, "payload": payload, "status": "pending", "created_at": now_iso()})
        return event_id

    async def add_outbox(self, kind: str, payload: dict) -> str:
        event_id = new_id("ev")
        await self.col("outbox").document(event_id).set(
            {"id": event_id, "kind": kind, "payload": payload, "status": "pending", "created_at": now_iso()})
        return event_id

    async def pending_outbox(self, limit: int = 50) -> list[dict]:
        out = []
        async for snap in self.col("outbox").where(filter=FieldFilter("status", "==", "pending")).limit(limit).stream():
            out.append(snap.to_dict())
        out.sort(key=lambda e: e["created_at"])
        return out

    async def mark_dispatched(self, event_id: str) -> None:
        await self.col("outbox").document(event_id).update({"status": "dispatched", "dispatched_at": now_iso()})

    async def put_notification(self, target: str, kind: str, payload: dict, dedupe_key: str) -> tuple[dict, bool]:
        """同じ dedupe_key の通知は 1 つだけ作る（何度呼ばれても重複しない）。"""
        doc_id = hashlib.sha256(dedupe_key.encode()).hexdigest()[:24]
        ref = self.col("notifications").document(doc_id)
        data = {"id": doc_id, "target": target, "kind": kind, "payload": payload, "acked": False, "created_at": now_iso()}
        try:
            await ref.create(data)
            return data, True
        except Exception as e:  # 既にある（AlreadyExists）
            if "already exists" in str(e).lower() or e.__class__.__name__ == "Conflict":
                snap = await ref.get()
                return snap.to_dict(), False
            raise

    async def unacked_notifications(self, target: str) -> list[dict]:
        out = []
        async for snap in self.col("notifications").where(filter=FieldFilter("target", "==", target)).where(filter=FieldFilter("acked", "==", False)).stream():
            out.append(snap.to_dict())
        out.sort(key=lambda n: n["created_at"])
        return out

    async def ack_notification(self, notification_id: str) -> None:
        await self.col("notifications").document(notification_id).update({"acked": True, "acked_at": now_iso()})

    async def create_escalation(self, session_id: str, table_no: str, reason: str, summary_ja: str, urgent: bool) -> dict:
        esc_id = new_id("esc")
        data = {"id": esc_id, "session_id": session_id, "table_no": table_no, "reason": reason, "summary": summary_ja,
                "urgent": urgent, "status": "pending", "handler": "", "reminders": 0, "created_at": now_iso()}

        @firestore.async_transactional
        async def txn(transaction):
            transaction.set(self.col("escalations").document(esc_id), data)
            self._outbox(transaction, "escalation_created", {"escalation_id": esc_id, "urgent": urgent})

        await txn(self.db.transaction())
        return data

    async def escalation(self, esc_id: str) -> dict:
        snap = await self.col("escalations").document(esc_id).get()
        return snap.to_dict()

    async def update_escalation(self, esc_id: str, fields: dict) -> None:
        await self.col("escalations").document(esc_id).update(fields)

    async def open_escalations(self) -> list[dict]:
        out = []
        async for snap in self.col("escalations").where(filter=FieldFilter("status", "in", ["pending", "acknowledged", "in_progress"])).stream():
            out.append(snap.to_dict())
        out.sort(key=lambda e: (not e["urgent"], e["created_at"]))
        return out

    async def audit(self, actor: str, action: str, args: dict, result: str, reason: str = "", correlation_id: str = "",
                    session_id: str = "") -> None:
        meta = await self.store_meta()
        await self.col("audit_logs").document(new_id("log")).set({
            "at": now_iso(), "actor": actor, "action": action, "args": args, "result": result, "reason": reason,
            "correlation_id": correlation_id, "session_id": session_id, "rule_version": meta.get("rule_version", 1),
        })

    async def audit_logs(self, limit: int = 100) -> list[dict]:
        out = []
        async for snap in self.col("audit_logs").order_by("at", direction=firestore.Query.DESCENDING).limit(limit).stream():
            out.append(snap.to_dict())
        return out


def make_client(project: str) -> firestore.AsyncClient:
    return firestore.AsyncClient(project=project)


__all__ = ["Repo", "OrderError", "make_client", "now_iso", "new_id", "SessionStatus"]

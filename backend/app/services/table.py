"""テーブル単位の操作。画面のボタンも AI のツールも、ここを呼ぶ（チェックを 1 か所にまとめる）。"""

from __future__ import annotations

import asyncio
import re

import uuid
from datetime import datetime, timedelta

from google.cloud import firestore

from app.domain import escalation as esc_rules
from app.domain.cart import CartCheck, Decision, cart_token, check_add, recheck_cart
from app.domain.dietary import describe_reasons, judge_table
from app.domain.models import AlcoholCheck, CartLine, LineStatus, MenuItem, SessionStatus, TableSession
from app.domain.orders import can_customer_cancel, estimate_wait_min, make_bill, unit_price
from app.domain.recommend import alternatives, recommend
from app.domain.table_state import derive_phase
from app.store.dispatcher import Dispatcher
from app.store.repo import JST, OrderError, Repo, now_iso

RETENTION_DAYS = 7


def now_hhmm() -> str:
    return datetime.now(JST).strftime("%H:%M")


def _dump(m) -> dict:
    return m.model_dump(by_alias=True, mode="json")


# 同じテーブルのカートへの書き込みは 1 つずつ（AI が add_item を同時に何回も呼ぶと、トランザクションが
# 互いに取り合って失敗した：ZI-075）。前提：Cloud Run は 1 インスタンス（ZI-045 と同じ）
_CART_LOCKS: dict[str, asyncio.Lock] = {}


def _cart_lock(session_id: str) -> asyncio.Lock:
    return _CART_LOCKS.setdefault(session_id, asyncio.Lock())


def _cart_fields(session: TableSession) -> dict:
    return {"cart": [l.model_dump(mode="json") for l in session.cart], "confirm_token": session.confirm_token}


STOPWORDS = {"the", "a", "an", "of", "and", "with", "some", "any", "do", "you", "have", "is", "there", "please", "menu"}


def _tokens(text: str) -> list[str]:
    """英語などの単語に分ける（複数形の s は取る）。日本語・中国語・韓国語は分けずにそのまま使う。"""
    words = re.findall(r"[a-z0-9]+", text.lower())
    return [w[:-1] if len(w) > 3 and w.endswith("s") else w for w in words if w not in STOPWORDS]


def _kana(text: str) -> str:
    """カタカナをひらがなにそろえる（「マグロ」で「まぐろの刺身」が見つかるように。音声の文字起こしはカタカナが多い）。"""
    return "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in text)


def match_score(item: MenuItem, query: str) -> int:
    """料理名・説明・食材に、検索語がどれだけ当たるか（0 は当たらない）。「chicken skewers」でも焼き鳥が見つかる。"""
    texts = [item.name.ja, item.name.en, item.name.zh_hans, item.name.zh_hant, item.name.ko, " ".join(item.ingredients),
             item.search_terms]
    if item.description:
        texts += [item.description.ja, item.description.en]
    hay = _kana(" ".join(t for t in texts if t).lower())
    q = _kana(query.lower().strip())
    if q and q in hay:
        return 100  # そのまま含まれる（日本語の料理名など）
    words = set(_tokens(hay))
    return sum(1 for w in _tokens(q) if len(w) >= 3 and w in words)


class TableService:
    def __init__(self, repo: Repo, dispatcher: Dispatcher, clock=now_hhmm):
        self.repo = repo
        self.dispatcher = dispatcher
        self.clock = clock

    # --- 着席・申告 -----------------------------------------------------------
    async def seat(self, table_no: str, party_size: int, lang: str) -> TableSession:
        session = await self.repo.create_session(table_no, party_size, lang)
        await self.repo.audit("customer", "seat", {"table_no": table_no, "party_size": party_size, "lang": lang}, "ok",
                              session_id=session.id)
        return session

    async def declare(self, session_id: str, declarations: list[dict]) -> TableSession:
        """食事の申告（会話で聞いた内容、または画面で入力）。declarations: [{guest_id, allergies, diets, severe, label?}]。「なし」も申告。"""
        async with _cart_lock(session_id):  # 同時に動くカートの書き込みと順番に（ZI-078）
            session = await self._declare(session_id, declarations)
        severe = [g for g in session.guests if g.severe and g.allergies]
        for g in severe:
            await self.escalate(session_id, esc_rules.Reason.severe_allergy, "", unknown_facts=[])
        return session

    async def _declare(self, session_id: str, declarations: list[dict]) -> TableSession:
        session = await self.repo.session(session_id)
        for d in declarations:
            g = session.guest(d["guest_id"])
            if g is None:
                continue
            g.allergies = sorted(set(d.get("allergies", [])))
            g.diets = sorted(set(d.get("diets", [])))
            g.severe = bool(d.get("severe", False))
            g.declared = True
            if d.get("label"):
                g.label = d["label"]
        await self.repo.update_session(session_id, {"guests": [g.model_dump(mode="json") for g in session.guests]})
        await self.repo.audit("customer", "declare", {"declarations": declarations}, "ok", session_id=session_id)
        return session

    async def set_alcohol(self, session_id: str, guest_id: str, adult: bool, driving: bool) -> TableSession:
        async with _cart_lock(session_id):
            return await self._set_alcohol(session_id, guest_id, adult, driving)

    async def _set_alcohol(self, session_id: str, guest_id: str, adult: bool, driving: bool) -> TableSession:
        session = await self.repo.session(session_id)
        g = session.guest(guest_id)
        if g is None:
            raise KeyError(guest_id)
        g.alcohol = AlcoholCheck(adult=adult, driving=driving, at=now_iso())
        await self.repo.update_session(session_id, {"guests": [x.model_dump(mode="json") for x in session.guests]})
        await self.repo.audit("customer", "confirm_alcohol", {"guest_id": guest_id, "adult": adult, "driving": driving},
                              "ok", reason="self_declared", session_id=session_id)
        return session

    # --- カート ---------------------------------------------------------------
    async def add_to_cart(self, session_id: str, item_id: str, qty: int = 1, for_guest: str | None = None,
                          options: dict | None = None, actor: str = "customer", correlation_id: str = "") -> tuple[CartCheck, TableSession]:
        """カートに追加。音声・文字・タッチのどこから来ても cart.check_add を通る。"""
        store = await self.repo.store()
        session_ref = self.repo.col("sessions").document(session_id)
        item_ref = self.repo.col("menu_items").document(item_id)
        clock = self.clock()

        @firestore.async_transactional
        async def txn(transaction):
            s_snap = await session_ref.get(transaction=transaction)
            i_snap = await item_ref.get(transaction=transaction)
            session = TableSession.model_validate(s_snap.to_dict())
            if not i_snap.exists:
                return CartCheck(Decision.rejected, ["not_available"]), session
            item = MenuItem.model_validate(i_snap.to_dict())
            existing = next((l for l in session.cart if l.item_id == item_id and l.options == (options or {})
                             and (l.for_guest == for_guest or (for_guest is None and l.for_guest is None))), None)
            total = qty + (existing.qty if existing else 0)
            check = check_add(session, store, item, total, for_guest, clock, options)
            if check.ok:
                if existing:
                    existing.qty = total
                    existing.for_guest = check.for_guest
                else:
                    session.cart.append(CartLine(line_id=f"cl_{uuid.uuid4().hex[:8]}", item_id=item_id, qty=qty,
                                                 for_guest=check.for_guest, options=options or {}))
                session.confirm_token = ""  # カートが変わったら復唱をやり直す
                transaction.update(session_ref, _cart_fields(session))  # 申告など他の欄を上書きしない（ZI-078）
            return check, session

        async with _cart_lock(session_id):
            check, session = await txn(self.repo.db.transaction(max_attempts=10))
        await self.repo.audit(actor, "add_item", {"item_id": item_id, "qty": qty, "for_guest": for_guest},
                              "allowed" if check.ok else "blocked", reason=",".join(check.reasons),
                              correlation_id=correlation_id, session_id=session_id)
        return check, session

    async def update_line(self, session_id: str, line_id: str, qty: int, actor: str = "customer") -> tuple[CartCheck | None, TableSession]:
        """カートの数量変更（0 で削除）。追加と同じくトランザクションで書く。"""
        store = await self.repo.store()
        session_ref = self.repo.col("sessions").document(session_id)
        clock = self.clock()

        @firestore.async_transactional
        async def txn(transaction):
            session = TableSession.model_validate((await session_ref.get(transaction=transaction)).to_dict())
            line = next((l for l in session.cart if l.line_id == line_id), None)
            if line is None:
                raise KeyError(line_id)
            item_snap = await self.repo.col("menu_items").document(line.item_id).get(transaction=transaction)
            if qty <= 0:
                session.cart = [l for l in session.cart if l.line_id != line_id]
                session.confirm_token = ""
                transaction.update(session_ref, _cart_fields(session))  # 申告など他の欄を上書きしない（ZI-078）
                return None, session
            check = check_add(session, store, MenuItem.model_validate(item_snap.to_dict()), qty, line.for_guest, clock)
            if check.ok:
                line.qty = qty
                session.confirm_token = ""
                transaction.update(session_ref, _cart_fields(session))  # 申告など他の欄を上書きしない（ZI-078）
            return check, session

        async with _cart_lock(session_id):
            check, session = await txn(self.repo.db.transaction(max_attempts=10))
        await self.repo.audit(actor, "update_item" if qty > 0 else "remove_item", {"line_id": line_id, "qty": qty},
                              "allowed" if check is None or check.ok else "blocked",
                              reason=",".join(check.reasons) if check else "", session_id=session_id)
        return check, session

    async def read_back(self, session_id: str) -> dict:
        """復唱：カート全体を再チェックし、確認トークンを発行する。"""
        session = await self.repo.session(session_id)
        store = await self.repo.store()
        items = await self.repo.menu()
        problems = recheck_cart(session, store, items, self.clock())
        lines = []
        for l in session.cart:
            item = items[l.item_id]
            lines.append({
                "line_id": l.line_id, "item_id": l.item_id, "name": item.name.get(session.lang), "qty": l.qty,
                "for_guest": l.for_guest, "price": unit_price(item, l.options), "amount": unit_price(item, l.options) * l.qty,
                "options": {g.key: next(c.label.get(session.lang) for c in g.choices if c.key == l.options.get(g.key, g.default))
                            for g in item.option_groups},
                "allergens": item.allergens, "cross_contact": item.cross_contact,
                "allergen_status": item.allergen_status.value,
            })
        result = {"lines": lines, "total": sum(x["amount"] for x in lines),
                  "problems": [{"line_id": l.line_id, **c.to_dict()} for l, c in problems],
                  "wait_min": estimate_wait_min(await self.repo.open_orders())}
        if session.cart and not problems:
            session.confirm_token = cart_token(session.cart)
            await self.repo.update_session(session_id, {"confirm_token": session.confirm_token})
            result["confirm_token"] = session.confirm_token
        return result

    async def submit(self, session_id: str, confirm_token: str, idempotency_key: str, actor: str = "customer",
                     correlation_id: str = "") -> dict:
        try:
            order = await self.repo.submit_order(session_id, confirm_token, idempotency_key, self.clock())
        except OrderError as e:
            await self.repo.audit(actor, "submit_order", {"idempotency_key": idempotency_key}, "blocked", reason=e.reason,
                                  correlation_id=correlation_id, session_id=session_id)
            return {"ok": False, "reason": e.reason, **e.detail}
        await self.repo.audit(actor, "submit_order", {"order_id": order.id}, "allowed", correlation_id=correlation_id,
                              session_id=session_id)
        self.dispatcher.kick(self.repo)
        if order.prepay:  # 先払い：レジで支払ってから厨房が作る
            return {"ok": True, "order": _dump(order), "message_key": "pay_first"}
        return {"ok": True, "order": _dump(order), "message_key": "received"}  # 厨房が受けるまでは「受け付けました」

    async def cancel_line(self, order_id: str, line_id: str) -> dict:
        snap = await self.repo.col("orders").document(order_id).get()
        line = next(l for l in snap.to_dict()["lines"] if l["line_id"] == line_id)
        from app.domain.models import OrderLine

        if not can_customer_cancel(OrderLine.model_validate(line)):
            return {"ok": False, "reason": "already_cooking"}  # 調理が始まったら店員へ
        if (await self.repo.store()).service_policy.get("cancel_before_cooking", "auto") == "staff":
            await self.escalate(snap.to_dict()["session_id"], esc_rules.Reason.other, f"「{line['name']['ja']}」を取り消したい",
                                actor="policy", translated=False)
            return {"ok": False, "reason": "staff_will_handle"}
        order = await self.repo.set_line_status(order_id, line_id, LineStatus.cancelled, actor="customer")
        self.dispatcher.kick(self.repo)
        return {"ok": True, "order": _dump(order)}

    # --- 照会・おすすめ ---------------------------------------------------------
    async def view(self, session_id: str) -> dict:
        """画面とAIの両方に渡すテーブルの状態。"""
        session = await self.repo.session(session_id)
        store = await self.repo.store()
        orders = await self.repo.orders_for(session_id)
        phase = derive_phase(session, orders, store, self.clock())
        return {"session": _dump(session), "orders": [_dump(o) for o in orders], "phase": phase.value,
                "store": _dump(store)}

    async def menu_for(self, session_id: str, category: str | None = None, query: str | None = None,
                       taste: list[str] | None = None) -> list[dict]:
        session = await self.repo.session(session_id)
        items = await self.repo.menu()
        scored = []
        for item in items.values():
            if not item.published:
                continue
            if category and item.category != category:
                continue
            if taste and not set(taste) & set(item.taste_tags):
                continue
            score = match_score(item, query) if query else 1
            if score:
                scored.append((score, item))
        scored.sort(key=lambda x: -x[0])  # よく合うものから
        return [self.card(item, session) for _, item in scored]

    def card(self, item: MenuItem, session: TableSession) -> dict:
        """料理カード。お客様ごとの判定（○✕△？）を必ず付ける。"""
        verdicts = judge_table(item, session.guests)
        lang = session.lang
        return {
            "item_id": item.id, "name": item.name.get(lang), "name_ja": item.name.ja, "price": item.price,
            "category": item.category, "photo_url": item.photo_url, "photo_is_generated": item.photo_is_generated,
            "is_alcohol": item.is_alcohol, "sold_out": item.sold_out, "remaining": item.remaining,
            "description": item.description.get(lang) if item.description else "",
            "how_to_eat": item.how_to_eat.get(lang) if item.how_to_eat else "",
            "portion": item.portion.get(lang) if item.portion else "",
            "allergens": item.allergens, "cross_contact": item.cross_contact, "taste_tags": item.taste_tags,
            "options": [{"key": g.key, "label": g.label.get(lang), "default": g.default,
                         "choices": [{"key": c.key, "label": c.label.get(lang), "price_delta": c.price_delta} for c in g.choices]}
                        for g in item.option_groups],
            "allergen_status": item.allergen_status.value,
            "marks": {gid: {"mark": v.verdict.mark, "reasons": describe_reasons(v.reasons, "ja" if lang == "ja" else "en")}
                      for gid, v in verdicts.items()},
        }

    async def recommend(self, session_id: str, taste: list[str] | None = None, category: str | None = None,
                        for_guest: str | None = None, limit: int = 3) -> list[dict]:
        session = await self.repo.session(session_id)
        store = await self.repo.store()
        items = await self.repo.menu()
        orders = await self.repo.orders_for(session_id)
        phase = derive_phase(session, orders, store, self.clock())
        recs = recommend(items, session, phase, taste, category, for_guest, limit)
        if recs:
            await self.repo.update_session(session_id, {"recommended": firestore.ArrayUnion([r.item_id for r in recs])})
        return [{**self.card(items[r.item_id], session), "reasons": r.to_dict(items, session.lang)["reasons"]} for r in recs]

    async def alternatives_for(self, session_id: str, item_id: str, for_guest: str | None) -> list[dict]:
        session = await self.repo.session(session_id)
        items = await self.repo.menu()
        recs = alternatives(items, session, items[item_id], for_guest)
        return [self.card(items[r.item_id], session) for r in recs]

    async def check_dietary(self, session_id: str, item_id: str) -> dict:
        session = await self.repo.session(session_id)
        items = await self.repo.menu()
        return self.card(items[item_id], session)

    # --- 会計・閉店・引き継ぎ ------------------------------------------------------
    async def request_bill(self, session_id: str) -> dict:
        session = await self.repo.session(session_id)
        orders = await self.repo.orders_for(session_id)
        bill = make_bill(session, orders)
        if session.status == SessionStatus.dining:
            await self.repo.update_session(session_id, {
                "status": SessionStatus.billing.value,
                "bill_requested_at": now_iso(),
            })
        return bill.__dict__

    async def close(self, session_id: str, actor: str = "staff") -> None:
        await self.repo.update_session(session_id, {
            "status": SessionStatus.closed.value, "closed_at": now_iso(),
            "delete_after": (datetime.now(JST) + timedelta(days=RETENTION_DAYS)).isoformat(timespec="seconds")})
        await self.repo.audit(actor, "close_table", {"session_id": session_id}, "ok", session_id=session_id)

    async def escalate(self, session_id: str, reason: esc_rules.Reason, utterance_ja: str = "",
                       unknown_facts: list[str] | None = None, actor: str = "reception_agent", correlation_id: str = "",
                       translated: bool = True) -> dict:
        session = await self.repo.session(session_id)
        orders = await self.repo.orders_for(session_id)
        summary = esc_rules.build_summary(session.table_no, reason, utterance_ja, session, orders, unknown_facts, translated)
        data = await self.repo.create_escalation(session_id, session.table_no, reason.value, summary, reason in esc_rules.URGENT)
        await self.repo.audit(actor, "escalate", {"reason": reason.value}, "escalated", reason=summary,
                              correlation_id=correlation_id, session_id=session_id)
        self.dispatcher.kick(self.repo)
        return data

    async def emergency(self, session_id: str) -> dict:
        """緊急ボタン：AI を通さず、そのまま店員へ。"""
        return await self.escalate(session_id, esc_rules.Reason.medical, "", ["症状"], actor="emergency_button")

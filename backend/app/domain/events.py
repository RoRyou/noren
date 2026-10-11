"""イベント → 通知のルール。「知らせるか・誰に知らせるか」はコードで決め、AI は言い方だけを担当する。

通知はお客様の画面（カード + 通知音）か店員の端末に出す。お客様側のマイクを勝手に開けることはない。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from app.domain.models import LineStatus, Order, SessionStatus, StoreSettings, TableSession

URGENT_REPEAT_S = 30
URGENT_ESCALATE_S = 120
NORMAL_ESCALATE_S = 120
OVERDUE_MIN = 20
LAST_ORDER_NOTICE_MIN = 15
UNPAID_BILL_MIN = 15


@dataclass
class Notification:
    target: str  # "table:<session_id>" か "staff"
    kind: str  # 通知の種類：sold_out / rejected / last_order / overdue / escalation_repeat / escalation_all
    payload: dict = field(default_factory=dict)
    dedupe_key: str = ""  # 同じ通知を二度出さないためのキー（イベント ID から作る）


def on_sold_out(event_id: str, item_id: str, sessions: list[TableSession], orders_by_session: dict[str, list[Order]]) -> list[Notification]:
    """品切れ：カートに入っているテーブルと、受付済みだが厨房がまだ受けていないテーブルに知らせる。"""
    out: list[Notification] = []
    for s in sessions:
        in_cart = any(l.item_id == item_id for l in s.cart)
        unconfirmed = [
            l.line_id for o in orders_by_session.get(s.id, []) for l in o.lines
            if l.item_id == item_id and l.status == LineStatus.accepted
        ]
        if in_cart or unconfirmed:
            out.append(Notification(f"table:{s.id}", "sold_out", {"item_id": item_id, "in_cart": in_cart,
                                                                  "lines": unconfirmed}, f"{event_id}:{s.id}"))
        if unconfirmed:
            out.append(Notification("staff", "sold_out_ordered", {"item_id": item_id, "session_id": s.id,
                                                                  "lines": unconfirmed}, f"{event_id}:staff:{s.id}"))
    return out


def on_rejected(event_id: str, session_id: str, line_id: str, item_id: str, reason: str) -> list[Notification]:
    return [Notification(f"table:{session_id}", "rejected", {"line_id": line_id, "item_id": item_id, "reason": reason},
                         f"{event_id}:{session_id}")]


def last_order_notice(event_id: str, sessions: list[TableSession]) -> list[Notification]:
    return [Notification(f"table:{s.id}", "last_order", {"minutes": LAST_ORDER_NOTICE_MIN}, f"{event_id}:{s.id}")
            for s in sessions if s.status.value == "dining"]


def overdue(event_id: str, session_id: str, line_id: str, minutes: int) -> list[Notification]:
    return [Notification("staff", "overdue", {"session_id": session_id, "line_id": line_id, "minutes": minutes},
                         f"{event_id}:{line_id}")]


def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def time_rules(store: StoreSettings, sessions: list[TableSession], orders: list[Order], now_hhmm: str,
               now: datetime) -> list[Notification]:
    """時間で決まる通知（定期的な見回りで呼ぶ。dedupe_key で 1 回だけになる）。
    - ラストオーダーの LAST_ORDER_NOTICE_MIN 分前から：食事中のテーブルに 1 回だけ知らせる
    - 注文から OVERDUE_MIN 分たっても出ていない料理：店員に 1 回だけ知らせる"""
    out: list[Notification] = []
    if store.last_order and now_hhmm and 0 <= _minutes(store.last_order) - _minutes(now_hhmm) <= LAST_ORDER_NOTICE_MIN:
        out += last_order_notice(f"lo:{now.date().isoformat()}", sessions)
    for s in sessions:
        if s.status == SessionStatus.billing and s.bill_requested_at:
            waited = int((now - datetime.fromisoformat(s.bill_requested_at)).total_seconds() // 60)
            if waited >= UNPAID_BILL_MIN:
                out.append(Notification("staff", "unpaid_bill",
                                        {"session_id": s.id, "table_no": s.table_no, "minutes": waited},
                                        f"unpaid:{s.id}"))
    for o in orders:
        if o.prepay and not o.paid:  # 先払いでまだ払っていない：厨房の遅れではない
            continue
        waited = int((now - datetime.fromisoformat(o.created_at)).total_seconds() // 60) if o.created_at else 0
        if waited < OVERDUE_MIN:
            continue
        for l in o.lines:
            if l.status in (LineStatus.accepted, LineStatus.confirmed, LineStatus.cooking):
                out += overdue("overdue", o.session_id, l.line_id, waited)
    return out


def escalation_followups(escalation_id: str, urgent: bool) -> list[tuple[int, str]]:
    """未対応の引き継ぎに対する再通知の予定（何秒後に、何をするか）。Cloud Tasks に登録する。"""
    if urgent:
        steps = [(URGENT_REPEAT_S * i, "escalation_repeat") for i in range(1, URGENT_ESCALATE_S // URGENT_REPEAT_S)]
        steps.append((URGENT_ESCALATE_S, "escalation_all"))
        return steps
    return [(NORMAL_ESCALATE_S, "escalation_repeat")]

"""テーブルの状態（いまどの段階か）を、注文と時刻から推定する。

AI の指示文に入れて「いま何をすべきか」を判断させ、イベントのルールもこれを見る。
"""

from __future__ import annotations

from enum import StrEnum

from app.domain.models import LineStatus, Order, SessionStatus, StoreSettings, TableSession


class Phase(StrEnum):
    seated = "seated"  # 着席したばかり
    ordering = "ordering"  # 注文を選んでいる
    waiting = "waiting"  # 料理待ち
    eating = "eating"  # 食事中
    near_last_order = "near_last_order"  # ラストオーダー前
    billing = "billing"  # 会計中
    left = "left"  # 退店済み


PHASE_GUIDE = {
    Phase.seated: "料金ルールを伝え、雑談しながらおすすめを始める。",
    Phase.ordering: "追加のおすすめや質問に答える。",
    Phase.waiting: "料理の状況を聞かれたら、料理ごとの状態で答える。",
    Phase.eating: "合う料理や飲み物をすすめ、追加注文を受ける。",
    Phase.near_last_order: "ラストオーダーが近いことを一度だけ伝え、追加するか聞く。",
    Phase.billing: "会計の内訳を説明する。追加注文は受けない。",
    Phase.left: "何もしない。",
}


def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def derive_phase(session: TableSession, orders: list[Order], store: StoreSettings, now_hhmm: str | None = None) -> Phase:
    if session.status == SessionStatus.closed:
        return Phase.left
    if session.status == SessionStatus.billing:
        return Phase.billing
    live_lines = [l for o in orders for l in o.lines if l.status not in (LineStatus.rejected, LineStatus.cancelled)]
    served = any(l.status == LineStatus.served for l in live_lines)
    pending = any(l.status in (LineStatus.accepted, LineStatus.confirmed, LineStatus.cooking) for l in live_lines)
    if store.last_order and now_hhmm:
        left = _minutes(store.last_order) - _minutes(now_hhmm)
        if 0 <= left <= 15:
            return Phase.near_last_order
    if served:
        return Phase.eating
    if pending:
        return Phase.waiting
    if session.cart:
        return Phase.ordering
    return Phase.seated

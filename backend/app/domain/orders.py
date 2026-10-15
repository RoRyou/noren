"""注文の状態遷移（料理ごと）、在庫の増減、会計。

料理ごと：accepted（受付）→ confirmed（厨房が受けた）/ rejected（断った）→ cooking → served。
調理が始まる前なら、お客様が自分で取り消せる（cancelled）。
注文全体の状態は、料理ごとの状態をまとめて決める。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.domain.models import CartLine, Charge, LineStatus, MenuItem, Order, OrderLine, TableSession

ALLOWED: dict[LineStatus, set[LineStatus]] = {
    LineStatus.accepted: {LineStatus.confirmed, LineStatus.rejected, LineStatus.cancelled},
    LineStatus.confirmed: {LineStatus.cooking, LineStatus.rejected, LineStatus.cancelled},
    LineStatus.cooking: {LineStatus.served},
    LineStatus.served: set(),
    LineStatus.rejected: set(),
    LineStatus.cancelled: set(),
}
CUSTOMER_CANCELLABLE = {LineStatus.accepted, LineStatus.confirmed}
RELEASES_STOCK = {LineStatus.rejected, LineStatus.cancelled}


class TransitionError(ValueError):
    pass


def transition(line: OrderLine, to: LineStatus) -> OrderLine:
    if to not in ALLOWED[line.status]:
        raise TransitionError(f"{line.status} → {to} はできません")
    return line.model_copy(update={"status": to})


def can_customer_cancel(line: OrderLine) -> bool:
    return line.status in CUSTOMER_CANCELLABLE


def aggregate_status(order: Order) -> str:
    """料理ごとの状態から注文全体の状態を決める。"""
    states = {l.status for l in order.lines}
    live = states - {LineStatus.rejected, LineStatus.cancelled}
    if not live:
        return "void"
    if live == {LineStatus.served}:
        return "all_served"
    if LineStatus.served in live:
        return "partly_served"
    if live & {LineStatus.confirmed, LineStatus.cooking}:
        return "in_kitchen"
    return "accepted"


def resolve_options(item: MenuItem, chosen: dict[str, str]) -> tuple[dict[str, str], int]:
    """選択肢を確定する（選ばなかったものは既定値）。戻り値は（確定した選択, 追加料金の合計）。不正なら ValueError。"""
    groups = {g.key: g for g in item.option_groups}
    for key in chosen:
        if key not in groups:
            raise ValueError(f"invalid_option:{key}")
    resolved, delta = {}, 0
    for g in item.option_groups:
        choice_key = chosen.get(g.key, g.default)
        choice = next((c for c in g.choices if c.key == choice_key), None)
        if choice is None:
            raise ValueError(f"invalid_option:{g.key}")
        resolved[g.key] = choice.key
        delta += choice.price_delta
    return resolved, delta


def unit_price(item: MenuItem, chosen: dict[str, str]) -> int:
    return item.price + resolve_options(item, chosen)[1]


def build_lines(cart: list[CartLine], items: dict[str, MenuItem]) -> list[OrderLine]:
    """カート → 注文明細（名前と価格はこの時点のスナップショット。選択肢の追加料金を含む）。"""
    return [
        OrderLine(
            line_id=c.line_id,
            item_id=c.item_id,
            name=items[c.item_id].name,
            qty=c.qty,
            unit_price=unit_price(items[c.item_id], c.options),
            for_guest=c.for_guest,
            options=c.options,
            not_for=c.not_for,
            is_alcohol=items[c.item_id].is_alcohol,
        )
        for c in cart
    ]


@dataclass
class StockChange:
    item_id: str
    remaining: int | None
    sold_out: bool
    sold_out_reason: str
    became_sold_out: bool = False
    became_available: bool = False


class StockError(ValueError):
    def __init__(self, item_id: str, reason: str):
        super().__init__(f"{item_id}: {reason}")
        self.item_id = item_id
        self.reason = reason


def take_stock(items: dict[str, MenuItem], lines: list[OrderLine]) -> list[StockChange]:
    """受付時の在庫引き当て（トランザクションの中で呼ぶ）。足りなければ StockError。"""
    need: dict[str, int] = {}
    for l in lines:
        need[l.item_id] = need.get(l.item_id, 0) + l.qty
    changes = []
    for item_id, qty in need.items():
        item = items[item_id]
        if item.sold_out:
            raise StockError(item_id, "sold_out")
        if item.remaining is None:
            continue
        if item.remaining < qty:
            raise StockError(item_id, "not_enough_stock")
        left = item.remaining - qty
        changes.append(StockChange(item_id, left, left == 0, "stock" if left == 0 else "", became_sold_out=left == 0))
    return changes


def release_stock(item: MenuItem, qty: int) -> StockChange | None:
    """厨房が断った・お客様が取り消したときに在庫を戻す。在庫ゼロで品切れになっていたら解除する。"""
    if item.remaining is None:
        return None
    left = item.remaining + qty
    if item.sold_out and item.sold_out_reason == "stock":
        return StockChange(item.id, left, False, "", became_available=True)
    return StockChange(item.id, left, item.sold_out, item.sold_out_reason)


@dataclass
class Bill:
    lines: list[dict] = field(default_factory=list)
    charges: list[dict] = field(default_factory=list)
    total: int = 0
    per_person: int = 0
    remainder: int = 0  # 割り切れない分（誰か 1 人が多く払う）
    per_guest: dict[str, int] = field(default_factory=dict)


def make_bill(session: TableSession, orders: list[Order]) -> Bill:
    """会計 = 料理の価格スナップショット（断られた・取り消した分は除く）+ お通しなどの charges。"""
    bill = Bill()
    for order in orders:
        for l in order.lines:
            if l.status in (LineStatus.rejected, LineStatus.cancelled):
                continue
            amount = l.unit_price * l.qty
            bill.lines.append({"line_id": l.line_id, "name": l.name.model_dump(by_alias=True), "qty": l.qty,
                               "unit_price": l.unit_price, "amount": amount, "for_guest": l.for_guest, "not_for": l.not_for})
            bill.total += amount
    for c in session.charges:
        amount = c.unit_price * c.qty
        bill.charges.append({"kind": c.kind, "label": c.label.model_dump(by_alias=True), "qty": c.qty,
                             "unit_price": c.unit_price, "amount": amount, "reason": c.reason})
        bill.total += amount
    people = max(session.party_size, 1)
    bill.per_person = bill.total // people
    bill.remainder = bill.total - bill.per_person * people

    guest_ids = [g.id for g in session.guests]
    own = {gid: 0 for gid in guest_ids}
    for line in bill.lines:
        gid = line["for_guest"]
        if gid in own:
            own[gid] += line["amount"]
        elif line.get("not_for"):  # 召し上がらない方を除いた人数で分ける
            payers = [g for g in guest_ids if g not in line["not_for"]] or guest_ids
            each, rest = divmod(line["amount"], len(payers))
            for i, g in enumerate(payers):
                own[g] += each + (rest if i == 0 else 0)
    shared = bill.total - sum(own.values())
    n = max(len(guest_ids), 1)
    shared_per_person = shared // n
    shared_rem = shared - shared_per_person * n
    bill.per_guest = {gid: own[gid] + shared_per_person for gid in guest_ids}
    if guest_ids:
        bill.per_guest[guest_ids[0]] += shared_rem
    return bill


def otoshi_charge(price: int, party_size: int, label) -> Charge:
    return Charge(kind="otoshi", label=label, unit_price=price, qty=party_size, reason="seated")


def estimate_wait_min(open_orders: list[Order]) -> int:
    """厨房待ちの品数から料理が出るまでのおおよその待ち時間（分）を見積もる。

    5 + 2 × 品数の簡易計算（上限 60 分）。混雑状況をお客様に伝えるための目安値。
    先払いで未払いの注文は厨房が着手しないため除外する。
    """
    wait_statuses = {LineStatus.accepted, LineStatus.confirmed, LineStatus.cooking}
    n = 0
    for order in open_orders:
        if order.prepay and not order.paid:
            continue
        for line in order.lines:
            if line.status in wait_statuses:
                n += line.qty
    return min(5 + 2 * n, 60)

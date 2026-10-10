"""カートの共通チェック。音声・文字・タッチのどこから追加しても、必ずここを通る。

判定の順番：セッション状態 → ラストオーダー → 提供可否・在庫 → 数量 → 申告済みか →
「どなたの分か」 → 食事判定（○✕△？） → お酒の確認。
AI が聞き忘れても、ここで止まる（AI に頼らない）。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import StrEnum

from app.domain.dietary import GuestVerdict, Verdict, judge_table
from app.domain.models import CartLine, MenuItem, SessionStatus, StoreSettings, TableSession


class Decision(StrEnum):
    ok = "ok"
    need_declaration = "need_declaration"  # まだ食事制限の申告をしていないお客様がいる
    need_guest = "need_guest"  # どなたの分か、画面でゲストを選んでもらう
    need_alcohol_check = "need_alcohol_check"  # 20 歳以上か・運転しないかの確認
    need_staff = "need_staff"  # △ / ？ → 自動では通さない。店員に確認できる
    rejected = "rejected"


@dataclass
class CartCheck:
    decision: Decision
    reasons: list[str] = field(default_factory=list)
    for_guest: str | None = None
    verdicts: dict[str, GuestVerdict] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)  # 「同じテーブルの g1 は食べられないので取り分けない」など

    @property
    def ok(self) -> bool:
        return self.decision == Decision.ok

    def to_dict(self) -> dict:
        return {
            "decision": self.decision.value,
            "reasons": self.reasons,
            "for_guest": self.for_guest,
            "verdicts": {gid: {"mark": v.verdict.mark, "verdict": v.verdict.value, "reasons": v.reasons}
                         for gid, v in self.verdicts.items()},
            "warnings": self.warnings,
        }


def _past_last_order(store: StoreSettings, now_hhmm: str | None) -> bool:
    return bool(store.last_order and now_hhmm and now_hhmm >= store.last_order)


def check_add(
    session: TableSession,
    store: StoreSettings,
    item: MenuItem,
    qty: int,
    for_guest: str | None,
    now_hhmm: str | None = None,
) -> CartCheck:
    """1 行追加（または数量変更）してよいか。"""
    if session.status in (SessionStatus.billing, SessionStatus.closed):
        return CartCheck(Decision.rejected, ["session_billing"])
    if _past_last_order(store, now_hhmm):
        return CartCheck(Decision.rejected, ["last_order_passed"])
    if not item.published:
        return CartCheck(Decision.rejected, ["not_available"])
    if item.sold_out:
        return CartCheck(Decision.rejected, ["sold_out"])
    if item.remaining is not None and item.remaining < qty:
        return CartCheck(Decision.rejected, ["not_enough_stock"])
    if qty < 1 or qty > store.max_qty_per_line:
        return CartCheck(Decision.rejected, ["qty_limit"])
    if any(not g.declared for g in session.guests):
        return CartCheck(Decision.need_declaration, ["undeclared_guest"])

    if for_guest is not None and session.guest(for_guest) is None:
        return CartCheck(Decision.rejected, ["unknown_guest"])
    target = for_guest
    if target is None and len(session.guests) == 1:
        target = session.guests[0].id

    verdicts = judge_table(item, session.guests)
    differs = any(v.verdict != Verdict.ok for v in verdicts.values())
    if target is None and (differs or item.is_alcohol):
        # 「私」「me」を AI がゲストに結び付けない。画面で選んでもらう
        return CartCheck(Decision.need_guest, ["who_is_it_for"], None, verdicts)

    check = CartCheck(Decision.ok, [], target, verdicts)
    if target is not None:
        tv = verdicts[target]
        if tv.verdict == Verdict.ng:
            return CartCheck(Decision.rejected, ["dietary_conflict", *tv.reasons], target, verdicts)
        if tv.verdict in (Verdict.cross, Verdict.unknown):
            return CartCheck(Decision.need_staff, ["dietary_needs_staff", *tv.reasons], target, verdicts)
        if item.is_alcohol:
            guest = session.guest(target)
            assert guest is not None
            if guest.alcohol is None:
                return CartCheck(Decision.need_alcohol_check, ["alcohol_check_required"], target, verdicts)
            if not guest.alcohol.adult or guest.alcohol.driving:
                return CartCheck(Decision.rejected, ["alcohol_not_allowed"], target, verdicts)
        for gid, v in verdicts.items():
            if gid != target and v.verdict != Verdict.ok:
                check.warnings.append(f"do_not_share:{gid}")
    return check


def recheck_cart(
    session: TableSession, store: StoreSettings, items: dict[str, MenuItem], now_hhmm: str | None = None
) -> list[tuple[CartLine, CartCheck]]:
    """復唱・送信の前にカート全体をもう一度チェックし、通らない行を返す（申告や在庫が変わることがあるため）。"""
    problems = []
    for line in session.cart:
        item = items.get(line.item_id)
        if item is None:
            problems.append((line, CartCheck(Decision.rejected, ["not_available"])))
            continue
        result = check_add(session, store, item, line.qty, line.for_guest, now_hhmm)
        if not result.ok:
            problems.append((line, result))
    return problems


def cart_token(cart: list[CartLine]) -> str:
    """復唱したときのカートの指紋。送信時に一致しなければ、復唱後にカートが変わったことになる。"""
    body = json.dumps(
        sorted([[l.item_id, l.qty, l.for_guest or "", sorted(l.options.items())] for l in cart]),
        ensure_ascii=False,
    )
    return hashlib.sha256(body.encode()).hexdigest()[:16]

"""おすすめの選び方（コードで決める）：絞り込み → 点数 → 上位 N 件 + 理由。

AI はここで選ばれた料理の中からしか勧めない（料理や理由を自分で作らない）。
結果にはお客様ごとの食事判定（○✕△？）を必ず付ける。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.domain.dietary import Verdict, judge_table
from app.domain.models import MenuItem, TableSession
from app.domain.table_state import Phase

REASON_TEXT = {
    "owner_pick": {"ja": "店主のおすすめ", "en": "chef's pick"},
    "popular": {"ja": "人気", "en": "popular"},
    "taste": {"ja": "お好みの味", "en": "matches your taste"},
    "pairs_with_cart": {"ja": "注文中のものと合う", "en": "goes well with your order"},
    "first_drink": {"ja": "まずは一杯", "en": "start with a drink"},
    "shime": {"ja": "〆に", "en": "to finish"},
}
DRINK = "drink"
SHIME = {"rice", "noodle", "dessert"}


@dataclass
class Recommendation:
    item_id: str
    score: float
    reasons: list[str] = field(default_factory=list)
    marks: dict[str, str] = field(default_factory=dict)  # ゲスト ID → ○✕△？

    def to_dict(self, items: dict[str, MenuItem], lang: str) -> dict:
        item = items[self.item_id]
        return {
            "item_id": self.item_id,
            "name": item.name.get(lang),
            "price": item.price,
            "reasons": [REASON_TEXT[r]["ja" if lang == "ja" else "en"] for r in self.reasons],
            "marks": self.marks,
        }


def recommend(
    items: dict[str, MenuItem],
    session: TableSession,
    phase: Phase,
    taste: list[str] | None = None,
    category: str | None = None,
    for_guest: str | None = None,
    limit: int = 3,
) -> list[Recommendation]:
    taste = taste or []
    cart_items = [items[l.item_id] for l in session.cart if l.item_id in items]
    cart_has_drink = any(i.category == DRINK for i in cart_items)
    cart_pairs = {t for i in cart_items for t in i.pairing_tags} | {i.id for i in cart_items}
    halal_at_table = any("halal" in g.diets for g in session.guests)

    out: list[Recommendation] = []
    taste_hits: set[str] = set()
    for item in items.values():
        if not item.published or item.sold_out or item.id in session.rejected_recs:
            continue
        if category and item.category != category:
            continue
        if item.is_alcohol and halal_at_table:
            continue  # ハラールのお客様がいる卓には、お酒を自分からは勧めない
        verdicts = judge_table(item, session.guests)
        if for_guest is not None:
            if verdicts[for_guest].verdict != Verdict.ok:
                continue
        elif session.guests and not any(v.verdict == Verdict.ok for v in verdicts.values()):
            continue
        score = 0.0
        reasons: list[str] = []
        if item.owner_weight:
            score += 3 * item.owner_weight
            reasons.append("owner_pick")
        if item.popularity >= 70:
            score += 2
            reasons.append("popular")
        score += item.popularity / 50
        matched = set(taste) & set(item.taste_tags)
        if matched:
            score += 4 * len(matched)
            reasons.insert(0, "taste")
            taste_hits.add(item.id)
        if cart_pairs & set(item.pairing_tags) or any(item.id in i.pairing_tags for i in cart_items):
            score += 2
            reasons.append("pairs_with_cart")
        if phase == Phase.seated and item.category == DRINK and not cart_has_drink:
            score += 2
            reasons.append("first_drink")
        if phase == Phase.eating and item.category in SHIME:
            score += 1.5
            reasons.append("shime")
        if any(l.item_id == item.id for l in session.cart):
            score -= 5  # すでにカートにあるものは後ろへ
        out.append(Recommendation(item.id, score, reasons[:2], {gid: v.verdict.mark for gid, v in verdicts.items()}))
    if taste_hits:  # 好みの味を言われたら、その味の料理だけ（店主のおすすめでも違う味は出さない）
        out = [r for r in out if r.item_id in taste_hits]
    out.sort(key=lambda r: (-r.score, r.item_id))
    return out[:limit]


def alternatives(items: dict[str, MenuItem], session: TableSession, original: MenuItem, for_guest: str | None,
                 limit: int = 2) -> list[Recommendation]:
    """断ったとき・品切れのときの代わり：その人（指定がなければ全員）が ○ のものだけ。

    まず同じ分類から探し、なければ（例：揚げ物は全部同じ油で △）飲み物以外のほかの分類から探す。
    """

    def pick(candidates: dict[str, MenuItem]) -> list[Recommendation]:
        recs = recommend(candidates, session, Phase.ordering, taste=original.taste_tags, for_guest=for_guest, limit=limit * 3)
        if for_guest is None and session.guests:
            recs = [r for r in recs if all(m == "○" for m in r.marks.values())]
        return recs[:limit]

    same = {k: v for k, v in items.items() if v.category == original.category and k != original.id}
    recs = pick(same)
    if not recs and original.category != DRINK:
        recs = pick({k: v for k, v in items.items() if v.category != DRINK and k != original.id})
    return recs

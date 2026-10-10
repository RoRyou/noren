"""お客様ごとの食事判定：○ 食べられる / ✕ 含む / △ 混入の可能性 / ？ 確認できていない。

- 判定はコードだけで行う。AI が呼ぶかどうかに関係なく、メニュー検索・おすすめ・カート追加のたびに付ける。
- アレルギーの重さ（自己申告）は判定に使わない。△ と ？ は、制限のあるお客様には自動で通さない。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from app.domain.models import AllergenStatus, Guest, MenuItem


class Verdict(StrEnum):
    ok = "ok"  # ○
    ng = "ng"  # ✕
    cross = "cross"  # △
    unknown = "unknown"  # ？

    @property
    def mark(self) -> str:
        return {"ok": "○", "ng": "✕", "cross": "△", "unknown": "？"}[self.value]


_RANK = {Verdict.ok: 0, Verdict.cross: 1, Verdict.unknown: 2, Verdict.ng: 3}


@dataclass
class GuestVerdict:
    guest_id: str
    verdict: Verdict
    reasons: list[str] = field(default_factory=list)  # "allergen:shrimp" / "cross:shrimp" / "diet:halal:pork" など

    @property
    def auto_ok(self) -> bool:
        return self.verdict == Verdict.ok


def _worse(a: Verdict, b: Verdict) -> Verdict:
    return a if _RANK[a] >= _RANK[b] else b


# 食事制限 → (はっきり違反するタグ・アレルゲン, 確認が必要になる条件)
def _diet_check(diet: str, item: MenuItem) -> tuple[Verdict, list[str]]:
    tags = set(item.diet_tags)
    allergens = set(item.allergens)
    maybe = set(item.maybe_allergens)
    reasons: list[str] = []
    verdict = Verdict.ok

    def ng(reason: str) -> None:
        nonlocal verdict
        verdict = _worse(verdict, Verdict.ng)
        reasons.append(f"diet:{diet}:{reason}")

    def unknown(reason: str) -> None:
        nonlocal verdict
        verdict = _worse(verdict, Verdict.unknown)
        reasons.append(f"diet:{diet}:{reason}")

    if diet in ("halal", "no_pork"):
        if "pork" in allergens:
            ng("pork")
        elif "pork" in maybe:
            unknown("pork")
    if diet == "halal":
        if "alcohol" in tags or item.is_alcohol:
            ng("alcohol")
        if "meat" in tags and "pork" not in allergens and not item.halal_certified:
            unknown("non_certified_meat")
        if "gelatin" in allergens or "animal_derived" in tags:
            unknown("animal_derived")
    if diet == "no_alcohol" and ("alcohol" in tags or item.is_alcohol):
        ng("alcohol")
    if diet == "no_beef" and "beef" in allergens:
        ng("beef")
    if diet in ("vegetarian", "vegan"):
        if "meat" in tags:
            ng("meat")
        if "seafood" in tags:
            ng("seafood")
        if "animal_dashi" in tags:
            ng("animal_dashi")
        if "gelatin" in allergens or "animal_derived" in tags:
            ng("animal_derived")
    if diet == "vegan":
        if "egg" in allergens or "milk" in allergens:
            ng("egg_or_milk")
        elif "egg" in maybe or "milk" in maybe:
            unknown("egg_or_milk")
        if "honey" in tags:
            ng("honey")
    if diet == "gluten_free":
        if "wheat" in allergens or "gluten" in tags:
            ng("gluten")
        elif "wheat" in maybe:
            unknown("gluten")
    if diet == "no_five_pungent" and "five_pungent" in tags:
        ng("five_pungent")
    return verdict, reasons


def judge(item: MenuItem, guest: Guest) -> GuestVerdict:
    """1 品 × 1 人の判定。"""
    if not guest.allergies and not guest.diets:
        return GuestVerdict(guest.id, Verdict.ok)
    if item.allergen_status != AllergenStatus.confirmed:
        return GuestVerdict(guest.id, Verdict.unknown, ["unconfirmed"])

    verdict = Verdict.ok
    reasons: list[str] = []
    allergens = set(item.allergens)
    maybe = set(item.maybe_allergens)
    cross = set(item.cross_contact)
    for code in guest.allergies:
        if code in allergens:
            verdict = _worse(verdict, Verdict.ng)
            reasons.append(f"allergen:{code}")
        elif code in maybe:
            verdict = _worse(verdict, Verdict.unknown)
            reasons.append(f"maybe:{code}")
        elif code in cross:
            verdict = _worse(verdict, Verdict.cross)
            reasons.append(f"cross:{code}")
    for diet in guest.diets:
        v, r = _diet_check(diet, item)
        verdict = _worse(verdict, v)
        reasons += r
    return GuestVerdict(guest.id, verdict, reasons)


def judge_table(item: MenuItem, guests: list[Guest]) -> dict[str, GuestVerdict]:
    """1 品 × テーブル全員の判定（ゲスト ID → 判定）。"""
    return {g.id: judge(item, g) for g in guests}


def describe_reasons(reasons: list[str], lang: str = "ja") -> list[str]:
    """判定理由を画面と AI 向けの短い文に変える（事実だけ。安全とは言わない）。"""
    from app.domain.allergens import allergen_label

    out: list[str] = []
    for r in reasons:
        kind, _, rest = r.partition(":")
        if kind == "allergen":
            out.append(f"{allergen_label(rest, lang)}を含みます" if lang == "ja" else f"contains {allergen_label(rest, lang)}")
        elif kind == "maybe":
            out.append(f"{allergen_label(rest, lang)}を含む可能性があります（確認できていません）" if lang == "ja"
                       else f"may contain {allergen_label(rest, lang)} (not confirmed)")
        elif kind == "cross":
            out.append(f"{allergen_label(rest, lang)}が混入する可能性があります（同じ油・器具）" if lang == "ja"
                       else f"may have contact with {allergen_label(rest, lang)} (shared oil or equipment)")
        elif kind == "unconfirmed":
            out.append("アレルギー情報を確認できていません" if lang == "ja" else "allergy information has not been confirmed")
        elif kind == "diet":
            diet, _, what = rest.partition(":")
            out.append(f"{diet}: {what}")
    return out

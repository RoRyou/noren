"""店主の操作：メニュー取り込み（書き写し → 食材の確認 → 翻訳）と、業態・店舗ルールの設定。

AI は書き写し・質問・翻訳だけ。アレルゲンは店主が答えた食材から、コードが辞書で換算する。
"""

from __future__ import annotations

import uuid

from app.agent import menu_import
from app.domain.allergens import INGREDIENTS, convert_ingredients
from app.domain.models import AllergenStatus, Localized, MenuItem
from app.store.repo import Repo, now_iso

# 業態ごとの初期設定（K1）
PRESETS = {
    "izakaya": {"payment_timing": "postpay", "otoshi_price": 500, "last_order": "22:30"},
    "teishoku": {"payment_timing": "postpay", "otoshi_price": 0, "last_order": "20:30"},
    "ramen": {"payment_timing": "prepay", "otoshi_price": 0, "last_order": ""},
    "cafe": {"payment_timing": "prepay", "otoshi_price": 0, "last_order": "17:30"},
}


def _dump(m) -> dict:
    return m.model_dump(by_alias=True, mode="json")


class OwnerService:
    def __init__(self, repo: Repo):
        self.repo = repo

    async def import_photo(self, image: bytes, mime: str) -> dict:
        """1) 書き写し：下書き（未公開・アレルギー情報は未確認）を作る。"""
        result = await menu_import.transcribe(image, mime)
        drafts = []
        for t in result.items:
            item = MenuItem(
                id=f"m_{uuid.uuid4().hex[:8]}", name=Localized(ja=t.name_ja), price=t.price or 0,
                category=t.category, published=False, allergen_status=AllergenStatus.unconfirmed,
                photo_url="", photo_is_generated=True,
            )
            await self.repo.col("menu_items").document(item.id).set({**_dump(item), "draft": {"readable": t.readable,
                                                                                               "price_missing": t.price is None}})
            drafts.append({**_dump(item), "readable": t.readable})
        await self.repo.audit("menu_import_agent", "transcribe", {"count": len(drafts)}, "drafted")
        return {"items": drafts, "business_type_guess": result.business_type_guess}

    async def questions(self, item_id: str) -> dict:
        """2) 質問づくり：AI は食材を決めず、店主への質問と選択肢だけを返す。"""
        item = (await self.repo.menu())[item_id]
        q = await menu_import.questions_for(item.name.ja, item.category)
        known = [i for i in q.likely_ingredients if i in INGREDIENTS]
        return {"questions": q.questions, "choices": known, "all_ingredients": sorted(INGREDIENTS)}

    async def answer_ingredients(self, item_id: str, ingredients: list[str], cross_contact_items: list[str],
                                 confirmed_by: str = "店主") -> dict:
        """店主の答え → コードで換算。辞書にない食材があれば「未確認」のまま（推測しない）。"""
        menu = await self.repo.menu()
        conv = convert_ingredients(ingredients)
        cross: set[str] = set()
        for other in cross_contact_items:  # 「〇〇と同じ油」など
            if other in menu:
                cross |= set(menu[other].allergens) | set(menu[other].maybe_allergens)
        cross -= conv.allergens | conv.maybe_allergens
        fields = {
            "ingredients": ingredients, "allergens": sorted(conv.allergens), "maybe_allergens": sorted(conv.maybe_allergens),
            "diet_tags": sorted(conv.tags), "cross_contact": sorted(cross),
            "allergen_status": AllergenStatus.confirmed.value if conv.complete else AllergenStatus.unconfirmed.value,
            "allergen_confirmed_by": confirmed_by if conv.complete else "",
            "allergen_confirmed_at": now_iso() if conv.complete else "",
            "data_version": menu[item_id].data_version + 1,
        }
        await self.repo.update_item(item_id, fields)
        await self.repo.audit(confirmed_by, "confirm_ingredients", {"item_id": item_id, "ingredients": ingredients},
                              "confirmed" if conv.complete else "unconfirmed", reason=",".join(conv.unknown_ingredients))
        return {**fields, "unknown_ingredients": conv.unknown_ingredients}

    async def translate(self, item_id: str) -> dict:
        """3) 翻訳：名前・説明・味のタグと、店主の確認用の逆翻訳。"""
        item = (await self.repo.menu())[item_id]
        tr = await menu_import.translate(item.name.ja, item.ingredients)
        fields = {
            "name": {"ja": item.name.ja, "en": tr.en, "zh-Hans": tr.zh_hans, "zh-Hant": tr.zh_hant, "ko": tr.ko},
            "description": {"ja": "", "en": tr.description_en, "zh-Hans": "", "zh-Hant": "", "ko": ""},
            "taste_tags": list(tr.taste_tags),
        }
        await self.repo.update_item(item_id, fields)
        return {**fields, "back_translation_ja": tr.back_translation_ja}

    async def publish(self, item_id: str, name_ja: str, price: int, published: bool = True) -> dict:
        """店主が名前と価格を確かめて公開する。"""
        item = (await self.repo.menu())[item_id]
        name = item.name.model_dump(by_alias=True)
        name["ja"] = name_ja
        await self.repo.update_item(item_id, {"name": name, "price": price, "published": published,
                                              "photo_url": item.photo_url or f"/media/menu/{item_id}.jpg"})
        await self.repo.audit("owner", "publish", {"item_id": item_id, "price": price}, "ok")
        return {"ok": True}

    async def set_business_type(self, business_type: str) -> dict:
        preset = PRESETS[business_type]
        await self.repo.root.update({"business_type": business_type, **preset})
        await self.repo.audit("owner", "business_type", {"business_type": business_type, **preset}, "ok")
        return {"business_type": business_type, **preset}

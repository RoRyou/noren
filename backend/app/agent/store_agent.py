"""店舗エージェント：店員・店主の一言を、店のデータの変更に変える（テキストの Gemini）。

AI がするのは「何をしたいか」と「どの食材・料理のことか」を、店に実際にある候補の中から選ぶことだけ。
影響する料理はコードが食材から探す。2 品以上に影響する品切れは、店員が確認してから反映する。
価格とアレルゲンは変えられない。
"""

from __future__ import annotations

from typing import Literal

from google.genai import types
from pydantic import BaseModel, Field

from app.agent.menu_import import client
from app.config import settings
from app.domain.allergens import normalize
from app.domain.models import MenuItem


class Intent(BaseModel):
    intent: Literal["sold_out", "restock", "todays_pick", "knowledge", "unknown"]
    ingredients: list[str] = Field(default_factory=list, description="候補の食材の中から選ぶ（品切れの原因の食材）")
    item_ids: list[str] = Field(default_factory=list, description="候補の料理 ID の中から選ぶ")
    quantity: int | None = Field(default=None, description="追加した数（restock のとき）")
    knowledge_ja: str = Field(default="", description="お客様に話してよい店の事実（knowledge のとき）。言われたことだけを書く")


async def understand(text: str, items: dict[str, MenuItem]) -> Intent:
    ingredients = sorted({i for it in items.values() for i in it.ingredients})
    catalog = "\n".join(f"- {it.id}: {it.name.ja}" for it in items.values())
    instruction = (
        "あなたは飲食店の店舗エージェントです。店員・店主の一言が何を意味するかを判断します。\n"
        "- sold_out：食材や料理がなくなった（例：出汁が切れた、唐揚げ終わり）\n"
        "- restock：補充した・追加で作った（例：さばを 3 食追加）\n"
        "- todays_pick：今日のおすすめ（例：今日のおすすめはまぐろ）\n"
        "- knowledge：お客様に話してよい店の事実（例：本日のさばは鳥取産）\n"
        "食材と料理は、必ず下の候補の中から選んでください。候補にないものは選ばないでください。\n"
        f"食材の候補：{'、'.join(ingredients)}\n料理の候補：\n{catalog}"
    )
    r = await client().aio.models.generate_content(
        model=settings.text_model, contents=text,
        config=types.GenerateContentConfig(system_instruction=instruction, response_mime_type="application/json",
                                           response_schema=Intent, temperature=0),
    )
    return Intent.model_validate_json(r.text)


def affected_by_ingredients(items: dict[str, MenuItem], ingredients: list[str]) -> list[str]:
    """食材を使っている料理（コードで探す）。"""
    wanted = {normalize(i) for i in ingredients}
    return sorted(it.id for it in items.values() if wanted & {normalize(x) for x in it.ingredients} and not it.sold_out)

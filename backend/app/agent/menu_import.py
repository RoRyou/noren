"""メニュー取り込みエージェント（テキスト・画像の Gemini。音声の経路ではないので、賢いモデルを使う）。

3 つの仕事だけをする。アレルゲンは決めない（店主の答えをコードが辞書で換算する）：
1. 書き写し：メニュー写真から料理名・税込価格・分類を書き写す（推測しない。読めないものは読めないと返す）
2. 質問づくり：料理ごとに、アレルゲンや混入に関わる食材を店主に聞く短い質問を作る
3. 翻訳：英語・簡体字・繁体字・韓国語の名前と説明、確認用の日本語への逆翻訳、味のタグ
"""

from __future__ import annotations

from typing import Literal

from google import genai
from google.genai import types
from pydantic import BaseModel, Field

from app.config import settings

CATEGORIES = ["drink", "snack", "fried", "grill", "sashimi", "noodle", "rice", "dessert", "topping", "set"]


class TranscribedItem(BaseModel):
    name_ja: str = Field(description="メニューに書いてある料理名（そのまま書き写す）")
    price: int | None = Field(description="税込価格（円）。読めない・書いていないときは null")
    category: Literal["drink", "snack", "fried", "grill", "sashimi", "noodle", "rice", "dessert", "topping", "set"]
    readable: bool = Field(description="文字がはっきり読めたか")


class Transcription(BaseModel):
    items: list[TranscribedItem]
    business_type_guess: Literal["izakaya", "teishoku", "ramen", "cafe", "unknown"]


class Questions(BaseModel):
    questions: list[str] = Field(description="店主への短い質問（最大 4 つ）。答えは食材名になるように")
    likely_ingredients: list[str] = Field(description="質問の選択肢にする一般的な食材名（料理名から考えられるもの）")


class Translation(BaseModel):
    en: str
    zh_hans: str
    zh_hant: str
    ko: str
    description_en: str = Field(description="外国のお客様向けの 1 文の説明（店の事実は書かない）")
    back_translation_ja: str = Field(description="en の名前と説明を日本語に戻したもの（店主の確認用）")
    taste_tags: list[Literal["spicy", "light", "rich", "sweet", "sour"]]


_client: genai.Client | None = None


def client() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(vertexai=True, project=settings.google_cloud_project, location=settings.text_location)
    return _client


async def _generate(contents, schema: type[BaseModel], instruction: str) -> BaseModel:
    r = await client().aio.models.generate_content(
        model=settings.text_model,
        contents=contents,
        config=types.GenerateContentConfig(system_instruction=instruction, response_mime_type="application/json",
                                           response_schema=schema, temperature=0.1),
    )
    return schema.model_validate_json(r.text)


async def transcribe(image: bytes, mime: str) -> Transcription:
    instruction = (
        "あなたは飲食店のメニューを書き写す係です。写真に書いてある料理名・価格をそのまま書き写してください。"
        "書いていないこと（材料・アレルゲン・説明）は書かないでください。価格が読めないときは null にしてください。"
        "トッピングや大盛りなどの追加料金の項目は category を topping にしてください。"
    )
    parts = [types.Part.from_bytes(data=image, mime_type=mime), "このメニューを書き写してください。"]
    return await _generate(parts, Transcription, instruction)  # type: ignore[return-value]


async def questions_for(name_ja: str, category: str) -> Questions:
    instruction = (
        "あなたは飲食店の店主に、料理の食材を確認する係です。アレルギーや宗教上の制限（豚・酒・動物性のだし）、"
        "揚げ油や器具の共用による混入に関係する食材を、店主に短く聞いてください。"
        "あなたが食材を決めてはいけません。質問だけを作ります。例：「スープは豚骨ですか、鶏ガラですか？」"
        "「麺に卵は入っていますか？」「海老天と同じ油で揚げていますか？」"
    )
    return await _generate(f"料理名：{name_ja}（分類：{category}）", Questions, instruction)  # type: ignore[return-value]


async def translate(name_ja: str, ingredients: list[str]) -> Translation:
    instruction = (
        "日本の飲食店のメニューを訪日客向けに訳します。料理名はローマ字だけにせず、意味が分かる訳にしてください。"
        "説明は一般的な料理の説明だけにし、産地など店の事実は書かないでください。"
    )
    return await _generate(f"料理名：{name_ja}\n店主が答えた食材：{'、'.join(ingredients) or '未回答'}", Translation,
                           instruction)  # type: ignore[return-value]

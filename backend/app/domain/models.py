"""業務データのモデル（Firestore のドキュメントとそのまま相互変換できる形）。"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class Lang(StrEnum):
    ja = "ja"
    en = "en"
    zh_hans = "zh-Hans"
    zh_hant = "zh-Hant"
    ko = "ko"


class Localized(BaseModel):
    ja: str
    en: str = ""
    zh_hans: str = Field("", alias="zh-Hans")
    zh_hant: str = Field("", alias="zh-Hant")
    ko: str = ""

    model_config = {"populate_by_name": True}

    def get(self, lang: str) -> str:
        value = {"ja": self.ja, "en": self.en, "zh-Hans": self.zh_hans, "zh-Hant": self.zh_hant, "ko": self.ko}.get(lang, "")
        return value or self.en or self.ja


class AllergenStatus(StrEnum):
    unconfirmed = "unconfirmed"  # 店主の確認がまだ → 制限のあるお客様には自動で通さない
    confirmed = "confirmed"


class OptionChoice(BaseModel):
    key: str
    label: Localized
    price_delta: int = 0  # 追加料金（円、税込）


class OptionGroup(BaseModel):
    """料理の選択肢（麺の硬さ・大盛りなど）。アレルゲンが変わる選択肢は扱わない（既知の制限）。"""

    key: str
    label: Localized
    choices: list[OptionChoice]
    default: str  # 選ばなかったときの選択肢


class MenuItem(BaseModel):
    id: str
    name: Localized
    price: int  # 税込（円）
    option_groups: list[OptionGroup] = []
    category: str  # drink / snack / fried / grill / rice / dessert など
    description: Localized | None = None
    how_to_eat: Localized | None = None
    portion: Localized | None = None
    photo_url: str = ""
    photo_is_generated: bool = True  # 生成画像なら画面に「イメージ」と表示
    is_alcohol: bool = False
    ingredients: list[str] = []  # 店主の回答
    allergens: list[str] = []  # 配合から換算（確定）
    maybe_allergens: list[str] = []  # 製品により含む可能性
    cross_contact: list[str] = []  # 同じ油・器具などで混入の可能性があるアレルゲン
    diet_tags: list[str] = []
    halal_certified: bool = False
    allergen_status: AllergenStatus = AllergenStatus.unconfirmed
    allergen_confirmed_by: str = ""
    allergen_confirmed_at: str = ""
    taste_tags: list[str] = []  # spicy / light / rich / sweet / sour など
    pairing_tags: list[str] = []  # beer / sake / highball / rice など
    owner_weight: int = 0  # 店主のおすすめ度 0〜3
    popularity: int = 0  # 人気（デモデータ）
    sold_out: bool = False
    sold_out_reason: Literal["", "manual", "stock"] = ""
    remaining: int | None = None  # 残り数。None = 数量管理なし
    published: bool = False  # 店主が料理名・価格を確認済み
    data_version: int = 1


class AlcoholCheck(BaseModel):
    adult: bool  # 20 歳以上（自己申告）
    driving: bool  # 今日、車・自転車を運転する
    at: str = ""


class Guest(BaseModel):
    id: str  # g1, g2, ...
    label: str  # 画面表示（「ゲスト 1」や名前）
    allergies: list[str] = []  # アレルゲンコード
    diets: list[str] = []  # 食事制限のコード：halal / vegetarian / vegan / gluten_free / no_pork / no_beef / no_alcohol / no_five_pungent
    severe: bool = False  # 重いアレルギー（自己申告） → 強制的に店員へ
    declared: bool = False  # 入座時の申告を済ませたか（「なし」も申告済み）
    alcohol: AlcoholCheck | None = None


class CartLine(BaseModel):
    line_id: str
    item_id: str
    qty: int
    for_guest: str | None = None
    options: dict[str, str] = {}


class Charge(BaseModel):
    """お通し・席料・割引・調整など、料理以外の請求。"""

    kind: Literal["otoshi", "seat", "discount", "adjustment"]
    label: Localized
    unit_price: int  # 割引はマイナス
    qty: int
    reason: str = ""
    added_by: str = "system"


class LineStatus(StrEnum):
    accepted = "accepted"  # システムが受け付けた（厨房はまだ）
    confirmed = "confirmed"  # 厨房が受けた
    rejected = "rejected"  # 厨房が断った
    cooking = "cooking"
    served = "served"
    cancelled = "cancelled"


class OrderLine(BaseModel):
    line_id: str
    item_id: str
    name: Localized  # 注文時点の名前
    qty: int
    unit_price: int  # 注文時点の価格（スナップショット）
    for_guest: str | None = None
    options: dict[str, str] = {}
    is_alcohol: bool = False
    status: LineStatus = LineStatus.accepted
    reject_reason: str = ""
    updated_at: str = ""


class Order(BaseModel):
    id: str
    session_id: str
    lines: list[OrderLine]
    idempotency_key: str
    created_at: str = ""
    paid: bool = False
    prepay: bool = False  # 先払いの店（ラーメン店など）：支払いを確認するまで厨房は受けない


class SessionStatus(StrEnum):
    dining = "dining"
    awaiting_prepay = "awaiting_prepay"
    billing = "billing"
    closed = "closed"


class TableSession(BaseModel):
    id: str
    table_no: str
    party_size: int
    lang: str = "ja"
    guests: list[Guest] = []
    cart: list[CartLine] = []
    confirm_token: str = ""  # 復唱したときのカートの指紋
    charges: list[Charge] = []
    recommended: list[str] = []  # 表示済みのおすすめ
    accepted_recs: list[str] = []
    rejected_recs: list[str] = []
    recent_turns: list[dict] = []  # 直近の会話（role, text）
    status: SessionStatus = SessionStatus.dining
    seated_at: str = ""
    bill_requested_at: str = ""
    closed_at: str = ""
    delete_after: str = ""

    def guest(self, guest_id: str | None) -> Guest | None:
        return next((g for g in self.guests if g.id == guest_id), None)


class StoreSettings(BaseModel):
    id: str
    name: Localized
    business_type: Literal["izakaya", "teishoku", "ramen", "cafe"] = "izakaya"
    otoshi_price: int = 0  # 0 ならお通しなし
    otoshi_label: Localized | None = None
    payment_timing: Literal["postpay", "prepay"] = "postpay"
    last_order: str = ""  # "22:30" など。空ならなし
    max_qty_per_line: int = 10
    rules: list[dict] = []  # 店舗ルール（石川県 18 項目のテンプレート）
    self_service: dict[str, Localized] = {}  # 「お冷はセルフ」など
    rule_version: int = 1
    # サービスポリシー：AI が自動で行うか（auto）、店員の確認を待つか（confirm）、店員に任せるか（staff）。
    # 重いアレルギー・急病などは常に店員へ、価格・アレルゲンは AI が変えられない（ここでは変えられない）
    service_policy: dict[str, str] = {
        "sold_out_multi": "confirm",  # 一言で 2 品以上が品切れになるとき
        "sold_out_single": "auto",  # 1 品だけの品切れ
        "todays_pick": "auto",  # 今日のおすすめ
        "cancel_before_cooking": "auto",  # 調理前のお客様の取り消し（staff なら店員へ）
    }

"""アレルゲン 29 品目と、食材 → アレルゲン・食事タグの辞書。

2026-04-01 からの区分：特定原材料（表示義務）9 品目 + 特定原材料に準ずるもの（推奨）20 品目。
AI はこの辞書を使わない。店主が答えた食材を、コードがこの辞書で換算する（AI に推測させない）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

# コード → (日本語, 英語, 区分)。区分は "mandatory"（義務 9）か "recommended"（推奨 20）
ALLERGENS: dict[str, tuple[str, str, str]] = {
    # 特定原材料（9）
    "shrimp": ("えび", "shrimp", "mandatory"),
    "crab": ("かに", "crab", "mandatory"),
    "walnut": ("くるみ", "walnut", "mandatory"),
    "wheat": ("小麦", "wheat", "mandatory"),
    "buckwheat": ("そば", "buckwheat", "mandatory"),
    "egg": ("卵", "egg", "mandatory"),
    "milk": ("乳", "milk", "mandatory"),
    "peanut": ("落花生", "peanut", "mandatory"),
    "cashew": ("カシューナッツ", "cashew", "mandatory"),
    # 特定原材料に準ずるもの（20）
    "almond": ("アーモンド", "almond", "recommended"),
    "abalone": ("あわび", "abalone", "recommended"),
    "squid": ("いか", "squid", "recommended"),
    "salmon_roe": ("いくら", "salmon roe", "recommended"),
    "orange": ("オレンジ", "orange", "recommended"),
    "kiwi": ("キウイフルーツ", "kiwi", "recommended"),
    "beef": ("牛肉", "beef", "recommended"),
    "sesame": ("ごま", "sesame", "recommended"),
    "salmon": ("さけ", "salmon", "recommended"),
    "mackerel": ("さば", "mackerel", "recommended"),
    "soybean": ("大豆", "soybean", "recommended"),
    "chicken": ("鶏肉", "chicken", "recommended"),
    "banana": ("バナナ", "banana", "recommended"),
    "pistachio": ("ピスタチオ", "pistachio", "recommended"),
    "pork": ("豚肉", "pork", "recommended"),
    "macadamia": ("マカダミアナッツ", "macadamia", "recommended"),
    "peach": ("もも", "peach", "recommended"),
    "yam": ("やまいも", "yam", "recommended"),
    "apple": ("りんご", "apple", "recommended"),
    "gelatin": ("ゼラチン", "gelatin", "recommended"),
}
assert len(ALLERGENS) == 29
assert sum(1 for v in ALLERGENS.values() if v[2] == "mandatory") == 9

# 食事タグ（アレルゲン以外で、宗教・菜食などの判定に使う）
#   meat: 肉全般 / seafood: 魚介全般 / animal_dashi: 動物性のだし / alcohol: 酒類（みりん・料理酒を含む）
#   five_pungent: 五葷（にんにく・ねぎ類） / gluten: 小麦・大麦など / honey: はちみつ / animal_derived: 動物由来（ゼラチン等）
DIET_TAGS = {"meat", "seafood", "animal_dashi", "alcohol", "five_pungent", "gluten", "honey", "animal_derived"}


@dataclass(frozen=True)
class IngredientInfo:
    allergens: frozenset[str] = frozenset()
    maybe_allergens: frozenset[str] = frozenset()  # 製品によって含むことがある → 判定は「？」
    tags: frozenset[str] = frozenset()


def _i(allergens=(), maybe=(), tags=()) -> IngredientInfo:
    return IngredientInfo(frozenset(allergens), frozenset(maybe), frozenset(tags))


# 食材辞書：見出し語 → 情報。別名は ALIASES で見出し語に寄せる
INGREDIENTS: dict[str, IngredientInfo] = {
    # 調味料
    "醤油": _i(["wheat", "soybean"], tags=["gluten"]),
    "味噌": _i(["soybean"], maybe=["wheat"]),
    "みりん": _i(tags=["alcohol"]),
    "料理酒": _i(tags=["alcohol"]),
    "マヨネーズ": _i(["egg"], maybe=["soybean"]),
    "ポン酢": _i(["wheat", "soybean"], maybe=["orange"], tags=["gluten"]),
    "ごま油": _i(["sesame"]),
    "ごま": _i(["sesame"]),
    "塩": _i(),
    "砂糖": _i(),
    "酢": _i(),
    "わさび": _i(),
    "からし": _i(),
    "七味": _i(maybe=["sesame"]),
    "はちみつ": _i(tags=["honey"]),
    "市販のタレ": _i(),  # 中身が分からない → UNKNOWN 扱い（UNKNOWN_INGREDIENTS 参照）
    # だし
    "かつおだし": _i(tags=["seafood", "animal_dashi"]),
    "昆布だし": _i(),
    "鶏ガラ": _i(["chicken"], tags=["meat", "animal_dashi"]),
    "豚骨": _i(["pork"], tags=["meat", "animal_dashi"]),
    # 粉・麺・ご飯
    "小麦粉": _i(["wheat"], tags=["gluten"]),
    "天ぷら粉": _i(["wheat"], maybe=["egg"], tags=["gluten"]),
    "パン粉": _i(["wheat"], maybe=["milk", "egg"], tags=["gluten"]),
    "片栗粉": _i(),
    "中華麺": _i(["wheat"], maybe=["egg"], tags=["gluten"]),
    "うどん": _i(["wheat"], tags=["gluten"]),
    "そば": _i(["buckwheat"], maybe=["wheat"]),
    "ご飯": _i(),
    "米": _i(),
    "海苔": _i(),
    # 卵・乳
    "卵": _i(["egg"]),
    "牛乳": _i(["milk"]),
    "バター": _i(["milk"]),
    "チーズ": _i(["milk"]),
    "生クリーム": _i(["milk"]),
    # 大豆製品
    "豆腐": _i(["soybean"]),
    "油揚げ": _i(["soybean"]),
    "納豆": _i(["soybean"]),
    "枝豆": _i(["soybean"]),
    # 肉
    "鶏肉": _i(["chicken"], tags=["meat"]),
    "豚肉": _i(["pork"], tags=["meat"]),
    "ベーコン": _i(["pork"], tags=["meat"]),
    "ラード": _i(["pork"], tags=["meat", "animal_derived"]),
    "牛肉": _i(["beef"], tags=["meat"]),
    "ゼラチン": _i(["gelatin"], tags=["animal_derived"]),
    # 魚介
    "えび": _i(["shrimp"], tags=["seafood"]),
    "かに": _i(["crab"], tags=["seafood"]),
    "いか": _i(["squid"], tags=["seafood"]),
    "たこ": _i(tags=["seafood"]),
    "いくら": _i(["salmon_roe"], tags=["seafood"]),
    "鮭": _i(["salmon"], tags=["seafood"]),
    "さば": _i(["mackerel"], tags=["seafood"]),
    "まぐろ": _i(tags=["seafood"]),
    "さんま": _i(tags=["seafood"]),
    "ぶり": _i(tags=["seafood"]),
    "ほたて": _i(tags=["seafood"]),
    "あわび": _i(["abalone"], tags=["seafood"]),
    "かつお節": _i(tags=["seafood"]),
    # 野菜・果物・ナッツ
    "にんにく": _i(tags=["five_pungent"]),
    "玉ねぎ": _i(tags=["five_pungent"]),
    "ねぎ": _i(tags=["five_pungent"]),
    "にら": _i(tags=["five_pungent"]),
    "しょうが": _i(),
    "大根": _i(),
    "大葉": _i(),
    "すだち": _i(),
    "柚子": _i(),
    "栗": _i(),
    "銀杏": _i(),
    "里芋": _i(),
    "かぼちゃ": _i(),
    "しいたけ": _i(),
    "まいたけ": _i(),
    "しめじ": _i(),
    "白菜": _i(),
    "もやし": _i(),
    "こんにゃく": _i(),
    "レモン": _i(),
    "きゅうり": _i(),
    "キャベツ": _i(),
    "じゃがいも": _i(),
    "なす": _i(),
    "長芋": _i(["yam"]),
    "りんご": _i(["apple"]),
    "もも": _i(["peach"]),
    "バナナ": _i(["banana"]),
    "キウイ": _i(["kiwi"]),
    "オレンジ": _i(["orange"]),
    "くるみ": _i(["walnut"]),
    "カシューナッツ": _i(["cashew"]),
    "アーモンド": _i(["almond"]),
    "ピスタチオ": _i(["pistachio"]),
    "マカダミアナッツ": _i(["macadamia"]),
    "落花生": _i(["peanut"]),
    "抹茶": _i(),
    "あんこ": _i(),
    # 酒類
    "ビール": _i(maybe=["wheat"], tags=["alcohol", "gluten"]),
    "日本酒": _i(tags=["alcohol"]),
    "焼酎": _i(tags=["alcohol"]),
    "ウイスキー": _i(tags=["alcohol"]),
    "炭酸水": _i(),
    "緑茶": _i(),
    "烏龍茶": _i(),
    "柚子果汁": _i(),
}

ALIASES: dict[str, str] = {
    "しょうゆ": "醤油", "しょう油": "醤油", "玉子": "卵", "鶏卵": "卵", "卵黄": "卵",
    "海老": "えび", "エビ": "えび", "蟹": "かに", "カニ": "かに", "イカ": "いか", "タコ": "たこ",
    "鶏もも": "鶏肉", "鶏むね": "鶏肉", "手羽": "鶏肉", "豚バラ": "豚肉", "牛すじ": "牛肉",
    "サーモン": "鮭", "鯖": "さば", "鮪": "まぐろ", "帆立": "ほたて", "ホタテ": "ほたて",
    "ピーナッツ": "落花生", "胡麻": "ごま", "ニンニク": "にんにく", "長ねぎ": "ねぎ", "青ねぎ": "ねぎ", "しそ": "大葉", "青じそ": "大葉", "秋刀魚": "さんま", "椎茸": "しいたけ", "舞茸": "まいたけ", "南瓜": "かぼちゃ",
    "とろろ": "長芋", "山芋": "長芋", "だし（かつお）": "かつおだし", "片栗": "片栗粉", "牛乳（成分）": "牛乳",
}

# 中身が確認できない食材：これが入っていると、その料理のアレルギー情報は「未確認」扱い
UNKNOWN_INGREDIENTS = {"市販のタレ", "市販のキムチ", "不明"}


@dataclass
class Conversion:
    """店主が答えた食材の一覧から、コードで換算した結果。"""

    allergens: set[str] = field(default_factory=set)
    maybe_allergens: set[str] = field(default_factory=set)
    tags: set[str] = field(default_factory=set)
    unknown_ingredients: list[str] = field(default_factory=list)  # 辞書にない・中身不明 → 未確認

    @property
    def complete(self) -> bool:
        return not self.unknown_ingredients


def normalize(name: str) -> str:
    name = name.strip()
    return ALIASES.get(name, name)


def convert_ingredients(ingredients: list[str]) -> Conversion:
    """食材名の一覧 → アレルゲン・食事タグ。辞書にない食材は推測せず unknown に入れる。"""
    out = Conversion()
    for raw in ingredients:
        name = normalize(raw)
        if name in UNKNOWN_INGREDIENTS or name not in INGREDIENTS:
            out.unknown_ingredients.append(raw)
            continue
        info = INGREDIENTS[name]
        out.allergens |= info.allergens
        out.maybe_allergens |= info.maybe_allergens
        out.tags |= info.tags
    out.maybe_allergens -= out.allergens
    return out


def allergen_label(code: str, lang: str = "ja") -> str:
    ja, en, _ = ALLERGENS[code]
    return ja if lang == "ja" else en

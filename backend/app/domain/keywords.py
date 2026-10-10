"""会話の文字起こしから、アレルギー・食事制限らしき言葉を拾う（AI が記録し忘れたときの保険）。

見つけても自動では登録しない。画面に「〇〇のアレルギーを登録しますか？」と出し、お客様に選んでもらう。
"""

from __future__ import annotations

ALLERGY_WORDS: dict[str, list[str]] = {
    "shrimp": ["えび", "エビ", "海老", "shrimp", "prawn"],
    "crab": ["かに", "カニ", "蟹", "crab"],
    "wheat": ["小麦", "wheat"],
    "buckwheat": ["そば", "蕎麦", "buckwheat", "soba"],
    "egg": ["卵", "たまご", "玉子", "egg"],
    "milk": ["乳製品", "牛乳", "乳アレルギー", "dairy", "milk", "lactose"],
    "peanut": ["落花生", "ピーナッツ", "peanut"],
    "walnut": ["くるみ", "walnut"],
    "cashew": ["カシュー", "cashew"],
    "almond": ["アーモンド", "almond"],
    "sesame": ["ごま", "胡麻", "sesame"],
    "soybean": ["大豆", "soy"],
    "squid": ["いか", "イカ", "squid"],
    "salmon": ["さけ", "鮭", "サーモン", "salmon"],
    "mackerel": ["さば", "鯖", "mackerel"],
    "beef": ["牛肉", "beef"],
    "pork": ["豚肉", "pork"],
    "chicken": ["鶏肉", "chicken"],
    "kiwi": ["キウイ", "kiwi"],
    "peach": ["もも", "桃", "peach"],
    "apple": ["りんご", "apple"],
    "banana": ["バナナ", "banana"],
    "pistachio": ["ピスタチオ", "pistachio"],
    "macadamia": ["マカダミア", "macadamia"],
    "gelatin": ["ゼラチン", "gelatin"],
}
# アレルギーの話をしているか（単語だけでは「えびが好き」と区別できないため、こちらと組み合わせる）
ALLERGY_CONTEXT = ["アレルギー", "食べられない", "だめ", "ダメ", "allergic", "allergy", "can't eat", "cannot eat", "intoleran"]
DIET_WORDS: dict[str, list[str]] = {
    "halal": ["ハラール", "ハラル", "halal", "muslim", "ムスリム"],
    "vegetarian": ["ベジタリアン", "菜食", "vegetarian"],
    "vegan": ["ヴィーガン", "ビーガン", "vegan"],
    "gluten_free": ["グルテンフリー", "グルテン", "gluten", "celiac"],
    "no_pork": ["豚肉を食べない", "no pork", "don't eat pork"],
    "no_alcohol": ["お酒は飲めない", "お酒を飲まない", "don't drink", "no alcohol"],
}


def find_mentions(text: str) -> tuple[set[str], set[str]]:
    """文字起こしから（アレルゲンコード, 食事制限コード）を拾う。"""
    lower = text.lower()
    allergens: set[str] = set()
    if any(w.lower() in lower for w in ALLERGY_CONTEXT):
        for code, words in ALLERGY_WORDS.items():
            if any(w.lower() in lower for w in words):
                allergens.add(code)
    diets = {code for code, words in DIET_WORDS.items() if any(w.lower() in lower for w in words)}
    return allergens, diets

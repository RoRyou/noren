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
ALLERGY_CONTEXT = ["アレルギー", "食べられ", "苦手", "だめ", "ダメ", "抜き", "can't have", "avoid", "allergic", "allergy", "can't eat", "cannot eat", "intoleran"]
DIET_WORDS: dict[str, list[str]] = {
    "halal": ["ハラール", "ハラル", "halal", "muslim", "ムスリム"],
    "vegetarian": ["ベジタリアン", "菜食", "vegetarian"],
    "vegan": ["ヴィーガン", "ビーガン", "vegan"],
    "gluten_free": ["グルテンフリー", "グルテン", "gluten", "celiac"],
    "no_pork": ["豚肉を食べない", "no pork", "don't eat pork"],
    "no_alcohol": ["お酒は飲めない", "お酒を飲まない", "don't drink", "no alcohol"],
}


NEAR = 25


def _find_all(text: str, word: str) -> list[int]:
    out, i = [], text.find(word)
    while i != -1:
        out.append(i)
        i = text.find(word, i + 1)
    return out


def find_mentions(text: str) -> tuple[set[str], set[str]]:
    """文字起こしから（アレルゲンコード, 食事制限コード）を拾う。"""
    lower = text.lower()
    allergens: set[str] = set()
    # アレルゲンの語は「アレルギー」などの語の近く（前後 25 文字）にあるときだけ拾う
    # （「えびアレルギーです。友だちには唐揚げ」の唐揚げ＝鶏肉を拾わない）
    spots = [i for w in ALLERGY_CONTEXT for i in _find_all(lower, w.lower())]
    for code, words in ALLERGY_WORDS.items():
        for w in words:
            if any(abs(j - i) <= NEAR for j in _find_all(lower, w.lower()) for i in spots):
                allergens.add(code)
                break
    diets = {code for code, words in DIET_WORDS.items() if any(w.lower() in lower for w in words)}
    return allergens, diets

"""デモ用の居酒屋「のれん」（架空の店）。

料理ごとに「店主が答えた食材」だけを書き、アレルゲン・食事タグはコード（allergens.convert_ingredients）で換算する。
同じフライヤーを使う揚げ物は、ほかの揚げ物のアレルゲンを「混入の可能性」として持つ。
"""

from __future__ import annotations

from app.domain.allergens import convert_ingredients
from app.domain.models import AllergenStatus, Localized, MenuItem, OptionChoice, OptionGroup, StoreSettings

CONFIRMED_BY = "店主（デモ）"
CONFIRMED_AT = "2026-10-10T10:00:00+09:00"


def L(ja: str, en: str, zh_hans: str = "", zh_hant: str = "", ko: str = "") -> Localized:
    return Localized(ja=ja, en=en, **{"zh-Hans": zh_hans, "zh-Hant": zh_hant}, ko=ko)


# (id, 名前, 価格, 分類, 食材, 追加情報)
RAW: list[tuple] = [
    ("d_beer", L("生ビール", "Draft beer", "生啤酒", "生啤酒", "생맥주"), 600, "drink", ["ビール"],
     dict(is_alcohol=True, pairing_tags=["fried", "grill"], popularity=95,
          portion=L("中ジョッキ（約 400 ml）", "Medium mug (about 400 ml)"),
          option_groups=[OptionGroup(key="size", label=L("サイズ", "Size", "杯型", "杯型", "사이즈"), default="medium", choices=[
              OptionChoice(key="medium", label=L("中ジョッキ", "Medium", "中杯", "中杯", "중")),
              OptionChoice(key="large", label=L("大ジョッキ", "Large", "大杯", "大杯", "대"), price_delta=200)])])),
    ("d_highball", L("ハイボール", "Highball", "嗨棒", "嗨棒", "하이볼"), 500, "drink", ["ウイスキー", "炭酸水"],
     dict(is_alcohol=True, taste_tags=["light"], pairing_tags=["fried"], popularity=80)),
    ("d_sake", L("日本酒（新潟・辛口）", "Sake (Niigata, dry)", "日本清酒（新潟·辣口）", "日本清酒（新潟·辛口）", "니혼슈（니가타·드라이）"),
     700, "drink", ["日本酒"], dict(is_alcohol=True, pairing_tags=["sashimi", "grill"], popularity=60, owner_weight=1,
                                   portion=L("1 合（180 ml）", "1 go (180 ml)"))),
    ("d_yuzu_sour", L("柚子サワー", "Yuzu sour", "柚子沙瓦", "柚子沙瓦", "유자 사와"), 550, "drink", ["焼酎", "柚子果汁", "炭酸水"],
     dict(is_alcohol=True, taste_tags=["sour", "light"], pairing_tags=["fried"], popularity=70)),
    ("d_oolong", L("烏龍茶", "Oolong tea", "乌龙茶", "烏龍茶", "우롱차"), 350, "drink", ["烏龍茶"], dict(popularity=50)),
    ("d_yuzu_soda", L("柚子ソーダ（ノンアルコール）", "Yuzu soda (non-alcoholic)", "柚子苏打（无酒精）", "柚子蘇打（無酒精）", "유자 소다（무알코올）"),
     400, "drink", ["柚子果汁", "炭酸水", "砂糖"], dict(taste_tags=["sour", "sweet"], popularity=45)),
    ("s_edamame", L("枝豆", "Edamame", "毛豆", "毛豆", "에다마메"), 418, "snack", ["枝豆", "塩"],
     dict(taste_tags=["light"], pairing_tags=["beer"], popularity=85,
          how_to_eat=L("さやを口に当てて、豆だけを押し出して食べます", "Squeeze the beans out of the pod into your mouth; don't eat the pod"))),
    ("s_hiyayakko", L("冷奴", "Chilled tofu", "凉拌豆腐", "涼拌豆腐", "히야얏코"), 385, "snack",
     ["豆腐", "しょうが", "ねぎ", "かつお節", "醤油"], dict(taste_tags=["light"], popularity=55)),
    ("s_cucumber", L("たたききゅうり", "Smashed cucumber", "拍黄瓜", "拍黃瓜", "오이무침"), 380, "snack",
     ["きゅうり", "ごま油", "塩", "にんにく"], dict(taste_tags=["light"], pairing_tags=["beer"], popularity=40)),
    ("s_dashimaki", L("だし巻き玉子", "Japanese rolled omelette", "高汤玉子烧", "高湯玉子燒", "다시마키 계란말이"), 528, "snack",
     ["卵", "かつおだし", "醤油", "みりん", "砂糖"], dict(taste_tags=["light", "sweet"], popularity=75, owner_weight=1)),
    ("f_karaage", L("鶏の唐揚げ", "Fried chicken (karaage)", "日式炸鸡块", "日式炸雞塊", "가라아게"), 638, "fried",
     ["鶏肉", "醤油", "しょうが", "にんにく", "小麦粉", "片栗粉", "料理酒"],
     dict(taste_tags=["rich"], pairing_tags=["beer", "highball"], popularity=90, owner_weight=2,
          portion=L("5 個", "5 pieces"), how_to_eat=L("お好みでレモンをかけてどうぞ", "Squeeze lemon on top if you like"))),
    ("f_ebi_ten", L("海老の天ぷら", "Shrimp tempura", "炸虾天妇罗", "炸蝦天婦羅", "새우튀김"), 858, "fried",
     ["えび", "天ぷら粉", "卵", "かつおだし", "醤油", "みりん"],
     dict(taste_tags=["rich"], pairing_tags=["beer", "sake"], popularity=70, portion=L("3 本", "3 pieces"),
          how_to_eat=L("天つゆか塩でどうぞ", "Dip in tentsuyu sauce or salt"))),
    ("f_fries", L("フライドポテト", "French fries", "炸薯条", "炸薯條", "감자튀김"), 450, "fried", ["じゃがいも", "塩"],
     dict(taste_tags=["rich"], pairing_tags=["beer", "highball"], popularity=65)),
    ("f_agedashi", L("揚げ出し豆腐", "Deep-fried tofu in broth", "日式炸豆腐", "日式炸豆腐", "아게다시도후"), 520, "fried",
     ["豆腐", "片栗粉", "かつおだし", "醤油", "みりん", "大根", "ねぎ"], dict(taste_tags=["light"], popularity=50)),
    ("g_yakitori", L("焼き鳥盛り合わせ（塩）", "Assorted yakitori (salt)", "盐烤鸡肉串拼盘", "鹽烤雞肉串拼盤", "야키토리 모둠（소금）"),
     780, "grill", ["鶏肉", "塩"], dict(taste_tags=["rich"], pairing_tags=["beer", "sake"], popularity=85,
                                      portion=L("5 本", "5 skewers"))),
    ("g_saba", L("さばの塩焼き", "Grilled mackerel with salt", "盐烤青花鱼", "鹽烤鯖魚", "고등어 소금구이"), 680, "grill",
     ["さば", "塩", "大根"], dict(taste_tags=["rich"], pairing_tags=["sake", "rice"], popularity=60, remaining=5,
                                description=L("本日限定 5 食", "Limited to 5 today"))),
    ("g_buta_kimchi", L("豚キムチ炒め", "Stir-fried pork and kimchi", "泡菜炒猪肉", "泡菜炒豬肉", "돼지고기 김치볶음"), 682, "grill",
     ["豚肉", "市販のタレ", "ごま油"], dict(taste_tags=["spicy", "rich"], pairing_tags=["beer", "rice"], popularity=55)),
    ("g_mapo", L("麻婆豆腐", "Mapo tofu", "麻婆豆腐", "麻婆豆腐", "마파두부"), 748, "grill",
     ["豆腐", "豚肉", "味噌", "醤油", "にんにく", "ねぎ", "ごま油"], dict(taste_tags=["spicy", "rich"], pairing_tags=["beer", "rice"], popularity=60)),
    ("g_maguro", L("まぐろの刺身", "Tuna sashimi", "金枪鱼刺身", "鮪魚生魚片", "참치회"), 980, "sashimi",
     ["まぐろ", "わさび", "醤油"], dict(taste_tags=["light"], pairing_tags=["sake"], popularity=75, owner_weight=1,
                                     how_to_eat=L("わさびを少し載せ、醤油を少しつけて", "Put a little wasabi on top and dip lightly in soy sauce"))),
    ("r_ochazuke", L("鮭茶漬け", "Salmon ochazuke (rice in tea)", "三文鱼茶泡饭", "鮭魚茶泡飯", "연어 오차즈케"), 550, "rice",
     ["ご飯", "鮭", "海苔", "緑茶", "かつおだし"], dict(taste_tags=["light"], popularity=55)),
    ("r_onigiri", L("焼きおにぎり", "Grilled rice ball", "烤饭团", "烤飯糰", "구운 주먹밥"), 380, "rice", ["ご飯", "醤油"],
     dict(taste_tags=["rich"], popularity=45)),
    ("ds_matcha_ice", L("抹茶アイス", "Matcha ice cream", "抹茶冰淇淋", "抹茶冰淇淋", "말차 아이스크림"), 400, "dessert",
     ["抹茶", "牛乳", "生クリーム", "砂糖"], dict(taste_tags=["sweet"], popularity=50)),
]

FRYER_SHARED = ["f_karaage", "f_ebi_ten", "f_fries", "f_agedashi"]  # 同じ油で揚げる


# 料理の一般的な説明（訪日客向け。店の事実は書かない）。検索でも使う（「chicken skewers」→ 焼き鳥）
DESCRIPTIONS: dict[str, Localized] = {
    "d_beer": L("樽から注ぐ生ビール", "Japanese lager on tap"),
    "d_highball": L("ウイスキーの炭酸割り", "Whisky and soda"),
    "d_sake": L("米から造る日本の酒。辛口", "Japanese rice wine, dry"),
    "d_yuzu_sour": L("焼酎を柚子の果汁と炭酸で割ったもの", "Shochu with yuzu citrus juice and soda"),
    "d_yuzu_soda": L("柚子の炭酸飲料（アルコールなし）", "Yuzu citrus soda, no alcohol"),
    "s_edamame": L("塩ゆでした若い大豆", "Boiled young soybeans with salt"),
    "s_hiyayakko": L("冷たい豆腐に薬味と醤油", "Cold tofu with toppings and soy sauce"),
    "s_cucumber": L("たたいたきゅうりの和え物", "Smashed cucumber with a light dressing"),
    "s_dashimaki": L("だしを入れたふんわりした卵焼き", "Fluffy rolled omelette made with soup stock"),
    "f_karaage": L("日本の鶏の唐揚げ", "Japanese-style fried chicken"),
    "f_ebi_ten": L("海老を薄い衣で揚げたもの", "Shrimp fried in a light batter"),
    "f_agedashi": L("揚げた豆腐に温かいつゆ", "Fried tofu in a warm soy broth"),
    "g_yakitori": L("鶏肉の串焼き（塩味）", "Grilled chicken skewers with salt"),
    "g_buta_kimchi": L("豚肉とキムチの炒め物（辛い）", "Pork stir-fried with spicy kimchi"),
    "g_mapo": L("豆腐の辛い炒め煮", "Tofu in a spicy chili sauce"),
    "g_maguro": L("生のまぐろの切り身", "Sliced raw tuna"),
    "r_ochazuke": L("ご飯に鮭をのせ、お茶をかけたもの", "Rice with salmon, topped with hot tea"),
    "r_onigiri": L("醤油を塗って焼いたおにぎり", "Rice ball grilled with soy sauce"),
    "ds_matcha_ice": L("抹茶のアイスクリーム", "Green tea ice cream"),
}


def build_menu() -> dict[str, MenuItem]:
    items: dict[str, MenuItem] = {}
    for item_id, name, price, category, ingredients, extra in RAW:
        conv = convert_ingredients(ingredients)
        items[item_id] = MenuItem(
            id=item_id, name=name, price=price, category=category, ingredients=ingredients,
            allergens=sorted(conv.allergens), maybe_allergens=sorted(conv.maybe_allergens), diet_tags=sorted(conv.tags),
            allergen_status=AllergenStatus.confirmed if conv.complete else AllergenStatus.unconfirmed,
            allergen_confirmed_by=CONFIRMED_BY if conv.complete else "", allergen_confirmed_at=CONFIRMED_AT if conv.complete else "",
            photo_url=f"/media/menu/{item_id}.jpg", published=True, **{"description": DESCRIPTIONS.get(item_id), **extra},
        )
    # 同じフライヤー：ほかの揚げ物のアレルゲン（確定・可能性）を混入の可能性として持たせる
    for item_id in FRYER_SHARED:
        others = set()
        for other in FRYER_SHARED:
            if other != item_id:
                others |= set(items[other].allergens) | set(items[other].maybe_allergens)
        own = set(items[item_id].allergens) | set(items[item_id].maybe_allergens)
        items[item_id].cross_contact = sorted(others - own)
    return items


def build_store(store_id: str = "demo") -> StoreSettings:
    return StoreSettings(
        id=store_id,
        name=L("居酒屋 のれん", "Izakaya Noren", "居酒屋 暖帘", "居酒屋 暖簾", "이자카야 노렌"),
        business_type="izakaya",
        otoshi_price=500,
        otoshi_label=L("お通し（小鉢）", "Otoshi (small starter)", "下酒小菜", "下酒小菜", "오토오시（기본 안주）"),
        payment_timing="postpay",
        last_order="22:30",
        rules=[
            {"key": "payment", "announce": True, "text": L("お支払いは現金・クレジットカード・QR コード決済が使えます", "We accept cash, credit cards and QR code payments").model_dump(by_alias=True)},
            {"key": "tax", "announce": True, "text": L("表示価格はすべて税込です", "All prices include tax").model_dump(by_alias=True)},
            {"key": "tipping", "announce": True, "text": L("チップは不要です", "No tipping needed").model_dump(by_alias=True)},
            {"key": "split_bill", "announce": True, "text": L("お会計はテーブルごとにまとめてお願いします（画面で割り勘の金額は表示できます）", "One bill per table (the screen can show the split per person)").model_dump(by_alias=True)},
            {"key": "allergy", "announce": False, "text": L("アレルギーや食事制限は必ずお知らせください。混入の可能性はゼロにはできません", "Please tell us about allergies or dietary needs. We cannot rule out cross-contact completely").model_dump(by_alias=True)},
            {"key": "takeout", "announce": False, "text": L("衛生上、食べ残しのお持ち帰りはできません", "For hygiene reasons, leftovers cannot be taken home").model_dump(by_alias=True)},
            {"key": "outside_food", "announce": False, "text": L("飲食物の持ち込みはご遠慮ください", "Please do not bring outside food or drinks").model_dump(by_alias=True)},
            {"key": "smoking", "announce": False, "text": L("店内は全面禁煙です", "The restaurant is non-smoking").model_dump(by_alias=True)},
            {"key": "photos", "announce": False, "text": L("料理の写真撮影は自由です。ほかのお客様は写さないでください", "Feel free to photograph your food; please don't photograph other guests").model_dump(by_alias=True)},
            {"key": "busy", "announce": False, "text": L("混雑時は料理の提供に時間がかかることがあります", "Dishes may take longer when we are busy").model_dump(by_alias=True)},
        ],
        self_service={"water": L("お冷はセルフサービスです。入口横のウォーターサーバーをご利用ください", "Water is self-service — the water server is by the entrance")},
    )


KNOWLEDGE: list[dict] = [
    {"id": "k_karaage", "category": "origin", "items": ["f_karaage"], "text": L("唐揚げの鶏は宮崎県産のもも肉です", "The karaage uses chicken thigh from Miyazaki")},
    {"id": "k_sake", "category": "sake", "items": ["d_sake"], "text": L("日本酒は新潟の辛口で、すっきりした味わいです。刺身や焼き魚に合います", "Our sake is a dry one from Niigata — clean and crisp, good with sashimi or grilled fish")},
    {"id": "k_maguro", "category": "season", "items": ["g_maguro"], "text": L("まぐろは毎朝市場で仕入れています", "We buy our tuna at the market every morning")},
    {"id": "k_saba", "category": "season", "items": ["g_saba"], "text": L("さばは秋が旬で、脂がのっています。本日は 5 食限定です", "Mackerel is in season in autumn and rich in flavor — only 5 today")},
    {"id": "k_dashimaki", "category": "story", "items": ["s_dashimaki"], "text": L("だし巻き玉子は注文ごとに焼いています", "We make each rolled omelette to order")},
    {"id": "k_yuzu", "category": "origin", "items": ["d_yuzu_sour", "d_yuzu_soda"], "text": L("柚子は高知県産の果汁を使っています", "The yuzu juice comes from Kochi")},
    {"id": "k_matcha", "category": "origin", "items": ["ds_matcha_ice"], "text": L("抹茶は京都・宇治のものです", "The matcha is from Uji, Kyoto")},
    {"id": "k_otoshi", "category": "store", "items": [], "text": L("お通しは日替わりの小鉢で、居酒屋では席料のような意味があります", "Otoshi is a small daily starter — at izakaya it works like a table charge")},
    {"id": "k_store", "category": "store", "items": [], "text": L("のれんは 2015 年に開いた、カウンター 8 席・テーブル 4 卓の小さな店です", "Noren opened in 2015 — a small place with 8 counter seats and 4 tables")},
    {"id": "k_area", "category": "area", "items": [], "text": L("最寄り駅までは歩いて 5 分です", "The nearest station is a 5-minute walk")},
]

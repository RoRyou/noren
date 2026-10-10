// 画面の文言（日本語・英語）。中国語・韓国語は P1 で追加する。

export type Lang = "ja" | "en";

const dict = {
  welcome: { ja: "いらっしゃいませ", en: "Welcome" },
  tapToStart: { ja: "タップしてはじめる", en: "Tap to start" },
  howMany: { ja: "何名様ですか？", en: "How many people?" },
  next: { ja: "次へ", en: "Next" },
  back: { ja: "戻る", en: "Back" },
  guest: { ja: "ゲスト", en: "Guest" },
  declareTitle: { ja: "アレルギー・食事制限はありますか？", en: "Any allergies or dietary needs?" },
  declareNote: {
    ja: "お一人ずつ選んでください。ない方は「なし」を押してください。",
    en: "Please choose for each person. Tap “None” if there are none.",
  },
  none: { ja: "なし", en: "None" },
  severe: { ja: "重いアレルギー（少量でも症状が出る）", en: "Severe (reacts to traces)" },
  privacyTitle: { ja: "ご利用の前に", en: "Before you start" },
  privacy: {
    ja: "会話はご注文のために AI が処理します。処理は米国のサーバーで行い、音声は保存しません。会話の文字は 7 日後に削除します。",
    en: "Your conversation is processed by AI to take your order. Processing happens on servers in the US; audio is not stored. Text is deleted after 7 days.",
  },
  rulesTitle: { ja: "お店からのお知らせ", en: "From the restaurant" },
  agree: { ja: "はじめる", en: "Start" },
  talk: { ja: "タップして話す", en: "Tap to talk" },
  connecting: { ja: "接続中…", en: "Connecting…" },
  listening: { ja: "聞いています", en: "Listening" },
  speaking: { ja: "話しています（タップで止める）", en: "Speaking (tap to interrupt)" },
  typeHere: { ja: "文字でも話せます", en: "Or type here" },
  send: { ja: "送信", en: "Send" },
  quickRecommend: { ja: "おすすめは？", en: "Any recommendations?" },
  quickAllergy: { ja: "アレルギーがあります", en: "I have an allergy" },
  quickBill: { ja: "お会計", en: "Check, please" },
  quickOrder: { ja: "すぐ注文", en: "Order now" },
  emergency: { ja: "緊急", en: "Emergency" },
  emergencySent: { ja: "店員を呼びました。すぐに参ります。", en: "Staff has been called and is on the way." },
  tabRecommend: { ja: "おすすめ", en: "For you" },
  tabMenu: { ja: "メニュー", en: "Menu" },
  tabOrder: { ja: "注文", en: "Order" },
  add: { ja: "追加", en: "Add" },
  soldOut: { ja: "品切れ", en: "Sold out" },
  image: { ja: "イメージ", en: "Image" },
  cart: { ja: "カート", en: "Cart" },
  orderNow: { ja: "注文する", en: "Place order" },
  confirmTitle: { ja: "ご注文の確認", en: "Confirm your order" },
  sendOrder: { ja: "この内容で送る", en: "Send order" },
  fix: { ja: "修正する", en: "Edit" },
  received: { ja: "受け付けました", en: "Received" },
  whoFor: { ja: "どなたの分ですか？", en: "Who is this for?" },
  alcoholTitle: { ja: "お酒の確認", en: "Before we serve alcohol" },
  alcoholQ: { ja: "20 歳以上で、今日は車・自転車を運転しませんか？", en: "Are you 20 or older and not driving or cycling today?" },
  yes: { ja: "はい", en: "Yes" },
  no: { ja: "いいえ", en: "No" },
  blocked: { ja: "この料理はおすすめできません", en: "We can't recommend this dish" },
  needStaff: { ja: "店員の確認が必要です", en: "A staff member needs to confirm" },
  askStaff: { ja: "店員に確認する", en: "Ask staff" },
  alternatives: { ja: "代わりにいかがですか", en: "How about these instead" },
  registerAllergy: { ja: "アレルギー・食事制限を登録しますか？", en: "Register this allergy or dietary need?" },
  register: { ja: "登録する", en: "Register" },
  close: { ja: "閉じる", en: "Close" },
  billTitle: { ja: "お会計", en: "Your bill" },
  perPerson: { ja: "お一人あたり", en: "Per person" },
  payAtRegister: { ja: "お支払いはレジでお願いします", en: "Please pay at the register" },
  total: { ja: "合計", en: "Total" },
  status: {
    ja: { accepted: "受付済み", confirmed: "厨房確認済み", cooking: "調理中", served: "提供済み", rejected: "お断り", cancelled: "取消" },
    en: { accepted: "Received", confirmed: "Kitchen confirmed", cooking: "Cooking", served: "Served", rejected: "Declined", cancelled: "Cancelled" },
  },
  experts: {
    ja: { menu: "メニュー", recommend: "おすすめ", knowledge: "お店の豆知識", allergy: "アレルギー確認", order: "注文", staff: "店員連絡", bill: "会計" },
    en: { menu: "Menu", recommend: "Picks", knowledge: "Store facts", allergy: "Allergy check", order: "Order", staff: "Staff", bill: "Bill" },
  },
  markLegend: {
    ja: "○ 食べられる　✕ 含む　△ 混入の可能性　？ 未確認",
    en: "○ OK　✕ Contains　△ May have contact　？ Not confirmed",
  },
  demoClock: { ja: "デモの時刻 19:00", en: "Demo time 19:00" },
  voiceUnavailable: {
    ja: "ただいま音声の会話は混み合っているか、本日の上限に達しました。文字とタッチでご注文いただけます。",
    en: "Voice is busy or has reached today's limit. You can still type or tap to order.",
  },
  voiceError: { ja: "音声が途切れました。文字とタッチで続けられます。", en: "Voice was interrupted. You can keep going by typing or tapping." },
};

export function t(lang: Lang, key: keyof typeof dict): string {
  const v = dict[key] as Record<Lang, unknown>;
  return v[lang] as string;
}

export function statusLabel(lang: Lang, s: string): string {
  return (dict.status[lang] as Record<string, string>)[s] ?? s;
}

export function expertLabel(lang: Lang, e: string): string {
  return (dict.experts[lang] as Record<string, string>)[e] ?? e;
}

// 申告画面のアレルゲン（よく使うもの。残りは「その他」から）
export const ALLERGENS: { code: string; ja: string; en: string }[] = [
  { code: "shrimp", ja: "えび", en: "Shrimp" },
  { code: "crab", ja: "かに", en: "Crab" },
  { code: "wheat", ja: "小麦", en: "Wheat" },
  { code: "egg", ja: "卵", en: "Egg" },
  { code: "milk", ja: "乳", en: "Milk" },
  { code: "peanut", ja: "落花生", en: "Peanut" },
  { code: "buckwheat", ja: "そば", en: "Buckwheat" },
  { code: "walnut", ja: "くるみ", en: "Walnut" },
  { code: "cashew", ja: "カシューナッツ", en: "Cashew" },
  { code: "sesame", ja: "ごま", en: "Sesame" },
  { code: "soybean", ja: "大豆", en: "Soy" },
  { code: "squid", ja: "いか", en: "Squid" },
  { code: "salmon", ja: "さけ", en: "Salmon" },
  { code: "mackerel", ja: "さば", en: "Mackerel" },
  { code: "chicken", ja: "鶏肉", en: "Chicken" },
  { code: "pork", ja: "豚肉", en: "Pork" },
  { code: "beef", ja: "牛肉", en: "Beef" },
  { code: "gelatin", ja: "ゼラチン", en: "Gelatin" },
];

export const DIETS: { code: string; ja: string; en: string }[] = [
  { code: "halal", ja: "ハラール", en: "Halal" },
  { code: "vegetarian", ja: "ベジタリアン", en: "Vegetarian" },
  { code: "vegan", ja: "ヴィーガン", en: "Vegan" },
  { code: "gluten_free", ja: "グルテンフリー", en: "Gluten-free" },
  { code: "no_pork", ja: "豚肉なし", en: "No pork" },
  { code: "no_alcohol", ja: "お酒なし", en: "No alcohol" },
];

export function allergenName(lang: Lang, code: string): string {
  const a = ALLERGENS.find((x) => x.code === code);
  return a ? a[lang] : code;
}

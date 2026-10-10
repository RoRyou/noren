// 画面の文言（日本語・英語・簡体字・繁体字・韓国語）。音声の会話は日本語・英語のみ（中国語・韓国語は文字で会話）。

export type Lang = "ja" | "en" | "zh-Hans" | "zh-Hant" | "ko";
export const LANGS: { code: Lang; label: string; voice: boolean }[] = [
  { code: "ja", label: "日本語", voice: true },
  { code: "en", label: "English", voice: true },
  { code: "zh-Hans", label: "简体中文", voice: false },
  { code: "zh-Hant", label: "繁體中文", voice: false },
  { code: "ko", label: "한국어", voice: false },
];
export const hasVoice = (lang: Lang) => LANGS.find((l) => l.code === lang)?.voice ?? false;

type T = Record<Lang, string>;
const T = (ja: string, en: string, zhs: string, zht: string, ko: string): T => ({ ja, en, "zh-Hans": zhs, "zh-Hant": zht, ko });

const dict = {
  welcome: T("いらっしゃいませ", "Welcome", "欢迎光临", "歡迎光臨", "어서 오세요"),
  tapToStart: T("タップしてはじめる", "Tap to start", "点击开始", "點擊開始", "눌러서 시작"),
  howMany: T("何名様ですか？", "How many people?", "请问几位？", "請問幾位？", "몇 분이세요?"),
  next: T("次へ", "Next", "下一步", "下一步", "다음"),
  back: T("戻る", "Back", "返回", "返回", "뒤로"),
  guest: T("ゲスト", "Guest", "客人", "客人", "손님"),
  declareTitle: T("アレルギー・食事制限はありますか？", "Any allergies or dietary needs?", "有过敏或饮食限制吗？", "有過敏或飲食限制嗎？", "알레르기나 식사 제한이 있나요?"),
  declareNote: T(
    "お一人ずつ選んでください。ない方は「なし」を押してください。",
    "Please choose for each person. Tap “None” if there are none.",
    "请每位分别选择。没有的请点「无」。",
    "請每位分別選擇。沒有的請點「無」。",
    "한 분씩 선택해 주세요. 없으면 「없음」을 눌러 주세요.",
  ),
  none: T("なし", "None", "无", "無", "없음"),
  severe: T("重いアレルギー（少量でも症状が出る）", "Severe (reacts to traces)", "严重过敏（微量也会过敏）", "嚴重過敏（微量也會過敏）", "심한 알레르기(미량에도 반응)"),
  privacyTitle: T("ご利用の前に", "Before you start", "使用前须知", "使用前須知", "이용 전 안내"),
  privacy: T(
    "会話はご注文のために AI が処理します。処理は米国のサーバーで行い、音声は保存しません。会話の文字は 7 日後に削除します。",
    "Your conversation is processed by AI to take your order. Processing happens on servers in the US; audio is not stored. Text is deleted after 7 days.",
    "对话由 AI 处理，仅用于点餐。处理在美国的服务器上进行，不保存语音，文字记录 7 天后删除。",
    "對話由 AI 處理，僅用於點餐。處理在美國的伺服器上進行，不保存語音，文字記錄 7 天後刪除。",
    "대화는 주문을 위해 AI가 처리합니다. 처리는 미국 서버에서 이루어지며 음성은 저장하지 않습니다. 대화 문자는 7일 후 삭제됩니다.",
  ),
  rulesTitle: T("お店からのお知らせ", "From the restaurant", "本店须知", "本店須知", "가게 안내"),
  agree: T("はじめる", "Start", "开始", "開始", "시작"),
  talk: T("タップして話す", "Tap to talk", "点击说话", "點擊說話", "눌러서 말하기"),
  connecting: T("接続中…", "Connecting…", "连接中…", "連線中…", "연결 중…"),
  listening: T("聞いています", "Listening", "正在听", "正在聽", "듣고 있어요"),
  speaking: T("話しています（タップで止める）", "Speaking (tap to interrupt)", "正在说话（点击打断）", "正在說話（點擊打斷）", "말하는 중(눌러서 중단)"),
  textOnly: T("", "", "语音仅支持日语和英语，请用文字输入", "語音僅支援日語和英語，請用文字輸入", "음성은 일본어·영어만 지원합니다. 문자로 입력해 주세요"),
  typeHere: T("文字でも話せます", "Or type here", "也可以输入文字", "也可以輸入文字", "문자로도 대화할 수 있어요"),
  send: T("送信", "Send", "发送", "發送", "보내기"),
  quickRecommend: T("おすすめは？", "Any recommendations?", "有什么推荐？", "有什麼推薦？", "추천 메뉴는?"),
  quickAllergy: T("アレルギーがあります", "I have an allergy", "我有过敏", "我有過敏", "알레르기가 있어요"),
  quickBill: T("お会計", "Check, please", "结账", "結帳", "계산"),
  quickOrder: T("すぐ注文", "Order now", "直接点餐", "直接點餐", "바로 주문"),
  emergency: T("緊急", "Emergency", "紧急", "緊急", "긴급"),
  emergencySent: T("店員を呼びました。すぐに参ります。", "Staff has been called and is on the way.", "已呼叫店员，马上就到。", "已呼叫店員，馬上就到。", "직원을 불렀어요. 곧 갑니다."),
  staffComing: T("店員が向かっています", "A staff member is on the way", "店员正在过来", "店員正在過來", "직원이 가고 있어요"),
  interpret: T("店員と話す（通訳）", "Talk with staff (interpreter)", "和店员说话（翻译）", "和店員說話（翻譯）", "직원과 대화(통역)"),
  tabRecommend: T("おすすめ", "For you", "推荐", "推薦", "추천"),
  tabMenu: T("メニュー", "Menu", "菜单", "菜單", "메뉴"),
  tabOrder: T("注文", "Order", "订单", "訂單", "주문"),
  add: T("追加", "Add", "加入", "加入", "담기"),
  soldOut: T("品切れ", "Sold out", "售罄", "售罄", "품절"),
  image: T("イメージ", "Image", "示意图", "示意圖", "이미지"),
  cart: T("カート", "Cart", "购物车", "購物車", "장바구니"),
  orderNow: T("注文する", "Place order", "下单", "下單", "주문하기"),
  confirmTitle: T("ご注文の確認", "Confirm your order", "确认订单", "確認訂單", "주문 확인"),
  sendOrder: T("この内容で送る", "Send order", "确认下单", "確認下單", "이대로 주문"),
  fix: T("修正する", "Edit", "修改", "修改", "수정"),
  received: T("受け付けました", "Received", "已接单", "已接單", "접수했어요"),
  payFirst: T(
    "受け付けました。先にレジでお支払いください。お支払いの後にお作りします。",
    "Received. Please pay at the register first — we'll start cooking after payment.",
    "已接单。请先到收银台付款，付款后开始制作。",
    "已接單。請先到收銀台付款，付款後開始製作。",
    "접수했어요. 먼저 계산대에서 결제해 주세요. 결제 후 조리합니다.",
  ),
  whoFor: T("どなたの分ですか？", "Who is this for?", "是哪位的？", "是哪位的？", "어느 분 것인가요?"),
  onePerPerson: T("お一人一つずつ選んでください", "Choose one per person", "请每人选一份", "請每人選一份", "한 분씩 선택해 주세요"),
  alcoholTitle: T("お酒の確認", "Before we serve alcohol", "饮酒确认", "飲酒確認", "음주 확인"),
  isAdult: T("20 歳以上ですか？", "Are you 20 or older?", "您已满 20 岁吗？", "您已滿 20 歲嗎？", "20세 이상이신가요?"),
  isDriving: T("今日、車・自転車を運転しますか？", "Will you drive or cycle today?", "今天要开车或骑自行车吗？", "今天要開車或騎自行車嗎？", "오늘 차나 자전거를 운전하시나요?"),
  yes: T("はい", "Yes", "是", "是", "네"),
  no: T("いいえ", "No", "否", "否", "아니요"),
  blocked: T("この料理はおすすめできません", "We can't recommend this dish", "不建议这道菜", "不建議這道菜", "이 요리는 권하지 않아요"),
  needStaff: T("店員の確認が必要です", "A staff member needs to confirm", "需要店员确认", "需要店員確認", "직원 확인이 필요해요"),
  askStaff: T("店員に確認する", "Ask staff", "请店员确认", "請店員確認", "직원에게 확인"),
  staffWillCheck: T("店員に確認します", "We'll check with the staff", "将由店员确认", "將由店員確認", "직원에게 확인할게요"),
  alternatives: T("代わりにいかがですか", "How about these instead", "可以试试这些", "可以試試這些", "대신 이건 어떠세요"),
  registerAllergy: T("アレルギー・食事制限を登録しますか？", "Register this allergy or dietary need?", "要登记这项过敏或饮食限制吗？", "要登記這項過敏或飲食限制嗎？", "이 알레르기·식사 제한을 등록할까요?"),
  close: T("閉じる", "Close", "关闭", "關閉", "닫기"),
  billTitle: T("お会計", "Your bill", "账单", "帳單", "계산서"),
  perPerson: T("お一人あたり", "Per person", "每人", "每人", "1인당"),
  payAtRegister: T("お支払いはレジでお願いします", "Please pay at the register", "请到收银台付款", "請到收銀台付款", "계산대에서 결제해 주세요"),
  total: T("合計", "Total", "合计", "合計", "합계"),
  staffNotified: T("店員に連絡しました", "We've let the staff know", "已通知店员", "已通知店員", "직원에게 알렸어요"),
  markLegend: T(
    "○ 食べられる　✕ 含む　△ 混入の可能性　？ 未確認",
    "○ OK　✕ Contains　△ May have contact　？ Not confirmed",
    "○ 可以吃　✕ 含有　△ 可能混入　？ 未确认",
    "○ 可以吃　✕ 含有　△ 可能混入　？ 未確認",
    "○ 먹을 수 있음　✕ 포함　△ 혼입 가능성　？ 미확인",
  ),
  mayContact: T("混入の可能性", "may have contact", "可能混入", "可能混入", "혼입 가능성"),
  noteSoldOut: T("{name}が品切れになりました", "{name} just sold out", "{name}已售罄", "{name}已售罄", "{name}이(가) 품절되었어요"),
  noteRejected: T("{name}は厨房でお受けできませんでした", "The kitchen couldn't take {name}", "厨房无法提供{name}", "廚房無法提供{name}", "주방에서 {name}을(를) 준비할 수 없어요"),
  noteLastOrder: T("まもなくラストオーダーです", "Last orders soon", "即将最后点餐", "即將最後點餐", "곧 마지막 주문이에요"),
  noteUpdated: T("ご注文の状況が更新されました", "Your order was updated", "订单状态已更新", "訂單狀態已更新", "주문 상태가 업데이트되었어요"),
  demoClock: T("デモの時刻 19:00", "Demo time 19:00", "演示时间 19:00", "示範時間 19:00", "데모 시각 19:00"),
  voiceUnavailable: T(
    "ただいま音声の会話は混み合っているか、本日の上限に達しました。文字とタッチでご注文いただけます。",
    "Voice is busy or has reached today's limit. You can still type or tap to order.",
    "语音繁忙或已达今日上限，仍可用文字或点选点餐。",
    "語音繁忙或已達今日上限，仍可用文字或點選點餐。",
    "음성이 혼잡하거나 오늘 한도에 도달했어요. 문자나 터치로 주문할 수 있어요.",
  ),
  voiceError: T("音声が途切れました。文字とタッチで続けられます。", "Voice was interrupted. You can keep going by typing or tapping.", "语音中断，可以继续用文字或点选。", "語音中斷，可以繼續用文字或點選。", "음성이 끊겼어요. 문자나 터치로 계속할 수 있어요."),
};

const status: Record<string, T> = {
  accepted: T("受付済み", "Received", "已接单", "已接單", "접수됨"),
  confirmed: T("厨房確認済み", "Kitchen confirmed", "厨房已确认", "廚房已確認", "주방 확인"),
  cooking: T("調理中", "Cooking", "制作中", "製作中", "조리 중"),
  served: T("提供済み", "Served", "已上菜", "已上菜", "제공됨"),
  rejected: T("お断り", "Declined", "无法提供", "無法提供", "제공 불가"),
  cancelled: T("取消", "Cancelled", "已取消", "已取消", "취소됨"),
};
const experts: Record<string, T> = {
  menu: T("メニュー", "Menu", "菜单", "菜單", "메뉴"),
  recommend: T("おすすめ", "Picks", "推荐", "推薦", "추천"),
  knowledge: T("お店の豆知識", "Store facts", "本店知识", "本店知識", "가게 정보"),
  allergy: T("アレルギー確認", "Allergy check", "过敏确认", "過敏確認", "알레르기 확인"),
  order: T("注文", "Order", "订单", "訂單", "주문"),
  staff: T("店員連絡", "Staff", "联系店员", "聯絡店員", "직원 연락"),
  bill: T("会計", "Bill", "结账", "結帳", "계산"),
};

export function t(lang: Lang, key: keyof typeof dict): string {
  return dict[key][lang] || dict[key].en;
}
export const statusLabel = (lang: Lang, s: string) => status[s]?.[lang] ?? s;
export const expertLabel = (lang: Lang, e: string) => experts[e]?.[lang] ?? e;

type Named = { code: string } & T;
const N = (code: string, ja: string, en: string, zhs: string, zht: string, ko: string): Named => ({ code, ...T(ja, en, zhs, zht, ko) });

// 申告画面のアレルゲン（よく使うもの）
export const ALLERGENS: Named[] = [
  N("shrimp", "えび", "Shrimp", "虾", "蝦", "새우"),
  N("crab", "かに", "Crab", "蟹", "蟹", "게"),
  N("wheat", "小麦", "Wheat", "小麦", "小麥", "밀"),
  N("egg", "卵", "Egg", "鸡蛋", "雞蛋", "달걀"),
  N("milk", "乳", "Milk", "乳制品", "乳製品", "우유"),
  N("peanut", "落花生", "Peanut", "花生", "花生", "땅콩"),
  N("buckwheat", "そば", "Buckwheat", "荞麦", "蕎麥", "메밀"),
  N("walnut", "くるみ", "Walnut", "核桃", "核桃", "호두"),
  N("cashew", "カシューナッツ", "Cashew", "腰果", "腰果", "캐슈너트"),
  N("sesame", "ごま", "Sesame", "芝麻", "芝麻", "참깨"),
  N("soybean", "大豆", "Soy", "大豆", "大豆", "대두"),
  N("squid", "いか", "Squid", "鱿鱼", "魷魚", "오징어"),
  N("salmon", "さけ", "Salmon", "三文鱼", "鮭魚", "연어"),
  N("mackerel", "さば", "Mackerel", "青花鱼", "鯖魚", "고등어"),
  N("chicken", "鶏肉", "Chicken", "鸡肉", "雞肉", "닭고기"),
  N("pork", "豚肉", "Pork", "猪肉", "豬肉", "돼지고기"),
  N("beef", "牛肉", "Beef", "牛肉", "牛肉", "소고기"),
  N("gelatin", "ゼラチン", "Gelatin", "明胶", "明膠", "젤라틴"),
];

export const DIETS: Named[] = [
  N("halal", "ハラール", "Halal", "清真", "清真", "할랄"),
  N("vegetarian", "ベジタリアン", "Vegetarian", "素食", "素食", "채식"),
  N("vegan", "ヴィーガン", "Vegan", "纯素", "純素", "비건"),
  N("gluten_free", "グルテンフリー", "Gluten-free", "无麸质", "無麩質", "글루텐 프리"),
  N("no_pork", "豚肉なし", "No pork", "不吃猪肉", "不吃豬肉", "돼지고기 없이"),
  N("no_alcohol", "お酒なし", "No alcohol", "不喝酒", "不喝酒", "술 없이"),
];

export function allergenName(lang: Lang, code: string): string {
  const a = ALLERGENS.find((x) => x.code === code);
  return a ? a[lang] : code;
}

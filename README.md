# NOREN

席に着いたら、テーブルの AI と話しているうちに、食べたいものが決まる。
着席から注文・会計まで画面ひとつで完結し、いつもの注文受けに店員の手はいりません。
人が判断すべき場面だけは、AI が見極めて店員につなぎます。

第5回 Agentic AI Hackathon with Google Cloud 応募作品（開発中）。

![アーキテクチャ](docs/architecture.png)

設計の詳細は [docs/design.md](docs/design.md) を参照してください。

## できること

- **お客様**（テーブルのタブレット。スマートフォンからも利用可）
  - 左に AI との会話（音声・文字）、右に写真付きのおすすめ・メニュー・注文。会話でもタッチでも同じカートを操作
  - 着席 → 全員のアレルギー・食事制限の申告 → お通し・支払い方法などのお知らせ → 雑談しながらおすすめ → 注文 → 追加・呼び出し → 会計
  - AI はメニュー・おすすめ・店舗知識・アレルギー判定・注文などの専門機能を同時に呼び、ひとつの答えにまとめます
  - 料理にはお客様ごとの判定（○ 食べられる / ✕ 含む / △ 混入の可能性 / ？ 確認できていない）。△ と ？ は自動では通さず店員の確認へ
  - お酒は人ごとに 20 歳以上・運転しないことを確認。「どなたの分か」は画面で選ぶ（誰が話しているかを推測しない）
  - タップで会話を開始し、話さなければ 20 秒で終了。急病の言葉は AI を待たずに店員へ。どの画面からでも緊急呼び出し
  - 品切れ・厨房のお断り・ラストオーダー 15 分前は画面のカードでお知らせ（マイクは勝手に開けない）
  - 言語：音声は日本語・英語、画面と文字の会話は日本語・英語・簡体字・繁体字・韓国語
- **店員**：料理ごとの注文ボード（注文から 20 分たっても出ていない料理を通知）、引き継ぎ（日本語の理由と要約、対応するまで再通知）、テーブルでの通訳、
  「出汁が切れた」の一言で影響する料理を探して品切れ（2 品以上は確定してから）、先払いの支払い確認、会計済み
- **店主**：メニュー写真の取り込み（書き写し → 食材の質問 → アレルゲン 29 品目はコードが換算 → 4 言語に翻訳）、
  業態（居酒屋・定食屋・ラーメン店・カフェ）、AI に任せる範囲（サービスポリシー）

## 技術スタック

Cloud Run（FastAPI + Agent Development Kit）· Gemini Live API（`gemini-3.8-live`）· Gemini API（`gemini-3.8-flash`）·
Firestore · Cloud Tasks · Vite + React

## ローカルで動かす

```bash
cp .env.example .env                                         # GCP プロジェクトなどを記入
gcloud emulators firestore start --host-port=127.0.0.1:8681  # Firestore エミュレーター（別のターミナル）
cd frontend && npm ci && npm run build && cd ..
cd backend && uv sync
FIRESTORE_EMULATOR_HOST=127.0.0.1:8681 FIRESTORE_PROJECT=demo-noren uv run uvicorn app.main:app --port 8080
```

ブラウザで http://127.0.0.1:8080/demo を開くと、お客様の画面と店員の画面が並びます（/owner は店主の画面）。
デモの店は訪問者ごとに作られ、時刻は 19:00 に固定しています。

認証は Application Default Credentials を使います（Cloud Run ではサービスアカウント）。API キーは不要です。

## テスト

| コマンド（backend から） | 内容 |
|---|---|
| `uv run pytest` | 業務ルール・Firestore（エミュレーター）・費用の保護・Cloud Tasks の単体・結合テスト |
| `uv run python ../scripts/e2e_voice.py` | 合成音声での会話例（どなたの分・お酒の確認・復唱・注文） |
| `uv run python ../scripts/eval_run.py` | 評価：AI が演じるお客様 × 接客エージェント（普通の一餐 8 件・止める/つなぐ 8 件） |
| `uv run python ../scripts/eval_judge.py <report.json>` | 評価：会話の質の採点 |
| `uv run python ../scripts/simulate_tables.py` | 複数卓の同時注文（在庫の取り合い・重複注文・応答時間） |
| `uv run python ../scripts/e2e_menu_import.py --image <menu.png>` | メニュー写真の取り込み |
| `uv run python ../scripts/e2e_store_agent.py` / `e2e_interpret.py` | 店舗エージェント / 通訳モード |
| `node ../frontend/tests/e2e_browser.mjs --mic <voice.wav>` | ヘッドレスブラウザの通しテスト（偽マイクの音声を含む） |

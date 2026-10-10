# NOREN

飲食店の「着席から注文・会計まで」を、テーブルの画面ひとつで。
お客様は AI との会話と写真付きメニューで注文し、店員は料理と、お支払いの受け取りに集中できます。
訪日外国人のお客様も、日本語・英語で安心して食事ができます。

第5回 Agentic AI Hackathon with Google Cloud 応募作品（開発中）。

## 主な機能（予定）

- **お客様**（テーブルのタブレット。スマートフォンからも利用可）
  - 画面の左に AI との会話（音声・文字）、右に写真付きのおすすめ・メニュー・注文内容。会話でもタッチでも、同じカートを操作します
  - 着席・人数入力 → お通しや支払い方法などの店舗ルールを案内 → アレルギー・食事制限の申告 → 注文 → 追加注文・呼び出し → 会計案内
  - アレルギー・食事制限に合わない料理は、AI の判断ではなくルールで確実に止めます
- **店員**：注文ボード、AI からの引き継ぎ（理由と要約つき）、「出汁が切れた」の一言で品切れを反映、会計済みでテーブルを閉じる
- **店主**：メニュー写真から料理データの下書きを作成（AI が下書き → 店主が確認して公開）、業態と店舗ルールの設定

## 技術スタック

Cloud Run（FastAPI + Agent Development Kit）· Gemini Enterprise Agent Platform（`gemini-3.8-live`）· Firestore · Cloud Logging · Vite + React

## 開発

```bash
cp .env.example .env        # GCP プロジェクトなどを記入
cd backend && uv sync
uv run python ../scripts/live_smoke.py        # Gemini Live の疎通確認
uv run python ../scripts/agent_tool_spike.py  # 音声エージェントからのサブエージェント呼び出しの検証
uv run uvicorn app.main:app --reload          # API サーバ
```

認証は Application Default Credentials を使います（ローカルは `gcloud auth application-default login`、Cloud Run はサービスアカウント）。API キーは不要です。

# NOREN

訪日外国人客が多い小規模飲食店のための、多言語 AI 接客エージェント。
第5回 Agentic AI Hackathon with Google Cloud 応募作品（開発中）。

## 構成（予定）

- **お客様**：テーブルの QR から音声 / タッチで注文（日本語・英語）
- **店員**：開卓・注文ボード・呼び出し / 引き継ぎ対応・品切れ設定
- **店主**：メニュー写真からの登録（AI 下書き → 店主確認）・店舗ルール設定

## 技術スタック

Cloud Run（FastAPI + Agent Development Kit）· Gemini API（`gemini-3.8-live`）· Firestore · Cloud Logging · Secret Manager · Vite + React

## 開発

```bash
cp .env.example .env        # GOOGLE_API_KEY などを記入
cd backend && uv sync
uv run python ../scripts/live_smoke.py   # Gemini Live の疎通確認
uv run uvicorn app.main:app --reload     # API サーバ
```

"""環境変数から読み込む設定（ローカルは .env、Cloud Run は環境変数）。"""

import os
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=REPO_ROOT / ".env", extra="ignore")

    google_api_key: str = Field("", validation_alias=AliasChoices("GOOGLE_API_KEY", "GEMINI_API_KEY"))
    google_genai_use_vertexai: bool = False
    google_cloud_project: str = ""
    google_cloud_location: str = "asia-northeast1"
    google_application_credentials: str = ""

    live_model: str = "gemini-3.8-live"
    # Agent Platform の gemini-3.8-live は us / eu リージョンのみ（東京なし）
    live_location: str = "us-central1"
    live_voice: str = "Kore"

    # Firestore（ローカルはエミュレーター：FIRESTORE_EMULATOR_HOST を設定する）
    firestore_project: str = ""
    # デモ店の cookie の署名鍵（未設定なら起動ごとに生成。Cloud Run では環境変数で固定する）
    demo_secret: str = ""
    # デモ店の時刻（ラストオーダーなどの判定に使う）。審査員がいつ試してもいいように固定する。空なら実時刻
    demo_clock: str = "19:00"

    # 音声ではない処理（メニュー取り込み・店舗エージェント）のモデル。global エンドポイントだけで提供
    text_model: str = "gemini-3.8-flash"
    text_location: str = "global"

    # 音声の会話のサイト全体の上限（1 日あたりの分）。超えたら文字とタッチだけになる
    daily_voice_minutes: int = 300
    # 訪問者（デモ店）ごとの音声の上限（1 日あたりの分）。評価をまとめて回すときは環境変数で上げる
    per_visitor_voice_minutes: int = 15

    # コスト対策：無通話の Live セッションは閉じ、同時セッション数に上限を設ける
    live_idle_timeout_s: int = 120
    live_max_concurrent_sessions: int = 20


settings = Settings()

# ローカルでは、個人アカウントの ADC ファイルを必ず使う（マシン既定の ADC は使わない）。
# Google のクライアントは環境変数から認証情報を探すので、ここで設定しておく。Cloud Run では設定しない。
if settings.google_application_credentials:
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = settings.google_application_credentials
if settings.google_genai_use_vertexai:
    os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "TRUE")
    os.environ.setdefault("GOOGLE_CLOUD_PROJECT", settings.google_cloud_project)

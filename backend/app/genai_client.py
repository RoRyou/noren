"""Gemini への接続方法をここ 1 か所で決める。

- Agent Platform（Vertex AI）：GCP の請求先アカウントに課金。認証は ADC。
  ローカルでは ADC を専用ファイル（GOOGLE_APPLICATION_CREDENTIALS）に置き、
  マシン上の他の gcloud ログインと混ざらないようにする。Cloud Run では
  サービスアカウントを使い、この変数は設定しない。
- Gemini API（AI Studio の API キー）：予備のみ。
"""

import os

from google import genai

from app.config import settings


def make_client() -> genai.Client:
    if settings.google_genai_use_vertexai:
        if settings.google_application_credentials:
            os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", settings.google_application_credentials)
        return genai.Client(
            vertexai=True,
            project=settings.google_cloud_project,
            location=settings.live_location,
        )
    return genai.Client(api_key=settings.google_api_key)


def describe() -> str:
    if settings.google_genai_use_vertexai:
        return f"agent-platform project={settings.google_cloud_project} location={settings.live_location}"
    return "gemini-api (api key)"

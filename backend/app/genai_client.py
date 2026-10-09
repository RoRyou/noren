"""Single place that decides how we talk to Gemini.

- Agent Platform (Vertex): billed to the GCP billing account. Auth = ADC.
  Locally, ADC lives in a dedicated file (GOOGLE_APPLICATION_CREDENTIALS) so it
  never mixes with other gcloud logins on the machine. On Cloud Run, the service
  account is used and the variable is not set.
- Gemini API (AI Studio key): fallback only.
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

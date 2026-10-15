#!/usr/bin/env bash
# NOREN を Cloud Run にデプロイする（GCP プロジェクト noren-vol5）。
#
# 作るもの：API の有効化、Firestore（asia-northeast1、作成後は場所を変えられない）、Cloud Run 用のサービスアカウント、
# Cloud Tasks のキュー、Cloud Run サービス（公開。審査員が使う）、Cloud Scheduler の見回り（1 分ごと）。
# 何度実行してもよい（ある物は作り直さない）。
#
# 使い方：
#   CLOUDSDK_CONFIG=~/.config/gcloud-noren gcloud auth login   # 最初に一度
#   scripts/deploy.sh            # 実行前に、使うアカウントとプロジェクトを表示して確認する
set -euo pipefail

export CLOUDSDK_CONFIG="${CLOUDSDK_CONFIG:-$HOME/.config/gcloud-noren}"
PROJECT="${PROJECT:-noren-vol5}"
REGION="${REGION:-asia-northeast1}"
SERVICE="${SERVICE:-noren}"
QUEUE="${QUEUE:-noren-timers}"
SA_NAME="noren-run"
SA="${SA_NAME}@${PROJECT}.iam.gserviceaccount.com"
SECRET_FILE="${CLOUDSDK_CONFIG}/noren_demo_secret"  # デモ店の cookie の署名鍵（デプロイし直しても変えない）

g() { gcloud --project "$PROJECT" --quiet "$@"; }
step() { printf '\n== %s\n' "$*"; }

ACCOUNT="$(gcloud config get-value account 2>/dev/null || true)"
echo "アカウント：${ACCOUNT:-（未ログイン）}"
echo "プロジェクト：$PROJECT  リージョン：$REGION  サービス：$SERVICE"
[ -n "$ACCOUNT" ] || { echo "先に gcloud auth login してください"; exit 1; }
read -r -p "このアカウントとプロジェクトで続けますか？ [y/N] " ok
[ "$ok" = "y" ] || exit 1

step "API を有効にする"
g services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com \
  firestore.googleapis.com cloudtasks.googleapis.com cloudscheduler.googleapis.com aiplatform.googleapis.com

step "Firestore（ネイティブモード、${REGION}）"
g firestore databases describe --database='(default)' >/dev/null 2>&1 \
  || g firestore databases create --database='(default)' --location="$REGION" --type=firestore-native

step "サービスアカウントと権限"
g iam service-accounts describe "$SA" >/dev/null 2>&1 \
  || g iam service-accounts create "$SA_NAME" --display-name="NOREN Cloud Run"
for role in roles/datastore.user roles/aiplatform.user roles/cloudtasks.enqueuer roles/logging.logWriter; do
  g projects add-iam-policy-binding "$PROJECT" --member="serviceAccount:$SA" --role="$role" --condition=None >/dev/null
done
# Cloud Tasks・Cloud Scheduler が、このサービスアカウントの OIDC トークンを付けて呼べるように
g iam service-accounts add-iam-policy-binding "$SA" --member="serviceAccount:$SA" \
  --role=roles/iam.serviceAccountUser >/dev/null
# ソースからのビルド（Cloud Build）は Compute Engine の既定のサービスアカウントで動く
NUMBER="$(g projects describe "$PROJECT" --format='value(projectNumber)')"
g projects add-iam-policy-binding "$PROJECT" --member="serviceAccount:${NUMBER}-compute@developer.gserviceaccount.com" \
  --role=roles/run.builder --condition=None >/dev/null

step "Cloud Tasks のキュー"
g tasks queues describe "$QUEUE" --location="$REGION" >/dev/null 2>&1 \
  || g tasks queues create "$QUEUE" --location="$REGION" --max-attempts=5

step "デモ店の署名鍵"
if [ ! -s "$SECRET_FILE" ]; then
  umask 077
  openssl rand -hex 32 > "$SECRET_FILE"
fi
DEMO_SECRET="$(cat "$SECRET_FILE")"

ENV_VARS="GOOGLE_GENAI_USE_VERTEXAI=TRUE,GOOGLE_CLOUD_PROJECT=$PROJECT,FIRESTORE_PROJECT=$PROJECT"
ENV_VARS="$ENV_VARS,LIVE_LOCATION=us-central1,DEMO_SECRET=$DEMO_SECRET"
ENV_VARS="$ENV_VARS,TASKS_QUEUE=$QUEUE,TASKS_LOCATION=$REGION,TASKS_SERVICE_ACCOUNT=$SA"
ENV_VARS="$ENV_VARS,PER_VISITOR_VOICE_MINUTES=${PER_VISITOR_VOICE_MINUTES:-30}"  # 審査員が何度か試せるように

step "Cloud Run にデプロイ（ソースからビルド）"
# 最大 1 インスタンス：画面への配信（Hub）と店ごとのロックがプロセス内のため（ZI-045、ZI-046）
# タイムアウト 3600 秒：音声の WebSocket を長く保つ。セッションアフィニティ：再接続で同じインスタンスへ
cd "$(dirname "$0")/.."
g run deploy "$SERVICE" --source . --region="$REGION" --service-account="$SA" \
  --allow-unauthenticated --max-instances=1 --min-instances=0 --timeout=3600 --session-affinity \
  --cpu=1 --memory=1Gi --cpu-boost --set-env-vars="$ENV_VARS"

URL="$(g run services describe "$SERVICE" --region="$REGION" --format='value(status.url)')"
step "公開 URL を設定：$URL"
g run services update "$SERVICE" --region="$REGION" --update-env-vars="PUBLIC_BASE_URL=$URL" >/dev/null

step "見回り（Cloud Scheduler、1 分ごと）"
if g scheduler jobs describe noren-sweep --location="$REGION" >/dev/null 2>&1; then
  g scheduler jobs update http noren-sweep --location="$REGION" --schedule="* * * * *" \
    --uri="$URL/api/tasks/sweep_all" --http-method=POST --oidc-service-account-email="$SA" --oidc-token-audience="$URL"
else
  g scheduler jobs create http noren-sweep --location="$REGION" --schedule="* * * * *" \
    --uri="$URL/api/tasks/sweep_all" --http-method=POST --oidc-service-account-email="$SA" --oidc-token-audience="$URL"
fi

step "確認"
curl -fsS "$URL/api/healthz" && echo
echo "デモ：$URL/demo"

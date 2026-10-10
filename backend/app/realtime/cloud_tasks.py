"""Cloud Tasks を使った時間指定の処理（本番用）。

- タスクは「指定した時刻に /api/tasks/{name} を呼ぶ」HTTP タスク。失敗したら Cloud Tasks が再試行する
- タスク名に処理のキーを入れるので、同じ処理は 1 回だけ登録される（AlreadyExists は無視）
- 呼び出しには OIDC トークン（サービスアカウント）を付け、受け側で検証する
settings.tasks_queue が空なら使わない（ローカルでは LocalScheduler を使う）。
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

from google.api_core import exceptions
from google.cloud import tasks_v2
from google.protobuf import timestamp_pb2

from app.config import settings
from app.realtime.scheduler import Handler


class CloudTasksScheduler:
    def __init__(self, client: tasks_v2.CloudTasksAsyncClient | None = None):
        self.client = client or tasks_v2.CloudTasksAsyncClient()
        self.handlers: dict[str, Handler] = {}
        self.queue = self.client.queue_path(settings.google_cloud_project, settings.tasks_location, settings.tasks_queue)

    def register(self, name: str, handler: Handler) -> None:
        self.handlers[name] = handler

    def task_id(self, task_name: str) -> str:
        # Cloud Tasks のタスク名に使える文字だけにする
        return "t-" + hashlib.sha256(task_name.encode()).hexdigest()[:40]

    async def schedule_async(self, delay_s: float, name: str, payload: dict, task_name: str) -> bool:
        when = timestamp_pb2.Timestamp()
        when.FromDatetime(datetime.now(timezone.utc) + timedelta(seconds=delay_s))
        task = tasks_v2.Task(
            name=f"{self.queue}/tasks/{self.task_id(task_name)}",
            schedule_time=when,
            http_request=tasks_v2.HttpRequest(
                http_method=tasks_v2.HttpMethod.POST,
                url=f"{settings.public_base_url}/api/tasks/run/{name}",
                headers={"Content-Type": "application/json"},
                body=json.dumps(payload).encode(),
                oidc_token=tasks_v2.OidcToken(service_account_email=settings.tasks_service_account,
                                              audience=settings.public_base_url),
            ),
        )
        try:
            await self.client.create_task(parent=self.queue, task=task)
            return True
        except exceptions.AlreadyExists:
            return False

    def schedule(self, delay_s: float, name: str, payload: dict, task_name: str) -> bool:
        import asyncio

        asyncio.create_task(self.schedule_async(delay_s, name, payload, task_name))
        return True

    async def drain(self) -> None:
        return None


def verify_oidc(authorization: str | None) -> bool:
    """Cloud Tasks / Cloud Scheduler からの呼び出しか（OIDC トークンの検証）。"""
    if not authorization or not authorization.startswith("Bearer "):
        return False
    from google.auth.transport import requests as g_requests
    from google.oauth2 import id_token

    try:
        claims = id_token.verify_oauth2_token(authorization[7:], g_requests.Request(), audience=settings.public_base_url)
    except Exception:
        return False
    return claims.get("email") == settings.tasks_service_account and claims.get("email_verified", False)

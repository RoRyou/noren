"""Cloud Tasks のスケジューラ（実際の Cloud Tasks の代わりに偽のクライアントで確認）と、タスク受け口の認証。"""

from fastapi.testclient import TestClient
from google.api_core import exceptions

from app.config import settings
from app.realtime.cloud_tasks import CloudTasksScheduler


class FakeClient:
    def __init__(self):
        self.created = []

    def queue_path(self, project, location, queue):
        return f"projects/{project}/locations/{location}/queues/{queue}"

    async def create_task(self, parent, task):
        if any(t.name == task.name for t in self.created):
            raise exceptions.AlreadyExists("dup")
        self.created.append(task)


async def test_schedules_http_task_once(monkeypatch):
    monkeypatch.setattr(settings, "tasks_queue", "noren-reminders")
    monkeypatch.setattr(settings, "public_base_url", "https://noren.example.run.app")
    monkeypatch.setattr(settings, "tasks_service_account", "tasks@noren.iam.gserviceaccount.com")
    fake = FakeClient()
    s = CloudTasksScheduler(client=fake)
    assert await s.schedule_async(30, "escalation_followup", {"escalation_id": "e1"}, "store:e1:30")
    assert not await s.schedule_async(30, "escalation_followup", {"escalation_id": "e1"}, "store:e1:30")  # 同じ処理は 1 回
    task = fake.created[0]
    assert task.http_request.url == "https://noren.example.run.app/api/tasks/run/escalation_followup"
    assert task.http_request.oidc_token.service_account_email == "tasks@noren.iam.gserviceaccount.com"
    assert len(fake.created) == 1


def test_task_endpoint_requires_oidc():
    from app.main import app

    c = TestClient(app)
    assert c.post("/api/tasks/run/escalation_followup", json={}).status_code == 403
    assert c.post("/api/tasks/sweep_all").status_code == 403
    assert c.post("/api/tasks/run/escalation_followup", json={}, headers={"authorization": "Bearer nope"}).status_code == 403

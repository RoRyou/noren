"""費用の保護（デモ店の作成数、音声の 1 日の上限）。"""

import os
import socket
import uuid

import pytest
from google.cloud import firestore

from app.api import limits
from app.config import settings

EMULATOR = os.environ.setdefault("FIRESTORE_EMULATOR_HOST", "127.0.0.1:8681")


def _up() -> bool:
    h, p = EMULATOR.split(":")
    try:
        socket.create_connection((h, int(p)), timeout=0.5).close()
        return True
    except OSError:
        return False


def test_demo_creation_per_ip():
    ip = f"10.0.0.{uuid.uuid4().int % 250}"
    assert all(limits.allow_demo_creation(ip) for _ in range(limits.DEMO_PER_IP_HOUR))
    assert not limits.allow_demo_creation(ip)


@pytest.mark.skipif(not _up(), reason="Firestore emulator is not running")
async def test_voice_budget(monkeypatch):
    client = firestore.AsyncClient(project=f"demo-limits-{uuid.uuid4().hex[:6]}")
    budget = limits.VoiceBudget(client)
    assert await budget.check("demo_a") is None
    await budget.add("demo_a", settings.per_visitor_voice_minutes * 60)
    assert await budget.check("demo_a") == "visitor_limit"
    assert await budget.check("demo_b") is None
    monkeypatch.setattr(settings, "daily_voice_minutes", 15)
    assert await budget.check("demo_b") == "daily_limit"

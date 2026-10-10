"""費用の保護（公開 URL を 12/01 まで開けておくため）。

- デモ店の作成：同じ IP から 1 時間に DEMO_PER_IP_HOUR 店まで
- 音声の会話：訪問者（デモ店）ごとに 1 日 PER_VISITOR_DAILY_S 秒、サイト全体で 1 日 settings.daily_voice_minutes 分まで
- 上限を超えたら音声は開かず、文字とタッチで続けてもらう（画面に理由を出す）
使った秒数は Firestore の usage/{日付} に記録する（インスタンスが再起動しても消えない）。
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from datetime import datetime

from google.cloud import firestore

from app.config import settings
from app.store.repo import JST

DEMO_PER_IP_HOUR = 10
PER_VISITOR_DAILY_S = 15 * 60  # 既定値（settings.per_visitor_voice_minutes で上書き）

_demo_creations: dict[str, deque] = defaultdict(deque)


def allow_demo_creation(ip: str) -> bool:
    now = time.time()
    q = _demo_creations[ip]
    while q and now - q[0] > 3600:
        q.popleft()
    if len(q) >= DEMO_PER_IP_HOUR:
        return False
    q.append(now)
    return True


def _today() -> str:
    return datetime.now(JST).strftime("%Y-%m-%d")


class VoiceBudget:
    def __init__(self, client: firestore.AsyncClient):
        self.client = client

    def _doc(self):
        return self.client.collection("usage").document(_today())

    async def check(self, store_id: str) -> str | None:
        """音声を開いてよければ None、だめなら理由（visitor_limit / daily_limit）。"""
        snap = await self._doc().get()
        data = snap.to_dict() or {}
        if data.get("total_s", 0) >= settings.daily_voice_minutes * 60:
            return "daily_limit"
        if data.get("by_store", {}).get(store_id, 0) >= settings.per_visitor_voice_minutes * 60:
            return "visitor_limit"
        return None

    async def add(self, store_id: str, seconds: float) -> None:
        s = int(seconds + 0.999)
        if s <= 0:
            return
        await self._doc().set({"total_s": firestore.Increment(s), "by_store": {store_id: firestore.Increment(s)}}, merge=True)
